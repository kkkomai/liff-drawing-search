#!/usr/bin/env python3
"""Guard the editor-overlay listeners that assume their DOM nodes exist.

The 類似検索 / 画像調整 tabs were removed from the markup, but their editor
JavaScript stayed behind. Those handlers call addEventListener on elements that
no longer exist, and an unguarded TypeError there aborts the entire <script>
block — including the DOMContentLoaded handler that draws the calendar, which
is why the calendar stayed blank with no visible error.

Rather than rip out several hundred lines of editor code (risky to slice by
line number), make every listener registration null-safe. Missing elements then
simply skip their own wiring while the rest of the page boots normally.
"""
import io
import re
import sys

PATH = "index.html"

# document.getElementById('x').addEventListener(...)  ->  guarded
PATTERN = re.compile(
    r"([ \t]*)document\.getElementById\((?P<q>['\"])(?P<id>[\w-]+)(?P=q)\)\s*\.addEventListener\("
)

GUARD = (
    "var __el = document.getElementById('{id}');\n"
    "{indent}if (__el) __el.addEventListener("
)


def main() -> int:
    src = io.open(PATH, encoding="utf-8").read()
    before = src

    out = []
    last = 0
    hits = 0
    for m in PATTERN.finditer(src):
        out.append(src[last:m.start()])
        indent = m.group(1)
        out.append(GUARD.format(id=m.group("id"), indent=indent))
        last = m.end()
        hits += 1
    out.append(src[last:])
    src = "".join(out)

    if hits == 0:
        print("FAIL: no unguarded listeners found (already guarded?)")
        return 1

    io.open(PATH, "w", encoding="utf-8", newline="").write(src)
    print("OK: guarded %d listener registration(s)" % hits)
    print("    bytes %d -> %d" % (len(before), len(src)))

    # Nothing unguarded should remain by the same pattern.
    left = len(PATTERN.findall(src))
    print("    remaining unguarded: %d" % left)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())