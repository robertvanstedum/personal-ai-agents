"""The Chief of Staff as the Shop floor's conversation partner (``MINIMOI_GUILD_MC=cos``).

On production there is no Master Craftsman relay. The one assistant Robert already talks to there is the Chief of Staff (CoS),
reached by the portal over the internal address it already uses for ``/app/cos`` (``COS_BACKEND``). This backend sends a kept
note to CoS's own ``/chat`` route and keeps the reply as the Chief of Staff's, never Master Craftsman's.

What it deliberately does not do:
* No new credential, key, token or privilege: it is the same internal call the portal's CoS proxy makes, and only an owner
  who passed the Shop floor's owner guard and write guard reaches it.
* No Private / no-retention claim: CoS keeps its own conversation record, so a Private question is refused here.
* No files: a note with attached documents is refused rather than sent to CoS in text form.
* No streaming, no retries: one POST per note; an unknown outcome is ``timeout_uncertain``, never re-sent.
* No fallback: an unreachable CoS is "unavailable"; nothing is ever answered in its place.
"""
from __future__ import annotations

import hashlib
import logging
import threading

import requests

from .backend import Health, MasterCraftsmanBackend, TurnResult, UnavailableBackend

log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 3
TURN_TIMEOUT_S = 120
CHANNEL = "api_text"


class ChiefOfStaffBackend(MasterCraftsmanBackend):
    kind = "cos"
    display_name = "Chief of Staff"
    accepts_files = False
    supports_private = False

    def __init__(self, base_url: str, *, http_get=None, http_post=None):
        self.base_url = base_url.rstrip("/")
        self._get = http_get or requests.get
        self._post = http_post or requests.post

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        base = str(environ.get("COS_BACKEND", "") or "").strip()
        if not base.startswith(("http://", "https://")):
            return UnavailableBackend(cls.kind, "not_connected")
        return cls(base, http_get=http_get, http_post=http_post)

    def health(self) -> Health:
        try:
            response = self._get(f"{self.base_url}/health", timeout=HEALTH_TIMEOUT_S)
        except Exception:
            return Health("unavailable", "not_ready")
        if getattr(response, "status_code", None) == 200:
            return Health("ready", reachable=True)
        return Health("unavailable", "not_ready")

    @staticmethod
    def _session(conversation_id: str) -> str:
        # Its own CoS conversation per Shop floor conversation, never CoS's default "owner" thread.
        return "guild-" + hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:24]

    def turn(self, req, cancel: threading.Event | None = None) -> TurnResult:
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        if (req.context or {}).get("mode") == "private":
            return TurnResult("refused", self.kind, failure_class="private_unsupported",
                              message="Private questions are not available through the Chief of Staff.")
        body = {"text": req.text, "channel": CHANNEL, "conversation_id": self._session(req.conversation_id),
                "request_id": req.note_request_id}
        try:
            response = self._post(f"{self.base_url}/chat", json=body, timeout=TURN_TIMEOUT_S)
        except requests.Timeout:
            return TurnResult("timeout_uncertain", self.kind, failure_class="local_timeout")
        except Exception:
            return TurnResult("unavailable", self.kind, failure_class="not_ready")
        status = getattr(response, "status_code", 0)
        try:
            payload = response.json()
        except Exception:
            payload = None
        if status == 200 and isinstance(payload, dict) and str(payload.get("reply") or "").strip():
            return TurnResult("answered", self.kind, text=str(payload["reply"]))
        if status == 200:
            return TurnResult("error", self.kind, failure_class="malformed_answer")
        if status in (502, 503, 504):
            return TurnResult("unavailable", self.kind, failure_class="not_ready")
        return TurnResult("error", self.kind, failure_class="runtime_error")
