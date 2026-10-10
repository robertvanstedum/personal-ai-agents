"""Guild 1.1 slice 3: the migrations 002_board.sql and 003_media.sql are
additive (new columns, tables and a schema only; nothing dropped, deleted or
rewritten), idempotent, labelled for hand application on staging, and their
down scripts are for disposable databases only. The media files' host folder
is mounted on staging only."""
from __future__ import annotations

import re
import sqlite3

import pytest
import yaml

from floor_db_helpers import REPO, SqliteFloor, sqlite_ddl, sqlite_ddl_board, sqlite_ddl_media

SQL = REPO / "minimoi_portal" / "guild_ui" / "sql"


def _code(path):
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("--"))


@pytest.mark.parametrize("name", ["002_board.sql", "003_media.sql"])
def test_the_up_scripts_are_additive_and_idempotent(name):
    code = _code(SQL / name).upper()
    for word in ("DROP", "DELETE", "TRUNCATE", "RENAME", "ALTER COLUMN", "UPDATE "):
        assert word not in code, (name, word)
    assert "IF NOT EXISTS" in code
    head = (SQL / name).read_text()
    assert "by hand" in head and "staging only" in head and "backup" in head


def test_002_adds_exactly_the_board_columns_and_meta_table():
    code = _code(SQL / "002_board.sql")
    added = re.findall(r"ALTER TABLE guild\.floor_postits ADD COLUMN IF NOT EXISTS (\w+)", code)
    assert added == ["done_at", "done_by", "label", "item_ref", "kind", "asset_id", "sort_key", "version"]
    assert "purged_at" not in code                                            # no tombstone on the Board
    assert re.findall(r"CREATE TABLE IF NOT EXISTS guild\.(\w+)", code) == ["floor_board_meta"]
    assert "order_rev" in code and "trash_rev" in code and "floor_postits_kind_shape" in code


def test_003_creates_the_media_index_only():
    code = _code(SQL / "003_media.sql")
    assert re.findall(r'CREATE TABLE IF NOT EXISTS media\.("?\w+"?)', code) == ["assets", '"references"', "library_meta"]
    for col in ("purged_at", "version", "storage_key", "emoji", "access_scope", "trashed_at"):
        assert col in code, col
    assert "released_at" in code and "library_trash_rev" in code
    assert "bytea" not in code.lower() and "blob" not in code.lower()             # the files are never in the database


@pytest.mark.parametrize("name", ["002_board_down.sql", "003_media_down.sql"])
def test_the_down_scripts_say_disposable_databases_only(name):
    text = (SQL / name).read_text()
    assert text.startswith("-- DISPOSABLE DATABASES ONLY.") and "Never run this on staging or production" in text


def test_the_migrations_run_twice_on_sqlite_with_existing_rows(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ? AS guild", (str(tmp_path / "g.sqlite"),))
    conn.execute("ATTACH DATABASE ? AS media", (str(tmp_path / "m.sqlite"),))
    conn.executescript(sqlite_ddl())
    conn.execute("INSERT INTO guild.floor_postits (floor, text, author, author_kind, author_label, created_at) "
                 "VALUES ('guild', 'written before slice 3', 'robert', 'owner', 'Robert', '2026-09-30T10:00:00+00:00')")
    conn.executescript(sqlite_ddl_board())
    conn.executescript(sqlite_ddl_media())
    conn.executescript(sqlite_ddl_media())
    row = conn.execute("SELECT kind, version, sort_key, done_at, label FROM guild.floor_postits").fetchone()
    assert row == ("note", 1, None, None, None)                                  # an ordinary active note
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE guild.floor_postits SET label = 'urgent'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO media.assets (id, owner, kind, created_at) VALUES ('a', 'robert', 'photo', 'now')")


def test_the_test_floor_is_built_from_the_real_migrations(tmp_path):
    db = SqliteFloor(tmp_path / "floor")
    assert db.count("floor_board_meta") == 0 and db.rows("media.assets") == []


def test_the_media_folder_is_mounted_read_write_on_staging_only():
    staging = yaml.safe_load((REPO / "docker-compose.staging.yml").read_text())["services"]["portal"]
    assert "${MINIMOI_ROOT}/data/media:/app/runtime/media" in staging["volumes"]
    assert "MINIMOI_MEDIA_DIR=/app/runtime/media" in staging["environment"]
    prod = (REPO / "docker-compose.prod.yml").read_text()
    assert "data/media" not in prod and "MINIMOI_MEDIA_DIR" not in prod
    assert 'mkdir -p "$STAGING_ROOT/data/media"' in (REPO / "scripts" / "staging" / "build.sh").read_text()
    assert re.search(r"(?im)^pillow", (REPO / "docker" / "requirements.portal.txt").read_text())
