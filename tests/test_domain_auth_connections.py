"""Domain auth releases opened DB connections on both success and failure."""
from unittest.mock import MagicMock

import pytest
from minimoi_portal import domain_auth as auth


@pytest.fixture
def connection(monkeypatch):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.description = ('result',)
    cursor.fetchall.return_value = [{'id': 1}]
    cursor.fetchone.return_value = (1,)
    monkeypatch.setattr(auth, '_db_url', lambda: 'synthetic-dsn')
    monkeypatch.setattr(auth.psycopg2, 'connect', lambda *a, **k: conn)
    return conn, cursor


@pytest.mark.parametrize('operation,stage', [
    ('_query', 'cursor'), ('_query', 'execute'), ('_query', 'fetchall'),
    ('_execute', 'cursor'), ('_execute', 'execute'), ('_execute', 'fetchone'), ('_execute', 'commit'),
])
def test_error_propagates_and_connection_is_closed(connection, operation, stage):
    conn, cursor = connection
    error = RuntimeError('synthetic database failure')
    target = conn if stage in {'cursor', 'commit'} else cursor
    getattr(target, stage).side_effect = error
    with pytest.raises(RuntimeError) as raised:
        getattr(auth, operation)('SELECT value WHERE id=%s', [7])
    assert raised.value is error
    conn.close.assert_called_once_with()
    if stage != 'commit': conn.commit.assert_not_called()


def test_query_retains_rows_parameters_and_read_transaction_behavior(connection):
    conn, cursor = connection
    assert auth._query('SELECT value WHERE id=%s', [7]) == [{'id': 1}]
    cursor.execute.assert_called_once_with('SELECT value WHERE id=%s', [7])
    conn.commit.assert_not_called()
    conn.close.assert_called_once_with()


@pytest.mark.parametrize('returns_rows', [False, True])
def test_execute_retains_result_and_commit(connection, returns_rows):
    conn, cursor = connection
    cursor.description = ('result',) if returns_rows else None
    assert auth._execute('synthetic SQL') == ((1,) if returns_rows else None)
    cursor.execute.assert_called_once_with('synthetic SQL', [])
    conn.commit.assert_called_once_with()
    conn.close.assert_called_once_with()
