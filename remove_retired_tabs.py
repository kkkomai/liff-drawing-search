#!/usr/bin/env python3
"""Remove the retired 類似検索 / 画像調整 tab blocks from index.html.

These two tabs were superseded by the LINE chat triggers (the FAISS flow answers
in chat now), so the tab bodies and their buttons are dead weight. Deleting them
by hand risks slicing through an unrelated element, so the block boundaries are
located by their wrapper ids and the slice is asserted before writing.
"""
import io
import re
import sys

PATH = "index.html"
START_ID = 'class="tab-content" id="tab-search"'
END_MARKER = 'class="tab-content" id="tab-bento"'


def main() -> int:
    html = io.open(PATH, encoding="utf-8").read()
    before_lines = html.count("\n") + 1

    start = html.find(START_ID)
    if start == -1:
        print("FAIL: %s not found (already removed?)" % START_ID)
        return 1
    # Rewind to the start of that line so no stray indentation is left behind.
    line_start = html.rfind("\n", 0, start) + 1

    end = html.find(END_MARKER, start)
    if end == -1:
        print("FAIL: %s not found after the search tab" % END_MARKER)
        return 1
    # Keep the indentation of the end marker's own line.
    end_line_start = html.rfind("\n", start, end) + 1

    removed = html[line_start:end_line_start]
    n_search = removed.count('id="tab-search"')
    n_adjust = removed.count('id="tab-adjust"')
    if n_search != 1 or n_adjust != 1:
        print("FAIL: expected 1 search + 1 adjust block, got %d/%d" % (n_search, n_adjust))
        return 1

    html = html[:line_start] + html[end_line_start:]
    io.open(PATH, "w", encoding="utf-8", newline="").write(html)

    after_lines = html.count("\n") + 1
    print("OK: removed tab-search + tab-adjust blocks")
    print("    lines %d -> %d (-%d)" % (before_lines, after_lines, before_lines - after_lines))

    # No dangling references to the deleted ids may remain in markup.
    leftovers = [i for i in ("tab-search", "tab-adjust")
                 if re.search(r'id="%s"' % i, html)]
    if leftovers:
        print("WARN: still referenced: %s" % ", ".join(leftovers))
    else:
        print("    no remaining id=\"tab-search\" / id=\"tab-adjust\" markup")
    return 0


if __name__ == "__main__":
    sys.exit(main())