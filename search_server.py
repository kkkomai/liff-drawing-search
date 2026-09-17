#!/usr/bin/env python3
"""LIFF form + schedule API + image upload server (Render-ready)."""

import json
import os
import re
import subprocess
import sys
import uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", "10000"))
BASE_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
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
    def send_response(self, code, message=None):
        # Add cache-control headers to prevent LINE WebView caching
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, public, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().send_response(code, message)
    def do_GET(self):
        if self.path.startswith("/uploads/"):
            self._handle_upload()
            return
        if self.path == "/schedules":
            self._handle_create_schedule()
            return
        self.send_json(404, {"error": "not found"})

    def do_GET(self):
        if self.path.startswith("/uploads/"):
            fname = os.path.basename(self.path)
            fpath = UPLOAD_DIR / fname
            if fpath.is_file():
                self.send_response(200)
                ext = fpath.suffix.lower()
                ct = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/png")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(fpath.stat().st_size))
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
                return
            self.send_json(404, {"error": "image not found"})
            return
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "port": PORT})
            return
        if self.path == "/schedules":
            schedules = load_schedules()
            self.send_json(200, {"schedules": schedules})
            return
        super().do_GET()

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

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_json(400, {"error": "multipart/form-data required"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        bm = content_type.find("boundary=")
        if bm < 0:
            self.send_json(400, {"error": "no boundary"})
            return
        boundary = content_type[bm + 9:].strip().strip(chr(34)).strip(chr(39)).encode()
        parts = raw.split(b"--" + boundary)
        image_data = None
        image_filename = None
        for part in parts:
            if b"name=\"image\"" in part:
                fn_m = re.search(b"filename=\"([^\"]+)\"", part)
                if fn_m:
                    image_filename = fn_m.group(1).decode()
                he = part.find(bytes([10, 10]))
                if he >= 0:
                    image_data = part[he + 2:].rstrip(bytes([10]))
                break
        if not image_data or not image_filename:
            self.send_json(400, {"error": "no image provided"})
            return
        ext = os.path.splitext(image_filename)[1].lower() or ".jpg"
        fname = f"{uuid.uuid4().hex}{ext}"
        fpath = UPLOAD_DIR / fname
        fpath.write_bytes(image_data)
        download_url = f"/uploads/{fname}"
        self.send_json(200, {"download_url": download_url, "message": "\u30e2\u30c3\u30d7\u30fb\u30d5\u30a1\u30a4\u30eb\u304c\u5b8c\u6210\u3067\u3057\u305f\u3002HERMES\u4fa1\u307f\u307e\u305f\u305f\u3001\u3053\u306eURL\u3092\u4ea4\u3048\u3066\u304f\u3060\u3055\u3044\u3002"})
    def _handle_create_schedule(self):
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

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write(fmt % args + chr(10))


def main():
    os.chdir(BASE_DIR)
    try:
        with ThreadingHTTPServer(("0.0.0.0", PORT), Handler) as httpd:
            print("Starting server...", flush=True); print(f"Server starting on port {PORT}...", flush=True)
            httpd.serve_forever()
    except Exception as e:
        print(f"Server error: {e}", flush=True)
        raise


if __name__ == "__main__":
    main()
