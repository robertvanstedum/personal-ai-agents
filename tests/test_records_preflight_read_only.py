"""Records' production preflight mounts its state read-only. A WAL database there can't make its shared-memory file."""
import os
import sqlite3
from contextlib import closing

import pytest

from services import records_runtime as runtime


def _wal_database(folder):
    path = folder / "records.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE meta (key TEXT)")
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for name in ("records.sqlite3-wal", "records.sqlite3-shm"):
        assert not (folder / name).exists()
    return path


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the folder's permissions")
def test_a_wal_database_opens_from_a_read_only_folder(tmp_path):
    path = _wal_database(tmp_path)
    tmp_path.chmod(0o500)
    try:
        with closing(runtime.open_read_only(path)) as db:
            assert db.execute("SELECT name FROM sqlite_master").fetchall() == [("meta",)]
    finally:
        tmp_path.chmod(0o700)


def test_a_writable_folder_still_opens_normally_and_stays_read_only(tmp_path):
    path = _wal_database(tmp_path)
    with closing(runtime.open_read_only(path)) as db:
        assert db.execute("SELECT count(*) FROM meta").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO meta VALUES ('x')")
