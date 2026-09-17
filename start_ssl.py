#!/usr/bin/env python3
"""Start search_server.py with SSL (mkcert) and bind to 0.0.0.0:8765."""
import ssl
import sys
import os
import http.server
from http.server import ThreadingHTTPServer

# Reuse the search_server.py handler
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search_server
from search_server import Handler, PORT, BASE_DIR, UPLOAD_DIR, SCHEDULES_FILE

CERT = str(BASE_DIR / "cert.pem")
KEY = str(BASE_DIR / "key.pem")
BIND_HOST = "0.0.0.0"

def main():
    httpd = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print(f"Starting HTTPS LIFF server on https://{BIND_HOST}:{PORT} ...", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
    finally:
        httpd.server_close()

if __name__ == "__main__":
    main()
