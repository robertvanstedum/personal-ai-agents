"""HTTP client for the task service (B4 connection design v0.1, step 1).

Each caller holds one seat token (read from a private file, never logged) and talks to ``http_api``. Commands carry
ids the caller derives from where they come from (a binding turn and tool call, a portal request), so a repeat is
the same command and the journal answers it from its first result.

Never a blind resend: when a send's outcome is unknown (timeout, connection lost after sending, a 5xx), the client
asks the service's receipt lookup for that exact command. ``recorded`` returns the stored result; ``absent`` means it
never landed, and the caller may send it again under the same id; ``different`` and ``unknown`` are reported, never
retried.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Callable

import requests

from core.canonical_json import digest


class TaskServiceUnavailable(RuntimeError):
    """The outcome could not be confirmed. ``answer`` is the receipt lookup's word, or None if it was unreachable."""

    def __init__(self, message: str, answer: str | None = None):
        super().__init__(message)
        self.answer = answer


def read_token(path: str | os.PathLike) -> str:
    """A seat token from a private file (owner-only permissions). Refuses anything else."""
    p = Path(path)
    st = p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
        raise PermissionError("task service token file must be a private regular file")
    token = p.read_text("utf-8").strip()
    if not token:
        raise PermissionError("task service token file is empty")
    return token


def command(kind: str, target_type: str, target_id: str, payload: dict, *, command_id: str, environment: str,
            scope: str, expected_revision: int | None = None, causal_parent: dict | None = None,
            session_id: str | None = None, runtime: dict | None = None, basis_ref: dict | None = None) -> dict:
    """One command in the v1 contract shape. The seat, origin and authority are never part of it: the service takes
    them from the caller's binding."""
    return {"schema": "minimoi.partnership.command/1", "command_id": command_id, "kind": kind,
            "target": {"type": target_type, "id": target_id}, "expected_revision": expected_revision,
            "payload": payload, "payload_digest": digest(payload), "environment": environment, "data_scope": scope,
            "basis_ref": basis_ref, "causal_parent": causal_parent, "session_id": session_id,
            "runtime": runtime or {"kind": "deterministic", "model": None, "provider": None,
                                   "build": "partnership_tasks.client/1", "config_version": None},
            "event_at": None}


class TaskClient:
    def __init__(self, base_url: str, token: str | Callable[[], str], *, session=None, timeout=(3, 15), signer=None):
        """``signer(method, path, body_bytes) -> {header: value}`` adds per-request headers over the exact bytes
        sent: the portal's owner assertion for a principal client (B4 design v0.2 §2)."""
        self.base = base_url.rstrip("/")
        self._signer = signer
        self._token = token
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self.timeout = timeout

    def _headers(self):
        token = self._token() if callable(self._token) else self._token
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"}

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body)
        headers = self._headers()
        if self._signer is not None:
            headers.update(self._signer(method, path, (data or "").encode("utf-8")))
        return self.session.request(method, self.base + path, headers=headers, data=data, timeout=self.timeout,
                                    allow_redirects=False)

    def pending(self) -> list:
        r = self._call("GET", "/v1/pending")
        if r.status_code != 200:
            raise TaskServiceUnavailable(f"pending failed: HTTP {r.status_code}")
        return r.json()

    def unfinished(self) -> list:
        r = self._call("GET", "/v1/pending/unfinished")
        if r.status_code != 200:
            raise TaskServiceUnavailable(f"unfinished failed: HTTP {r.status_code}")
        return r.json()

    def submit(self, cmd: dict) -> dict:
        """The service's result for this command. Raises TaskServiceUnavailable when the outcome stays unknown."""
        try:
            r = self._call("POST", "/v1/commands", cmd)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 401, 413):
                return {"command_id": cmd.get("command_id"), "outcome": "refused", "code": f"http_{r.status_code}"}
        except (requests.RequestException, ValueError):
            pass
        return self._resolve(cmd)

    def _resolve(self, cmd: dict) -> dict:
        try:
            r = self._call("POST", "/v1/receipts", cmd)
            answer = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            answer = None
        word = answer.get("answer") if isinstance(answer, dict) else None
        if word == "recorded" and isinstance(answer.get("result"), dict):
            return answer["result"]
        if word == "absent":
            raise TaskServiceUnavailable("the command did not land; it may be sent again under the same id", word)
        raise TaskServiceUnavailable("the command's outcome could not be confirmed; do not resend", word)

    def get(self, target_type: str, target_id: str) -> dict | None:
        r = self._call("GET", f"/v1/records/{target_type}/{target_id}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise TaskServiceUnavailable(f"read failed: HTTP {r.status_code}")
        return r.json()

    def reconstruct(self, start_type: str, start_id: str) -> dict | None:
        r = self._call("GET", f"/v1/reconstruct/{start_type}/{start_id}")
        if r.status_code == 404:
            return None
        if r.status_code not in (200, 503):
            raise TaskServiceUnavailable(f"reconstruct failed: HTTP {r.status_code}")
        return r.json()

    def outbox_pending(self) -> list:
        """B4 v0.6: admissions and notices still to send or settle (drainer seat or principal)."""
        r = self._call("GET", "/v1/outbox/pending")
        if r.status_code != 200:
            raise TaskServiceUnavailable(f"outbox read failed: HTTP {r.status_code}")
        return r.json()

    def open_work(self) -> list:
        r = self._call("GET", "/v1/open")
        if r.status_code != 200:
            raise TaskServiceUnavailable(f"open work failed: HTTP {r.status_code}")
        return r.json()
