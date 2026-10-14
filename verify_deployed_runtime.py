#!/usr/bin/env python3
"""Download the deployed page and run the same runtime probe against it.

Parsing the local file only proves the repo is healthy; this fetches what
render.com actually serves and executes that, so a deploy that shipped a stale
or partially-cached artifact is caught here rather than in LINE.
"""
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://liff-drawing-search.onrender.com").rstrip("/")
HERE = Path(__file__).parent


def main() -> int:
    print("fetching %s/ ..." % BASE)
    html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")

    ver = re.search(r'window.__v="([^"]+)"', html)
    print("  served __v:", ver.group(1) if ver else "?")
    print("  bytes     :", len(html))

    tmp = HERE / "_deployed_probe.html"
    tmp.write_text(html, encoding="utf-8")

    probe = HERE / "probe_runtime.js"
    print("")
    print("running runtime probe on the SERVED file ...")
    proc = subprocess.run(
        ["node", str(probe), str(tmp.name)],
        cwd=str(HERE), capture_output=True, text=True, timeout=180,
    )
    print(proc.stdout)
    if proc.stderr.strip():
        print("stderr:", proc.stderr.strip()[:400])

    tmp.unlink(missing_ok=True)
    out = proc.stdout
    if "calendar RENDERS" in out:
        print("RESULT: deployed page boots cleanly and draws the calendar.")
        return 0
    print("RESULT: deployed page still fails — see the probe output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())