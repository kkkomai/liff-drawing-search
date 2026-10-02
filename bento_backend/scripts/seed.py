"""Seed the employee master (spec 3.1).

Usage:
    python scripts/seed.py --count 20 --admin-code E001 --line-prefix Udemo
    python scripts/seed.py --csv employees.csv

LINE user IDs are placeholders unless supplied; admins normally fill in the
real ``U...`` IDs before enabling the LIFF app.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_conn, migrate  # noqa: E402
from app.timeutil import now_tokyo_iso  # noqa: E402

FAMILY = ["山田", "鈴木", "佐藤", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林",
          "加藤", "吉田", "山田", "松本", "井上", "木村", "林", "清水", "山口", "森"]
GIVEN = ["太郎", "花子", "健一", "美咲", "翔太", "彩香", "拓也", "優子", "大輔", "麻衣"]


def seed_csv(conn, path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            conn.execute(
                "INSERT INTO employees (employee_code, name, line_user_id, role, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(employee_code) DO UPDATE SET"
                "  name = excluded.name, line_user_id = excluded.line_user_id, role = excluded.role",
                (
                    row["employee_code"],
                    row["name"],
                    (row.get("line_user_id") or None),
                    row.get("role") or "employee",
                    now_tokyo_iso(),
                ),
            )
            count += 1
    return count


def seed_generated(conn, count: int, admin_code: str, line_prefix: str | None) -> int:
    for i in range(count):
        code = f"E{i + 1:03d}"
        name = f"{FAMILY[i % len(FAMILY)]} {GIVEN[i % len(GIVEN)]}"
        line_id = f"{line_prefix}{i + 1:04d}" if line_prefix else None
        conn.execute(
            "INSERT INTO employees (employee_code, name, line_user_id, role, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (code, name, line_id, "admin" if code == admin_code else "employee", now_tokyo_iso()),
        )
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed employee master")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--admin-code", default="E001")
    ap.add_argument("--line-prefix", default=None)
    ap.add_argument("--csv", type=Path, default=None)
    args = ap.parse_args()

    conn = get_conn()
    migrate(conn)
    with conn:
        if args.csv:
            n = seed_csv(conn, args.csv)
        else:
            n = seed_generated(conn, args.count, args.admin_code, args.line_prefix)
    total = conn.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
    print(f"seeded {n} employee(s); master now holds {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())