"""The connection factory owns cleanup until it returns a usable index."""
import sqlite3
from types import SimpleNamespace

import pytest

from core.memory_index import store


@pytest.fixture
def opened(monkeypatch):
    connections = []
    real_connect = sqlite3.connect

    def capture(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", capture)
    monkeypatch.setattr(store, "sqlite_vec", None)
    yield connections
    for connection in connections:
        connection.close()


def assert_closed(connections):
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_schema_setup_failure_closes_owned_connection(tmp_path, monkeypatch, opened):
    monkeypatch.setattr(store, "KEYWORD_SCHEMA", "CREATE TABLE partial(x); NOT SQL;")
    with pytest.raises(sqlite3.OperationalError):
        store.connect(tmp_path / "index.sqlite", create=True, keyword_only=True)
    assert_closed(opened)


def test_permission_setup_failure_closes_owned_connection(tmp_path, monkeypatch, opened):
    def refuse(*args):
        raise PermissionError("synthetic permission refusal")

    monkeypatch.setattr(store.os, "chmod", refuse)
    with pytest.raises(PermissionError, match="synthetic"):
        store.connect(tmp_path / "index.sqlite", create=True, keyword_only=True)
    assert_closed(opened)


def test_extension_load_failure_closes_owned_connection(tmp_path, monkeypatch, opened):
    def refuse(connection):
        raise RuntimeError("synthetic extension failure")

    monkeypatch.setattr(store, "sqlite_vec", SimpleNamespace(load=refuse))
    with pytest.raises((RuntimeError, AttributeError, sqlite3.NotSupportedError)):
        # Some host Python builds cannot enable extensions; that setup failure
        # must also close the connection before propagating.
        store.connect(tmp_path / "index.sqlite", create=True)
    assert_closed(opened)


def test_success_transfers_open_connection_to_caller(tmp_path, opened):
    connection = store.connect(tmp_path / "index.sqlite", create=True, keyword_only=True)
    assert connection is opened[0]
    assert connection.execute("SELECT 1").fetchone() == (1,)
    connection.close()
    assert_closed(opened)


def test_variant_refusal_still_closes_without_deleting_index(tmp_path, opened):
    path = tmp_path / "index.sqlite"
    connection = store.connect(path, create=True, keyword_only=True)
    connection.close()
    opened.clear()
    # Present a vector schema marker without invoking an extension.
    con = sqlite3.connect(path)
    try:
        con.execute("DELETE FROM meta WHERE key='keyword_only'")
        con.commit()
    finally:
        con.close()
    opened.clear()
    original = path.read_bytes()
    with pytest.raises(store.IndexIncompatible):
        store.connect(path, keyword_only=True)
    assert_closed(opened)
    assert path.read_bytes() == original
