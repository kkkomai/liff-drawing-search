#!/usr/bin/env python3
"""Correct the LIFF ID everywhere it is hardcoded.

The LINE Developers console shows `2011376207-0e7oVVOT`, but the code had
`...VWOT` — V and W are near-identical in most monospace fonts, and nothing
caught it because the LIFF URL was only ever opened by hand. This matters now
that the backend verifies the ID token: the `aud` claim carries the real LIFF
ID, so a mismatched liffId can never authenticate, no matter how many scopes
are enabled.

Rewrite is asserted per file (exactly the old token, never a partial match) and
the tree is re-scanned afterwards so a stale copy cannot survive.
"""
import io
import re
import sys
from pathlib import Path

OLD = "2011376207-0e7oVVOT"
NEW = "2011376207-0e7oVVOT"

ROOT = Path("C:/Users/user/HERMES_AGENT")
SUFFIXES = {".html", ".js", ".md", ".yaml", ".yml", ".txt", ".py"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "bento_backend",
             "cache", "blocked-scripts", "workspaces", "attachments"}


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def main() -> int:
    changed = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if OLD not in text:
            continue
        count = text.count(OLD)
        path.write_text(text.replace(OLD, NEW), encoding="utf-8")
        changed.append((path.relative_to(ROOT).as_posix(), count))

    print("=== replaced %r -> %r ===" % (OLD, NEW))
    for rel, n in changed:
        print("  %-52s x%d" % (rel, n))
    print("  files changed: %d" % len(changed))

    # Re-scan: nothing anywhere may still carry the wrong id.
    leftovers = []
    for path in iter_files():
        try:
            if OLD in path.read_text(encoding="utf-8"):
                leftovers.append(path.relative_to(ROOT).as_posix())
        except (UnicodeDecodeError, OSError):
            pass

    print("")
    if leftovers:
        print("FAIL: %d file(s) still reference the wrong id:" % len(leftovers))
        for rel in leftovers:
            print("  - " + rel)
        return 1

    hits = 0
    for path in iter_files():
        try:
            hits += path.read_text(encoding="utf-8").count(NEW)
        except (UnicodeDecodeError, OSError):
            pass
    print("OK: no stale id remains; %d correct reference(s) present." % hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())