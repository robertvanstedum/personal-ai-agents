"""Fixtures for the Board and Media tests (Guild 1.1 slice 3): one floor
store on SQLite (always), and on a disposable Postgres when
GUILD_FLOOR_TEST_DATABASE_URL is set (never staging's database).

The Postgres variant migrates the disposable database with 001, 002 and 003
(they are idempotent), works on a random floor and owner, and removes its
rows at the end.
"""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import pytest

from minimoi_portal.guild_ui.media import MediaStore
from minimoi_portal.guild_ui.stores import Author, FloorStores

from floor_db_helpers import REPO, SqliteFloor

PG_URL = os.environ.get("GUILD_FLOOR_TEST_DATABASE_URL")
SQL = REPO / "minimoi_portal" / "guild_ui" / "sql"
ROBERT = Author("robert", "owner", "Robert")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


def migrate_pg(url: str = PG_URL) -> None:
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=3)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for name in ("001_floor_b1.sql", "002_board.sql", "003_media.sql"):
                cur.execute((SQL / name).read_text())
    finally:
        conn.close()


def _pg_cleanup(floor: str, owner: str) -> None:
    import psycopg2
    conn = psycopg2.connect(PG_URL, connect_timeout=3)
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM media."references" WHERE asset_id IN (SELECT id FROM media.assets WHERE owner = %s)',
                        (owner,))
            cur.execute("DELETE FROM media.assets WHERE owner = %s", (owner,))
            cur.execute("DELETE FROM media.library_meta WHERE owner = %s", (owner,))
            for table in ("floor_messages", "floor_postits", "floor_continue", "floor_board_meta"):
                cur.execute(f"DELETE FROM guild.{table} WHERE floor = %s OR floor LIKE %s", (floor, f"{floor}/%"))
            cur.execute("DELETE FROM guild.floor_requests WHERE floor IN (%s, %s)", (floor, f"media/{owner}"))
        conn.commit()
    finally:
        conn.close()


class Board:
    """A floor store, its media store and the owner, on one backend."""

    def __init__(self, backend: str, tmp_path: Path):
        self.backend = backend
        self.media_root = tmp_path / "media"
        self.media_root.mkdir(parents=True)
        if backend == "sqlite":
            self.db = SqliteFloor(tmp_path / "floor")
            self.floor = self.db.store()
            self.owner = "robert"
        else:
            migrate_pg()
            self.floor = FloorStores(lambda: PG_URL, floor=f"test-{uuid.uuid4().hex[:10]}")
            self.owner = f"t{uuid.uuid4().hex[:10]}"
        self.author = Author(self.owner, "owner", "Robert")
        self.media = MediaStore(self.floor, self.media_root)

    def rows(self, sql: str, params=()):
        return self.floor._run(lambda q: q(sql, params), write=False)

    def close(self):
        if self.backend == "postgres":
            _pg_cleanup(self.floor.floor, self.owner)


@pytest.fixture(params=BACKENDS)
def board(request, tmp_path):
    b = Board(request.param, tmp_path)
    yield b
    b.close()


def key() -> str:
    return uuid.uuid4().hex


def image_bytes(fmt="JPEG", size=(64, 48), color=(200, 120, 40), exif=True, frames=1) -> bytes:
    """A small test image. JPEGs carry EXIF with a GPS block, so the tests can
    prove it is gone after sanitising."""
    from PIL import Image
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    if fmt == "JPEG" and exif:
        ex = Image.Exif()
        ex[0x010F] = "TestCam"                         # Make
        ex[0x0132] = "2025:11:23 16:22:00"             # DateTime
        gps = ex.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (41.0, 52.0, 30.0)
        img.save(out, "JPEG", exif=ex)
    elif fmt == "GIF" and frames > 1:
        others = [Image.new("RGB", size, (i * 40 % 255, 10, 10)) for i in range(1, frames)]
        img.save(out, "GIF", save_all=True, append_images=others, duration=50, loop=0)
    else:
        img.save(out, fmt)
    return out.getvalue()
