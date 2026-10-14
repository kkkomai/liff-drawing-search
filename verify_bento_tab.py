#!/usr/bin/env python3
"""Verify the bento tab wiring in the served form.html (no browser needed)."""
import re
import urllib.request

URL = "https://liff-drawing-search.onrender.com/form.html"
html = urllib.request.urlopen(URL, timeout=10).read().decode("utf-8")

print("=== タブボタン ===")
for m in re.finditer(r'<button id="(tab-\w+-btn)"[^>]*data-tab="(\w+)"[^>]*>([^<]+)</button>', html):
    print("  %-10s | %s" % (m.group(2), m.group(3)))

print()
print("=== タブコンテンツ ===")
for m in re.finditer(r'class="tab-content([^"]*)" id="tab-(\w+)"', html):
    print("  %-10s | active=%s" % (m.group(2), bool(m.group(1).strip())))

print()
print("=== JS関数定義 ===")
for fn in ("switchTab", "ensureBentoFrame", "sendBentoCredentials", "resetBentoCredentials"):
    print("  %-22s %s" % (fn + ":", "OK" if ("function " + fn) in html else "NG"))

print()
print("=== 構造整合性 ===")
btns = set(re.findall(r'data-tab="(\w+)"', html))
cts = set(re.findall(r'id="tab-(\w+)"(?![-\w]*-btn)', html))
print("  ボタン      :", sorted(btns))
print("  コンテンツ  :", sorted(cts))
print("  差分        :", sorted(btns ^ cts) or "なし（整合）")

needle = "tabName === 'bento'"
print("  switchTab→bento 呼出 :", "OK" if needle in html else "NG")
print("  message リスナー      :", "OK" if "bento-ready" in html else "NG")
print("  iframe data-src      :", "OK" if 'id="bento-frame"' in html else "NG")