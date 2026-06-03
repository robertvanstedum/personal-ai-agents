"""
db/postgres.py — PostgreSQL CRUD layer for the Guild spine.

Pure data layer — no Flask wiring, no HTTP concerns.
Uses psycopg2 directly. Connection pool via simple context manager.

Usage:
    from db.postgres import get_topic, list_topics, upsert_topic
"""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

DSN = "postgresql://minimoi:simple123@localhost:5432/personal_agents"


@contextmanager
def _conn():
    """Yield a connection, commit on exit, rollback on error."""
    conn = psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Topics ────────────────────────────────────────────────────────────────────

def get_topic(slug: str) -> Optional[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM topics WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_topics(status: str = None) -> list:
    with _conn() as conn:
        cur = conn.cursor()
        if status:
            cur.execute("SELECT * FROM topics WHERE status = %s ORDER BY slug", (status,))
        else:
            cur.execute("SELECT * FROM topics ORDER BY slug")
        return [dict(r) for r in cur.fetchall()]


def upsert_topic(data: dict) -> None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO topics (slug, name, status, queries, motivation, tags,
                                   expires, schema_version, updated_at)
               VALUES (%(slug)s, %(name)s, %(status)s, %(queries)s, %(motivation)s,
                       %(tags)s, %(expires)s, %(schema_version)s, now())
               ON CONFLICT (slug) DO UPDATE SET
                 name=EXCLUDED.name, status=EXCLUDED.status,
                 queries=EXCLUDED.queries, motivation=EXCLUDED.motivation,
                 tags=EXCLUDED.tags, expires=EXCLUDED.expires, updated_at=now()""",
            {
                "slug":           data.get("slug"),
                "name":           data.get("name"),
                "status":         data.get("status", "dormant"),
                "queries":        json.dumps(data.get("queries", [])),
                "motivation":     data.get("motivation"),
                "tags":           data.get("tags", []),
                "expires":        data.get("expires") or None,
                "schema_version": data.get("schema_version"),
            }
        )


# ── Sources ───────────────────────────────────────────────────────────────────

def get_source(source_id: str) -> Optional[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_sources(tags: list = None) -> list:
    with _conn() as conn:
        cur = conn.cursor()
        if tags:
            cur.execute(
                "SELECT * FROM sources WHERE tags && %s ORDER BY created_at DESC",
                (tags,)
            )
        else:
            cur.execute("SELECT * FROM sources ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]


def upsert_source(data: dict) -> None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO sources (id, type, title, url, origin, date_recency, tags, note)
               VALUES (%(id)s, %(type)s, %(title)s, %(url)s, %(origin)s,
                       %(date_recency)s, %(tags)s, %(note)s)
               ON CONFLICT (id) DO UPDATE SET
                 title=EXCLUDED.title, tags=EXCLUDED.tags, note=EXCLUDED.note""",
            {
                "id":           data.get("id"),
                "type":         data.get("type", "article"),
                "title":        data.get("title"),
                "url":          data.get("url"),
                "origin":       data.get("origin", "added-by-robert"),
                "date_recency": data.get("date") or data.get("date_recency"),
                "tags":         data.get("tags", []),
                "note":         data.get("note"),
            }
        )


# ── Groups ────────────────────────────────────────────────────────────────────

def get_group(group_id: str) -> Optional[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM groups WHERE id = %s", (group_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_groups() -> list:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM groups ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]


def upsert_group(data: dict) -> None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO groups (id, name, member_tags, member_topic_slugs)
               VALUES (%(id)s, %(name)s, %(member_tags)s, %(member_topic_slugs)s)
               ON CONFLICT (id) DO UPDATE SET
                 name=EXCLUDED.name, member_tags=EXCLUDED.member_tags,
                 member_topic_slugs=EXCLUDED.member_topic_slugs""",
            {
                "id":                  data.get("id"),
                "name":                data.get("name"),
                "member_tags":         data.get("member_tags", []),
                "member_topic_slugs":  data.get("member_topic_slugs", []),
            }
        )


# ── Leanings ──────────────────────────────────────────────────────────────────

def get_leaning(leaning_id: str) -> Optional[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM leanings WHERE id = %s", (leaning_id,))
        row = cur.fetchone()
        if not row:
            return None
        lean = dict(row)
        cur.execute("SELECT * FROM evidence WHERE leaning_id = %s ORDER BY added", (leaning_id,))
        lean["evidence"] = [dict(r) for r in cur.fetchall()]
        return lean


def list_leanings() -> list:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM leanings ORDER BY updated_at DESC")
        return [dict(r) for r in cur.fetchall()]


def upsert_leaning(data: dict) -> None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO leanings (id, title, state, topics, notes, updated_at)
               VALUES (%(id)s, %(title)s, %(state)s, %(topics)s, %(notes)s, now())
               ON CONFLICT (id) DO UPDATE SET
                 title=EXCLUDED.title, state=EXCLUDED.state,
                 topics=EXCLUDED.topics, notes=EXCLUDED.notes, updated_at=now()""",
            {
                "id":     data.get("id"),
                "title":  data.get("title"),
                "state":  data.get("state", "question"),
                "topics": data.get("topics", []),
                "notes":  data.get("notes") or data.get("note"),
            }
        )


def add_evidence(leaning_id: str, ev: dict) -> None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO evidence (id, leaning_id, title, url, source, stance, added, note)
               VALUES (%(id)s, %(leaning_id)s, %(title)s, %(url)s, %(source)s,
                       %(stance)s, %(added)s, %(note)s)
               ON CONFLICT (id) DO UPDATE SET
                 stance=EXCLUDED.stance, note=EXCLUDED.note""",
            {
                "id":         ev.get("id"),
                "leaning_id": leaning_id,
                "title":      ev.get("title"),
                "url":        ev.get("url"),
                "source":     ev.get("source"),
                "stance":     ev.get("stance", "neutral"),
                "added":      ev.get("added"),
                "note":       ev.get("note"),
            }
        )


# ── Tag aliases ───────────────────────────────────────────────────────────────

def get_tag_aliases() -> dict:
    """Return {alias: canonical} mapping."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT alias, canonical FROM tag_aliases")
        return {r["alias"]: r["canonical"] for r in cur.fetchall()}


def resolve_tag(tag: str) -> str:
    """Resolve a tag to its canonical form. Returns tag unchanged if no alias."""
    aliases = get_tag_aliases()
    return aliases.get(tag.lower(), tag)
