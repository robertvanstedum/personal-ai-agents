"""The notice channel for tracked work (B4 design v0.2 §4.8, v0.6): an outbox ``notify`` entry becomes one item in
the portal's existing "waiting on Robert" store, raised through its agent door (``POST /internal/notices/v1/raise``)
as agent ``partnership``, keyed by the outbox operation.

The door is idempotent per key: the same key with the same words answers the existing item, so ``lookup`` is the
same raise again and can never create a second notice. A different text under the same key is refused
(``key_conflict``), which the drainer records as a conflict. Words are built here from the packet's fields (task,
outcome, accepted set, delivered disagreements, or the needs-owner reason), never from model text, and are kept
inside the door's 10-1500 character limit. The receipt names the operation and the packet's own digest, which is
what the drainer's ``applied`` check requires.
"""
from __future__ import annotations

import json

import requests

from services.partnership_tasks.outbox import DestinationUnreachable, HarnessRefused

RAISE = "/internal/notices/v1/raise"


def words_for(packet: dict) -> str:
    kind = packet.get("kind")
    if kind == "return_packet":
        text = (f"Tracked work {packet.get('task_id')} is done and accepted by {packet.get('accepted_by')}: "
                f"{str(packet.get('outcome') or '')[:400]}. Accepted set {str(packet.get('accepted_set') or '')[:20]}"
                f" with {len(packet.get('artifacts') or [])} artifact(s).")
        dis = packet.get("disagreements_delivered") or []
        if dis:
            text += " Delivered with disagreement: " + "; ".join(
                f"{d.get('reviewer')} {d.get('verdict')}" for d in dis[:5]) + "."
    elif kind == "needs_owner":
        text = (f"Tracked work {packet.get('task_id')} needs you: its lead {packet.get('lead')} missed a checkpoint. "
                "Open the task view to decide.")
    else:
        text = f"Tracked work {packet.get('task_id')}: {kind}. Open the task view."
    text = "".join(ch for ch in text if ch >= " ")[:1500]
    return text if len(text) >= 10 else (text + " " * 10)[:10]


class PortalNotices:
    def __init__(self, base_url: str, token, read_outbox, *, session=None, timeout=(3, 10)):
        self.base, self._token, self.read_outbox = base_url.rstrip("/"), token, read_outbox
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self.timeout = timeout

    def _raise(self, operation_id):
        ob = self.read_outbox(operation_id)
        if ob is None or ob.get("operation") != "notify" or not isinstance(ob.get("packet"), dict):
            raise HarnessRefused("entry_not_found")
        body = json.dumps({"key": operation_id[:80], "words": words_for(ob["packet"])})
        token = self._token() if callable(self._token) else self._token
        try:
            r = self.session.post(self.base + RAISE, data=body, timeout=self.timeout, allow_redirects=False,
                                  headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        except requests.RequestException:
            raise DestinationUnreachable() from None
        if r.status_code == 409:
            raise HarnessRefused("operation_id_reused")              # same key, other words: a conflict, not a send
        if r.status_code in (400, 401, 404):
            raise HarnessRefused(f"notice_http_{r.status_code}")
        if r.status_code != 200:
            raise DestinationUnreachable()
        try:
            code = r.json().get("code")
        except ValueError:
            raise DestinationUnreachable() from None
        return {"store": "notices", "id": operation_id, "edition": str(code), "digest": ob["bundle_digest"]}

    def deliver(self, operation_id, payload_digest, *, task_id, kind, attempt):
        return {"receipt": self._raise(operation_id)}

    def lookup(self, operation_id, *, attempt):
        try:
            return {"state": "applied", "receipt": self._raise(operation_id)}
        except DestinationUnreachable:
            return {"state": "unknown"}
