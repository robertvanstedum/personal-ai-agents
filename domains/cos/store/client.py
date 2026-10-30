"""The one client for the CoS store's internal API (spec v0.3 section 4.5).

Used by every CoS process except the scheduler, which owns the writer. It never raises for a store problem and
never claims a save it didn't get: a transport failure, a timeout or an unreadable answer is
{"outcome": "failed", "code": "store_unavailable"}, so the caller says plainly that the follow-up couldn't be saved.
A retry with the same command_id is always safe: the writer replays the first receipt.
"""
from __future__ import annotations

from pathlib import Path

import requests

from domains.cos.store.protocol import PREFIX


class StoreClient:
    def __init__(self, base_url: str, token_file: str, *, timeout: float = 5.0, session=None):
        self.base_url = base_url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        try:
            token = Path(self.token_file).read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    def submit(self, command: dict) -> dict:
        command_id = command.get("command_id", "unknown") if isinstance(command, dict) else "unknown"
        failed = {"schema": "minimoi.cos.store_result/1", "command_id": command_id, "outcome": "failed",
                  "code": "store_unavailable"}
        headers = self._headers()
        if not headers:
            return failed
        try:
            response = self.session.post(f"{self.base_url}{PREFIX}/commands", json=command, headers=headers,
                                         timeout=self.timeout)
        except (requests.RequestException, OSError):
            return failed
        if response.status_code != 200:
            return failed
        try:
            body = response.json()
        except ValueError:
            return failed
        if not isinstance(body, dict) or body.get("command_id") != command_id or body.get("outcome") not in (
                "accepted", "conflict", "refused", "failed"):
            return failed
        return body

    def get(self, target_type: str, target_id: str) -> dict:
        unavailable = {"schema": "minimoi.cos.reader_response/1", "reader": "store", "state": "unavailable",
                       "as_of": None, "reason": "timeout"}
        headers = self._headers()
        if not headers:
            return dict(unavailable, reason="not_granted")
        try:
            response = self.session.get(f"{self.base_url}{PREFIX}/records/{target_type}/{target_id}",
                                        headers=headers, timeout=self.timeout)
            body = response.json()
        except (requests.RequestException, OSError, ValueError):
            return unavailable
        return body if isinstance(body, dict) and body.get("schema") == "minimoi.cos.reader_response/1" else unavailable
