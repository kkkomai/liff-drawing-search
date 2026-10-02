#!/usr/bin/env python3
"""Verify the deployed page carries the fixes that unblock the calendar."""
import re
import sys
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://liff-drawing-search.onrender.com").rstrip("/")
HTML = urllib.request.urlopen(BASE + "/", timeout=20).read().decode("utf-8")

CHECKS = [
    ("version bumped",            "v20261001d" in HTML),
    ("initTabs is invoked",       "initTabs();" in HTML and "function initTabs" in HTML),
    ("liff.init not awaited",     "Fire and forget" in HTML),
    ("tab buttons == 3",          len(re.findall(r'data-tab="(\w+)"', HTML)) == 3),
    ("tab contents == 3",         len(re.findall(r'class="tab-content[^"]*" id="tab-(\w+)"', HTML)) == 3),
    ("no retired search tab",     'data-tab="search"' not in HTML),
    ("no retired adjust tab",     'data-tab="adjust"' not in HTML),
    ("bento frame present",       'id="bento-frame"' in HTML),
    ("bento auth-error reported", "bento-auth-error" in HTML),
]

print("=== deployed checks (%s) ===" % BASE)
failed = 0
for label, ok in CHECKS:
    print("  %-28s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        failed += 1

ver = re.search(r"window.__v=\"([^\"]+)\"", HTML)
print("  %-28s %s" % ("window.__v", ver.group(1) if ver else "?"))
print("")
print("VERDICT:", "all checks passed" if failed == 0 else "%d check(s) failed" % failed)
sys.exit(1 if failed else 0)