import http.server
import os
import threading
import webbrowser

PORT = 8888
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AppData", "Local", "hermes", "cache", "images", "line_search")
IMAGE_DIR = os.path.normpath(IMAGE_DIR)

class ImageHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=IMAGE_DIR, **kwargs)
    
    def log_message(self, format, *args):
        pass  # Suppress logs

def start_server():
    server = http.server.HTTPServer(("0.0.0.0", PORT), ImageHandler)
    server.serve_forever()

if __name__ == "__main__":
    print(f"Image server serving {IMAGE_DIR} on port {PORT}")
    start_server()
