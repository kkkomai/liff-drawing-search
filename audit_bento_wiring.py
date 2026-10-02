#!/usr/bin/env python3
"""Static audit of the bento iframe wiring on the served index.html.

The browser driver is unreliable here, so verify the pieces that decide whether
the tab renders at all: the iframe element, the asset URLs it pulls, the
postMessage handshake on both sides, and the tab/content id pairing.
"""
import re
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:10000"
HOST_HTML = urllib.request.urlopen(BASE + "/index.html", timeout=10).read().decode("utf-8")


def http(path: str) -> int:
    try:
        return urllib.request.urlopen(BASE + path, timeout=10).status
    except Exception:
        return 0


print("=== tabs on host page ===")
for m in re.finditer(r'data-tab="(\w+)"', HOST_HTML):
    print("  button   :", m.group(1))
for m in re.finditer(r'class="tab-content[^"]*" id="tab-(\w+)"', HOST_HTML):
    print("  content  :", m.group(1))

btns = set(re.findall(r'data-tab="(\w+)"', HOST_HTML))
cts = set(re.findall(r'id="tab-(\w+)"(?![-\w]*-btn)', HOST_HTML))
print("  unmatched:", sorted(btns ^ cts) or "none")

print()
print("=== iframe element ===")
frame = re.search(r'<iframe id="bento-frame"[^>]*>', HOST_HTML, re.S)
if not frame:
    print("  FAIL: #bento-frame not found")
    sys.exit(1)
tag = frame.group(0)
print("  found")
src = re.search(r'data-src="([^"]+)"', tag)
print("  data-src :", src.group(1) if src else "MISSING")
has_src = re.search(r'\ssrc="', tag)
print("  eager src:", "yes (bad, bypasses lazy load)" if has_src else "no (lazy, correct)")
height = re.search(r'height:([^"]+)', tag)
print("  height   :", height.group(1) if height else "MISSING")

print()
print("=== host-side handshake ===")
print("  ensureBentoFrame   :", "OK" if "function ensureBentoFrame" in HOST_HTML else "NG")
print("  sendBentoCredentials:", "OK" if "function sendBentoCredentials" in HOST_HTML else "NG")
print("  lineUserId sent    :", "OK" if "lineUserId:" in HOST_HTML else "NG")
print("  bento-ready listen :", "OK" if "bento-ready" in HOST_HTML else "NG")
print("  bento-auth-error   :", "OK" if "bento-auth-error" in HOST_HTML else "NG")

print()
print("=== bento assets ===")
bento = urllib.request.urlopen(BASE + "/bento/index.html", timeout=10).read().decode("utf-8")
for m in re.finditer(r'(?:src|href)="([^"]+)"', bento):
    ref = m.group(1)
    if ref.startswith("http"):
        print("  %-44s external" % ref)
        continue
    print("  %-44s HTTP %s" % (ref, http("/bento/" + ref.lstrip("/"))))

print()
print("=== bento app wiring ===")
app = urllib.request.urlopen(BASE + "/bento/js/app.js", timeout=10).read().decode("utf-8")
print("  embedded branch  :", "OK" if "cfg.embedded" in app else "NG")
print("  announceReady    :", "OK" if "bento-ready" in app else "NG")
print("  watchdog         :", "OK" if "WATCHDOG_MS" in app else "NG")
print("  showSplash scope :", "OK" if "function showSplash" in app else "NG")