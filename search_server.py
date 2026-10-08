#!/usr/bin/env python3
"""LIFF schedule API server (Render-ready)."""

import json
import logging
import os, subprocess, sys, uuid
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger(__name__)
PORT = int(os.environ.get("PORT", "10000"))
BASE_DIR = Path(__file__).parent.resolve()
SCHEDULES_FILE = BASE_DIR / "schedules.json"

# When DATABASE_URL is set, schedules live in Postgres instead of schedules.json.
# Render's free tier has ephemeral disk; without this, all entries vanish
# after the 15-minute cold-start. The JSON-file path remains the local-dev
# default and the fallback when DATABASE_URL is absent.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    try:
        import psycopg  # type: ignore  # noqa: F401
    except Exception as _pg_exc:  # noqa: BLE001
        logger.error("DATABASE_URL is set but psycopg is not installed: %s", _pg_exc)
        raise
    from schedules_db import (  # type: ignore
        list_schedules as _pg_list,
        insert_schedule as _pg_insert,
        update_schedule as _pg_update,
        delete_schedule as _pg_delete,
    )

    def load_schedules():
        return _pg_list()

    def save_schedules(schedules):
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM schedules")
                for s in schedules:
                    cur.execute(
                        "INSERT INTO schedules (id, title, date, time, description, created) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            s.get("id"),
                            s.get("title"),
                            s.get("date"),
                            s.get("time", ""),
                            s.get("description", ""),
                            s.get("created", ""),
                        ),
                    )

    def _delete_schedule_by_id(sid: str) -> bool:
        return _pg_delete(sid)

    def _update_schedule(sid: str, updates: dict):
        return _pg_update(sid, updates)

    def _append_schedule(schedule: dict) -> None:
        _pg_insert(schedule)

    logger.info("Schedules store: Postgres (%s...)", DATABASE_URL[:32])
else:
    def load_schedules():
        try:
            with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []


    def save_schedules(schedules):
        with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
            json.dump(schedules, f, ensure_ascii=False, indent=2)


    def _delete_schedule_by_id(sid: str) -> bool:
        schedules = load_schedules()
        new = [s for s in schedules if s.get("id") != sid]
        if len(new) == len(schedules):
            return False
        save_schedules(new)
        return True


    def _update_schedule(sid: str, updates: dict):
        schedules = load_schedules()
        for s in schedules:
            if s.get("id") == sid:
                for key in ("title", "date", "time", "description"):
                    if key in updates:
                        s[key] = str(updates[key]).strip()
                save_schedules(schedules)
                return s
        return None


    def _append_schedule(schedule: dict) -> None:
        schedules = load_schedules()
        schedules.append(schedule)
        save_schedules(schedules)


    logger.info("Schedules store: JSON file (%s)", SCHEDULES_FILE)

# The bento ordering app is developed in projects/bento-liff/frontend and embedded
# here as a tab. On Render only this repo is deployed, so the files are copied into
# bento/ at build time; locally we serve them straight from the project so there is
# exactly one copy of the source.
BENTO_SRC_CANDIDATES = [
    BASE_DIR / "bento",
    BASE_DIR.parent / "projects" / "bento-liff" / "frontend",
]


def resolve_bento_dir():
    for cand in BENTO_SRC_CANDIDATES:
        if (cand / "index.html").is_file():
            return cand
    return None


# The bike-log tab is served as a self-contained HTML page at /bike/. Local
# development reads straight from bike/; the same folder is committed to the
# repo so Render serves it without a build step.
BIKE_SRC_CANDIDATES = [
    BASE_DIR / "bike",
]


def resolve_bike_dir():
    for cand in BIKE_SRC_CANDIDATES:
        if (cand / "index.html").is_file():
            return cand
    return None


# ---- Bike-log store ----
# Mirrors the schedules Postgres-or-JSON switch above. When DATABASE_URL is
# set, logs live in bike_db.py (Neon Postgres); otherwise the JSON file in
# the working directory is the local-dev fallback. Render's free tier has
# ephemeral disk, so without DATABASE_URL the data would vanish every 15
# minutes of idle.
BIKE_FILE = BASE_DIR / "bike_logs.json"


def _load_bike_logs():
    if USE_POSTGRES:
        from bike_db import list_logs as _pg_bike_list  # type: ignore
        return _pg_bike_list()
    try:
        with open(BIKE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_bike_logs(logs):
    if USE_POSTGRES:
        from bike_db import insert_log as _pg_bike_insert  # type: ignore
        # Caller is responsible for clearing; we only support append via POST.
        raise NotImplementedError("use bike_db directly for inserts")
    with open(BIKE_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def _append_bike_log(entry: dict) -> None:
    if USE_POSTGRES:
        from bike_db import insert_log as _pg_bike_insert  # type: ignore
        _pg_bike_insert(entry)
        return
    logs = _load_bike_logs()
    logs.append(entry)
    _save_bike_logs(logs)


def _update_bike_log(log_id: str, updates: dict):
    if USE_POSTGRES:
        from bike_db import update_log as _pg_bike_update  # type: ignore
        return _pg_bike_update(log_id, updates)
    logs = _load_bike_logs()
    for s in logs:
        if s.get("id") == log_id:
            for key, v in updates.items():
                s[key] = v
            _save_bike_logs(logs)
            return s
    return None


def _delete_bike_log(log_id: str) -> bool:
    if USE_POSTGRES:
        from bike_db import delete_log as _pg_bike_delete  # type: ignore
        return _pg_bike_delete(log_id)
    logs = _load_bike_logs()
    new = [s for s in logs if s.get("id") != log_id]
    if len(new) == len(logs):
        return False
    _save_bike_logs(new)
    return True


def _serve_static_from(self, root_dir, rel_path: str, prefix: str):
    """Serve a file from ``root_dir`` under the given URL prefix.

    Used for both ``/bento/...`` and ``/bike/...``. Returns True when the
    request matched, False otherwise (so the caller can fall through).
    Path traversal is blocked with a relative_to check, exactly as the
    bento handler does.
    """
    if not rel_path:
        rel_path = "index.html"
    target = (root_dir / rel_path).resolve()
    try:
        target.relative_to(root_dir.resolve())
    except ValueError:
        self.send_json(403, {"error": "forbidden"})
        return True
    if not target.is_file():
        self.send_json(404, {"error": "not_found"})
        return True
    ctype = self.guess_type(str(target))
    body = target.read_bytes()
    self.send_response(200)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)
    return True


class Handler(SimpleHTTPRequestHandler):
    """HTTP request handler with anti-cache headers for LINE WebView."""

    def end_headers(self):
        """Override end_headers to add anti-cache headers to ALL responses."""
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, public, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        SimpleHTTPRequestHandler.end_headers(self)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "port": PORT})
            return
        if self.path.startswith("/bento/"):
            bento_dir = resolve_bento_dir()
            if bento_dir is None:
                self.send_json(503, {
                    "error": "bento_not_deployed",
                    "message": "bento app not found on this server",
                })
                return
            rel = unquote(self.path[len("/bento/"):]).split("?")[0]
            _serve_static_from(self, bento_dir, rel, "/bento/")
            return
        if self.path.startswith("/bike/"):
            bike_dir = resolve_bike_dir()
            if bike_dir is None:
                self.send_json(503, {
                    "error": "bike_not_deployed",
                    "message": "bike tab not found on this server",
                })
                return
            rel = unquote(self.path[len("/bike/"):]).split("?")[0]
            _serve_static_from(self, bike_dir, rel, "/bike/")
            return
        if self.path == "/schedules":
            schedules = load_schedules()
            self.send_json(200, {"schedules": schedules})
            return
        if self.path == "/bike-logs":
            self.send_json(200, {"logs": _load_bike_logs()})
            return
        if self.path.startswith("/bike-logs/summary/"):
            year_month = unquote(self.path.split("/")[-1])
            if not USE_POSTGRES:
                self.send_json(503, {"error": "summary requires Postgres"})
                return
            from bike_db import monthly_summary  # type: ignore
            self.send_json(200, monthly_summary(year_month))
            return
        # For HTML/CSS/JS/SDK static files - use parent handler
        SimpleHTTPRequestHandler.do_GET(self)

    def do_DELETE(self):
        if self.path.startswith("/schedules/"):
            sid = self.path.split("/")[-1]
            if _delete_schedule_by_id(sid):
                self.send_json(200, {"success": True, "message": "Schedule deleted"})
            else:
                self.send_json(404, {"error": "Schedule not found"})
            return
        if self.path.startswith("/bike-logs/"):
            lid = self.path.split("/")[-1]
            if _delete_bike_log(lid):
                self.send_json(200, {"success": True, "message": "Bike log deleted"})
            else:
                self.send_json(404, {"error": "Bike log not found"})
            return
        self.send_json(404, {"error": "not found"})

    def do_PUT(self):
        if self.path.startswith("/schedules/"):
            sid = self.path.split("/")[-1]
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return
            updated = _update_schedule(sid, data)
            if updated is not None:
                self.send_json(200, {"success": True, "schedule": updated})
            else:
                self.send_json(404, {"error": "Schedule not found"})
            return
        if self.path.startswith("/bike-logs/"):
            lid = self.path.split("/")[-1]
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return
            updated = _update_bike_log(lid, data)
            if updated is not None:
                self.send_json(200, {"success": True, "log": updated})
            else:
                self.send_json(404, {"error": "Bike log not found"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        # POST /schedules (LIFF form.html) and POST /create (legacy) share the same create path.
        if self.path.split("?")[0] in ("/create", "/schedules"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = b""
                while len(raw) < length:
                    chunk = self.rfile.read(min(length - len(raw), 8192))
                    if not chunk:
                        break
                    raw += chunk
                text = raw.decode("utf-8", errors="replace")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    self.send_json(400, {"error": "invalid JSON"})
                    return
                title = data.get("title", "").strip()
                date = data.get("date", "").strip()
                if not title or not date:
                    self.send_json(400, {"error": "title and date are required"})
                    return
                new = {"id": str(uuid.uuid4()), "title": title, "date": date, "time": data.get("time", "").strip(), "description": data.get("description", "").strip(), "created": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S+0900")}
                _append_schedule(new)
                self.send_json(201, {"success": True, "schedule": new})
            except Exception as exc:
                logger.error("POST /create error: %s", exc)
                self.send_json(500, {"error": str(exc)})
            return
        if self.path.split("?")[0] == "/bike-logs":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = b""
                while len(raw) < length:
                    chunk = self.rfile.read(min(length - len(raw), 8192))
                    if not chunk:
                        break
                    raw += chunk
                text = raw.decode("utf-8", errors="replace")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    self.send_json(400, {"error": "invalid JSON"})
                    return
                date = str(data.get("date", "")).strip()
                if not date:
                    self.send_json(400, {"error": "date is required"})
                    return
                # Coerce numeric fields. Frontend may send strings.
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
                avg_speed_kmh = _f(
                    data.get("avg_speed_kmh", 0.0),
                    (distance_km / (duration_min / 60.0)) if duration_min else 0.0,
                )
                polyline = data.get("polyline", "[]")
                if isinstance(polyline, list):
                    polyline = json.dumps(polyline, ensure_ascii=False)
                entry = {
                    "id": str(uuid.uuid4()),
                    "date": date,
                    "distance_km": round(distance_km, 2),
                    "duration_min": int(duration_min),
                    "avg_speed_kmh": round(avg_speed_kmh, 2),
                    "title": str(data.get("title", "")).strip(),
                    "note": str(data.get("note", "")).strip(),
                    "polyline": str(polyline),
                    "created": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S+0900"),
                }
                _append_bike_log(entry)
                self.send_json(201, {"success": True, "log": entry})
            except Exception as exc:
                logger.error("POST /bike-logs error: %s", exc)
                self.send_json(500, {"error": str(exc)})
            return
        self.send_json(404, {"error": "not found"})

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        SimpleHTTPRequestHandler.send_header(self, "Content-Type", "application/json; charset=utf-8")
        SimpleHTTPRequestHandler.send_header(self, "Cache-Control", "no-cache, no-store, must-revalidate, public, max-age=0")
        SimpleHTTPRequestHandler.send_header(self, "Pragma", "no-cache")
        SimpleHTTPRequestHandler.send_header(self, "Expires", "0")
        SimpleHTTPRequestHandler.send_header(self, "Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    # Serve through the ASGI app. The render.yaml Start Command asks for
    # `uvicorn asgi_app:app`, but this service was created from the Render
    # dashboard, which ignores render.yaml — so its Start Command is still
    # `python3 search_server.py`. Booting uvicorn here keeps that command valid
    # and gives local and deployed runs the exact same code path, instead of
    # two servers whose behaviour drifts.
    #
    # On Render the Build Command installs into a different environment than the
    # `python3` that runs the Start Command — the build log shows
    # "Successfully installed uvicorn" followed immediately by
    # "No module named 'uvicorn'" at startup. So if the import fails, install
    # into THIS interpreter (sys.executable -m pip) and retry once. That keeps
    # the fix in the repo instead of relying on a dashboard setting we cannot
    # verify, and it is a no-op wherever the deps are already present.
    #
    # If it still cannot be imported, fall back to the original
    # ThreadingHTTPServer so the form site stays reachable — and publish WHY,
    # because a silent fallback looks exactly like "the change did not deploy".
    _asgi_error = ""
_asgi_app = None
try:
    import uvicorn  # noqa: F401
    from asgi_app import app as asgi_app_instance  # noqa: F401

    _asgi_app = asgi_app_instance
except Exception as first_exc:  # noqa: BLE001 - any import failure retries
    logger.warning("ASGI import failed (%s); installing deps into %s", first_exc, sys.executable)
    try:
        # No --quiet: when this fails on Render the error text is the only clue,
        # and it has to reach /health because the deploy log is not always
        # reachable from the browser.
        #
        # --break-system-packages is required, not merely convenient: the Build
        # Command installs into a different interpreter than the `python3` that
        # runs this file, and Debian marks its system Python
        # "externally-managed" (PEP 668), so a plain `pip install` is refused
        # with exit 1. The container is disposable, so the override is safe.
        _pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--break-system-packages",
             "--disable-pip-version-check", "-r", str(BASE_DIR / "requirements.txt")],
            capture_output=True, text=True, timeout=600,
        )
        if _pip.returncode != 0:
            raise RuntimeError(
                "pip exit %d: %s" % (_pip.returncode, (_pip.stderr or _pip.stdout or "")[-400:])
            )
        # Even a successful install can land somewhere importlib does not scan.
        # Debian puts user site-packages under ~/.local/lib/pythonX.Y, and when
        # HOME differs between the build and the runtime (or the dir is simply
        # absent) that path is missing from sys.path. Register it explicitly
        # rather than guessing why the import still fails.
        import site
        import sysconfig

        for _p in (
            site.getusersitepackages(),
            sysconfig.get_paths().get("purelib"),
            sysconfig.get_paths().get("platlib"),
        ):
            if _p and os.path.isdir(_p) and _p not in sys.path:
                sys.path.insert(0, _p)
                logger.warning("added %s to sys.path", _p)

        import uvicorn  # noqa: F811
        from asgi_app import app as asgi_app_instance  # noqa: F811

        _asgi_app = asgi_app_instance
        logger.warning("deps installed; ASGI app now available")
    except Exception as retry_exc:  # pragma: no cover - still degraded
        _asgi_error = f"{retry_exc.__class__.__name__}: {retry_exc}"
        logger.warning("ASGI app unavailable (%s); falling back to http.server", _asgi_error)

if _asgi_app is not None:
    # Primary path. Reached either directly or after the self-install above.
    import uvicorn

    print(f"Server starting on port {PORT} (ASGI app)...")
    uvicorn.run(_asgi_app, host="0.0.0.0", port=PORT, log_level="info")
else:
    # Degraded path: keep the form site reachable and publish WHY, because a
    # silent fallback looks exactly like "the change did not deploy".
    os.environ["BENTO_ASGI_IMPORT_ERROR"] = _asgi_error

    def _health_with_reason(_handler) -> None:
        body = json.dumps(
            {"status": "ok", "port": PORT, "asgi": "unavailable", "asgi_import_error": _asgi_error},
            ensure_ascii=False,
        ).encode("utf-8")
        _handler.send_response(200)
        _handler.send_header("Content-Type", "application/json; charset=utf-8")
        _handler.send_header("Content-Length", str(len(body)))
        _handler.end_headers()
        _handler.wfile.write(body)

    Handler.do_GET_backup = Handler.do_GET

    def _do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.split("?")[0] == "/health":
            _health_with_reason(self)
            return
        Handler.do_GET_backup(self)

    Handler.do_GET = _do_GET

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server starting on port {PORT} (FALLBACK: {_asgi_error})")
    server.serve_forever()
