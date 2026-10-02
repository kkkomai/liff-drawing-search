#!/usr/bin/env python3
"""LIFF schedule API server (Render-ready)."""

import json
import logging
import os, subprocess, uuid
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

logger = logging.getLogger(__name__)
PORT = int(os.environ.get("PORT", "10000"))
BASE_DIR = Path(__file__).parent.resolve()
SCHEDULES_FILE = BASE_DIR / "schedules.json"

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


def load_schedules():
    try:
        with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_schedules(schedules):
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)


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
            rel = self.path[len("/bento/"):].split("?")[0]
            if not rel:
                rel = "index.html"
            target = (bento_dir / rel).resolve()
            # Path traversal guard: never serve anything outside bento_dir.
            try:
                target.relative_to(bento_dir.resolve())
            except ValueError:
                self.send_json(403, {"error": "forbidden"})
                return
            if not target.is_file():
                self.send_json(404, {"error": "not_found"})
                return
            ctype = self.guess_type(str(target))
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/schedules":
            schedules = load_schedules()
            self.send_json(200, {"schedules": schedules})
            return
        # For HTML/CSS/JS/SDK static files - use parent handler
        SimpleHTTPRequestHandler.do_GET(self)

    def do_DELETE(self):
        if self.path.startswith("/schedules/"):
            sid = self.path.split("/")[-1]
            schedules = load_schedules()
            new = [s for s in schedules if s.get("id") != sid]
            if len(new) < len(schedules):
                save_schedules(new)
                self.send_json(200, {"success": True, "message": "Schedule deleted"})
                return
            self.send_json(404, {"error": "Schedule not found"})
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
            schedules = load_schedules()
            for s in schedules:
                if s.get("id") == sid:
                    s["title"] = data.get("title", s["title"]).strip()
                    s["date"] = data.get("date", s["date"]).strip()
                    s["time"] = data.get("time", s["time"]).strip()
                    s["description"] = data.get("description", s["description"]).strip()
                    save_schedules(schedules)
                    self.send_json(200, {"success": True, "schedule": s})
                    return
            self.send_json(404, {"error": "Schedule not found"})
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
                schedules = load_schedules()
                new = {"id": str(uuid.uuid4()), "title": title, "date": date, "time": data.get("time", "").strip(), "description": data.get("description", "").strip(), "created": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M:%S+0900")}
                schedules.append(new)
                save_schedules(schedules)
                self.send_json(201, {"success": True, "schedule": new})
            except Exception as exc:
                logger.error("POST /create error: %s", exc)
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
    # If uvicorn or the ASGI app is unavailable (e.g. dependencies not yet
    # installed), fall back to the original ThreadingHTTPServer so the form
    # site stays reachable and /health still answers.
    try:
        import uvicorn  # noqa: F401
        from asgi_app import app as asgi_app_instance  # noqa: F401
    except Exception as exc:  # pragma: no cover - degraded but still serving
        logger.warning("ASGI app unavailable (%s); falling back to http.server", exc)
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        print(f"Server starting on port {PORT}...")
        server.serve_forever()
    else:
        import uvicorn

        print(f"Server starting on port {PORT} (ASGI app)...")
        uvicorn.run(asgi_app_instance, host="0.0.0.0", port=PORT, log_level="info")
