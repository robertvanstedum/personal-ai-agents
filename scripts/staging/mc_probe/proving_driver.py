"""Authenticated Guild proving client. No backend bypass or automatic retries.

Operator supplies a live budget admission callback and an isolated dev endpoint.
This module alone cannot admit paid calls or certify deployment/data isolation.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import http.cookiejar
import json
import os
from pathlib import Path
import re
from urllib import request, parse, error
from uuid import uuid4
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Refused(RuntimeError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Journal:
    """Write intent before the request. An unresolved intent prevents replay."""
    def __init__(self, directory):
        self.path = Path(directory)
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise Refused('journal_symlink')

    @contextmanager
    def exclusive(self):
        """One writer across processes, from reconciliation through response journal."""
        fd = os.open(self.path / '.writer.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('another_proving_write_is_active') from None
            yield
        finally:
            os.close(fd)

    def put(self, name, value):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
            raise Refused('invalid_event_name')
        fd = os.open(self.path / (name + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        fd = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def require_reconciled(self):
        for p in self.path.glob('*-intent.json'):
            if not p.with_name(p.name.replace('-intent.json', '-response.json')).exists():
                raise Refused('unresolved_write_requires_operator_reconciliation')


class GuildClient:
    API = '/guild-next/api/v1'

    def __init__(self, origin, principal, journal, admit, *, instance_id=None):
        # A local isolated dev portal may use an explicitly bound loopback port.
        u = parse.urlsplit(origin)
        if u.username or u.password or u.query or u.fragment or u.path:
            raise Refused('origin_only_required')
        if not ((u.scheme == 'https' and u.netloc == 'dev.minimoi.ai') or
                (u.scheme == 'http' and u.hostname == '127.0.0.1' and u.port == 15001)):
            raise Refused('not_approved_dev_origin')
        if not re.fullmatch(r'mc-proving-[a-z0-9-]+', principal):
            raise Refused('test_principal_required')
        self.origin, self.principal = origin, principal
        self.journal, self.admit = journal, admit
        self.opener = request.build_opener(NoRedirect(), request.ProxyHandler({}),
                                         request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None
        self.instance_id = instance_id
        self.environment = None

    def _request(self, path, data=None, form=False):
        headers = {'Origin': self.origin, 'Sec-Fetch-Site': 'same-origin'}
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
            headers['X-Record-Mode'] = 'on_record'
            if self.csrf:
                headers['X-CSRF-Token'] = self.csrf
            body = (parse.urlencode(data) if form else json.dumps(data)).encode()
        else:
            body = None
        req = request.Request(self.origin + path, data=body, headers=headers)
        try:
            response = self.opener.open(req, timeout=180)
        except error.HTTPError as exc:
            response = exc
        with response:
            status = response.status
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise Refused('response_too_large')
            if form:
                return status, None
            try:
                return status, json.loads(raw)
            except ValueError:
                raise Refused('non_json_response') from None

    def verify_environment(self):
        if not isinstance(self.instance_id, str) or not re.fullmatch(r'[0-9a-f]{32}', self.instance_id):
            raise Refused('isolated_instance_binding_required')
        status, health = self._request('/health')
        expected = {'environment': 'dev', 'instance_id': self.instance_id}
        if status != 200 or not isinstance(health, dict) or health.get('service') != 'portal' or health.get('proving') != expected:
            raise Refused('isolated_dev_environment_not_verified')
        self.environment = expected

    def login(self, password):
        self.verify_environment()
        status, _ = self._request('/login', {'username': self.principal, 'password': password}, form=True)
        if status != 302:
            raise Refused('login_failed')
        status, state = self._request(self.API + '/session')
        if status != 200 or state.get('user', {}).get('username') != self.principal or state['user'].get('tier') != 'owner':
            raise Refused('authenticated_identity_mismatch')
        if state.get('base') != self.API:
            raise Refused('api_base_mismatch')
        self.csrf = state.get('csrf_token')
        if not self.csrf:
            raise Refused('missing_csrf')
        return {k: v for k, v in state.items() if k != 'csrf_token'}

    def write(self, resource, payload, event):
        with self.journal.exclusive():
            return self._write_locked(resource, payload, event)

    def _write_locked(self, resource, payload, event):
        if not self.csrf or not self.environment:
            raise Refused('not_authenticated_or_environment_unverified')
        if resource not in ('/conversations', '/notes', '/mc/turns'):
            raise Refused('unsupported_write')
        self.journal.require_reconciled()
        admission = None
        if resource == '/mc/turns':
            # Callback must read the enforced gateway limit and reserve the
            # bounded turn cost. Never infer zero spend from absent evidence.
            admission = self.admit()
            if not isinstance(admission, dict) or admission.get('admitted') is not True:
                raise Refused('spend_not_admitted')
        self.journal.put(event + '-intent', {'at_utc': now(), 'principal': self.principal,
                         'resource': resource, 'payload': payload, 'spend_admission': admission})
        try:
            status, body = self._request(self.API + resource, payload)
        except Exception as exc:
            # Exception messages may contain endpoints or response content.
            self.journal.put(event + '-failure', {'at_utc': now(), 'uncertain': True,
                              'error_class': type(exc).__name__})
            raise

        # Even an HTTP error can follow a partial write. Preserve it but do
        # not mark reconciliation complete: a human must inspect receipts.
        if status != 200 or not isinstance(body, dict) or body.get('error'):
            self.journal.put(event + '-failure', {'status': status, 'at_utc': now(), 'body': body, 'uncertain': True})
            raise Refused('write_failed_or_uncertain_no_retry')
        self.journal.put(event + '-response', {'status': status, 'at_utc': now(), 'body': body})
        return body

    def conversation(self):
        key = 'proving-' + uuid4().hex
        response = self.write('/conversations', {'idempotency_key': key}, key)
        return response['conversation']['id']

    def turn(self, conversation, text):
        if not isinstance(text, str) or not 0 < len(text) <= 2000:
            raise Refused('text_must_fit_normal_composer')
        key = 'proving-' + uuid4().hex
        self.write('/notes', {'idempotency_key': key, 'conversation_id': conversation, 'text': text}, key)
        return self.write('/mc/turns', {'note_request_id': key, 'request_id': key,
                          'conversation_id': conversation}, key + '-turn')
