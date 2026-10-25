"""Durable owner control and worker acknowledgement (§14.2), not chat authority.

The owner-authenticated/CSRF-checked adapter supplies principal/environment.
Desired state and request dedupe commit together. Export is a recoverable outbox:
an operator-configured control file is atomically replaced while the scope row
is locked. File publication is not reported as worker acknowledgement.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from uuid import UUID

from .binding_store import BindingStore
from .context import ActionContractError, _identifier


@dataclass(frozen=True, slots=True)
class ControlRequest:
    request_id: str
    action: str
    expected_version: int

    def __post_init__(self):
        try:
            if type(self.request_id) is not str or str(UUID(self.request_id)) != self.request_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ActionContractError('control_request_invalid') from None
        if type(self.action) is not str or self.action not in ('pause', 'resume'):
            raise ActionContractError('control_action_invalid')
        if type(self.expected_version) is not int or not 0 <= self.expected_version < 2**63 - 1:
            raise ActionContractError('control_version_invalid')


def _scope(principal, environment):
    if not _identifier(principal) or environment not in ('dev', 'prod'):
        raise ActionContractError('control_scope_invalid')
    return environment, principal


def _document(row):
    version, desired, request_id, updated = row
    return {'schema': 'mc-standing-control/1', 'version': version, 'state': desired,
            'request_id': str(request_id) if request_id else None,
            'updated_at': updated.astimezone(timezone.utc).isoformat()}


def _publish(path: Path, document: dict):
    """Trusted private directory only; no user/model-selected path or symlink."""
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ActionContractError('control_directory_unsafe')
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ActionContractError('control_file_unsafe')
    fd, name = tempfile.mkstemp(prefix='.mc-control-', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(json.dumps(document, sort_keys=True, separators=(',', ':')).encode())
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ControlStore:
    def __init__(self, connect):
        self._transactions = BindingStore(connect)

    def command(self, principal: str, environment: str, request: ControlRequest) -> dict:
        scope = _scope(principal, environment)
        if type(request) is not ControlRequest:
            raise ActionContractError('control_request_invalid')
        with self._transactions._transaction() as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO guild.mc_standing_control(environment,principal_id)
                VALUES (%s,%s) ON CONFLICT DO NOTHING''', scope)
            cur.execute('''SELECT version,desired_state,request_id,updated_at
                FROM guild.mc_standing_control WHERE environment=%s AND principal_id=%s FOR UPDATE''', scope)
            current = cur.fetchone()
            cur.execute('''SELECT action,expected_version,applied_version
                FROM guild.mc_standing_control_requests WHERE environment=%s AND principal_id=%s
                AND request_id=%s''', scope + (request.request_id,))
            previous = cur.fetchone()
            if previous:
                if previous[:2] != (request.action, request.expected_version):
                    raise ActionContractError('control_request_reused')
                return {'code': 'already_applied', 'applied_version': previous[2],
                        'desired': _document(current)}
            if current[0] != request.expected_version:
                raise ActionContractError('control_version_conflict')
            desired = 'paused' if request.action == 'pause' else 'enabled'
            cur.execute('''UPDATE guild.mc_standing_control SET version=version+1,
                desired_state=%s,request_id=%s,updated_at=clock_timestamp()
                WHERE environment=%s AND principal_id=%s
                RETURNING version,desired_state,request_id,updated_at''',
                (desired, request.request_id) + scope)
            updated = cur.fetchone()
            cur.execute('''INSERT INTO guild.mc_standing_control_requests
                (environment,principal_id,request_id,action,expected_version,applied_version)
                VALUES (%s,%s,%s,%s,%s,%s)''',
                scope + (request.request_id, request.action, request.expected_version, updated[0]))
            return {'code': 'accepted', 'applied_version': updated[0], 'desired': _document(updated)}

    def desired(self, principal: str, environment: str) -> dict | None:
        with self._transactions._transaction() as conn, conn.cursor() as cur:
            cur.execute('''SELECT version,desired_state,request_id,updated_at
                FROM guild.mc_standing_control WHERE environment=%s AND principal_id=%s''',
                _scope(principal, environment))
            row = cur.fetchone()
            return _document(row) if row else None

    def export(self, principal: str, environment: str, control_file: Path) -> dict | None:
        # The deployment binds exactly one principal/environment to this path.
        # Never share a control path between scopes. Row locking prevents two
        # exporters from publishing an older version after a newer command.
        with self._transactions._transaction() as conn, conn.cursor() as cur:
            cur.execute('''SELECT version,desired_state,request_id,updated_at
                FROM guild.mc_standing_control WHERE environment=%s AND principal_id=%s FOR UPDATE''',
                _scope(principal, environment))
            row = cur.fetchone()
            if not row or row[0] == 0:
                return None
            doc = _document(row)
            _publish(Path(control_file), doc)
            return doc


def effective_status(desired: dict | None, acknowledgement: dict | None, *, now: datetime) -> dict:
    """An absent, stale or malformed worker acknowledgement never means applied.

    The caller reads the dedicated worker's ack from its configured, bounded
    authenticated channel. This function does not authenticate arbitrary JSON.
    """
    result = {'state': 'pending', 'version': desired['version'] if desired else 0,
              'effective_at': None, 'in_flight_cycle': None}
    if not desired or not acknowledgement or not isinstance(acknowledgement, dict):
        return result
    try:
        when = datetime.fromisoformat(acknowledgement['effective_at'].replace('Z', '+00:00'))
        changed = datetime.fromisoformat(desired['updated_at'])
        if (acknowledgement.get('schema') != 'mc-standing-control-ack/1'
                or type(acknowledgement.get('applied_version')) is not int
                or acknowledgement['applied_version'] != desired['version']
                or acknowledgement.get('applied_state') != desired['state']
                or acknowledgement.get('reason') is not None
                # Worker contract serializes effective_at to whole seconds.
                # Exact version/state establishes ordering; do not require
                # microsecond precision the worker does not publish.
                or when.utcoffset() is None or not changed.replace(microsecond=0) <= when <= now):
            return result
    except (ValueError, TypeError, KeyError, AttributeError):
        return result
    result.update(state=desired['state'], effective_at=when.astimezone(timezone.utc).isoformat())
    return result
