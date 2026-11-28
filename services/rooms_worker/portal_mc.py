"""Named owner turns use the portal's existing MC ledger/tool path, on Dev or production's own host.

No prompt, credential, transcript or tool declaration is supplied by this client.
Discussion continues to use its separate tool-free transport. Enabling this
transport never falls back to the unbound job agent on an error.
"""
import re
import requests
from .clients import read_secret

PATH = '/internal/mc/v1/rooms'
CODE = re.compile(r'[a-z0-9_]{1,40}')


class PortalMC:
    stop_guaranteed = False   # a stop fences future tools; cannot undo admitted actions

    def __init__(self, token_file, *, base='http://portal:5011', session=None):
        if base != 'http://portal:5011':
            raise ValueError('fixed_portal_required')
        self.token_file, self.base = token_file, base
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.held_reason = None   # why MC is connected but holding (a pause for the owner), from its last readiness answer

    def headers(self):
        return {'Authorization': 'Bearer ' + read_secret(self.token_file), 'Content-Type': 'application/json'}

    def ready(self):
        try:
            r = self.session.get(self.base + PATH, headers=self.headers(), timeout=5, allow_redirects=False)
            data = r.json() if r.status_code == 200 else {}
            ready = isinstance(data, dict) and data.get('ready') is True
            held = data.get('held') if ready else None
            self.held_reason = held if isinstance(held, str) and CODE.fullmatch(held) else None
            return ready
        except (requests.RequestException, ValueError):
            self.held_reason = None
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
            if not isinstance(data, dict):
                raise ValueError('invalid_portal_result')
            if data.get('outcome') == 'not_admitted':
                # #699: Records did not admit the turn at the portal, so nothing was prepared or sent. The worker
                # treats it like its own refused admission (a stop is acknowledged; a late answer is asked again).
                state = data.get('state')
                if not isinstance(state, str) or not CODE.fullmatch(state):
                    raise ValueError('invalid_portal_result')
                return {'outcome': 'not_admitted', 'detail': 'dispatch_refused', 'state': state, 'text': '', 'usage': None}
            if data.get('outcome') not in {'done', 'error', 'refused'}:
                raise ValueError('invalid_portal_result')
            if data.get('nothing_sent') is not True:
                data.pop('nothing_sent', None)       # only the portal's plain True counts as "nothing reached MC"
            return data
        except (requests.RequestException, ValueError):
            return {'outcome': 'error', 'detail': 'portal_outcome_uncertain', 'text': '', 'usage': None}
