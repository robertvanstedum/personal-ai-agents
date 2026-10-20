from datetime import datetime, timezone
from functools import wraps
import unittest
from uuid import uuid4

from flask import Flask, redirect, session

from core.minimoi_actions.context import ActionContractError
from minimoi_portal.guild_ui.standing_control_routes import create_blueprint


class ControlRouteTests(unittest.TestCase):
    def setUp(self):
        self.calls = []; self.doc = None; self.ack = None
        outer = self
        class Store:
            def command(self, who, env, req):
                outer.calls.append((who, env, req))
                if req.expected_version != 0:
                    raise ActionContractError('control_version_conflict')
                outer.doc = {'version': 1, 'state': 'paused' if req.action == 'pause' else 'enabled',
                             'updated_at': datetime.now(timezone.utc).isoformat()}
            def desired(self, who, env):
                return outer.doc
        def user():
            return session.get('user')
        def guard(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not user() or user().get('tier') != 'owner':
                    return redirect('/login')
                return fn(*args, **kwargs)
            return wrapped
        app = Flask(__name__);app.secret_key = 'synthetic-fixture'
        app.register_blueprint(create_blueprint(store=Store(), owner_guard=guard,
            current_user=user, environment='dev', read_ack=lambda *_: self.ack,
            publish=lambda *_: None, enabled=True), url_prefix='/guild-next')
        self.client = app.test_client()
        self.path = '/guild-next/api/v1/mc/standing/control'
        self.body = {'request_id': str(uuid4()), 'action': 'pause', 'expected_version': 0}
        self.headers = {'X-CSRF-Token': 'synthetic-csrf', 'Sec-Fetch-Site': 'same-origin'}

    def login(self, tier='owner'):
        with self.client.session_transaction() as sess:
            sess['user'] = {'username': 'owner-A', 'tier': tier}
            sess['guild_floor_csrf'] = 'synthetic-csrf'

    def test_owner_and_csrf_checks_precede_command(self):
        self.assertEqual(self.client.post(self.path, json=self.body).status_code, 401)
        self.login('guest')
        self.assertEqual(self.client.post(self.path, json=self.body, headers=self.headers).status_code, 403)
        self.login()
        self.assertEqual(self.client.post(self.path, json=self.body).status_code, 403)
        self.assertEqual(self.client.post(self.path, json=self.body,
                         headers=dict(self.headers, Origin='https://other.invalid')).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_pending_until_worker_applies_matching_version(self):
        self.login()
        response = self.client.post(self.path, json=self.body, headers=self.headers)
        self.assertEqual((response.status_code, response.json['state']), (202, 'pending'))
        self.assertEqual(self.calls[0][:2], ('owner-A', 'dev'))
        self.ack = {'schema': 'mc-standing-control-ack/1', 'applied_version': 1,
                    'applied_state': 'paused', 'reason': None,
                    'effective_at': datetime.now(timezone.utc).isoformat()}
        response = self.client.get(self.path)
        self.assertEqual((response.status_code, response.json['state']), (200, 'paused'))

    def test_extra_scope_duplicate_fields_size_and_conflict(self):
        self.login()
        for extra in ('principal_id', 'environment', 'watchlist', 'budget', 'schedule'):
            response = self.client.post(self.path, json=dict(self.body, **{extra: 'forged'}), headers=self.headers)
            self.assertEqual(response.status_code, 400)
        response = self.client.post(self.path, data='{"action":"pause","action":"resume"}',
                                    content_type='application/json', headers=self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.path, data='x'*5000, content_type='application/json', headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.calls, [])
        response = self.client.post(self.path, json=dict(self.body, expected_version=3), headers=self.headers)
        self.assertEqual(response.status_code, 409)
