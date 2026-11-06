"""Named owner turns use the portal's existing MC ledger/tool path, on Dev or production's own host.

No prompt, credential, transcript or tool declaration is supplied by this client.
Discussion continues to use its separate tool-free transport. Enabling this
transport never falls back to the unbound job agent on an error.
"""
import requests
from .clients import read_secret

PATH = '/internal/mc/v1/rooms'


class PortalMC:
    stop_guaranteed = False   # a stop fences future tools; cannot undo admitted actions

    def __init__(self, token_file, *, base='http://portal:5011', session=None):
        if base != 'http://portal:5011':
            raise ValueError('fixed_portal_required')
        self.token_file, self.base = token_file, base
        self.session = session or requests.Session()
        self.session.trust_env = False

    def headers(self):
        return {'Authorization': 'Bearer ' + read_secret(self.token_file), 'Content-Type': 'application/json'}

    def ready(self):
        try:
            r = self.session.get(self.base + PATH, headers=self.headers(), timeout=5, allow_redirects=False)
            return r.status_code == 200 and r.json().get('ready') is True
        except (requests.RequestException, ValueError):
            return False

    def stop(self, correlation):
        try:
            r = self.session.post(self.base + PATH + '/stop', headers=self.headers(),
                json={'correlation_id': correlation}, timeout=10, allow_redirects=False)
            return r.status_code
        except requests.RequestException:
            return 0

    def stream(self, messages, user, correlation, on_open=None, *, turn=None, admit=None, **_):
        if not turn or turn.get('discussion_reply') or admit is None:
            return {'outcome': 'refused', 'detail': 'fresh_owner_admission_required', 'text': '', 'usage': None}
        granted, state = admit()
        if not granted:
            return {'outcome': 'not_admitted', 'detail': 'dispatch_refused', 'state': state, 'text': '', 'usage': None}
        # Watcher may already have sent stop(correlation). Server keeps its tombstone.
        body = {'room': turn['room'], 'turn_id': turn['id'], 'claim_id': turn['claim_id'],
                'correlation_id': correlation}
        try:
            if on_open:
                on_open()
            r = self.session.post(self.base + PATH, headers=self.headers(), json=body,
                                  timeout=(5, 330), allow_redirects=False)
            if r.status_code != 200:
                return {'outcome': 'error', 'detail': 'portal_refused', 'text': '', 'usage': None}
            data = r.json()
            if data.get('outcome') not in {'done', 'error', 'refused'}:
                raise ValueError('invalid_portal_result')
            return data
        except (requests.RequestException, ValueError):
            return {'outcome': 'error', 'detail': 'portal_outcome_uncertain', 'text': '', 'usage': None}
