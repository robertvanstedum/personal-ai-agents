"""Exercise domain grant SQL and retained sessions against synthetic account state."""
import sqlite3

from flask import Flask
import pytest
from minimoi_portal import domain_auth as auth


@pytest.fixture
def accounts(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.execute("ATTACH DATABASE ':memory:' AS auth")
    db.executescript('''
        CREATE TABLE auth.users (id INTEGER PRIMARY KEY, is_active BOOLEAN);
        CREATE TABLE auth.domain_access (user_id INTEGER, domain TEXT);
        INSERT INTO auth.users VALUES (1, TRUE), (2, FALSE), (3, NULL);
        INSERT INTO auth.domain_access VALUES (1, 'portuguese'), (2, 'portuguese'),
            (3, 'portuguese'), (99, 'portuguese');
    ''')
    # Execute the actual access SQL; only DB-API placeholder spelling differs.
    monkeypatch.setattr(auth, '_query', lambda sql, params: list(db.execute(sql.replace('%s', '?'), params)))
    yield db
    db.close()


@pytest.mark.parametrize('user_id,domain,allowed', [(1, 'portuguese', True), (1, 'german', False),
    (2, 'portuguese', False), (3, 'portuguese', False), (99, 'portuguese', False), (1, "portuguese' OR 1=1 --", False)])
def test_access_requires_current_active_account_and_matching_grant(accounts, user_id, domain, allowed):
    assert auth.has_domain_access(user_id, domain) is allowed


def test_retained_session_loses_domain_access_when_account_deactivates(accounts, monkeypatch):
    from minimoi_portal import app as portal
    app = Flask('synthetic-domain-access')
    app.secret_key = 'synthetic-session-key'
    monkeypatch.setattr(portal, 'render_template', lambda *a, **k: 'access denied')
    @app.route('/protected')
    @portal.requires_domain('portuguese')
    def protected(): return 'allowed'
    client = app.test_client()
    user = {'username': 'synthetic@example.test', 'tier': 'guest', 'auth_id': 1}
    with client.session_transaction() as session:
        session['user'] = user
    assert client.get('/protected').status_code == 200
    accounts.execute('UPDATE auth.users SET is_active=FALSE WHERE id=1')
    assert client.get('/protected').status_code == 403
    with client.session_transaction() as session:
        assert session['user'] == user  # The existing cookie is the one being checked.
    accounts.execute('UPDATE auth.users SET is_active=TRUE WHERE id=1')
    assert client.get('/protected').status_code == 200
    accounts.execute("DELETE FROM auth.domain_access WHERE user_id=1 AND domain='portuguese'")
    assert client.get('/protected').status_code == 403


def test_owner_bypass_remains_separate_from_domain_grants(accounts, monkeypatch):
    from minimoi_portal import app as portal
    app = Flask('synthetic-owner-access')
    app.secret_key = 'synthetic-owner-session'
    @app.route('/protected')
    @portal.requires_domain('portuguese')
    def protected(): return 'allowed'
    monkeypatch.setattr(auth, 'has_domain_access', lambda *a: pytest.fail('owner must not query domain grants'))
    client = app.test_client()
    with client.session_transaction() as session:
        session['user'] = {'username': 'synthetic-owner', 'tier': 'owner'}
    assert client.get('/protected').status_code == 200
