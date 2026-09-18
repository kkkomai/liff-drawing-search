#!/usr/bin/env python3
# BUILD: 2026-09-17T16:00:00
# VERSION: v20260917ad
"""LIFF form + schedule API + image upload server (Render-ready)."""

import json
import os, time, time
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


def sendImageToLine(user_id, image_url, access_token):
    """Send image to LINE user using LINE Messaging API."""
    import urllib.request
    url = 'https://api.line.me/v2/bot/message/push'
    payload = json.dumps({
        'to': user_id,
        'messages': [{
            'type': 'image',
            'originalContentUrl': image_url,
            'previewImageUrl': image_url
        }]
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {'error': str(e)}


def findSimilarImages(imageDataUrl, searchQuery='', imageCount=3, userId='', scheduleName=''):
    """Call HERMES API to find similar images."""
    import urllib.request
    hermes_url = 'https://hermes.example.com/api/search'
    payload = json.dumps({
        'image': imageDataUrl,
        'query': searchQuery,
        'imageCount': imageCount,
        'userId': userId,
        'scheduleName': scheduleName
    }).encode('utf-8')
    req = urllib.request.Request(hermes_url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {'error': str(e)}


class Handler(SimpleHTTPRequestHandler):
    """HTTP request handler with anti-cache headers for LINE WebView."""

    def end_headers(self, message_body=None):
        """Override end_headers to add anti-cache headers to ALL responses."""
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, public, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        SimpleHTTPRequestHandler.end_headers(self)

    def do_GET(self):
        if self.path.startswith("/uploads/"):
            fname = os.path.basename(self.path)
            fpath = UPLOAD_DIR / fname
            if fpath.is_file():
                self.send_response(200)
                ext = fpath.suffix.lower()
                ct = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/png")
                SimpleHTTPRequestHandler.send_header(self, "Content-Type", ct)
                SimpleHTTPRequestHandler.send_header(self, "Content-Length", str(fpath.stat().st_size))
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
        if self.path.startswith("/search"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            image_data_url = params.get('image', [''])[0]
            line_user_id = params.get('lineUserId', [''])[0]
            if not image_data_url:
                self.send_json(400, {"error": "no image data"})
                return
            # Save the image
            import time as _time
            fname = "adjusted_" + str(int(_time.time())) + ".jpg"
            fpath = UPLOAD_DIR / fname
            if ',' in image_data_url:
                base64_data = image_data_url.split(',')[1]
                import base64
                image_bytes = base64.b64decode(base64_data)
                with open(fpath, "wb") as f:
                    f.write(image_bytes)
            image_url = f"https://liff-drawing-search.onrender.com/uploads/{fname}"
            # Send to LINE
            line_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
            line_result = {}
            if line_user_id and line_access_token:
                line_result = sendImageToLine(line_user_id, image_url, line_access_token)
            # Call HERMES for similar search
            search_result = findSimilarImages(image_data_url)
            self.send_json(200, {"download_url": "/uploads/" + fname, "search_results": search_result, "line_result": line_result})
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
        self.send_json(200, {"download_url": download_url, "message": "ファイルが完了しました。"})

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

    def do_POST(self):
        if self.path == "/search":
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)
            content_type = self.headers.get("Content-Type", "")
            boundary = None
            if "boundary=" in content_type:
                boundary = content_type.split("boundary=")[-1].encode()
            image_data = None
            line_user_id = ""
            if boundary:
                parts = raw.split(bytes([45, 45]) + boundary)
                for part in parts:
                    if b"filename=" in part:
                        hdr_end = part.find(bytes([13, 10, 13, 10]))
                        if hdr_end != -1:
                            image_data = part[hdr_end + 4:]
                            while image_data[-2:] == bytes([13, 10]):
                                image_data = image_data[:-2]
                        break
                    if b"lineUserId=" in part:
                        lm = __import__("re").search(b"lineUserId=([^&]+)", self.path)
                        if lm:
                            line_user_id = lm.group(1).decode()
            if image_data:
                fname = "adjusted_" + str(int(time.time())) + ".jpg"
                fpath = UPLOAD_DIR / fname
                with open(fpath, "wb") as f:
                    f.write(image_data)
                image_url = f"https://liff-drawing-search.onrender.com/uploads/{fname}"
                # Send to LINE
                line_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
                line_result = {}
                if line_user_id and line_access_token:
                    line_result = sendImageToLine(line_user_id, image_url, line_access_token)
                # Call HERMES for similar search
                import base64 as _b64
                image_base64 = "data:image/jpeg;base64," + _b64.b64encode(image_data).decode()
                search_result = findSimilarImages(image_base64)
                self.send_json(200, {"download_url": "/uploads/" + fname, "search_results": search_result, "line_result": line_result})
                return
            self.send_json(400, {"error": "No image data"})
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
        pass  # Suppress console logs


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server starting on port {PORT}...")
    server.serve_forever()
