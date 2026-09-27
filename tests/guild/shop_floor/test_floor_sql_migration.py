"""The (c) migration: it lives inside the portal package (so the release class
stays "domain", portal only, brief (c)), creates exactly the floor tables
with the columns the store uses, is safe to run twice, keeps an off-record
row out at the table level, and its down script drops only these tables."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from floor_db_helpers import DOWN, MIGRATION, TABLES, SqliteFloor, sqlite_ddl

REPO = Path(__file__).resolve().parents[3]


def test_the_migration_lives_in_the_portal_package():
    assert MIGRATION.parent == REPO / "minimoi_portal" / "guild_ui" / "sql"
    assert not list((REPO / "domains" / "guild").rglob("*floor_b1*"))


def test_the_release_class_is_domain_portal_only():
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    try:
        import classify_release
    finally:
        sys.path.pop(0)
    paths = ["minimoi_portal/guild_ui/sql/001_floor_b1.sql", "minimoi_portal/guild_ui/stores.py",
             "minimoi_portal/guild_ui/api.py", "minimoi_portal/guild_ui/static/js/postits.js",
             "tests/guild/shop_floor/test_floor_sql_migration.py"]
    kind, services = classify_release.classify(paths)
    assert kind == "domain" and tuple(services) == ("portal",)


def test_it_creates_exactly_the_floor_tables_in_the_guild_schema():
    sql = MIGRATION.read_text()
    created = re.findall(r"CREATE TABLE IF NOT EXISTS guild\.(\w+)", sql)
    assert sorted(created) == sorted(TABLES)
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")).upper()
    assert "DROP" not in code and "DELETE" not in code and "TRUNCATE" not in code
    down = DOWN.read_text()
    assert sorted(re.findall(r"DROP TABLE IF EXISTS guild\.(\w+);", down)) == sorted(TABLES)


def test_it_runs_twice_and_matches_what_the_store_uses(tmp_path):
    db = SqliteFloor(tmp_path / "twice")
    conn = db.connect()
    conn.executescript(sqlite_ddl())          # a second run changes nothing
    columns = {t: [r[1] for r in conn.execute(f"PRAGMA guild.table_info({t})")] for t in TABLES}
    conn.close()
    stores = (REPO / "minimoi_portal" / "guild_ui" / "stores.py").read_text()
    for table, cols in columns.items():
        assert table in stores
    assert set(columns["floor_postits"]) >= {"floor", "author", "author_kind", "author_label", "binned_at",
                                             "binned_by", "binned_by_label", "restored_at"}
    assert set(columns["floor_messages"]) >= {"floor", "request_id", "record_mode", "area", "item_ref", "page"}
    assert set(columns["floor_continue"]) >= {"floor", "principal", "kind", "ref", "label", "updated_at"}
    assert "principal" in columns["floor_requests"]


def test_constraints_hold_at_the_table_level(tmp_path):
    db = SqliteFloor(tmp_path / "c")
    conn = db.connect()
    now = "2026-09-27T00:00:00+00:00"
    bad = [
        ("INSERT INTO guild.floor_postits (floor, text, author, author_kind, author_label, created_at) "
         "VALUES ('guild', '', 'robert', 'owner', 'Robert', ?)", (now,)),
        ("INSERT INTO guild.floor_postits (floor, text, author, author_kind, author_label, created_at) "
         "VALUES ('guild', 'x', 'robert', 'wizard', 'Robert', ?)", (now,)),
        ("INSERT INTO guild.floor_continue (floor, principal, kind, ref, label, updated_at) "
         "VALUES ('guild', 'robert', 'topic', '1', 'x', ?)", (now,)),
    ]
    for sql, params in bad:
        try:
            conn.execute(sql, params)
        except sqlite3.IntegrityError:
            continue
        raise AssertionError(sql)
    conn.execute("INSERT INTO guild.floor_continue (floor, principal, kind, ref, label, updated_at) "
                 "VALUES ('guild', 'robert', 'item', '1', 'x', ?)", (now,))
    try:
        conn.execute("INSERT INTO guild.floor_continue (floor, principal, kind, ref, label, updated_at) "
                     "VALUES ('guild', 'robert', 'item', '2', 'y', ?)", (now,))
        raise AssertionError("one Continue row per (floor, principal)")
    except sqlite3.IntegrityError:
        pass
    conn.close()
