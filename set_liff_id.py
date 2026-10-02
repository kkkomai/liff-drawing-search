#!/usr/bin/env python3
"""Set the LIFF ID to the authoritative value the user pasted from the console.

Context: I misread `VWOT` as `VVOT` from a screenshot and "corrected" 14 files
to a value that never existed. The user then pasted the console value directly,
which is authoritative. This script rewrites whatever is present to that exact
string, then verifies no variant survives anywhere.

LIFF IDs are case-sensitive: VWOT, VVOT, vWOT and vVOT are four different apps.
"""
import io
import sys
from pathlib import Path

TARGET = "2011376207-0e7oVWOT"
# Any suffix variant that could be mistaken for the target.
VARIANTS = ["2011376207-0e7oVWOT", "2011376207-0e7oVWOT", "2011376207-0e7oVWOT",
            "2011376207-0e7oVWOT", "2011376207-0e7oVWOT"]

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
    print("=== setting LIFF ID to %s ===" % TARGET)
    changed = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        original = text
        # Normalise every known variant to the authoritative string.
        for bad in VARIANTS:
            text = text.replace(bad, TARGET)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(path.relative_to(ROOT).as_posix())

    for rel in changed:
        print("  fixed: %s" % rel)
    print("  files changed: %d" % len(changed))

    # Verify.
    hits = 0
    stale = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += text.count(TARGET)
        for bad in VARIANTS:
            if bad in text:
                stale.append((path.relative_to(ROOT).as_posix(), bad))

    print("")
    print("  correct references : %d" % hits)
    if stale:
        print("  FAIL: stale variants remain:")
        for rel, bad in stale:
            print("    - %s contains %s" % (rel, bad))
        return 1
    print("  OK: only %s is present." % TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())