"""Opt-in exact-session queue transport. No server/session creation or receipt claim.

A trusted operator must establish the existing app routing and native return
channel before setting either admission boolean. This module cannot prove those
facts from a UUID or CLI success. It sends text only, using the existing outbox.
The current candidate has no enrollment, API route, daemon, or live activation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess

from core.agent_requests import ledger as L
from core.agent_requests.direct_contact import Delivery, Refused, Target

TRANSPORT = 'codex-desktop-queue'
BUNDLED_BINARY = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
_HASH = re.compile(r'[0-9a-f]{64}')


def _need(value, reason):
    if not value:
        raise Refused(reason)


def _binary(path):
    """Small metadata identity: do not hash the entire executable on every turn."""
    s = Path(path).lstat()
    _need(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_mode & 0o111
          and not s.st_mode & 0o022, 'unsafe_queue_binary')
    return (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


@dataclass(frozen=True)
class QueueEnrollment:
    """Host-owned reviewed binding, never browser/model authority or auto-discovery."""
    target: Target
    binary_identity: tuple
    routing_proof_sha256: str
    native_return_proof_sha256: str
    same_app_routing_verified: bool = False
    authenticated_native_return_ready: bool = False

    def __post_init__(self):
        _need(isinstance(self.target, Target) and self.target.recipient == 'codex'
              and self.target.transport == TRANSPORT and self.target.host == 'local',
              'unsupported_target')
        _need(type(self.binary_identity) is tuple and len(self.binary_identity) == 8
              and all(type(v) is int and v >= 0 for v in self.binary_identity),
              'invalid_binary_binding')
        _need(type(self.same_app_routing_verified) is bool
              and type(self.authenticated_native_return_ready) is bool,
              'invalid_admission')
        for value in (self.routing_proof_sha256, self.native_return_proof_sha256):
            _need(type(value) is str and _HASH.fullmatch(value), 'missing_proof_binding')


def admission(enrollment):
    """Pure operator plan: does not probe a server, read a ledger, or send a turn."""
    _need(isinstance(enrollment, QueueEnrollment), 'enrollment_required')
    holds = []
    if not enrollment.same_app_routing_verified:
        holds.append('same_app_routing_unproven')
    if not enrollment.authenticated_native_return_ready:
        holds.append('native_return_channel_unproven')
    return {'ready': not holds, 'holds': holds, 'target': asdict(enrollment.target),
            'automatic_retry': False, 'fresh_session_fallback': False,
            'queue_acceptance_is_pickup': False, 'text_only': True}


class CodexDesktopQueue:
    name = TRANSPORT

    def __init__(self, enrollment, *, run=subprocess.run):
        _need(isinstance(enrollment, QueueEnrollment), 'enrollment_required')
        self.enrollment, self._run = enrollment, run

    def deliver(self, target, envelope, *, timeout):
        """One queue command, called only behind Outbox's durable intent/lock.

        Exit0 is transport acceptance only. Any nonzero exit or exception after
        subprocess invocation is unknown, even when the client was killed. No
        error text, prompt, environment, or stdout is recorded. Native work may
        continue after the client deadline. Never retry through this transport.
        """
        e = self.enrollment
        _need(admission(e)['ready'], 'direct_contact_not_ready')
        _need(target == e.target, 'target_binding_changed')
        _need(_binary(BUNDLED_BINARY) == e.binary_identity, 'queue_binary_changed')
        _need(not isinstance(timeout, bool) and isinstance(timeout, (int, float))
              and math.isfinite(timeout) and 0 < timeout <= 25, 'invalid_timeout')
        _need(type(envelope) is dict and set(envelope) == {'request', 'owner_source', 'correlation'},
              'invalid_envelope')
        r, source, correlation = envelope['request'], envelope['owner_source'], envelope['correlation']
        _need(type(r) is dict and type(source) is dict and type(correlation) is str
              and re.fullmatch(r'[0-9a-f]{48}', correlation), 'invalid_envelope')
        _need(L.valid_request_id(r.get('request_id')) and r.get('from') == 'mc'
              and r.get('to') == 'codex' and r.get('origin') == 'owner_message_via_mc'
              and r.get('conversation_id') == source.get('conversation_id')
              and L.valid_conversation(r.get('conversation_id')), 'request_binding_changed')
        _need(type(r.get('action')) is str and 0 < len(r['action']) <= L.ACTION_MAX,
              'invalid_action')
        # No implied attachment transfer or arbitrary file-path expansion.
        _need(not r.get('pointer') and not r.get('files') and not r.get('attachments'),
              'attachment_transport_unavailable')
        message = json.dumps({'format': 'minimoi.direct-session-notice.v1',
                              'request_id': r['request_id'], 'correlation': correlation,
                              'from': 'mc', 'target_session': target.session_id,
                              'action': r['action'],
                              'authority': 'request_not_new_owner_approval'},
                             ensure_ascii=False, separators=(',', ':'))
        _need(len(message.encode('utf-8')) <= 20 * 1024, 'message_too_large')
        # Fixed binary + UUID. No remote/profile/model/cwd/sandbox/approval options;
        # no daemon/bootstrap/resume/start. Existing app settings stay in charge.
        argv = [BUNDLED_BINARY, 'queue', '--thread', target.session_id, '--message', message]
        try:
            result = self._run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                               cwd=target.cwd)
        except (Exception, KeyboardInterrupt):
            return Delivery('unknown', target.session_id)
        # A binary replacement during invocation is also uncertain, not a resend.
        try:
            stable = _binary(BUNDLED_BINARY) == e.binary_identity
        except (OSError, Refused):
            stable = False
        if not stable or type(result.returncode) is not int or result.returncode != 0:
            return Delivery('unknown', target.session_id)
        return Delivery('accepted', target.session_id)
