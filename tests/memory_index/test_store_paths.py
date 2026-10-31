"""SQLite URI syntax must not reinterpret the caller's filesystem path."""
from pathlib import Path
import sqlite3

import pytest

from core.memory_index import store


@pytest.fixture(autouse=True)
def no_vector_extension(monkeypatch):
    monkeypatch.setattr(store, 'sqlite_vec', None)


@pytest.mark.parametrize('name', ['plain.sqlite3', 'space name.sqlite3', 'Café.sqlite3',
                                  'percent%2f.sqlite3', 'query?part.sqlite3', 'hash#part.sqlite3'])
def test_create_and_reopen_exact_filename(tmp_path, name):
    path = tmp_path / name
    con = store.connect(path, create=True, keyword_only=True)
    try:
        store.set_meta(con, sentinel=name)
        con.commit()
    finally:
        con.close()
    assert path.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]
    con = store.connect(path, readonly=True)
    try:
        assert store.get_meta(con)['sentinel'] == name
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            store.set_meta(con, sentinel='cannot write')
    finally:
        con.close()


def test_punctuation_paths_do_not_open_a_neighboring_index(tmp_path):
    # Seed distinct real filenames using the path API, which has no URI syntax.
    for name in ['index', 'index?one.sqlite3', 'index#two.sqlite3']:
        con = sqlite3.connect(tmp_path / name)
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
        con.executemany('INSERT INTO meta VALUES(?,?)', [('keyword_only', '1'), ('sentinel', name)])
        con.commit()
        con.close()
    for name in ['index?one.sqlite3', 'index#two.sqlite3']:
        con = store.connect(tmp_path / name, keyword_only=True)
        try:
            assert store.get_meta(con)['sentinel'] == name
        finally:
            con.close()


def test_relative_path_support_is_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path('subdir') / 'relative?index.sqlite3'
    con = store.connect(path, create=True, keyword_only=True)
    con.close()
    assert path.is_file()


def test_missing_noncreate_path_is_not_initialized(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    with pytest.raises(store.IndexMissing):
        store.connect(path, keyword_only=True)
    assert not path.exists()


def test_disappearing_noncreate_index_is_not_recreated(tmp_path, monkeypatch):
    path = tmp_path / 'existing.sqlite3'
    con = store.connect(path, create=True, keyword_only=True)
    con.close()
    real_connect = sqlite3.connect
    def remove_before_connect(*args, **kwargs):
        path.unlink()
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(store.sqlite3, 'connect', remove_before_connect)
    with pytest.raises(sqlite3.OperationalError):
        store.connect(path, keyword_only=True)
    assert not path.exists()
