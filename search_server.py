#!/usr/bin/env python3
"""LIFF schedule API server (Render-ready)."""

import json
import os, subprocess, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", "10000"))
BASE_DIR = Path(__file__).parent.resolve()
SCHEDULES_FILE = BASE_DIR / "schedules.json"


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
        if self.path == "/create":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return
            title = data.get("title", "").strip()
            date = data.get("date", "").strip()
            if not title or not date:
                self.send_json(400, {"error": "title and date are required"})
                return
            schedules = load_schedules()
            new = {"id": str(uuid.uuid4()), "title": title, "date": date, "time": data.get("time", "").strip(), "description": data.get("description", "").strip(), "created": subprocess.check_output(["date", "+%Y-%m-%dT%H:%M:%S%z"], text=True).strip()}
            schedules.append(new)
            save_schedules(schedules)
            self.send_json(201, {"success": True, "schedule": new})
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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server starting on port {PORT}...")
    server.serve_forever()
