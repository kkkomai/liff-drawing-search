#!/usr/bin/env python3
"""End-to-end check of the single-origin ASGI app.

Checks the property that matters: the form site and the ordering API answer on
the SAME origin, so the LIFF iframe never issues a cross-origin request.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BENTO_PROBE_BASE", "http://localhost:10000")


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def main() -> int:
    print("=== single-origin ASGI probe (%s) ===" % BASE)

    st, body = get("/health")
    health = json.loads(body) if st == 200 else {}
    print("  /health                 : HTTP %s" % st)
    print("    bento_api_mounted     : %s" % health.get("bento_api_mounted"))
    print("    bento_app_env         : %s" % health.get("bento_app_env"))
    print("    line_verification     : %s" % health.get("line_verification"))

    failures = []
    if not health.get("bento_api_mounted"):
        failures.append("bento API not mounted")

    # Static site on this origin.
    for path in ("/", "/form.html", "/bento/index.html", "/bento/js/app.js", "/bento/js/mock.js"):
        st, _ = get(path)
        print("  GET %-22s: HTTP %s" % (path, st))
        if st != 200:
            failures.append("%s -> %s" % (path, st))

    # Existing schedules API preserved.
    st, body = get("/schedules")
    rows = json.loads(body).get("schedules", []) if st == 200 else []
    print("  GET /schedules           : HTTP %s (%d rows)" % (st, len(rows)))
    if st != 200:
        failures.append("/schedules")

    # Bento API on the SAME origin.
    st, body = get("/api/config")
    print("  GET /api/config          : HTTP %s" % st)
    if st != 200:
        failures.append("/api/config")

    st, _ = post("/api/auth/login", {})
    print("  POST /api/auth/login     : HTTP %s (422 = reached, validation)" % st)
    if st not in (401, 422):
        failures.append("/api/auth/login -> %s (expected 401/422)" % st)

    print("")
    if failures:
        print("RESULT: %d problem(s)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("RESULT: form site + ordering API answer on one origin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())