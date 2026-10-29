"""Backup readers and probes must use the exact requested SQLite file."""
import json
import sqlite3
import subprocess
import sys

import pytest

from core.memory_backup import production


def database(path, value):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE sentinel(value TEXT)')
    con.execute('INSERT INTO sentinel VALUES(?)', (value,))
    con.execute('CREATE VIRTUAL TABLE fts USING fts5(text)')
    con.execute('INSERT INTO fts VALUES(?)', ('synthetic minimoi words',))
    con.commit()
    con.close()


@pytest.mark.parametrize('reader', [production._ro, production._ro_live])
@pytest.mark.parametrize('name', ['plain.sqlite3', 'Café space.sqlite3', 'index?part.sqlite3',
                                  'index#part.sqlite3', 'percent%2f.sqlite3'])
def test_readers_preserve_path_and_readonly_mode(tmp_path, reader, name):
    path = tmp_path / name
    database(path, name)
    database(tmp_path / 'index', 'must not read this neighboring file')
    before = path.read_bytes()
    con = reader(path)
    try:
        assert con.execute('SELECT value FROM sentinel').fetchone()[0] == name
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            con.execute('DELETE FROM sentinel')
    finally:
        con.close()
    assert path.read_bytes() == before


@pytest.mark.parametrize('name', ['index?part.sqlite3', 'index#part.sqlite3', 'percent%2f.sqlite3'])
def test_online_backup_copies_the_requested_index(tmp_path, name):
    source = tmp_path / name
    destination = tmp_path / 'backup.sqlite3'
    database(source, 'requested source')
    database(tmp_path / 'index', 'wrong neighbor')
    production.copy_index(source, destination)
    con = sqlite3.connect(destination)
    try:
        assert con.execute('SELECT value FROM sentinel').fetchone()[0] == 'requested source'
    finally:
        con.close()


@pytest.mark.parametrize('name', ['index?part.sqlite3', 'index#part.sqlite3', 'percent%2f.sqlite3'])
def test_actual_fts_probe_code_handles_literal_filename(tmp_path, name):
    path = tmp_path / name
    database(path, name)
    result = subprocess.run([sys.executable, '-I', '-S', '-c', production.FTS_PROBE_CODE,
                             str(path), 'minimoi'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok'] is True
    assert production.fts_probe(path)['where'] == 'host'


@pytest.mark.parametrize('reader', [production._ro, production._ro_live])
def test_missing_source_is_not_created(tmp_path, reader):
    path = tmp_path / 'missing?index.sqlite3'
    with pytest.raises(sqlite3.OperationalError):
        reader(path)
    assert list(tmp_path.iterdir()) == []
