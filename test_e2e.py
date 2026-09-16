import unittest
import json
import urllib.request
import urllib.error
import threading
import time
import ssl
import sys
import os

# Add server path
sys.path.insert(0, r"C:/Users/user/HERMES_AGENT/liff_form_sample")
import search_server

class TestLIFFServerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8766
        # Start server in thread
        import http.server
        class TestHandler(search_server.Handler):
            def log_message(self, format, *args):
                pass # suppress logs

        cls.httpd = http.server.HTTPServer(("127.0.0.1", cls.port), TestHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(r"C:/Users/user/HERMES_AGENT/liff_form_sample/cert.pem", r"C:/Users/user/HERMES_AGENT/liff_form_sample/key.pem")
        cls.httpd.socket = ctx.wrap_socket(cls.httpd.socket, server_side=True)
        
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_01_get_schedules(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(f"https://127.0.0.1:{self.port}/schedules")
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("schedules", data)
        except urllib.error.URLError as e:
            self.fail(f"GET /schedules failed: {e}")

    def test_02_post_schedule_success(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        payload = {
            "title": "テスト会議",
            "date": "2026-09-10",
            "time": "14:00",
            "description": "エンドポイントテスト用スケジュール"
        }
        req = urllib.request.Request(
            f"https://127.0.0.1:{self.port}/schedules",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                self.assertEqual(resp.status, 201)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("success"))
                self.assertEqual(data["schedule"]["title"], "テスト会議")
                self.assertEqual(data["schedule"]["date"], "2026-09-10")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            self.fail(f"POST /schedules failed with {e.code}: {body}")

    def test_03_post_schedule_validation_error(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Missing title
        payload = {
            "date": "2026-09-10"
        }
        req = urllib.request.Request(
            f"https://127.0.0.1:{self.port}/schedules",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                self.fail("Expected 400 Bad Request")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode("utf-8"))
            self.assertIn("error", data)

if __name__ == "__main__":
    unittest.main()
