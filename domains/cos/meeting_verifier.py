"""CoS meeting turns bind the way MC's room bridge does (F2 design v0.2 D5, v0.3 D5/D8; step 4).

The rooms worker sends identifiers only (room, turn id, claim_id). cos-scheduler reads the authoritative Records
snapshot with its own **observe** credential (step 0d: heartbeat only) and requires the turn, room, claim,
addressee, state, owner trigger and members to match before the turn runs:

- ``admit(turn)`` returns the verified facts (members, the owner's trigger text, the claim and the room generation),
  or None. The trigger text comes from the snapshot, never from the worker's POST body.
- ``watch(...)`` re-admits once a second while the turn runs; the first failure revokes the turn's tool binding
  (reason ``admission_lost``), and the watch stops in ``finally``.

The checks are MC's (``minimoi_portal/guild_ui/mc_rooms_bridge.py`` ``admitted``) for addressee ``cos``; the
owner-private rule is shared (``core/room_scope``).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from uuid import uuid4

import requests

from core.room_scope import owner_private

_log = logging.getLogger("cos.meeting_verifier")
ADMIT_DEADLINE_S = 5.0


class RecordsMeetingVerifier:
    def __init__(self, base_url: str, token, *, principal: str = "robert", session=None, clock=time.monotonic):
        self.base = base_url.rstrip("/")
        self._token = token
        self.principal = principal
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self._clock = clock

    def _bearer(self) -> str:
        return self._token() if callable(self._token) else self._token

    def _heartbeat(self, turn_id: str, claim_id: str, snapshot: bool):
        started = self._clock()
        response = self.session.request(
            "POST", f"{self.base}/api/v1/turns/{turn_id}/heartbeat",
            data=json.dumps({"claim_id": claim_id, "intent": "dispatch", "snapshot": snapshot}),
            headers={"Authorization": "Bearer " + self._bearer(), "Idempotency-Key": str(uuid4()),
                     "Content-Type": "application/json", "Accept": "application/json"},
            timeout=(3, ADMIT_DEADLINE_S), allow_redirects=False)
        if response.status_code != 200 or self._clock() - started >= ADMIT_DEADLINE_S:
            return None
        body = response.json()
        return body if isinstance(body, dict) and body.get("dispatch") is True else None

    def admit(self, turn: dict) -> dict | None:
        """The verified facts for this meeting turn, or None (never raises on a refusal)."""
        try:
            turn_id, room, claim_id = turn["id"], turn["room"], turn.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id:
                return None
            body = self._heartbeat(turn_id, claim_id, snapshot=True)
        except (KeyError, TypeError, ValueError, requests.RequestException):
            return None
        if body is None:
            return None
        snap = body.get("turn") or {}
        trigger = snap.get("trigger") or {}
        people = snap.get("room_members") or []
        if (snap.get("id") != turn_id or snap.get("room") != room or snap.get("claim_id") != claim_id
                or snap.get("addressee") != "cos" or snap.get("state") != "running" or snap.get("discussion_reply")
                or trigger.get("actor") != self.principal or trigger.get("kind") != "message"
                or trigger.get("origin") is not None or not isinstance(trigger.get("text"), str)
                or not trigger["text"].strip() or not isinstance(snap.get("generation"), str) or not snap["generation"]):
            return None
        return {"members": people, "owner_private": owner_private(people, self.principal),
                "trigger_text": trigger["text"], "claim_id": claim_id, "room_generation": snap["generation"],
                "trigger_seq": snap.get("trigger_seq"),
                "binding": {"origin": "meeting", "principal": self.principal, "audience": room, "claim_id": claim_id,
                            "room_generation": snap["generation"], "trigger_seq": snap.get("trigger_seq")}}

    def still_admitted(self, turn_id: str, claim_id: str, generation: str) -> bool:
        try:
            body = self._heartbeat(turn_id, claim_id, snapshot=False)
        except (TypeError, ValueError, requests.RequestException):
            return False
        return body is not None and body.get("generation") == generation

    def watch(self, turn_id: str, claim_id: str, generation: str, on_lost, *, interval_s: float = 1.0):
        """Start the once-a-second re-admission. Returns a ``stop()`` callable for the turn's ``finally``."""
        halt = threading.Event()

        def loop():
            while not halt.wait(interval_s):
                if not self.still_admitted(turn_id, claim_id, generation):
                    _log.info("cos meeting turn %s lost admission; tools revoked", turn_id[:8])
                    try:
                        on_lost(turn_id)
                    finally:
                        return
        thread = threading.Thread(target=loop, name=f"cos-meeting-watch-{turn_id[:8]}", daemon=True)
        thread.start()

        def stop():
            halt.set()
            thread.join(timeout=interval_s + ADMIT_DEADLINE_S)
        return stop
