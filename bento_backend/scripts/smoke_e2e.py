"""Live smoke test: boots uvicorn on a real port and drives the API over HTTP.

Complements the TestClient suite by exercising the actual ASGI server,
CORS headers, startup migrations and JSON over a socket.

Usage:
    python scripts/smoke_e2e.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

DEV_TOKEN = "smoke-dev-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(base: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise SystemExit("server did not become ready")


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        raise SystemExit(f"smoke test failed at: {label}")


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="bento-smoke-")
    db_path = Path(tmpdir) / "smoke.db"
    env = {
        **os.environ,
        "BENTO_DATABASE_PATH": str(db_path),
        "BENTO_DEV_AUTH_TOKEN": DEV_TOKEN,
        "BENTO_CORS_ORIGINS": "https://liff.line.me",
        "PYTHONPATH": str(BACKEND),
    }
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [str(BACKEND / ".venv/Scripts/python.exe"), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND, env=env,
    )
    try:
        wait_ready(base)
        # 20 employees via the real seed script against the same DB
        subprocess.run(
            [str(BACKEND / ".venv/Scripts/python.exe"), str(BACKEND / "scripts/seed.py"),
             "--count", "20", "--admin-code", "E001", "--line-prefix", "Usmoke"],
            cwd=BACKEND, env=env, check=True, capture_output=True,
        )

        check("health", httpx.get(f"{base}/health").json()["status"] == "ok")

        # unregistered LINE ID -> 403
        r = httpx.post(f"{base}/api/auth/login",
                       json={"line_user_id": "Unotmaster0000000000000000", "dev_token": DEV_TOKEN})
        check("unregistered LINE id rejected", r.status_code == 403, r.text)
        check("error shape", set(r.json()) == {"success", "error", "message"}, r.text)

        def login(code: str, n: int) -> dict:
            resp = httpx.post(f"{base}/api/auth/login",
                              json={"line_user_id": f"Usmoke{n:04d}", "dev_token": DEV_TOKEN})
            check(f"login {code}", resp.status_code == 200, resp.text)
            return {"Authorization": f"Bearer {resp.json()['token']}"}

        emp = login("E002", 2)
        adm = login("E001", 1)

        check("no token -> 401", httpx.get(f"{base}/api/orders").status_code == 401)

        today = date_tokyo()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731

        r = httpx.post(f"{base}/api/orders", json={"date": d(0), "status": "needed"}, headers=emp)
        check("today order", r.status_code == 200 and r.json()["order"]["status"] == "needed", r.text)
        r = httpx.put(f"{base}/api/orders", json={"date": d(14), "status": "not_needed"}, headers=emp)
        check("far-future order (2 weeks)", r.status_code == 200, r.text)
        r = httpx.post(f"{base}/api/orders", json={"date": d(-1), "status": "needed"}, headers=emp)
        check("past date refused", r.status_code == 400
              and r.json()["error"] == "past_date_modification_not_allowed", r.text)

        r = httpx.get(f"{base}/api/orders?start_date={d(0)}&end_date={d(20)}", headers=emp)
        check("list returns 2 rows", len(r.json()["orders"]) == 2, r.text)

        r = httpx.get(f"{base}/api/admin/orders?date={d(0)}", headers=emp)
        check("employee blocked from admin", r.status_code == 403, r.text)
        r = httpx.get(f"{base}/api/admin/orders?date={d(0)}", headers=adm)
        body = r.json()
        check("admin sees 20", body["summary"]["total_employees"] == 20, r.text)
        check("admin counts", body["summary"]["needed_count"] == 1
              and body["summary"]["unregistered_count"] == 19, json.dumps(body["summary"]))

        r = httpx.get(f"{base}/api/admin/orders?start_date={d(0)}&end_date={d(6)}", headers=adm)
        check("admin range has 7 days", len(r.json()["days"]) == 7, r.text)

        r = httpx.get(f"{base}/api/orders/audit", headers=emp)
        check("audit trail present", len(r.json()["logs"]) == 2, r.text)

        r = httpx.options(f"{base}/api/orders", headers={
            "Origin": "https://liff.line.me", "Access-Control-Request-Method": "GET"})
        check("CORS allows LIFF origin", r.headers.get("access-control-allow-origin") == "https://liff.line.me")
        r = httpx.options(f"{base}/api/orders", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        check("CORS blocks other origins", r.headers.get("access-control-allow-origin") is None)

        print("\nAll smoke checks passed.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def date_tokyo():
    from app.timeutil import today_tokyo

    return today_tokyo()


if __name__ == "__main__":
    raise SystemExit(main())