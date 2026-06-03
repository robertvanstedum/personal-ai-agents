#!/usr/bin/env python3
"""
db/reconcile.py — Compare JSON record counts to Postgres row counts.

Run after migration (to confirm baseline) and after any bridge change
(to detect drift). Exits 1 if any table is out of sync.

Usage:
    python3 db/reconcile.py
"""

import json
import sys
from pathlib import Path

import psycopg2

REPO = Path(__file__).parent.parent
RI   = REPO / "_NewDomains" / "research-intelligence" / "data"
DSN  = "postgresql://minimoi:simple123@localhost:5432/personal_agents"


def json_count(path: Path, key: str = None) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    if isinstance(data, dict) and key:
        data = data.get(key, [])
    return len(data) if isinstance(data, list) else (1 if data else 0)


def pg_count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def main():
    print("── Reconcile: JSON vs Postgres ──────────────────────────────")
    try:
        conn = psycopg2.connect(DSN)
    except Exception as e:
        print(f"❌ Cannot connect to Postgres: {e}")
        sys.exit(1)

    cur = conn.cursor()
    drift = False

    checks = [
        ("tag_aliases", json_count(RI / "tag_aliases.json"), "tag_aliases"),
        ("topics",      len(list((RI / "threads").glob("*/thread.json")))
                        if (RI / "threads").exists() else 0, "topics"),
        ("sources",     json_count(RI / "sources" / "sources.json"), "sources"),
        ("groups",      json_count(RI / "groups" / "groups.json"), "groups"),
        ("leanings",    json_count(RI / "leanings" / "leanings.json", key="leanings"), "leanings"),
        ("evidence",    sum(
                            len(l.get("evidence", []))
                            for l in json.loads((RI / "leanings" / "leanings.json").read_text()).get("leanings", [])
                        ) if (RI / "leanings" / "leanings.json").exists() else 0, "evidence"),
    ]

    for label, json_n, table in checks:
        pg_n = pg_count(cur, table)
        status = "✅" if json_n == pg_n else "❌ DRIFT"
        if json_n != pg_n:
            drift = True
        print(f"  {status}  {label:<15} JSON={json_n}  PG={pg_n}")

    conn.close()
    print("─" * 50)
    if drift:
        print("❌ Drift detected — re-run db/migrate.py")
        sys.exit(1)
    else:
        print("✅ All counts match — no drift")


if __name__ == "__main__":
    main()
