"""Exact enrolled-session outbox, reusing the request ledger. No live transport.

HOST boundary: enrollment and OwnerSource must come from trusted server code after
current owner/conversation/note authorization. Strings/correlation are not auth.
Receipt methods are for a transport-authenticated native callback, never a browser
or model tool. The dedicated namespace MUST NOT be watched by managed providers.
"""
from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import stat
import threading
import time

from core.agent_requests import ledger as L


class Refused(ValueError):
    pass


def _text(value, maximum=200):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise Refused('invalid_token')
    return value


def _hash(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Target:
    alias: str
    recipient: str
    session_id: str
    host: str
    cwd: str
    transport: str
    revision: str

    def __post_init__(self):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', self.alias) or self.recipient not in L.CONNECTED:
            raise Refused('invalid_target')
        if not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', self.session_id):
            raise Refused('invalid_session')
        for value in (self.host, self.cwd, self.transport, self.revision):
            _text(value)
        if not self.cwd.startswith('/') or '..' in Path(self.cwd).parts:
            raise Refused('invalid_cwd')


@dataclass(frozen=True)
class OwnerSource:
    """Already-authorized kept owner note; constructing this is NOT authentication."""
    principal: str
    conversation_id: str
    note_id: str
    authorization_ref: str

    def __post_init__(self):
        for value in (self.principal, self.note_id, self.authorization_ref):
            _text(value)
        if not L.valid_conversation(self.conversation_id):
            raise Refused('invalid_conversation')


@dataclass(frozen=True)
class Delivery:
    state: str  # accepted, failed, refused, unknown; accepted is NOT pickup
    session_id: str
    native_ref: str = ''


class Unavailable:
    name = 'unavailable'
    def deliver(self, target, envelope, *, timeout):
        raise Refused('transport_unavailable')


def _private_dir(path):
    path.mkdir(mode=0o700, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise Refused('unsafe_directory')


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path, doc):
    """Exclusive immutable JSON; caller serializes decisions with .direct-lock."""
    raw = json.dumps(doc, sort_keys=True).encode()
    if len(raw) > 64 * 1024:
        raise Refused('entry_too_large')
    if not L._write_new(str(path), raw):
        raise Refused('already_exists')
    _sync_dir(path.parent)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 64 * 1024:
            raise Refused('unsafe_entry')
        raw = os.read(fd, 64 * 1024 + 1)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise Refused('invalid_entry')
        return value
    finally:
        os.close(fd)


class Outbox:
    def __init__(self, private_root, enrolled, dispatcher=None, *, clock=time.time):
        # Root is explicitly provisioned, not a path supplied by a browser.
        parent = Path(private_root)
        for component in (parent, *parent.parents):
            if component.is_symlink():
                raise Refused('symlink_root')
        st = parent.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise Refused('unsafe_root')
        self.root = parent / 'direct-targets-v1'
        _private_dir(self.root)
        _sync_dir(parent)
        st = self.root.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise Refused('unsafe_namespace')
        self.enrolled = dict(enrolled)
        if any(not isinstance(t, Target) or key != t.alias for key, t in self.enrolled.items()):
            raise Refused('invalid_enrollment')
        self.dispatcher = dispatcher or Unavailable()
        self.clock = clock

    @contextlib.contextmanager
    def _lock(self):
        fd = os.open(self.root / '.direct-lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
                raise Refused('unsafe_lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _target(self, alias):
        if alias not in self.enrolled:
            raise Refused('target_not_enrolled')
        return self.enrolled[alias]

    def enqueue(self, alias, source: OwnerSource, action, *, reply_protocol=None):
        if not isinstance(source, OwnerSource):
            raise Refused('owner_source_required')
        target = self._target(alias)
        action = L._text(action, L.ACTION_MAX, 'request', multiline=True)
        # Exact kept-note identity, including conversation, prevents cross-chat collisions.
        key = {'target': asdict(target), 'source': asdict(source)}
        rid = L.request_id_for(_hash(key))
        folder = self.root / 'requests' / rid
        with self._lock():
            _private_dir(self.root / 'requests')
            _private_dir(folder)
            _sync_dir(folder.parent)
            binding = folder / 'direct.json'
            wanted = {**key, 'v': 1, 'rid': rid, 'action_sha256': _hash(action)}
            if reply_protocol is not None:
                from .status_v2 import validate_protocol
                validate_protocol(reply_protocol,target,rid,reply_protocol.get('correlation'))
                wanted['reply_protocol']=reply_protocol
            if binding.exists():
                have = _read(binding)
                if any(have.get(k) != value for k, value in wanted.items()):
                    raise Refused('request_reused')
            else:
                _publish(binding, {**wanted, 'correlation': reply_protocol['correlation'] if reply_protocol else secrets.token_hex(24)})
            L.create_request(str(self.root), request_id=rid, frm='mc', to=target.recipient,
                             action=action, conversation_id=source.conversation_id, origin='owner_message_via_mc')
            _sync_dir(folder)
        return rid

    def _load(self, rid):
        if not L.valid_request_id(rid):
            raise Refused('invalid_request')
        folder = self.root / 'requests' / rid
        st = folder.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise Refused('unsafe_directory')
        binding = _read(folder / 'direct.json')
        target = self._target(binding['target']['alias'])
        if binding['target'] != asdict(target) or binding['rid'] != rid:
            raise Refused('enrollment_changed')
        if 'reply_protocol' in binding:
            from .status_v2 import validate_protocol
            validate_protocol(binding['reply_protocol'],target,rid,binding['correlation'])
        request = _read(folder / 'request.json')
        if request.get('request_id') != rid or request.get('to') != target.recipient or request.get('from') != 'mc' or request.get('origin') != 'owner_message_via_mc' or request.get('conversation_id') != binding['source']['conversation_id'] or _hash(request.get('action')) != binding['action_sha256']:
            raise Refused('request_binding_mismatch')
        # Existing ledger skips torn events. Direct decisions instead fail closed.
        event_path = folder / 'events.jsonl'
        fd = os.open(event_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1 or st.st_size > 1024 * 1024:
                raise Refused('unsafe_events')
            raw = os.read(fd, 1024 * 1024 + 1)
        finally:
            os.close(fd)
        if len(raw) > 1024 * 1024 or not raw.endswith(b'\n'):
            raise Refused('incomplete_events')
        try:
            events = [json.loads(line) for line in raw.splitlines()]
        except ValueError as exc:
            raise Refused('invalid_events') from exc
        if any(not isinstance(e, dict) or e.get('kind') not in L.EVENT_KINDS for e in events):
            raise Refused('invalid_events')
        return folder, binding, target, request, events

    def status(self, rid):
        folder, binding, target, request, events = self._load(rid)
        kinds = {e['kind'] for e in events}
        delivery = [e for e in events if e['kind'] == 'delivery' and e.get('actor') == 'connection']
        last = delivery[-1] if delivery else {}
        ack_index = max((i for i, e in enumerate(events) if e['kind'] == 'receipt'), default=-1)
        deadline_index = max((i for i, e in enumerate(events) if e.get('reason') == 'native_receipt_deadline'), default=-1)
        if 'cancel' in kinds:
            state, reason = 'failed', 'cancelled'
        elif 'reply_protocol' in binding and (folder/'native-status.json').exists():
            from .status_v2 import validate_receipt
            validate_receipt(_read(folder/'native-status.json'),binding['reply_protocol'],target)
            state,reason='status_report','observed_commentary_not_completion'
        elif 'result' in kinds:
            state, reason = 'result', 'agent_report_not_verification'
        elif deadline_index > ack_index:
            state, reason = 'unknown', 'native_receipt_deadline'
        elif 'receipt' in kinds:
            state, reason = 'acknowledged', 'native_pickup'
        elif last.get('state') == 'accepted':
            state, reason = 'dispatched', 'transport_accepted_not_ack'
        elif last.get('state') in ('failed', 'refused'):
            state, reason = 'failed', last.get('reason', 'transport_failed')
        elif (folder / 'intent.json').exists():
            state, reason = 'unknown', last.get('reason', 'dispatch_interrupted')
        else:
            state, reason = 'queued', 'transport_unavailable' if isinstance(self.dispatcher, Unavailable) else 'awaiting_dispatch'
        return {'request_id': rid, 'target_alias': target.alias, 'state': state, 'reason': reason,
                'source': binding['source'], 'fallback': 'rooms_available_not_sent', 'automatic_retry': False}

    def dispatch(self, rid, *, timeout=10):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise Refused('invalid_timeout')
        # Lock remains held until final outcome is recorded. A durable intent also
        # blocks a second process and survives a crash or late transport return.
        with self._lock():
            folder, binding, target, request, events = self._load(rid)
            current = self.status(rid)
            if current['state'] != 'queued':
                return current
            if isinstance(self.dispatcher, Unavailable):
                return current
            if self.dispatcher.name != target.transport:
                raise Refused('wrong_transport')
            if 'reply_protocol' in binding:request={**request,'reply_protocol':binding['reply_protocol']}
            if hasattr(self.dispatcher, 'prepare'):
                # Read-only native baseline is durable before dispatch intent.
                # A failed preparation sends nothing; an existing intent never retries.
                path = folder / 'native-baseline.json'
                if not path.exists():
                    prepared = self.dispatcher.prepare(target, request, binding['correlation'])
                    _publish(path, prepared)
            intent = {'v': 1, 'at': self.clock(), 'transport': target.transport, 'target_sha256': _hash(binding['target']),
                      'correlation': binding['correlation']}
            _publish(folder / 'intent.json', intent)
            # Daemon completion cannot mutate this ledger. Deadline is an UNKNOWN
            # result, not cancellation; native work may still finish. Never retry.
            outcome = queue.Queue(maxsize=1)
            def invoke():
                try:
                    outcome.put(self.dispatcher.deliver(target, {'request': request, 'owner_source': binding['source'],
                                                                'correlation': binding['correlation']}, timeout=timeout))
                except Exception:
                    outcome.put(None)  # Do not persist raw exceptions/arguments.
            threading.Thread(target=invoke, daemon=True).start()
            try:
                response = outcome.get(timeout=timeout)
            except queue.Empty:
                response = None
            state, reason, native = 'unknown', 'transport_uncertain', ''
            if isinstance(response, Delivery) and response.session_id == target.session_id and response.state in L.DELIVERY_STATES[:4]:
                state, reason = response.state, 'transport_' + response.state
                native = _text(response.native_ref) if response.native_ref else ''
            L.record_delivery(str(self.root), rid, adapter=target.transport, state=state,
                              attempt_id=binding['correlation'], native_ref=native, reason=reason)
            return self.status(rid)

    def observe(self, rid, observer):
        """Trusted host reader only; no browser/model-submitted native evidence.

        Proof is immutable before ledger acknowledgment. Reopening after a crash
        reuses that proof; a changed native report cannot rewrite its meaning.
        """
        from .codex_read_observer import correlate
        from .codex_desktop_queue import message_for
        with self._lock():
            folder, binding, target, request, events = self._load(rid)
            self._intent(folder, binding, target)
            if observer.enrollment.target != target:
                raise Refused('observer_binding_changed')
            prepared = _read(folder / 'native-baseline.json')
            if 'reply_protocol' in binding:
                return self._observe_status(folder,binding,target,request,prepared,observer)
            message = message_for(target, request, binding['correlation'])
            if prepared.get('message_sha256') != _hash(message):
                raise Refused('native_message_changed')
            final_path = folder / 'native-result.json'
            if final_path.exists():
                proof = _read(final_path)
            else:
                snapshot = observer.snapshot(prepared['baseline'])
                proof = correlate(snapshot, prepared['baseline'], message, binding['correlation'])
                if proof['state'] == 'result':
                    # Receipt/result ledger limits also apply before publishing.
                    L._text(proof['report'], L.TEXT_MAX, 'result', multiline=True)
                    _publish(final_path, proof)
            if proof['state'] == 'result':
                L.result(str(self.root), rid, target.recipient, proof['report'], session_ref=target.session_id)
            elif proof['state'] == 'acknowledged':
                path = folder / 'native-ack.json'
                if not path.exists():
                    _publish(path, proof)
                L.receipt(str(self.root), rid, target.recipient, session_ref=target.session_id)
            return {**self.status(rid), 'native_observation': proof['state']}

    def _observe_status(self,folder,binding,target,request,prepared,observer):
        from .status_v2 import validate_protocol,correlate_status,validate_receipt
        protocol=binding['reply_protocol']
        message=validate_protocol(protocol,target,request['request_id'],binding['correlation'])
        if prepared.get('reply_protocol')!=protocol or prepared.get('message_sha256')!=_hash(message):
            raise Refused('native_message_changed')
        final=folder/'native-status.json'
        if final.exists():proof=validate_receipt(_read(final),protocol,target)
        else:
            state_path=folder/'native-status-candidate.json'
            previous=_read(state_path) if state_path.exists() else None
            # Invalidate before reading: a crash or failed read/write cannot leave
            # an old A candidate reusable after an unrecorded B/missing snapshot.
            self._candidate_replace(state_path,{'state':'observation_in_progress','observed_at':self.clock()})
            snapshot=observer.snapshot(prepared['baseline'])
            try:proof=correlate_status(snapshot,prepared['baseline'],message,binding['correlation'],request['request_id'],previous)
            except Exception:
                # A failed/partial intervening observation breaks consecutive stability.
                self._candidate_replace(state_path,{'state':'unverified','observed_at':self.clock()})
                raise
            proof.setdefault('observed_at',snapshot['observed_at'])
            if proof['state']=='status_report':
                validate_receipt(proof,protocol,target)
                _publish(final,proof)
            else:self._candidate_replace(state_path,proof)
        return {**self.status(request['request_id']),'native_observation':proof['state']}

    def _candidate_replace(self,path,value):
        # One bounded last-observation pointer, serialized by the outbox lock.
        # Never resurrect an older per-hash candidate after A->B->A or missing data.
        if path.exists():_read(path)
        temp=path.with_name('.status-'+secrets.token_hex(12))
        _publish(temp,value)
        os.replace(temp,path)
        _sync_dir(path.parent)

    def _intent(self, folder, binding, target):
        intent = _read(folder / 'intent.json')
        if intent.get('v') != 1 or intent.get('transport') != target.transport or intent.get('target_sha256') != _hash(binding['target']) or intent.get('correlation') != binding['correlation'] or isinstance(intent.get('at'), bool) or not isinstance(intent.get('at'), (int, float)) or not math.isfinite(intent['at']):
            raise Refused('intent_binding_mismatch')
        return intent

    def native_receipt(self, rid, *, transport, session_id, correlation, result=None):
        """Trusted transport callback only. Correlation/session strings aren't auth.
        A native reported result proves pickup but never verification or approval.
        """
        with self._lock():
            folder, binding, target, request, events = self._load(rid)
            if not (folder / 'intent.json').exists() or transport != target.transport or session_id != target.session_id or not isinstance(correlation, str) or not secrets.compare_digest(correlation, binding['correlation']):
                raise Refused('receipt_binding_mismatch')
            self._intent(folder, binding, target)
            if 'reply_protocol' in binding:raise Refused('versioned_native_observer_required')
            if result is None:
                L.receipt(str(self.root), rid, target.recipient, session_ref=session_id)
            else:
                L.result(str(self.root), rid, target.recipient, result, session_ref=session_id)
            return self.status(rid)

    def expire(self, rid, *, after=300):
        """Explicit monitor step, not an automatic resend. Late correlated ACK/result
        can still reconcile an unknown request; no new provider work is started.
        """
        if isinstance(after, bool) or not isinstance(after, (int, float)) or not math.isfinite(after) or not 1 <= after <= 3600:
            raise Refused('invalid_timeout')
        with self._lock():
            folder, binding, target, request, events = self._load(rid)
            current = self.status(rid)
            if current['state'] not in ('dispatched', 'acknowledged'):
                return current
            intent = self._intent(folder, binding, target)
            if self.clock() - intent['at'] >= after:
                L.record_delivery(str(self.root), rid, adapter=target.transport, state='unknown',
                                  attempt_id=binding['correlation'], reason='native_receipt_deadline')
                # A receipt means pickup is known; deadline is outcome uncertainty.
                return {**self.status(rid), 'state': 'unknown', 'reason': 'native_receipt_deadline'}
            return current
