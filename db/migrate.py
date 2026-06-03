#!/usr/bin/env python3
"""
db/migrate.py — JSON flat files → PostgreSQL (idempotent)

Reads JSON (source of truth), upserts into Postgres.
Safe to re-run at any time including after a schema change.

Usage:
    cd ~/Projects/personal-ai-agents
    source venv/bin/activate
    python3 db/migrate.py
"""

import json
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

REPO = Path(__file__).parent.parent
RI   = REPO / "_NewDomains" / "research-intelligence" / "data"

DSN  = "postgresql://minimoi:simple123@localhost:5432/personal_agents"


def connect():
    return psycopg2.connect(DSN)


def upsert_tag_aliases(cur):
    path = RI / "tag_aliases.json"
    if not path.exists():
        print("  tag_aliases.json missing — skipping")
        return 0
    data = json.loads(path.read_text())
    if not data:
        print("  tag_aliases: 0 records (empty file)")
        return 0
    rows = [(alias, canonical) for alias, canonical in data.items()]
    execute_values(cur,
        "INSERT INTO tag_aliases (alias, canonical) VALUES %s "
        "ON CONFLICT (alias) DO UPDATE SET canonical = EXCLUDED.canonical",
        rows)
    print(f"  tag_aliases: {len(rows)} record(s)")
    return len(rows)


def upsert_topics(cur):
    threads_dir = RI / "threads"
    if not threads_dir.exists():
        print("  threads/ missing — skipping")
        return 0
    count = 0
    skipped = 0
    for thread_file in sorted(threads_dir.glob("*/thread.json")):
        try:
            t = json.loads(thread_file.read_text())
            cur.execute(
                """INSERT INTO topics (slug, name, status, queries, motivation, tags,
                                       expires, schema_version, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (slug) DO UPDATE SET
                     status=EXCLUDED.status, queries=EXCLUDED.queries,
                     motivation=EXCLUDED.motivation, tags=EXCLUDED.tags,
                     expires=EXCLUDED.expires, updated_at=now()""",
                (
                    t.get("slug", thread_file.parent.name),
                    t.get("name") or t.get("slug", thread_file.parent.name),
                    t.get("status", "dormant"),
                    json.dumps(t.get("queries", [])),
                    t.get("motivation"),
                    t.get("tags", []),
                    t.get("expires") or None,
                    t.get("schema_version"),
                )
            )
            count += 1
        except Exception as e:
            print(f"  ⚠ skipped {thread_file.parent.name}: {e}")
            skipped += 1
    print(f"  topics: {count} record(s)" + (f" ({skipped} skipped)" if skipped else ""))
    return count


def upsert_sources(cur):
    path = RI / "sources" / "sources.json"
    if not path.exists():
        print("  sources.json missing — skipping")
        return 0
    data = json.loads(path.read_text())
    if not data:
        print("  sources: 0 records (empty file)")
        return 0
    count = 0
    for s in data:
        cur.execute(
            """INSERT INTO sources (id, type, title, url, origin, date_recency, tags, note)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET
                 title=EXCLUDED.title, tags=EXCLUDED.tags, note=EXCLUDED.note""",
            (s.get("id"), s.get("type"), s.get("title"), s.get("url"),
             s.get("origin"), s.get("date") or s.get("date_recency"),
             s.get("tags", []), s.get("note"))
        )
        count += 1
    print(f"  sources: {count} record(s)")
    return count


def upsert_groups(cur):
    path = RI / "groups" / "groups.json"
    if not path.exists():
        print("  groups.json missing — skipping")
        return 0
    data = json.loads(path.read_text())
    if not data:
        print("  groups: 0 records (empty file)")
        return 0
    count = 0
    for g in data:
        cur.execute(
            """INSERT INTO groups (id, name, member_tags, member_topic_slugs)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET
                 name=EXCLUDED.name, member_tags=EXCLUDED.member_tags,
                 member_topic_slugs=EXCLUDED.member_topic_slugs""",
            (g.get("id"), g.get("name"),
             g.get("member_tags", []), g.get("member_topic_slugs", []))
        )
        count += 1
    print(f"  groups: {count} record(s)")
    return count


def upsert_leanings(cur):
    path = RI / "leanings" / "leanings.json"
    if not path.exists():
        print("  leanings.json missing — skipping")
        return 0
    raw = json.loads(path.read_text())
    # Format: {"schema_version": "1.0", "leanings": [...]} or plain list
    data = raw.get("leanings", raw) if isinstance(raw, dict) else raw
    if not data:
        print("  leanings: 0 records (empty file)")
        return 0
    lean_count = 0
    ev_count = 0
    for lean in data:
        cur.execute(
            """INSERT INTO leanings (id, title, state, topics, notes, updated_at)
               VALUES (%s,%s,%s,%s,%s,now())
               ON CONFLICT (id) DO UPDATE SET
                 title=EXCLUDED.title, state=EXCLUDED.state,
                 topics=EXCLUDED.topics, notes=EXCLUDED.notes, updated_at=now()""",
            (lean.get("id"), lean.get("title"), lean.get("state", "question"),
             lean.get("topics", []), lean.get("notes") or lean.get("note"))
        )
        lean_count += 1
        for ev in lean.get("evidence", []):
            cur.execute(
                """INSERT INTO evidence (id, leaning_id, title, url, source, stance, added, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                     stance=EXCLUDED.stance, note=EXCLUDED.note""",
                (ev.get("id"), lean.get("id"), ev.get("title"), ev.get("url"),
                 ev.get("source"), ev.get("stance"), ev.get("added"), ev.get("note"))
            )
            ev_count += 1
    print(f"  leanings: {lean_count} record(s), evidence: {ev_count} record(s)")
    return lean_count


def main():
    print("── JSON → Postgres migration ────────────────────────────────")
    try:
        conn = connect()
    except Exception as e:
        print(f"❌ Cannot connect to Postgres: {e}")
        sys.exit(1)

    cur = conn.cursor()
    try:
        # Order matters: tag_aliases and topics before sources/groups/leanings
        upsert_tag_aliases(cur)
        upsert_topics(cur)
        upsert_sources(cur)
        upsert_groups(cur)
        upsert_leanings(cur)
        conn.commit()
        print("✅ Migration complete")
    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed (rolled back): {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
