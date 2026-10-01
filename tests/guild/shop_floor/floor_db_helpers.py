"""The floor store (notes, post-its, Continue) for tests, on SQLite.

No Postgres runs on the test machine or in CI, so the tests run the store's
own SQL against SQLite. The schema is not written twice: ``sqlite_ddl()``
derives it from the package's Postgres migration (sql/001_floor_b1.sql) by
five mechanical substitutions, and the ``guild`` schema is an attached SQLite
database, so ``guild.floor_postits`` means the same table in both. Every
statement the store runs is therefore executed against the migration's own
tables, columns, keys and CHECK constraints.

What SQLite cannot prove is Postgres's own locking; the store relies only on
single-statement conditional writes and unique keys, which both honour, and
``test_floor_stores_postgres.py`` runs the same checks on a real Postgres
when GUILD_FLOOR_TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from minimoi_portal.guild_ui.stores import FloorStores

REPO = Path(__file__).resolve().parents[3]
MIGRATION = REPO / "minimoi_portal" / "guild_ui" / "sql" / "001_floor_b1.sql"
DOWN = REPO / "minimoi_portal" / "guild_ui" / "sql" / "001_floor_b1_down.sql"
TABLES = ("floor_messages", "floor_postits", "floor_continue", "floor_requests")


def sqlite_ddl(sql: str | None = None) -> str:
    sql = MIGRATION.read_text() if sql is None else sql
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    sql = sql.replace("CREATE SCHEMA IF NOT EXISTS guild;", "")
    # SQLite has no ADD COLUMN IF NOT EXISTS; those lines only upgrade an
    # earlier draft, and the CREATE TABLE above already has the columns.
    sql = re.sub(r"(?m)^ALTER TABLE guild\.\w+ ADD COLUMN IF NOT EXISTS .*$", "", sql)
    sql = sql.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    sql = sql.replace("TIMESTAMPTZ", "TEXT")
    sql = re.sub(r"CREATE INDEX IF NOT EXISTS (\w+) ON guild\.(\w+)", r"CREATE INDEX IF NOT EXISTS guild.\1 ON \2", sql)
    return sql


BOARD = REPO / "minimoi_portal" / "guild_ui" / "sql" / "002_board.sql"
MEDIA = REPO / "minimoi_portal" / "guild_ui" / "sql" / "003_media.sql"


def sqlite_ddl_board(sql: str | None = None) -> str:
    """002_board.sql for SQLite, by mechanical substitutions. The cross-column
    kind/asset CHECK is a Postgres DO block, which SQLite cannot add to an
    existing table; the store enforces the same rule and
    test_board_media_postgres.py checks the constraint on a real Postgres."""
    sql = BOARD.read_text() if sql is None else sql
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    sql = re.sub(r"DO \$\$.*?END \$\$;", "", sql, flags=re.S)
    sql = sql.replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN").replace("TIMESTAMPTZ", "TEXT").replace("UUID", "TEXT")
    sql = re.sub(r"CREATE INDEX IF NOT EXISTS (\w+) ON guild\.(\w+)", r"CREATE INDEX IF NOT EXISTS guild.\1 ON \2", sql)
    return sql


def sqlite_ddl_media(sql: str | None = None) -> str:
    """003_media.sql for SQLite (the media schema is a second attached database)."""
    sql = MEDIA.read_text() if sql is None else sql
    sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    sql = sql.replace("CREATE SCHEMA IF NOT EXISTS media;", "")
    sql = sql.replace("sha256 ~ '^[0-9a-f]{64}$'", "length(sha256) = 64")
    sql = sql.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    sql = sql.replace("TIMESTAMPTZ", "TEXT").replace("UUID", "TEXT")
    sql = sql.replace("REFERENCES media.assets (id)", "REFERENCES assets (id)")
    sql = re.sub(r"CREATE (UNIQUE )?INDEX IF NOT EXISTS (\w+) ON media\.(\"?\w+\"?)",
                 r"CREATE \1INDEX IF NOT EXISTS media.\2 ON \3", sql)
    return sql


class SqliteFloor:
    """A floor database file plus the connect function the store is given."""

    def __init__(self, folder: Path, *, migrate: bool = True):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "guild_floor.sqlite"
        self.connects = 0
        self.media_path = folder / "media.sqlite"
        if migrate:
            conn = self.connect()
            conn.executescript(sqlite_ddl())
            conn.executescript(sqlite_ddl_board())       # Guild 1.1 slice 3
            conn.executescript(sqlite_ddl_media())
            conn.commit()
            conn.close()

    def connect(self, _url: str | None = None):
        self.connects += 1
        conn = sqlite3.connect(":memory:", timeout=15)
        conn.execute("ATTACH DATABASE ? AS guild", (str(self.path),))
        conn.execute("PRAGMA guild.journal_mode=WAL")
        conn.execute("ATTACH DATABASE ? AS media", (str(self.media_path),))
        conn.execute("PRAGMA media.journal_mode=WAL")
        return conn

    def store(self, floor: str = "guild") -> FloorStores:
        return FloorStores(lambda: "sqlite:test-floor", connect=self.connect, paramstyle="qmark", floor=floor)

    def rows(self, table: str) -> list[dict]:
        if table.startswith("media."):
            return self._rows(self.media_path, table.split(".", 1)[1])
        return self._rows(self.path, table)

    def _rows(self, path, table: str) -> list[dict]:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        finally:
            conn.close()

    def all_text(self) -> str:
        """Every value in every floor table, for 'this never reached a row' checks."""
        return "\n".join(repr(r) for t in TABLES for r in self.rows(t))

    def count(self, table: str) -> int:
        return len(self.rows(table))


class DownFloor(FloorStores):
    """A configured floor database that cannot be reached."""

    def __init__(self):
        self.attempts = 0

        def refuse(_url):
            self.attempts += 1
            raise OSError("connection refused (test)")
        super().__init__(lambda: "postgresql://floor.invalid/none", connect=refuse)


def attach(portal, store) -> None:
    portal.app.extensions["guild_ui_next"]["services"].floor = store


@pytest.fixture
def floor_db(tmp_path) -> SqliteFloor:
    return SqliteFloor(tmp_path / "floor")


@pytest.fixture
def floored(load_portal, floor_db):
    """The staging portal with /guild-next on and a working floor store."""
    portal = load_portal()
    attach(portal, floor_db.store())
    portal.extra["floor"] = floor_db
    return portal


def keyed(**body) -> dict:
    """A write body with a fresh idempotency key."""
    import uuid
    body.setdefault("idempotency_key", uuid.uuid4().hex)
    return body
