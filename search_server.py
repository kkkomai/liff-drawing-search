#!/usr/bin/env python3
"""LIFF form + similarity search + schedule API server on HTTPS port 8765.

Uses ThreadingHTTPServer so a single crashing request (e.g. client disconnect
during /search) cannot take down the whole LIFF service.  See
line-liff-integration skill → references/internal-lan-liff-deployment.md.
"""

import base64
import json
import os
import re
import subprocess
import sys
import uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

SEARCH_SCRIPT = r"C:/Users/user/HERMES_AGENT/clip_faiss_index/dinov2_search.py"
SEARCH_DIR = r"C:/Users/user/HERMES_AGENT/clip_faiss_index"
RENDER_SCRIPT = r"C:/Users/user/HERMES_AGENT/clip_faiss_index/render_top_results.py"
RENDER_OUT = r"C:/Users/user/AppData/Local/hermes/cache/images/line_search"
os.makedirs(RENDER_OUT, exist_ok=True)
UPLOAD_DIR = r"C:/Users/user/AppData/Local/hermes/cache/images/search_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Schedule storage (in-memory for now; persists per server run)
SCHEDULES_FILE = r"C:/Users/user/HERMES_AGENT/liff_form_sample/schedules.json"


def load_schedules():
    """Load schedules from JSON file."""
    try:
        with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_schedules(schedules):
    """Save schedules to JSON file."""
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/search":
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_json(400, {"error": "multipart/form-data required"})
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)

            # Parse multipart
            boundary = content_type.split("boundary=")[-1].encode()
            parts = raw.split(b"--" + boundary)
            image_path = None
            index_file = "dinov2_large_index.faiss"
            meta_file = "dinov2_large_metadata.json"

            for part in parts:
                if b'name=\"image\"' in part:
                    header_end = part.find(b"\r\n\r\n")
                    data = part[header_end + 4 :].rstrip(b"\r\n")
                    ext = ".jpg"
                    if b"png" in part[:200]:
                        ext = ".png"
                    fname = f"{uuid.uuid4().hex}{ext}"
                    image_path = os.path.join(UPLOAD_DIR, fname)
                    Path(image_path).write_bytes(data)
                elif b'name="index"' in part:
                    header_end = part.find(b"\r\n\r\n")
                    data = part[header_end + 4 :].rstrip(b"\r\n").decode()
                    if data == "photo":
                        index_file = "dinov2_large_index_photo.faiss"
                        meta_file = "dinov2_large_metadata_photo.json"

            if not image_path or not os.path.exists(image_path):
                self.send_json(400, {"error": "no image uploaded"})
                return

            try:
                # 1) Run FAISS search
                cmd = [
                    r"C:\ProgramData\chocolatey\bin\python3.14.exe",
                    SEARCH_SCRIPT,
                    "--image",
                    image_path,
                    "--top",
                    "3",
                    "--index-file",
                    index_file,
                    "--meta-file",
                    meta_file,
                ]
                out = subprocess.check_output(
                    cmd, cwd=SEARCH_DIR, stderr=subprocess.STDOUT, text=True, timeout=120
                )
                results = self._parse(out)

                # 2) Render top results to PNG
                render_cmd = [
                    r"C:\ProgramData\chocolatey\bin\python3.14.exe",
                    RENDER_SCRIPT,
                    "--query",
                    image_path,
                    "--top",
                    "3",
                    "--outdir",
                    RENDER_OUT,
                ]
                r_out = subprocess.check_output(
                    render_cmd, cwd=SEARCH_DIR, stderr=subprocess.STDOUT, text=True, timeout=120
                )
                pngs = self._parse_render(r_out)

                # 3) Attach public PNG URL for each result
                for r in results:
                    png = next(
                        (p for p in pngs if p.get("name") == r.get("name")), None
                    )
                    if png and os.path.exists(png.get("png", "")):
                        r["image_url"] = "/search-images/" + os.path.basename(png["png"])

                self.send_json(200, {"results": results, "index": index_file})
            except subprocess.TimeoutExpired:
                self.send_json(504, {"error": "search timeout"})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            finally:
                try:
                    os.remove(image_path)
                except OSError:
                    pass
            return

        if self.path == "/schedules":
            """POST: Create a new schedule entry"""
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                self.send_json(400, {"error": "application/json required"})
                return

            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return

            # Validate required fields
            title = data.get("title", "").strip()
            date = data.get("date", "").strip()
            time_val = data.get("time", "").strip()
            description = data.get("description", "").strip()

            if not title or not date:
                self.send_json(400, {"error": "title and date are required"})
                return

            schedules = load_schedules()

            # Add new schedule
            new_schedule = {
                "id": str(uuid.uuid4()),
                "title": title,
                "date": date,
                "time": time_val,
                "description": description,
                "created": subprocess.check_output(
                    ["date", "+%Y-%m-%dT%H:%M:%S%z"], text=True
                ).strip(),
            }
            schedules.append(new_schedule)
            save_schedules(schedules)

            self.send_json(201, {"success": True, "schedule": new_schedule})
            return

        self.send_json(404, {"error": "not found"})
        return

    def do_DELETE(self):
        if self.path.startswith("/schedules/"):
            schedule_id = self.path.split("/")[-1]
            schedules = load_schedules()
            new_schedules = [s for s in schedules if s["id"] != schedule_id]
            if len(new_schedules) < len(schedules):
                save_schedules(new_schedules)
                self.send_json(200, {"success": True, "message": "Schedule deleted"})
                return
            self.send_json(404, {"error": "Schedule not found"})
            return
        self.send_json(404, {"error": "not found"})

    def do_PUT(self):
        if self.path.startswith("/schedules/"):
            schedule_id = self.path.split("/")[-1]
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                self.send_json(400, {"error": "application/json required"})
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return
            schedules = load_schedules()
            for s in schedules:
                if s["id"] == schedule_id:
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

    def do_GET(self):
        if self.path.startswith("/search-images/"):
            fname = os.path.basename(self.path)
            fpath = os.path.join(RENDER_OUT, fname)
            if os.path.isfile(fpath):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(os.path.getsize(fpath)))
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
                self.wfile.flush()
                return
            self.send_json(404, {"error": "image not found"})
            return

        if self.path == "/ca.html":
            ca_html = """<!DOCTYPE html>
<html>
<head><title>証明書ダウンロード - LIFFフォーム</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body { font-family: -apple-system, sans-serif; padding: 20px; text-align: center; }
.download-btn { display: inline-block; background: #00b900; color: white; padding: 16px 32px; text-decoration: none; border-radius: 8px; font-size: 18px; font-weight: bold; margin: 20px 0; }
h1 { font-size: 20px; color: #333; }
.note { font-size: 13px; color: #666; margin-top: 20px; }
</style>
</head>
<body>
<h1>📋 LIFF申請フォーム - 証明書インストール</h1>
<p>iPhoneから以下の手順で証明書をインストールしてください。</p>
<a href="/rootCA.pem" download class="download-btn">📲 証明書ダウンロード</a>
<div class="note">ダウンロード後、<br>「設定」→「一般」→「プロファイル」<br>からインストールしてください。</div>
<div class="note" style="margin-top:30px;">
インストール完了後、<br>
LIFFフォームを開いてください:<br>
<a href="https://liff.line.me/2011376207-0e7oVWOT" style="color:#1976d2;">https://liff.line.me/2011376207-0e7oVWOT</a>
</div>
</body>
</html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ca_html.encode("utf-8"))
            self.wfile.flush()
            return

        if self.path == "/rootCA.pem":
            ca_path = os.path.join(os.path.dirname(__file__), "cert.pem")
            if os.path.isfile(ca_path):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                self.send_header("Content-Disposition", "attachment; filename=rootCA.pem")
                self.send_header("Content-Length", str(os.path.getsize(ca_path)))
                self.end_headers()
                with open(ca_path, "rb") as f:
                    self.wfile.write(f.read())
                self.wfile.flush()
                return
            self.send_json(404, {"error": "CA cert not found"})
            return

        if self.path == "/schedules":
            """GET: Fetch all schedules"""
            schedules = load_schedules()
            self.send_json(200, {"schedules": schedules})
            return

        super().do_GET()

    def _parse(self, text: str):
        results = []
        current = {}
        for line in text.splitlines():
            m = re.match(r"^\s*\[(\d+)\]\s+(.+)$", line)
            if m:
                if current.get("name"):
                    results.append(current)
                current = {"rank": int(m.group(1)), "name": m.group(2).strip()}
                continue
            m = re.match(r"^\s*パス:\s*(.+)$", line)
            if m:
                current["path"] = m.group(1).strip().replace("\\", "/")
                continue
            m = re.match(r"^\s*類似度:\s*([\d.]+)", line)
            if m:
                current["similarity"] = float(m.group(1))
                continue
        if current.get("name"):
            results.append(current)
        return results

    def _parse_render(self, text: str):
        pngs = {}
        for line in text.splitlines():
            m = re.match(r"^\s*\[(\d+)\] rendered -> (.+)$", line)
            if m:
                pngs[int(m.group(1))] = m.group(2).strip()
        m = re.search(r"JSON_BEGIN\n(.+?)\nJSON_END", text, re.S)
        items = []
        if m:
            try:
                data = json.loads(m.group(1))
                for item in data:
                    rank = item.get("rank")
                    if rank and rank in pngs:
                        item["png"] = pngs[rank]
                    items.append(item)
            except Exception:
                pass
        return items

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Client disconnected before we finished writing — safe to swallow.
            pass
        except Exception:
            pass

    def log_message(self, fmt, *args):
        sys.stderr.write(fmt % args + "\n")


def main():
    port = 8765
    os.chdir(r"C:/Users/user/HERMES_AGENT/liff_form_sample")
    with ThreadingHTTPServer(("0.0.0.0", port), Handler) as httpd:
        ctx = __import__("ssl").SSLContext(__import__("ssl").PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain("cert.pem", "key.pem")
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        print(f"HTTPS server + /search API + /schedules API on https://0.0.0.0:{port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
