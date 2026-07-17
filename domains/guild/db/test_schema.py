#!/usr/bin/env python3
"""
domains/guild/db/test_schema.py — DB schema smoke test.

Verifies structure, not data. Passes on empty tables.
Run after any schema migration or fresh container bootstrap.

Usage:
    venv/bin/python3 domains/guild/db/test_schema.py

Exit 0 = all checks passed. Exit 1 = at least one failure.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import psycopg2
import psycopg2.extras

DSN = os.environ.get("DATABASE_URL", "postgresql://minimoi:simple123@localhost:5432/personal_agents")

PASS = "✅"
FAIL = "❌"
results = []


def check(label: str, ok: bool, detail: str = ""):
    mark = PASS if ok else FAIL
    line = f"  {mark}  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    results.append(ok)
    return ok


def run():
    print("\n══ DB Schema Smoke Test ════════════════════════════════════\n")

    # ── Connection ────────────────────────────────────────────────────────────
    try:
        conn = psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
        cur = conn.cursor()
        check("Postgres connection", True, "personal_agents")
    except Exception as e:
        check("Postgres connection", False, str(e))
        print(f"\n  Cannot continue — DB unreachable\n")
        sys.exit(1)

    # ── Schemas exist ─────────────────────────────────────────────────────────
    print("Schemas:")
    cur.execute("SELECT schema_name FROM information_schema.schemata")
    schemas = {r["schema_name"] for r in cur.fetchall()}
    for s in ("research", "guild", "jobs"):
        check(f"  schema {s}", s in schemas)

    # ── Tables in correct schemas ─────────────────────────────────────────────
    print("\nresearch.* tables:")
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'research' ORDER BY table_name"
    )
    research_tables = {r["table_name"] for r in cur.fetchall()}
    for t in ("evidence", "groups", "leanings", "sources", "tag_aliases", "topics"):
        check(f"  research.{t}", t in research_tables)

    print("\nguild.* tables:")
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'guild' ORDER BY table_name"
    )
    guild_tables = {r["table_name"] for r in cur.fetchall()}
    for t in ("agent_feedback", "challenger_exchanges", "cos_agenda",
              "design_log", "design_log_transitions"):
        check(f"  guild.{t}", t in guild_tables)

    print("\nguild.design_log columns (build discipline):")
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='guild' AND table_name='design_log'"
    )
    design_log_cols = {r["column_name"] for r in cur.fetchall()}
    for col in ("status", "spec_file", "spec_title",
                "last_transition_at", "blocked_reason", "github_issue"):
        check(f"  guild.design_log.{col}", col in design_log_cols)

    print("\npipeline.* tables:")
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'pipeline' ORDER BY table_name"
    )
    pipeline_tables = {r["table_name"] for r in cur.fetchall()}
    check("  pipeline.items", "items" in pipeline_tables)

    print("\npipeline.items columns:")
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='pipeline' AND table_name='items'"
    )
    pipeline_cols = {r["column_name"] for r in cur.fetchall()}
    for col in ("close_reason", "priority", "closed_at", "context"):
        check(f"  pipeline.items.{col}", col in pipeline_cols)

    # ── public schema is clean ────────────────────────────────────────────────
    print("\npublic schema:")
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    public_tables = [r["table_name"] for r in cur.fetchall()]
    check("  public schema is empty", len(public_tables) == 0,
          f"found: {public_tables}" if public_tables else "")

    # ── CRUD round-trip on research schema ────────────────────────────────────
    print("\nCRUD round-trip (research.tag_aliases — no side effects):")
    try:
        cur.execute(
            "INSERT INTO research.tag_aliases (alias, canonical) "
            "VALUES ('_test_alias_', '_test_canonical_') "
            "ON CONFLICT (alias) DO UPDATE SET canonical = EXCLUDED.canonical"
        )
        cur.execute(
            "SELECT canonical FROM research.tag_aliases WHERE alias = '_test_alias_'"
        )
        row = cur.fetchone()
        check("  INSERT", row is not None)
        check("  SELECT", row and row["canonical"] == "_test_canonical_")
        cur.execute("DELETE FROM research.tag_aliases WHERE alias = '_test_alias_'")
        check("  DELETE", True)
        conn.commit()
    except Exception as e:
        conn.rollback()
        check("  CRUD round-trip", False, str(e))

    # ── CRUD round-trip on guild schema ───────────────────────────────────────
    print("\nCRUD round-trip (guild.cos_agenda — no side effects):")
    try:
        cur.execute(
            "INSERT INTO guild.cos_agenda (domain, description, confidence, loop_name, status) "
            "VALUES ('_test_', '_test_entry_', 0.99, '_test_loop_', 'pending') "
            "RETURNING id"
        )
        row = cur.fetchone()
        test_id = row["id"] if row else None
        check("  INSERT", test_id is not None)
        cur.execute("SELECT id FROM guild.cos_agenda WHERE id = %s", (test_id,))
        check("  SELECT", cur.fetchone() is not None)
        cur.execute("DELETE FROM guild.cos_agenda WHERE id = %s", (test_id,))
        check("  DELETE", True)
        conn.commit()
    except Exception as e:
        conn.rollback()
        check("  CRUD round-trip", False, str(e))

    # ── User permissions ──────────────────────────────────────────────────────
    print("\nUser permissions:")
    try:
        ro_conn = psycopg2.connect(
            "postgresql://robert_ro:simple123@localhost:5432/personal_agents",
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        ro_cur = ro_conn.cursor()
        ro_cur.execute("SELECT count(*) FROM research.topics")
        check("  robert_ro can SELECT research.topics", True)
        ro_cur.execute("SELECT count(*) FROM guild.cos_agenda")
        check("  robert_ro can SELECT guild.cos_agenda", True)
        ro_cur.execute("SELECT count(*) FROM guild.challenger_exchanges")
        check("  robert_ro can SELECT guild.challenger_exchanges", True)
        ro_cur.execute("SELECT count(*) FROM guild.design_log_transitions")
        check("  robert_ro can SELECT guild.design_log_transitions", True)
        ro_conn.close()
    except Exception as e:
        check("  robert_ro read access", False, str(e))

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = sum(results)
    total = len(results)
    all_ok = passed == total
    print(f"\n{'═' * 60}")
    print(f"  {PASS if all_ok else FAIL}  {passed}/{total} checks passed")
    if not all_ok:
        print(f"  Schema migration may be incomplete — see failures above")
    print()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    run()
