#!/usr/bin/env python3
"""Sync the bento backend into this repo so Render can build it.

The ordering backend is developed at ``projects/bento-liff/backend`` but Render
only receives what is committed here. Copying it into ``bento_backend/`` is what
lets ``asgi_app.py`` import the routers on the deployed host.

``projects/bento-liff/backend`` stays the single source of truth — this script
mirrors it and prints what changed, so drift is visible in review rather than
discovered at runtime. Run it after editing the backend, then commit.

Deliberately excluded: caches, the venv, local databases, and anything holding
secrets (.env). A committed .env would leak credentials into git history.
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "projects" / "bento-liff" / "backend"
DST = HERE / "bento_backend"

EXCLUDE_DIRS = {
    "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache",
    "node_modules", ".git", "data",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".db-shm", ".db-wal", ".log"}
# Never copy these: .env carries secrets, .bat is Windows-only tooling.
EXCLUDE_NAMES = {".env", ".env.local", ".DS_Store", "run_tests.bat"}


def wanted(path: Path, src_root: Path) -> bool:
    rel = path.relative_to(src_root)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    return True


def main() -> int:
    if not SRC.is_dir():
        print("FAIL: source backend not found at %s" % SRC)
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    src_files = {p.relative_to(SRC) for p in SRC.rglob("*") if p.is_file() and wanted(p, SRC)}

    added, updated, removed = [], [], []
    dst_files = {p.relative_to(DST) for p in DST.rglob("*") if p.is_file()}

    for rel in sorted(src_files):
        s, d = SRC / rel, DST / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        if not d.exists():
            shutil.copy2(s, d)
            added.append(rel.as_posix())
        elif not filecmp.cmp(s, d, shallow=False):
            shutil.copy2(s, d)
            updated.append(rel.as_posix())

    for rel in sorted(dst_files - src_files):
        (DST / rel).unlink()
        removed.append(rel.as_posix())

    # Drop directories left empty by deletions.
    for d in sorted((p for p in DST.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()

    print("=== bento backend sync ===")
    print("  src: %s" % SRC)
    print("  dst: %s" % DST)
    print("")
    print("  added  : %d" % len(added))
    print("  updated: %d" % len(updated))
    print("  removed: %d" % len(removed))
    for label, rows in (("+", added), ("~", updated), ("-", removed)):
        for r in rows:
            print("    %s %s" % (label, r))

    # .env.example is a committed template of placeholder values, not a
    # credential; only a real .env (or .env.local) is a leak.
    leaked = sorted(
        p.name for p in DST.rglob(".env*")
        if p.is_file() and p.name not in {".env.example", ".env.sample"}
    )
    if leaked:
        print("")
        print("FAIL: secret-bearing files were copied: %s" % ", ".join(leaked))
        return 1

    total = sum(1 for _ in DST.rglob("*") if _.is_file())
    print("")
    print("OK: %d file(s) in bento_backend/, no .env present." % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())