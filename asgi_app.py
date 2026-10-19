#!/usr/bin/env python3
"""Single-origin ASGI app: 申請フォーム static site + bento ordering API.

Why this file exists
--------------------
The form was served by ``search_server.py`` (a ThreadingHTTPServer) while the
bento ordering API was a separate FastAPI service. That split meant:

* two Render services, two deploys, two places to keep env vars;
* CORS to configure, because the API lived on another origin;
* the LIFF iframe loaded ``bento/index.html`` from the form host but had to call
  an API on a different host.

Running one ASGI app on one port collapses all of that: static files, the
existing ``/schedules`` endpoints, and the bento ``/api/*`` routers share an
origin, so requests are same-origin and CORS stops being a factor.

Layout
------
* ``GET/POST/PUT/DELETE /schedules`` — unchanged behaviour from search_server.py,
  including the JST (Asia/Tokyo) timestamps and the past-date-free semantics.
* ``/api/*`` — the bento routers from ``projects/bento-liff/backend``.
* everything else — static files from this directory.

Startup
-------
``prod`` mode runs the bento settings' production checks and refuses to boot on
an incomplete configuration (see bento ``app/config.py``). Missing LINE channel
credentials therefore fail loudly at startup rather than silently at runtime.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent.resolve()
PORT = int(os.environ.get("PORT", "10000"))
SCHEDULES_FILE = BASE_DIR / "schedules.json"
JST = timezone(timedelta(hours=9))

logger = logging.getLogger("uvicorn.error")

# The bento backend is committed alongside this app at ./bento_backend (mirror
# it with sync_bento_backend.py after editing projects/bento-liff/backend). The
# development-tree path stays as a fallback so local runs work either way.
#
# 逆順で insert する: sys.path.insert(0, ...) は後から入れたものを先頭に
# 置くため、正順で回すと fallback の projects ツリーがコミット済みの
# bento_backend をシャドウしてしまう。このマシンには両方存在し、
# projects 側の db.py は bind() を持たない旧版(SQLite専用)なので、認証が
# 誤ったモジュールを引き「cannot import name 'bind'」で全リクエストが
# 401 になる。コミット済みの bento_backend を最優先にする。
_BENTO_CANDIDATES = [
    BASE_DIR / "bento_backend",
    BASE_DIR.parent / "projects" / "bento-liff" / "backend",
]
for _cand in reversed(_BENTO_CANDIDATES):
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

app = FastAPI(title="申請フォーム + お弁当注文", docs_url="/api/docs", redoc_url=None)

# Declared up front so /health (defined below) can report the mount state; the
# actual import that flips it to True happens further down, before the routers
# are included.
_bento_loaded = False
_bento_error = ""

# --------------------------------------------------------------------------- #
# schedules storage — Postgres when DATABASE_URL is set, else schedules.json.
# --------------------------------------------------------------------------- #

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    try:
        import psycopg  # type: ignore
    except Exception as _pg_exc:  # noqa: BLE001
        logger.error("DATABASE_URL is set but psycopg is not installed: %s", _pg_exc)
        raise
    from schedules_db import (  # type: ignore
        list_schedules as _pg_list,
        insert_schedule as _pg_insert,
        update_schedule as _pg_update,
        delete_schedule as _pg_delete,
    )
    from bike_db import (  # type: ignore
        list_logs as _pg_bike_list,
        insert_log as _pg_bike_insert,
        update_log as _pg_bike_update,
        delete_log as _pg_bike_delete,
        monthly_summary as _pg_bike_summary,
    )

    def load_schedules() -> list[dict]:
        return _pg_list()

    def save_schedules(rows: list[dict]) -> None:  # noqa: D401 - legacy shim
        # Bulk replace: delete everything then re-insert. Cheap on this table
        # size and avoids diverging from the JSON-file semantics (full snapshot).
        try:
            with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM schedules")
                    for row in rows:
                        cur.execute(
                            "INSERT INTO schedules (id, title, date, time, description, created) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (
                                row.get("id"),
                                row.get("title"),
                                row.get("date"),
                                row.get("time", ""),
                                row.get("description", ""),
                                row.get("created", ""),
                            ),
                        )
        except Exception as exc:
            logger.error("save_schedules (pg) failed: %s", exc)
            raise

    def _upsert_schedule(schedule: dict) -> None:
        _pg_insert(schedule)

    def _update_schedule(schedule_id: str, updates: dict) -> dict | None:
        return _pg_update(schedule_id, updates)

    def _delete_schedule(schedule_id: str) -> bool:
        return _pg_delete(schedule_id)

    def _append_bike_log(entry: dict) -> None:
        _pg_bike_insert(entry)

    def _update_bike_log(log_id: str, updates: dict) -> dict | None:
        return _pg_bike_update(log_id, updates)

    def _delete_bike_log(log_id: str) -> bool:
        return _pg_bike_delete(log_id)

    def load_bike_logs() -> list[dict]:
        return _pg_bike_list()

    def get_bike_summary(year_month: str) -> dict:
        return _pg_bike_summary(year_month)

    logger.info("Schedules store: Postgres (%s...)", DATABASE_URL[:32])
else:
    def load_schedules() -> list[dict]:
        try:
            with SCHEDULES_FILE.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_schedules(rows: list[dict]) -> None:
        with SCHEDULES_FILE.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)

    def _upsert_schedule(schedule: dict) -> None:
        rows = load_schedules()
        rows.append(schedule)
        save_schedules(rows)

    def _update_schedule(schedule_id: str, updates: dict) -> dict | None:
        rows = load_schedules()
        for idx, row in enumerate(rows):
            if row.get("id") == schedule_id:
                updated = dict(row)
                for key in ("title", "date", "time", "description"):
                    if key in updates:
                        updated[key] = str(updates[key]).strip()
                rows[idx] = updated
                save_schedules(rows)
                return updated
        return None

    def _delete_schedule(schedule_id: str) -> bool:
        rows = load_schedules()
        kept = [r for r in rows if r.get("id") != schedule_id]
        if len(kept) == len(rows):
            return False
        save_schedules(kept)
        return True

    BIKE_FILE = BASE_DIR / "bike_logs.json"

    def load_bike_logs() -> list[dict]:
        try:
            with BIKE_FILE.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_bike_logs(rows: list[dict]) -> None:
        with BIKE_FILE.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)

    def _append_bike_log(entry: dict) -> None:
        rows = load_bike_logs()
        rows.append(entry)
        save_bike_logs(rows)

    def _update_bike_log(log_id: str, updates: dict) -> dict | None:
        rows = load_bike_logs()
        for idx, row in enumerate(rows):
            if row.get("id") == log_id:
                updated = dict(row)
                for key, v in updates.items():
                    updated[key] = v
                rows[idx] = updated
                save_bike_logs(rows)
                return updated
        return None

    def _delete_bike_log(log_id: str) -> bool:
        rows = load_bike_logs()
        kept = [r for r in rows if r.get("id") != log_id]
        if len(kept) == len(rows):
            return False
        save_bike_logs(kept)
        return True

    def get_bike_summary(year_month: str) -> dict:
        # Local JSON: compute the same shape on the fly.
        rows = [r for r in load_bike_logs() if str(r.get("date", "")).startswith(year_month)]
        total_km = sum(float(r.get("distance_km", 0) or 0) for r in rows)
        total_min = sum(int(r.get("duration_min", 0) or 0) for r in rows)
        avg = (total_km / (total_min / 60.0)) if total_min else 0.0
        return {
            "year_month": year_month,
            "count": len(rows),
            "total_km": round(total_km, 2),
            "total_min": total_min,
            "avg_speed_kmh": round(avg, 2),
        }

    logger.info("Schedules store: JSON file (%s)", SCHEDULES_FILE)


def _schedule_from_payload(data: dict) -> dict:
    title = str(data.get("title", "")).strip()
    date = str(data.get("date", "")).strip()
    if not title or not date:
        raise HTTPException(status_code=400, detail="title and date are required")
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "date": date,
        "time": str(data.get("time", "")).strip(),
        "description": str(data.get("description", "")).strip(),
        "created": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900"),
    }


@app.get("/health")
def health() -> dict:
    payload = {"status": "ok", "port": PORT, "schedules": len(load_schedules()),
               "schedules_store": "postgres" if USE_POSTGRES else "json"}
    # The bike tab is a static file under /bike/. If that directory is missing
    # on the deployed build (push failed, sync script skipped it, ...), the
    # tab will 404 in the LIFF with no clue why. Probe it here so one GET
    # distinguishes "code bug" from "the file did not make it into the image".
    payload["bike_dir_exists"] = (BASE_DIR / "bike").is_dir()
    payload["bike_dir_listing"] = sorted(
        p.name for p in (BASE_DIR / "bike").iterdir()
    ) if (BASE_DIR / "bike").is_dir() else None
    # Same probe for the household tab: a 404 on /household/index.html after
    # a deploy is otherwise indistinguishable from "the directory never made
    # it into the build". New static mounts can also 404 for ~30s while the
    # process is mid-restart, so this boolean is what tells the two apart.
    payload["household_dir_exists"] = (BASE_DIR / "household").is_dir()
    payload["household_dir_listing"] = sorted(
        p.name for p in (BASE_DIR / "household").iterdir()
    ) if (BASE_DIR / "household").is_dir() else None
    # Same probe for the stocks tab: its own 404 mode is the missing
    # directory, and the iframe URL is the child the host hands over.
    payload["stocks_dir_exists"] = (BASE_DIR / "stocks").is_dir()
    payload["stocks_dir_listing"] = sorted(
        p.name for p in (BASE_DIR / "stocks").iterdir()
    ) if (BASE_DIR / "stocks").is_dir() else None
    # Report whether the ordering DB is actually reachable. /api/auth/login
    # returned a bare 500 with no body on Render while /health stayed green, so
    # "the app is mounted" was indistinguishable from "the database works".
    # Probe it here so one request tells us which half is broken.
    try:
        from app.db import bind, get_conn, kind  # type: ignore

        _conn = get_conn()
        _row = _conn.execute(
            bind("SELECT COUNT(*) AS n FROM employees")
        ).fetchone()
        # Reflect the *actual* backend the bento module connected to. Reading
        # BENTO_DATABASE_PATH was misleading once DATABASE_URL takes over (the
        # env var still holds the old SQLite path, so /health claimed SQLite
        # while the API was happily using Postgres).
        backend = kind()
        path = None
        if backend == "sqlite":
            from app.config import get_settings  # type: ignore
            path = get_settings().database_path
        payload["db"] = {
            "reachable": True,
            "employees": _row["n"] if _row else 0,
            "path": path,
            "backend": backend,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        payload["db"] = {
            "reachable": False,
            "error": f"{exc.__class__.__name__}: {exc}",
            "path": None,
            "backend": "unknown",
        }
    # The bento settings object is optional here: the form site must stay usable
    # even when the ordering backend is not wired in. Report the mode when we
    # can read it and say so plainly when we cannot.
    try:
        from app.config import get_settings  # type: ignore

        _s = get_settings()
        payload["bento_app_env"] = _s.app_env
        payload["line_verification"] = _s.line_verification_enabled
    except Exception as exc:
        payload["bento"] = f"unavailable ({exc.__class__.__name__})"
    payload["bento_api_mounted"] = _bento_loaded
    return payload


@app.get("/schedules")
def get_schedules() -> dict:
    return {"schedules": load_schedules()}


@app.post("/schedules")
@app.post("/create")
async def create_schedule(payload: dict = Body(...)) -> JSONResponse:
    new = _schedule_from_payload(payload)
    if USE_POSTGRES:
        _upsert_schedule(new)
    else:
        rows = load_schedules()
        rows.append(new)
        save_schedules(rows)
    return JSONResponse(status_code=201, content={"success": True, "schedule": new})


@app.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: dict = Body(...)) -> JSONResponse:
    updated = _update_schedule(schedule_id, payload)
    if not updated:
        return JSONResponse(status_code=404, content={"success": False, "error": "not found"})
    return JSONResponse(content={"success": True, "schedule": updated})


@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str) -> JSONResponse:
    if _delete_schedule(schedule_id):
        return JSONResponse(content={"success": True, "message": "Schedule deleted"})
    return JSONResponse(status_code=404, content={"success": False, "error": "not found"})


# --------------------------------------------------------------------------- #
# Runtime configuration endpoint.
#
# Tabs that need user-tunable values (GPS sampling interval, map defaults,
# page title) fetch this on boot. Reading env vars at request time means the
# dashboard change applies on the next cold start with no code change.
# Missing env vars fall back to the documented defaults so a fresh deploy
# is functional without dashboard work.
#
# Privacy: only non-secret tunables are exposed. LINE credentials and
# DATABASE_URL stay on the server.
# --------------------------------------------------------------------------- #


def _parse_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_center(value: str) -> list[float]:
    """``"lat,lng"`` -> [lat, lng]. Falls back to default on any parse error."""
    default = [35.0116, 135.7681]  # 草津駅
    if not value:
        return default
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 2:
        return default
    try:
        lat = float(parts[0])
        lng = float(parts[1])
    except (TypeError, ValueError):
        return default
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return default
    return [lat, lng]


@app.get("/api/runtime-config")
def get_runtime_config() -> dict:
    """Return tunables the frontend reads on boot.

    Path is ``/api/runtime-config`` (not ``/api/config``) because the bento
    ordering app already mounts ``GET /api/config`` for its own purposes
    via ``app.include_router(config_api.router)``, and FastAPI's router
    registration order lets the later one win — moving ours off the
    colliding path avoids a silent shadow.

    Anything not set in the Render dashboard falls back to the documented
    default. The shape is stable; adding a new key is non-breaking.
    """
    return {
        "gps_sample_interval_ms": _parse_int(
            os.environ.get("GPS_SAMPLE_INTERVAL_MS", ""), 180_000
        ),
        "route_map_default_zoom": _parse_int(
            os.environ.get("ROUTE_MAP_DEFAULT_ZOOM", ""), 11
        ),
        "route_map_default_center": _parse_center(
            os.environ.get("ROUTE_MAP_DEFAULT_CENTER", "")
        ),
        "bike_page_title": os.environ.get("BIKE_PAGE_TITLE", "自転車ログ") or "自転車ログ",
        # Route-detail map styling. All four accept a default if the env
        # var is missing or empty, and any unparseable colour string falls
        # back to the hard-coded value so a typo in the dashboard does
        # not break the map.
        "route_line_color_selected": (
            os.environ.get("ROUTE_LINE_COLOR_SELECTED", "").strip() or "#16a34a"
        ),
        "route_line_color_default": (
            os.environ.get("ROUTE_LINE_COLOR_DEFAULT", "").strip() or "#94a3b8"
        ),
        "route_line_weight_selected": _parse_int(
            os.environ.get("ROUTE_LINE_WEIGHT_SELECTED", ""), 8
        ),
        "route_line_weight_default": _parse_int(
            os.environ.get("ROUTE_LINE_WEIGHT_DEFAULT", ""), 3
        ),
        "route_marker_radius": _parse_int(
            os.environ.get("ROUTE_MARKER_RADIUS", ""), 9
        ),
    }


# --------------------------------------------------------------------------- #
# Weather forecast (Open-Meteo, no API key, 16-day forecast).
#
# The client posts the rider's current position (or a fallback default)
# and the server proxies to Open-Meteo. The response is cached in
# memory keyed by rounded (lat, lng) so the upstream API is called at
# most once per cache-window per cluster of nearby users, not once per
# request. Cache TTL defaults to 6 hours — short enough that the
# forecast updates whenever Open-Meteo refreshes (~hourly), long enough
# to absorb the day-to-day traffic from a small team.
#
# The proxy is required so the client never has to know the upstream URL
# or the cache policy. The bento auth gate does not protect this
# endpoint because the forecast is non-sensitive; the LIFF guard and
# the employees-table check on the host still cover write operations.
# --------------------------------------------------------------------------- #
_WEATHER_CACHE: dict[str, dict] = {}
_WEATHER_CACHE_LOCK_NAME = "weather_cache_lock"
try:
    import threading as _threading
    _WEATHER_CACHE_LOCK = _threading.Lock()
except Exception:  # pragma: no cover - threading is always available on CPython
    _WEATHER_CACHE_LOCK = None


def _weather_cache_key(lat: float, lng: float) -> str:
    # Round to 0.01° (~1.1 km) so users within the same neighbourhood
    # share a cache slot. The forecast at this resolution is identical
    # for practical purposes.
    return f"{round(lat, 2):.2f},{round(lng, 2):.2f}"


def _weather_cache_ttl_seconds() -> int:
    return _parse_int(os.environ.get("WEATHER_CACHE_HOURS", ""), 6) * 3600


def _parse_weather_args(lat: str, lng: str) -> tuple[float, float] | None:
    try:
        la = float(lat)
        ln = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and -180 <= ln <= 180):
        return None
    return la, ln


def _fetch_open_meteo(lat: float, lng: float) -> dict:
    """Hit Open-Meteo and return a normalised JSON payload.

    Raises ``urllib.error.URLError`` on network failure, ``TimeoutError`` on
    a slow upstream. The caller wraps these in a 502.
    """
    params = {
        "latitude": str(lat),
        "longitude": str(lng),
        "current": "temperature_2m,wind_speed_10m,wind_direction_10m,weather_code",
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max,wind_direction_10m_dominant,uv_index_max",
        "forecast_days": "16",
        "timezone": "Asia/Tokyo",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "liff-drawing-search/1.0 (weather-proxy)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


@app.get("/api/weather")
def get_weather_forecast(lat: str, lng: str) -> JSONResponse:
    """Return the 16-day Open-Meteo forecast for the given coordinates.

    ``lat`` and ``lng`` are required query parameters. The server caches
    the upstream response in-memory and serves a 503 if both the cache
    and Open-Meteo are unreachable, rather than fabricating data.
    """
    parsed = _parse_weather_args(lat, lng)
    if not parsed:
        raise HTTPException(status_code=400, detail="lat/lng must be valid coordinates")
    la, ln = parsed
    key = _weather_cache_key(la, ln)
    now = time.time()
    ttl = _weather_cache_ttl_seconds()
    if _WEATHER_CACHE_LOCK:
        with _WEATHER_CACHE_LOCK:
            entry = _WEATHER_CACHE.get(key)
        if entry and (now - entry["ts"]) < ttl:
            payload = entry["data"]
            return JSONResponse(
                content={
                    "cached": True,
                    "cached_at": entry["ts"],
                    "latitude": la,
                    "longitude": ln,
                    "place_name": entry.get("place_name"),
                    "forecast": payload,
                },
                headers={"Cache-Control": "public, max-age=600"},
            )
    # Cache miss or expired. Hit Open-Meteo.
    try:
        forecast = _fetch_open_meteo(la, ln)
    except urllib.error.URLError as exc:
        # If we have any cached entry (even expired), serve it as a
        # stale fallback. The client can decide whether to trust it.
        if _WEATHER_CACHE_LOCK:
            with _WEATHER_CACHE_LOCK:
                entry = _WEATHER_CACHE.get(key)
        if entry:
            return JSONResponse(
                status_code=200,
                content={
                    "cached": True,
                    "stale": True,
                    "cached_at": entry["ts"],
                    "latitude": la,
                    "longitude": ln,
                    "place_name": entry.get("place_name"),
                    "forecast": entry["data"],
                    "warning": "upstream unavailable, returning cached data: " + str(exc),
                },
                headers={"Cache-Control": "public, max-age=60"},
            )
        raise HTTPException(status_code=502, detail="weather upstream unavailable: " + str(exc))
    # Resolve a human-readable place name via Nominatim (OpenStreetMap).
    # We piggy-back on the same cache TTL as the forecast so we don't
    # add a second round-trip per request. Nominatim's usage policy
    # is 1 req/s, which our hourly cache comfortably respects.
    place_name = _reverse_geocode(la, ln)
    if _WEATHER_CACHE_LOCK:
        with _WEATHER_CACHE_LOCK:
            _WEATHER_CACHE[key] = {
                "ts": now,
                "data": forecast,
                "place_name": place_name,
            }
    return JSONResponse(
        content={
            "cached": False,
            "cached_at": now,
            "latitude": la,
            "longitude": ln,
            "place_name": place_name,
            "forecast": forecast,
        },
        headers={"Cache-Control": "public, max-age=600"},
    )


# --------------------------------------------------------------------------- #
# Stock indices proxy (Yahoo Finance chart API, no API key).
#
# The 株価情報 tab shows 日経平均 / S&P 500 / Nifty 50 plus ダウ平均 and
# NASDAQ総合. Yahoo's v8 chart endpoint serves every one of them
# (query1.finance.yahoo.com, range=1d). A browser User-Agent is
# REQUIRED — a bare urllib UA gets rate-limited with "Edge: Too Many
# Requests" (429), and the v7 multi-symbol quote endpoint is
# Unauthorized, so the basket is fetched symbol-by-symbol through the
# chart API and cached as one payload.
#
# Same proxy contract as /api/weather: the client never knows the
# upstream URL, the cache policy, or the symbol list. Public data —
# not gated by the employees auth middleware, exactly like the weather
# forecast. TTL defaults to 60s so quotes stay near-real-time without
# hammering the upstream on every page view.
# --------------------------------------------------------------------------- #

STOCK_SYMBOLS: dict[str, dict[str, str]] = {
    "^N225": {"name": "日経平均"},
    "^GSPC": {"name": "S&P 500"},
    "^NSEI": {"name": "Nifty 50"},
    "^DJI": {"name": "ダウ平均"},
    "^IXIC": {"name": "NASDAQ総合"},
}

_STOCKS_CACHE: dict[str, dict] = {}
_STOCKS_CACHE_LOCK = _threading.Lock()


def _stocks_cache_ttl_seconds() -> int:
    return _parse_int(os.environ.get("STOCKS_CACHE_SECONDS", ""), 60)


def _fetch_stock(symbol: str) -> dict:
    """Hit the Yahoo chart API and return a normalised quote.

    Yahoo rate-limits by IP: the Render instance's shared egress IP gets
    ``429 Too Many Requests`` persistently (other tenants on the same IP
    hammer Yahoo). Defences, in order:

    1. Alternate ``query1`` / ``query2`` across attempts — they sit on
       separate rate-limit buckets.
    2. A browser-like User-Agent — the custom UA looks like a bot and
       is limited harder.
    3. Backoff between attempts (3s/3s/6s, well under Render's request
       timeout).
    4. The caller (get_stocks) serves the stale cache when every
       attempt fails, so a rate-limited window still shows the last
       known prices with a warning instead of an error.
    """
    hosts = (
        "https://query1.finance.yahoo.com",
        "https://query2.finance.yahoo.com",
    )
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )
    last_http_status: int | None = None
    for attempt in range(4):
        base = hosts[attempt % 2]
        url = (
            base + "/v8/finance/chart/"
            + urllib.parse.quote(symbol)
            + "?range=1d&interval=5m"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": ua, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            result = (body.get("chart") or {}).get("result") or []
            if not result:
                raise RuntimeError(f"no data for {symbol}")
            meta = result[0].get("meta") or {}
            price = meta.get("regularMarketPrice")
            if price is None:
                raise RuntimeError(f"no price for {symbol}")
            previous_close = meta.get("chartPreviousClose")
            change = meta.get("fulldayChange")
            if change is None and price is not None and previous_close is not None:
                change = round(price - previous_close, 4)
            return {
                "symbol": symbol,
                "name": STOCK_SYMBOLS.get(symbol, {}).get("name", symbol),
                "price": price,
                "previous_close": previous_close,
                "change": change,
                "change_percent": meta.get("regularMarketChangePercent"),
                "currency": meta.get("currency"),
                "exchange": meta.get("exchangeName"),
                "timezone": meta.get("timezone"),
                "market_time": meta.get("regularMarketTime"),
            }
        except urllib.error.HTTPError as exc:
            last_http_status = exc.code
            # 429 (and 503, which Yahoo also uses when throttling) are
            # transient on a shared egress IP — back off and retry on the
            # other host.
            if exc.code in (429, 503) and attempt < 3:
                time.sleep(3 * (attempt // 2 + 1))
                continue
            raise
        except (json.JSONDecodeError, UnicodeDecodeError):
            # The rate-limit response is an HTML/plaintext error page,
            # which fails JSON parsing. Same transient treatment.
            if attempt < 3:
                time.sleep(3 * (attempt // 2 + 1))
                continue
            raise
    raise RuntimeError(
        f"fetch failed for {symbol} after retries (last HTTP {last_http_status})"
    )


@app.get("/api/stocks")
def get_stocks() -> JSONResponse:
    """Return the major-index basket with near-real-time quotes.

    One cache slot holds the whole basket, so all five symbols are
    fetched at most once per cache window. On an upstream failure an
    expired entry is served as a stale fallback (same contract as
    /api/weather); with no entry at all the request 502s with the
    upstream reason.
    """
    now = time.time()
    ttl = _stocks_cache_ttl_seconds()
    with _STOCKS_CACHE_LOCK:
        entry = _STOCKS_CACHE.get("basket")
    if entry and (now - entry["ts"]) < ttl:
        return JSONResponse(
            content={"cached": True, "cached_at": entry["ts"], "quotes": entry["data"]},
            headers={"Cache-Control": "public, max-age=30"},
        )
    # Cache miss or expired. Fetch the whole basket (symbol by symbol —
    # the multi-symbol quote endpoint is Unauthorized upstream) in
    # PARALLEL: five sequential ~2s handshakes cost a ~10s first load,
    # which reads as "broken" for a real-time tab. pool.map preserves
    # the STOCK_SYMBOLS order, so the response shape is unchanged.
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=5) as pool:
            quotes = list(pool.map(_fetch_stock, STOCK_SYMBOLS))
    except Exception as exc:
        with _STOCKS_CACHE_LOCK:
            entry = _STOCKS_CACHE.get("basket")
        if entry:
            return JSONResponse(
                status_code=200,
                content={
                    "cached": True,
                    "stale": True,
                    "cached_at": entry["ts"],
                    "quotes": entry["data"],
                    "warning": "upstream unavailable, returning cached data: " + str(exc),
                },
                headers={"Cache-Control": "public, max-age=15"},
            )
        raise HTTPException(status_code=502, detail="stock upstream unavailable: " + str(exc))
    with _STOCKS_CACHE_LOCK:
        _STOCKS_CACHE["basket"] = {"ts": now, "data": quotes}
    return JSONResponse(
        content={"cached": False, "cached_at": now, "quotes": quotes},
        headers={"Cache-Control": "public, max-age=30"},
    )


_GEOCODE_CACHE: dict[str, str | None] = {}
_GEOCODE_CACHE_LOCK = _threading.Lock() if "_threading" in dir() else None


def _reverse_geocode(lat: float, lng: float) -> str | None:
    """Look up a short place name for the given coordinates.

    Uses Nominatim (OpenStreetMap) which is free and needs no API key.
    The result is cached for 24 hours so a 1-req-per-second rate
    limit is comfortably respected for the lifetime of the cache.
    Returns ``None`` on any failure so the client can fall back to
    showing the raw coordinates.
    """
    key = f"{round(lat, 2):.2f},{round(lng, 2):.2f}"
    now = time.time()
    if _GEOCODE_CACHE_LOCK:
        with _GEOCODE_CACHE_LOCK:
            entry = _GEOCODE_CACHE.get(key)
        if entry and (now - entry[0]) < 86400:
            return entry[1]
    url = (
        "https://nominatim.openstreetmap.org/reverse?"
        + urllib.parse.urlencode({
            "format": "jsonv2",
            "lat": f"{lat:.4f}",
            "lon": f"{lng:.4f}",
            "accept-language": "ja",
            "zoom": "10",
        })
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "liff-drawing-search/1.0 (weather-geocoder)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
        j = json.loads(body)
    except Exception:
        return None
    addr = j.get("address") if isinstance(j, dict) else None
    if not isinstance(addr, dict):
        return None
    # Prefer the most specific label the user recognises: a town or
    # village when we have one, otherwise the city, otherwise the
    # county. Fall back to the full display_name if nothing matches.
    label = (
        addr.get("village")
        or addr.get("town")
        or addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("city")
        or addr.get("county")
        or addr.get("region")
        or j.get("display_name")
    )
    if _GEOCODE_CACHE_LOCK:
        with _GEOCODE_CACHE_LOCK:
            _GEOCODE_CACHE[key] = (now, label)
    return label


# --------------------------------------------------------------------------- #
# Bike-log API (mirrors /schedules). DB-backed when DATABASE_URL is set, else
# the JSON file at BASE_DIR/bike_logs.json — same Postgres-vs-JSON switch as
# the schedules store. Render's free tier has ephemeral disk, so the JSON
# fallback is for local dev only.
# --------------------------------------------------------------------------- #


def _bike_log_from_payload(data: dict) -> dict:
    date = str(data.get("date", "")).strip()
    if not date:
        raise HTTPException(status_code=400, detail="date is required")

    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _i(v, default=0):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    distance_km = _f(data.get("distance_km", 0), 0.0)
    duration_min = _i(data.get("duration_min", 0), 0)
    explicit_speed = data.get("avg_speed_kmh")
    avg_speed_kmh = (
        _f(explicit_speed, 0.0) if explicit_speed not in (None, "")
        else (distance_km / (duration_min / 60.0) if duration_min else 0.0)
    )
    polyline = data.get("polyline", "[]")
    if isinstance(polyline, list):
        polyline = json.dumps(polyline, ensure_ascii=False)
    return {
        "id": str(uuid.uuid4()),
        "date": date,
        "distance_km": round(distance_km, 2),
        "duration_min": int(duration_min),
        "avg_speed_kmh": round(float(avg_speed_kmh), 2),
        "title": str(data.get("title", "")).strip(),
        "note": str(data.get("note", "")).strip(),
        "polyline": str(polyline),
        "created": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900"),
    }


@app.get("/bike-logs")
def get_bike_logs() -> dict:
    return {"logs": load_bike_logs()}


@app.get("/bike-logs/summary/{year_month}")
def get_bike_logs_summary(year_month: str) -> dict:
    if not year_month or len(year_month) != 7 or year_month[4] != "-":
        raise HTTPException(status_code=400, detail="expected YYYY-MM")
    return get_bike_summary(year_month)


@app.post("/bike-logs")
async def create_bike_log(payload: dict = Body(...)) -> JSONResponse:
    entry = _bike_log_from_payload(payload)
    _append_bike_log(entry)
    return JSONResponse(status_code=201, content={"success": True, "log": entry})


@app.put("/bike-logs/{log_id}")
async def update_bike_log(log_id: str, payload: dict = Body(...)) -> JSONResponse:
    updated = _update_bike_log(log_id, payload)
    if not updated:
        return JSONResponse(status_code=404, content={"success": False, "error": "not found"})
    return JSONResponse(content={"success": True, "log": updated})


@app.delete("/bike-logs/{log_id}")
async def delete_bike_log(log_id: str) -> JSONResponse:
    if _delete_bike_log(log_id):
        return JSONResponse(content={"success": True, "message": "Bike log deleted"})
    return JSONResponse(status_code=404, content={"success": False, "error": "not found"})


# --------------------------------------------------------------------------- #
# Paid-leave API (mirrors /schedules and /bike-logs).
#
# Same pattern as the other employee-facing tables: Postgres when
# DATABASE_URL is set, else a JSON file in the working directory. The
# /paid-leave prefix is listed in _PROTECTED_PREFIXES so the
# employees-table auth gate rejects unauthenticated requests with 401.
# --------------------------------------------------------------------------- #


def _paid_leave_from_payload(data: dict) -> dict:
    date = str(data.get("date", "")).strip()
    type_ = str(data.get("type", "")).strip()
    if not date:
        raise HTTPException(status_code=400, detail="date is required")
    return {
        "id": str(uuid.uuid4()),
        "date": date,
        "type": type_,
        "reason": str(data.get("reason", "")).strip(),
        "created": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900"),
    }


# Lazy import: paid_db.py is only meaningful when DATABASE_URL is set. The
# JSON-file path does not need it. Both branches still share the request
# shape above.
def _load_paid_leaves() -> list[dict]:
    if USE_POSTGRES:
        from paid_db import list_paid_leaves  # type: ignore
        return list_paid_leaves()
    try:
        with (BASE_DIR / "paid_leaves.json").open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _append_paid_leave(entry: dict) -> None:
    if USE_POSTGRES:
        from paid_db import insert_paid_leave  # type: ignore
        insert_paid_leave(entry)
        return
    rows = _load_paid_leaves()
    rows.append(entry)
    with (BASE_DIR / "paid_leaves.json").open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)


def _update_paid_leave(leave_id: str, updates: dict):
    if USE_POSTGRES:
        from paid_db import update_paid_leave  # type: ignore
        return update_paid_leave(leave_id, updates)
    rows = _load_paid_leaves()
    for idx, r in enumerate(rows):
        if r.get("id") == leave_id:
            current = dict(r)
            for key in ("date", "type", "reason"):
                if key in updates and updates[key] is not None:
                    current[key] = str(updates[key])
            rows[idx] = current
            with (BASE_DIR / "paid_leaves.json").open("w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, indent=2)
            return current
    return None


def _delete_paid_leave(leave_id: str) -> bool:
    if USE_POSTGRES:
        from paid_db import delete_paid_leave  # type: ignore
        return delete_paid_leave(leave_id)
    rows = _load_paid_leaves()
    kept = [r for r in rows if r.get("id") != leave_id]
    if len(kept) == len(rows):
        return False
    with (BASE_DIR / "paid_leaves.json").open("w", encoding="utf-8") as fh:
        json.dump(kept, fh, ensure_ascii=False, indent=2)
    return True


@app.get("/paid-leave")
def get_paid_leaves() -> dict:
    return {"leaves": _load_paid_leaves()}


@app.post("/paid-leave")
async def create_paid_leave(payload: dict = Body(...)) -> JSONResponse:
    entry = _paid_leave_from_payload(payload)
    _append_paid_leave(entry)
    return JSONResponse(status_code=201, content={"success": True, "leave": entry})


@app.put("/paid-leave/{leave_id}")
async def update_paid_leave_route(leave_id: str, payload: dict = Body(...)) -> JSONResponse:
    updated = _update_paid_leave(leave_id, payload)
    if not updated:
        return JSONResponse(status_code=404, content={"success": False, "error": "not found"})
    return JSONResponse(content={"success": True, "leave": updated})


@app.delete("/paid-leave/{leave_id}")
async def delete_paid_leave_route(leave_id: str) -> JSONResponse:
    if _delete_paid_leave(leave_id):
        return JSONResponse(content={"success": True, "message": "Paid leave deleted"})
    return JSONResponse(status_code=404, content={"success": False, "error": "not found"})


# --------------------------------------------------------------------------- #
# Household bookkeeping API (mirrors /schedules, /bike-logs and /paid-leave).
#
# Same Postgres pattern as the other employee-facing tables. Unlike
# bike/paid-leave, every row is scoped to a LINE user id taken from the
# session (see below) — the client never gets to choose whose expenses it
# sees, which is why there is no JSON-file fallback branch: without a
# resolved employee there is nothing to scope the rows to.
#
# The /household prefix is listed in _PROTECTED_PREFIXES so the
# employees-table auth gate rejects unauthenticated requests with 401.
# --------------------------------------------------------------------------- #

# Fixed category whitelist. The client's value is validated against this
# list server-side (unknown value -> 400) because the summary groups by
# category text: an arbitrary string from the client would fragment the
# per-category aggregation instead of being rejected.
HOUSEHOLD_CATEGORIES = (
    "食費",
    "日用品",
    "交通費",
    "趣味・娯楽",
    "衣服",
    "医療・健康",
    "交際費",
    "光熱費",
    "住居",
    "その他",
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date_str(value: str) -> bool:
    """YYYY-MM-DD かつ実在する日付かの検証。

    正規表現だけでなく datetime.strptime で実在日付まで確認する。
    2026-02-31 のような存在しない日付を弾かないと TEXT 列に
    入った後に月フィルタ(LIKE 'YYYY-MM%')で集計が狂う。
    """
    if not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_month_str(value: str) -> bool:
    """YYYY-MM 形式かの検証(bike-logs summary と同じルール)。"""
    return bool(value) and len(value) == 7 and value[4] == "-" and value[:4].isdigit() and value[5:].isdigit()


def _household_line_user_id(request) -> str:
    """セッションから line_user_id を解決する。

    クライアントが送る line_user_id は一切信用しない(他人の家計簿を
    読み書きできてしまうため)。セッションで解決した社員の LINE ユーザー
    ID が未登録なら 400。
    """
    emp = _resolve_employee_or_401(request)
    line_user_id = emp.get("line_user_id")
    if not line_user_id:
        raise HTTPException(
            status_code=400,
            detail="社員情報にLINEユーザーIDが登録されていません。",
        )
    return str(line_user_id)


def _household_expense_from_payload(data: dict) -> dict:
    spent_on = str(data.get("spent_on", "")).strip()
    if not _valid_date_str(spent_on):
        raise HTTPException(status_code=400, detail="spent_on は YYYY-MM-DD 形式の実在する日付で入力してください")
    category = str(data.get("category", "") or "").strip() or "その他"
    if category not in HOUSEHOLD_CATEGORIES:
        raise HTTPException(status_code=400, detail="不正なカテゴリです: " + category)
    amount_raw = data.get("amount", 0)
    # amount は整数・0以上のみ。小数や数値でない文字列は弾く(自転車の
    # distance は float で黙って丸めていたが、金額の丸めはズレが直接
    # 見えるので厳格にする)。
    if isinstance(amount_raw, bool):
        raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
    try:
        if isinstance(amount_raw, str):
            amount = int(amount_raw.strip())
        elif isinstance(amount_raw, float):
            if not amount_raw.is_integer():
                raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
            amount = int(amount_raw)
        else:
            amount = int(amount_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
    if amount < 0:
        raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
    item = str(data.get("item", "") or "").strip()[:200]
    note = str(data.get("note", "") or "").strip()[:500]
    return {
        "id": str(uuid.uuid4()),
        "spent_on": spent_on,
        "item": item,
        "category": category,
        "amount": amount,
        "note": note,
        "created": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900"),
    }


@app.get("/household/expenses")
def get_household_expenses(request: Request, month: str | None = None, date: str | None = None) -> dict:
    line_user_id = _household_line_user_id(request)
    if month and date:
        raise HTTPException(status_code=400, detail="month と date は同時に指定できません")
    if month:
        if not _valid_month_str(month):
            raise HTTPException(status_code=400, detail="expected YYYY-MM")
    if date:
        if not _valid_date_str(date):
            raise HTTPException(status_code=400, detail="expected YYYY-MM-DD")
    from household_db import list_expenses  # type: ignore
    return {"expenses": list_expenses(line_user_id, month=month, date=date)}


@app.get("/household/summary")
def get_household_summary(request: Request, month: str) -> dict:
    line_user_id = _household_line_user_id(request)
    if not _valid_month_str(month):
        raise HTTPException(status_code=400, detail="expected YYYY-MM")
    from household_db import monthly_summary  # type: ignore
    return monthly_summary(line_user_id, month)


@app.post("/household/expenses")
async def create_household_expense(request: Request, payload: dict = Body(...)) -> JSONResponse:
    line_user_id = _household_line_user_id(request)
    entry = _household_expense_from_payload(payload)
    entry["line_user_id"] = line_user_id
    from household_db import insert_expense  # type: ignore
    insert_expense(entry)
    return JSONResponse(status_code=201, content={"success": True, "expense": entry})


@app.put("/household/expenses/{expense_id}")
async def update_household_expense(
    expense_id: str, request: Request, payload: dict = Body(...)
) -> JSONResponse:
    line_user_id = _household_line_user_id(request)
    # 部分更新なので item 以外の既定項目もここで検証する。spent_on は
    # 省略可(存在すれば日付検証)、category は省略時は現状値を維持
    # (household_db 側で未指定キーは書き換えない)。
    if "spent_on" in payload and payload["spent_on"] is not None:
        if not _valid_date_str(str(payload["spent_on"]).strip()):
            raise HTTPException(status_code=400, detail="spent_on は YYYY-MM-DD 形式の実在する日付で入力してください")
    if "category" in payload and payload["category"] is not None:
        category = str(payload["category"]).strip()
        if category not in HOUSEHOLD_CATEGORIES:
            raise HTTPException(status_code=400, detail="不正なカテゴリです: " + category)
    if "amount" in payload and payload["amount"] is not None:
        amount_raw = payload["amount"]
        if isinstance(amount_raw, bool):
            raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
        try:
            if isinstance(amount_raw, str):
                amount = int(amount_raw.strip())
            elif isinstance(amount_raw, float):
                if not amount_raw.is_integer():
                    raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
                amount = int(amount_raw)
            else:
                amount = int(amount_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
        if amount < 0:
            raise HTTPException(status_code=400, detail="amount は0以上の整数で入力してください")
        payload["amount"] = amount
    if "item" in payload and payload["item"] is not None:
        payload["item"] = str(payload["item"]).strip()[:200]
    if "note" in payload and payload["note"] is not None:
        payload["note"] = str(payload["note"]).strip()[:500]
    from household_db import update_expense  # type: ignore
    updated = update_expense(expense_id, line_user_id, payload)
    if not updated:
        return JSONResponse(status_code=404, content={"success": False, "error": "not found"})
    return JSONResponse(content={"success": True, "expense": updated})


@app.delete("/household/expenses/{expense_id}")
async def delete_household_expense(expense_id: str, request: Request) -> JSONResponse:
    line_user_id = _household_line_user_id(request)
    from household_db import delete_expense  # type: ignore
    if delete_expense(expense_id, line_user_id):
        return JSONResponse(content={"success": True, "message": "Household expense deleted"})
    return JSONResponse(status_code=404, content={"success": False, "error": "not found"})


# --------------------------------------------------------------------------- #
# bento ordering API (mounted last so it never shadows the routes above)
# --------------------------------------------------------------------------- #

try:
    from app.api import admin, auth, orders  # type: ignore
    from app.api import config_api as _config_api  # type: ignore
    from app.db import get_conn as _bento_get_conn, migrate as _bento_migrate  # type: ignore

    app.include_router(_config_api.router)
    app.include_router(auth.router)
    app.include_router(orders.router)
    app.include_router(admin.router)

    # Run the migrations here, not just inside the bento FastAPI app's lifespan.
    # This app only *includes* the bento routers, so that lifespan never runs and
    # nothing ever created the tables — every /api/auth/login died with a bare
    # "no such table: employees". Apply them once at import, before the server
    # accepts a request. migrate() is idempotent via schema_migrations.
    try:
        _applied = _bento_migrate(_bento_get_conn())
        logger.info("bento migrations applied: %s", _applied or "(already up to date)")
    except Exception as mig_exc:  # noqa: BLE001
        _bento_error = f"migration failed: {mig_exc.__class__.__name__}: {mig_exc}"
        logger.error("bento migrations FAILED: %s", mig_exc)
        raise

    # The /tmp/bento.db is ephemeral on Render's free tier, so seed once.
    # Postgres is permanent, so this becomes a no-op once the row exists.
    try:
        from app.db import bind, get_conn, kind  # type: ignore

        _seed_conn = get_conn()
        _count = _seed_conn.execute(bind("SELECT COUNT(*) AS n FROM employees")).fetchone()
        if _count and _count["n"] == 0:
            seed_path = Path(__file__).parent / "bento_backend" / "app" / "seed_employee.sql"
            if seed_path.exists():
                # executescript() is SQLite-only; psycopg connections have no
                # such method. Execute each statement individually instead, and
                # use the bind() translator so SQLite's `INSERT OR IGNORE`
                # becomes a portable `ON CONFLICT DO NOTHING` for Postgres.
                sql_text = seed_path.read_text(encoding="utf-8")
                is_pg = kind() == "pg"
                for raw in [s.strip() for s in sql_text.split(";") if s.strip()]:
                    if is_pg:
                        # SQLite: INSERT OR IGNORE INTO t ... -> Postgres: INSERT INTO t ... ON CONFLICT (id) DO NOTHING
                        translated = raw.replace(
                            "INSERT OR IGNORE INTO",
                            "INSERT INTO",
                        )
                        # Add ON CONFLICT (id) DO NOTHING for the employees table
                        if "employees" in translated and "ON CONFLICT" not in translated:
                            translated = translated.rstrip() + " ON CONFLICT (id) DO NOTHING"
                    else:
                        translated = raw
                    _seed_conn.execute(translated)
                logger.info("seeded employee from %s (backend=%s)", seed_path, kind())
            else:
                now_str = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900")
                if kind() == "pg":
                    _seed_conn.execute(
                        bind(
                            "INSERT INTO employees "
                            "(id, employee_code, name, role, is_active, line_user_id, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
                        ),
                        (1, "E001", "岩野", "employee", 1,
                         "U4786cd63cad2f9cf56f6c01494d5cb0e", now_str),
                    )
                else:
                    _seed_conn.execute(
                        bind(
                            "INSERT OR IGNORE INTO employees "
                            "(id, employee_code, name, role, is_active, line_user_id, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)"
                        ),
                        (1, "E001", "岩野", "employee", 1,
                         "U4786cd63cad2f9cf56f6c01494d5cb0e", now_str),
                    )
                logger.info("seeded employee (direct insert) for line_user_id=U4786cd63...")
    except Exception as seed_exc:  # diagnostics: never fatal
        logger.warning("employee seed failed (not fatal): %s", seed_exc)

    _bento_loaded = True
except Exception as exc:  # pragma: no cover - depends on deployment layout
    _bento_error = f"{exc.__class__.__name__}: {exc}"
    logger.warning("bento API not mounted: %s", _bento_error)

_origins = [o.strip() for o in os.environ.get("BENTO_CORS_ORIGINS", "").split(",") if o.strip()]
if not _origins:
    _origins = ["https://liff.line.me", "https://liff-drawing-search.onrender.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Line-User-Id"],
)

# --------------------------------------------------------------------------- #
# Security headers.
#
# The app runs inside the LINE LIFF WebView on the user's phone, so the
# usual web hardening still applies — anyone can hit the URL directly and
# anyone can try to embed it. These headers do not fix the auth boundary
# (that is the employees-table gate below) but they prevent the most
# common escalation paths an attacker would try first.
#
# - X-Frame-Options / frame-ancestors: deny embedding in a hostile iframe.
#   ``liff.line.me`` is whitelisted because the LINE WebView is itself a
#   frame, and the rest of the CSP / X-Frame-Options policy must allow
#   that single legitimate ancestor.
# - X-Content-Type-Options: nosniff: stop MIME guessing.
# - Referrer-Policy: no-referrer: we never need to leak the LIFF URL to
#   external tile / CDN hosts.
# - Permissions-Policy: geolocation=(self) is what the bike tab needs;
#   everything else (camera, microphone, payment) is denied because the
#   app does not use them. ``self`` covers the same-origin child frames
#   the bike and route tabs live in.
# - Content-Security-Policy: tighten the script and connect sources. The
#   only outbound calls are /api/*, the LIFF SDK, Leaflet via unpkg, and
#   OpenStreetMap tile servers.
# --------------------------------------------------------------------------- #
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(*), camera=(), microphone=(), payment=()",
    # No X-Frame-Options here. The modern equivalent below
    # (``frame-ancestors 'self' https://liff.line.me`` in CSP) covers the
    # same threat. Setting X-Frame-Options: ALLOW-FROM without 'self'
    # actively breaks the host page's own bento/bike/route iframes on
    # browsers that honour the legacy header — every child tab would
    # render blank.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.line-scripts.com https://static.line-scdn.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://*.tile.opentopomap.org; "
        "connect-src 'self' https://liff.line.me https://api.line.me; "
        "frame-src 'self' https://liff.line.me; "
        # The host page (served at / and /form.html, /bike/, /route/,
        # /bento/) is itself loaded inside the LINE WebView at
        # https://liff.line.me. ``frame-ancestors`` must therefore allow
        # liff.line.me. Each child iframe is loaded at the same origin
        # (https://liff-drawing-search.onrender.com) so ``self`` covers
        # that too. Without ``self``, the host's own bento/bike/route
        # iframes refuse to render and every tab after the first shows
        # blank — which is the failure mode the previous policy had.
        "frame-ancestors 'self' https://liff.line.me; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


@app.middleware("http")
async def _security_headers_middleware(request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        # Don't override headers a route set explicitly (e.g. Cache-Control
        # on /bike-logs). ``X-Frame-Options`` is the only one we need to
        # guarantee, so set it last and prefer the existing value if any.
        if name == "X-Frame-Options" and "x-frame-options" in {k.lower() for k in response.headers.keys()}:
            continue
        response.headers[name] = value
    return response

# --------------------------------------------------------------------------- #
# Employees-table access gate.
#
# Mirrors the rule /api/orders already enforces: the request must carry a
# Bearer session token issued by /api/auth/login against the employees
# table. /api/auth/login, /api/runtime-config, /api/config and the static
# assets are deliberately exempt — the login endpoint has to be reachable
# to issue a token, the config endpoints are public tunables, and the
# static pages are what the LIFF entry URL serves to bootstrap the app.
#
# Applied as a middleware (not a per-route dependency) so the gate is
# uniform across the public-facing endpoints without re-decorating each
# handler. Unauthenticated requests get 401 with the same shape the bento
# deps layer returns.
# --------------------------------------------------------------------------- #
import logging as _logging  # noqa: E402  (late import to keep top of file tidy)

_logger = _logging.getLogger("uvicorn.error")

# Lazy import target for the bento auth dependency. The bento app is only
# importable when its deps (app.db etc.) are reachable, so defer the import
# to the first request — by then the bento routers have already been
# mounted and the dep function exists.
def _resolve_employee_or_401(request):
    try:
        from app.api.deps import current_employee
    except Exception as exc:  # noqa: BLE001
        # bento backend not importable in this environment; refuse rather
        # than silently bypass. The /api/orders path would 500 in the same
        # situation, so refusing here keeps the failure mode consistent.
        raise HTTPException(
            status_code=503,
            detail="auth backend unavailable: " + str(exc),
        )
    return current_employee(request)


_PROTECTED_PREFIXES = (
    "/schedules",
    "/bike-logs",
    "/paid-leave",
    # household は API サブパスのみゲートする。"/household" 全体を
    # ゲートしてしまうと iframe が読み込む /household/index.html 自体が
    # 401 になり、タブが真っ白になる (bike は mount /bike と保護 prefix
    # /bike-logs が偶然分離していたためこの問題がなかった)。
    # startswith(p + "/") で /household/expenses/{id} も覆う。
    "/household/expenses",
    "/household/summary",
    # /api/orders and /api/admin are already protected by their own deps.
)


@app.middleware("http")
async def _employees_auth_gate(request, call_next):
    path = request.url.path
    # Allow the bento routers to handle their own auth via deps. We only
    # intercept the public endpoints the host used to expose unauthenticated.
    if any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES):
        try:
            _resolve_employee_or_401(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        except Exception as exc:  # noqa: BLE001
            # If the bento auth backend cannot even import, fail closed.
            return JSONResponse(
                status_code=503,
                content={"detail": "auth backend unavailable: " + str(exc)},
            )
    return await call_next(request)


# Static site last: index.html is the LIFF endpoint, bento/ holds the iframe app.
#
# Route order matters — FastAPI matches in registration order, so these mounts
# are registered after /health, /schedules and /api/* and can only serve files
# nothing above claimed.
#
# The root mount is what serves /sdk.js. Without it the app had explicit routes
# for only / and /form.html, so the LIFF SDK request returned 404, `liff` stayed
# undefined, liff.init threw "liff is not defined", and the form reported
# "LINE認証が必要です" — a failure that had nothing to do with LINE auth.
# Adding one explicit route per asset would only move the problem to the next
# missing file, so serve the whole directory instead.
if (BASE_DIR / "bento").is_dir():
    app.mount("/bento", StaticFiles(directory=str(BASE_DIR / "bento"), html=True), name="bento")

if (BASE_DIR / "bike").is_dir():
    app.mount("/bike", StaticFiles(directory=str(BASE_DIR / "bike"), html=True), name="bike")

if (BASE_DIR / "weather").is_dir():
    app.mount("/weather", StaticFiles(directory=str(BASE_DIR / "weather"), html=True), name="weather")

if (BASE_DIR / "stocks").is_dir():
    app.mount("/stocks", StaticFiles(directory=str(BASE_DIR / "stocks"), html=True), name="stocks")

# 家計簿フロントエンド用。フロントエンド担当が household/ を作成するまで
# 存在しない可能性があるため、bento/bike/weather と同じ条件付きマウントに
# する(ディレクトリが無くてもアプリは起動する)。
if (BASE_DIR / "household").is_dir():
    app.mount("/household", StaticFiles(directory=str(BASE_DIR / "household"), html=True), name="household")

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html", headers=_NO_CACHE)


@app.get("/form.html")
async def form() -> FileResponse:
    return FileResponse(BASE_DIR / "form.html", headers=_NO_CACHE)


_NEWS_CACHE: dict[str, Any] = {}
_NEWS_CACHE_LOCK = _threading.Lock()
_NEWS_TTL_SECONDS = 1800

_CATEGORY_QUERIES = {
    "主要": None,
    "政治": "政治",
    "経済": "経済",
    "科学": "科学",
    "エンタメ": "芸能 OR 音楽 OR 映画 OR ドラマ OR アニメ OR ゲーム OR 漫画 OR お笑い OR バラエティ",
    "スポーツ": "スポーツ",
}


def _news_url(category: str) -> str:
    q = _CATEGORY_QUERIES.get(category)
    base = "https://news.google.com/rss"
    if q:
        return base + "/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja"
    return base + "?hl=ja&gl=JP&ceid=JP:ja"


def _strip_tags(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _fetch_news(category: str = "主要", limit: int = 12) -> list[dict]:
    url = _news_url(category)
    req = urllib.request.Request(url, headers={
        "User-Agent": "liff-drawing-search/1.0 (news-proxy)",
        "Accept": "application/rss+xml, application/xml, text/xml",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
    root = ET.fromstring(body)
    items = []
    for node in root.iter("item"):
        title = _strip_tags(node.findtext("title") or "")
        link = (node.findtext("link") or "").strip()
        pubDate = (node.findtext("pubDate") or "").strip()
        desc = _strip_tags(node.findtext("description") or "")
        if title:
            items.append({
                "title": title,
                "link": link,
                "pubDate": pubDate,
                "summary": desc[:280],
            })
        if len(items) >= limit:
            break
    return items


@app.get("/api/news")
def get_news(category: str = "主要", limit: int = 12) -> JSONResponse:
    global _NEWS_CACHE
    now = time.time()
    cache_key = category + ":" + str(limit)
    with _NEWS_CACHE_LOCK:
        entry = _NEWS_CACHE.get(cache_key)
        if entry and entry["items"] and (now - entry["ts"]) < _NEWS_TTL_SECONDS:
            return JSONResponse(content={"cached": True, "category": category, "items": entry["items"]})
    try:
        items = _fetch_news(category, limit)
    except Exception as exc:
        with _NEWS_CACHE_LOCK:
            entry = _NEWS_CACHE.get(cache_key)
            if entry and entry["items"]:
                return JSONResponse(content={
                    "cached": True, "stale": True, "category": category, "items": entry["items"],
                    "warning": str(exc),
                })
        raise HTTPException(status_code=502, detail="news upstream unavailable: " + str(exc))
    with _NEWS_CACHE_LOCK:
        _NEWS_CACHE[cache_key] = {"ts": now, "items": items}
    return JSONResponse(content={"cached": False, "category": category, "items": items})


@app.get("/admin.html")
async def admin_page() -> FileResponse:
    """社員マスタの管理ページ。LIFFアプリとは別に、普通のブラウザから開く。

    認証は /api/admin-auth (ユーザー名+パスワード, httpOnlyセッションCookie)。
    LIFFのLINE認証とは完全に独立した認証系。 /api/admin/employees の
    CRUD はこの管理ページ認証(current_admin_user)で保護される。
    """
    return FileResponse(BASE_DIR / "admin.html", headers=_NO_CACHE)


# --------------------------------------------------------------------------- #
# PWA (Progressive Web App) support.
#
# The manifest and service worker let iOS Safari (and Android Chrome)
# install the LIFF form app to the home screen as a "standalone" app,
# so a user can launch it with one tap instead of going through LINE
# every time. Background location is still not possible from a PWA on
# iOS — that requires a native shell (see mobile/) — but the home-
# screen icon makes daily use much smoother.
# --------------------------------------------------------------------------- #
_STATIC_DIR = BASE_DIR / "static"


@app.get("/manifest.json")
def pwa_manifest() -> FileResponse:
    # iOS only reads the manifest at install time, so cache it
    # aggressively. The host page's no-cache header would be wrong
    # here — it would force iOS to re-download every install
    # attempt and occasionally invalidate the manifest mid-update.
    return FileResponse(
        _STATIC_DIR / "manifest.json",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sw.js")
def pwa_service_worker() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
        # The browser must re-validate the service worker file on
        # each load so a deploy can take effect without forcing the
        # user to clear the site data. iOS Safari honours this
        # header, Android Chrome does as well.
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# iOS Safari calls one of these depending on the device pixel ratio
# at install time. The 192px icon is the only one iOS Safari 16+
# actually reads (Apple's PWA install sheet ignores 512), so we
# serve that one for both URLs. Android Chrome will downscale the
# 512 variant correctly if it is ever installed there.
@app.get("/manifest-icon-192.png")
def pwa_icon_192() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "manifest-icon-192.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/manifest-icon-512.png")
def pwa_icon_512() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "manifest-icon-512.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# Catch-all. Registered last, after every explicit route and the /bento mount.
app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logger.info("bento API mounted: %s%s", _bento_loaded, "" if _bento_loaded else f" ({_bento_error})")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")