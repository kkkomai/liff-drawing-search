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
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
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
_BENTO_CANDIDATES = [
    BASE_DIR / "bento_backend",
    BASE_DIR.parent / "projects" / "bento-liff" / "backend",
]
for _cand in _BENTO_CANDIDATES:
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

if (BASE_DIR / "route").is_dir():
    app.mount("/route", StaticFiles(directory=str(BASE_DIR / "route"), html=True), name="route")

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html", headers=_NO_CACHE)


@app.get("/form.html")
async def form() -> FileResponse:
    return FileResponse(BASE_DIR / "form.html", headers=_NO_CACHE)


# Catch-all. Registered last, after every explicit route and the /bento mount.
app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logger.info("bento API mounted: %s%s", _bento_loaded, "" if _bento_loaded else f" ({_bento_error})")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")