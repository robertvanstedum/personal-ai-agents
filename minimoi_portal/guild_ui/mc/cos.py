"""The Chief of Staff as the Shop floor's conversation partner (``MINIMOI_GUILD_MC=cos``).

On production there is no Master Craftsman relay. The one assistant Robert already talks to there is the Chief of Staff (CoS),
reached by the portal over the internal address it already uses for ``/app/cos`` (``COS_BACKEND``). This backend sends a kept
note to CoS's own ``/chat`` route and keeps the reply as the Chief of Staff's, never Master Craftsman's.

What it deliberately does not do:
* No new credential, key, token or privilege: it is the same internal call the portal's CoS proxy makes, and only an owner
  who passed the Shop floor's owner guard and write guard reaches it.
* No Private / no-retention claim: CoS keeps its own conversation record, so a Private question is refused here.
* Files only through verified private copies: a note with attached documents is refused unless the portal is configured to place verified copies in the shared agent's private
  folder (``MINIMOI_MC_COS_FILES_DIR`` / ``..._AGENT_DIR``); then the agent is told the path. The message claims ``staged`` only, never that anything was opened or seen.
* No streaming, no retries: one POST per note; an unknown outcome is ``timeout_uncertain``, never re-sent.
* No fallback: an unreachable CoS is "unavailable"; nothing is ever answered in its place.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid

import requests

from .backend import Health, MasterCraftsmanBackend, TurnResult, UnavailableBackend

log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 3
TURN_TIMEOUT_S = 120
OPERATION_TIMEOUT_S = 20      # deterministic platform write, no model in the path
CHANNEL = "api_text"
# Host-only identity marker. Browser/model strings never select a voice channel.
MANAGED_VOICE_CHANNEL = object()


def _uuid_or_none(value):
    """A canonical UUID string, or None for anything else (never a guess)."""
    try:
        return str(uuid.UUID(str(value))) if isinstance(value, str) and value.strip() else None
    except ValueError:
        return None


def _base_url(environ):
    """CoS's internal address (``COS_BACKEND``, http or https only), or None: not connected."""
    base = str(environ.get("COS_BACKEND", "") or "").strip()
    return base if base.startswith(("http://", "https://")) else None


def operations_from_env(environ, *, http_post=None):
    """The Chief of Staff's own operation client for owner notes and reminders (owner_actions; A4-3), whichever backend
    Master Craftsman chats through. Built from the configuration the CoS backend uses: the same ``COS_BACKEND``, the same
    one-POST ``/chat`` operation (``ChiefOfStaffBackend.operation``) with its receipt, timeout and request id, and no caller
    headers, since that backend sends none. The owner and write guards stay the Shop floor's, before any owner action
    (api._mc_prelude; on voice, the direct host's guard). Only ``operation`` is used: never a turn, a file or a health
    read. No valid ``COS_BACKEND``: None, which owner_actions answers UNAVAILABLE exactly as before."""
    base = _base_url(environ)
    return ChiefOfStaffBackend(base, http_post=http_post) if base is not None else None


class ChiefOfStaffBackend(MasterCraftsmanBackend):
    kind = "cos"
    display_name = "Chief of Staff"
    supports_private = False

    def __init__(self, base_url: str, *, http_get=None, http_post=None, accepts_files: bool = False):
        self.base_url = base_url.rstrip("/")
        self.accepts_files = bool(accepts_files)
        self._get = http_get or requests.get
        self._post = http_post or requests.post

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        base = _base_url(environ)
        if base is None:
            return UnavailableBackend(cls.kind, "not_connected")
        from ..mc_files import cos_dirs
        return cls(base, http_get=http_get, http_post=http_post, accepts_files=cos_dirs(environ) is not None)

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
        channel = "html_voice" if (req.context or {}).get("host_channel") is MANAGED_VOICE_CHANNEL else CHANNEL
        body = {"text": req.text, "channel": channel, "conversation_id": self._session(req.conversation_id),
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
            completion = payload.get("completion")
            trace = {"finish_reason":"stop"} if completion == {"state":"complete", "source":"openclaw_connector"} else {}
            if completion == {"state":"cut_off", "reason":"output_limit", "source":"openclaw_connector"}:
                trace = {"finish_reason":"length"}
            # CoS's own turn id is the gateway routing-receipt id for this turn's model calls,
            # so cost can be settled against exact receipts rather than a time window.
            cos_turn_id = _uuid_or_none(payload.get("turn_id"))
            if cos_turn_id:
                trace["cos_turn_id"] = cos_turn_id
            return TurnResult("answered", self.kind, text=str(payload["reply"]), trace=trace or None)
        if status == 200:
            return TurnResult("error", self.kind, failure_class="malformed_answer")
        if status in (502, 503, 504):
            return TurnResult("unavailable", self.kind, failure_class="not_ready")
        return TurnResult("error", self.kind, failure_class="runtime_error")

    def operation(self, text: str, *, conversation_id: str, request_id: str) -> dict:
        """One canonical platform command (``/note …``, ``/remind …``) on the same /chat route, returning
        CoS's own ``operation`` receipt instead of dropping it like a chat answer. The text is the owner's
        command only, never context-enriched. One POST; an unknown outcome is ``uncertain``, never re-sent."""
        body = {"text": text, "channel": CHANNEL, "conversation_id": self._session(conversation_id),
                "request_id": request_id}
        try:
            response = self._post(f"{self.base_url}/chat", json=body, timeout=OPERATION_TIMEOUT_S)
        except requests.Timeout:
            return {"state": "uncertain"}
        except Exception:
            return {"state": "unavailable"}
        status = getattr(response, "status_code", 0)
        try:
            payload = response.json()
        except Exception:
            payload = None
        reply = str(payload.get("reply") or "").strip()[:300] if isinstance(payload, dict) else ""
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("operation"), dict):
            return {"state": "ok", "reply": reply, "operation": payload["operation"]}
        if status == 400 and reply:
            return {"state": "refused", "reply": reply}     # CoS's own platform sentence, e.g. a time bound
        if status in (502, 503, 504):
            return {"state": "unavailable"}
        return {"state": "failed"}
