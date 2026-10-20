"""Optional Telegram copy of the morning brief (standing spec v0.3.1 §7.1–§7.2).

A separate, explicitly enabled destination: never on merely because a bot token exists. It is a
concise notification plus the protected brief link, sent only after the site brief is verified
delivered (so the link works and the site stays the record). Counts only: no watch details, no
guest names, no raw logs, no learning topic labels.

Telegram is not idempotent, so every send goes through a per-cycle sending ledger in the spool
(``outbox/<cycle_key>.telegram``), keyed by the cycle's local date, not a UTC date:

  prepared → dispatched → acknowledged | not_sent | uncertain

* ``prepared`` is written (with the immutable message and its digest) before any send.
* ``dispatched`` is written immediately before the provider call. A crash after it is
  ``uncertain``: the message may have gone out, so it is never resent automatically.
* ``not_sent`` only when the sender is certain nothing was delivered (rejected before
  acceptance, connection refused). It is final for the cycle; no retry loop.
* A failed copy never changes the site delivery; results name each channel separately.
"""
from __future__ import annotations

import hashlib
import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .learning_view import LearningView, learning_line

SCHEMA = "mc-standing-telegram-copy/1"
FINAL = ("acknowledged", "not_sent", "uncertain")
MAX_CHARS = 600


class NotSent(Exception):
    """The provider certainly did not deliver the message (code says why)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SendUncertain(Exception):
    """The request may have reached the provider; delivery is unknown."""


def compose(observations, *, environment: str, local_date: str, learning: dict | None = None,
            details_link: str | None = None) -> str:
    """Counts only. ``learning`` is the brief payload's learning record (no labels in it)."""
    env = {"prod": "production", "dev": "dev"}.get(environment, environment)
    states = [o.state for o in observations]
    healthy = states.count("healthy")
    attention = sum(1 for s in states if s in ("failed", "degraded"))
    unknown = states.count("unknown")
    if attention or unknown:
        parts = [f"{attention} need{'s' if attention == 1 else ''} attention" if attention else None,
                 f"{unknown} could not be checked" if unknown else None, f"{healthy} healthy"]
        summary = ", ".join(p for p in parts if p) + "."
    else:
        summary = f"all {healthy} checks healthy."
    lines = [f"MC operations brief, {env}, {local_date}: {summary}"]
    if learning:
        view = LearningView(status=learning.get("status", "unavailable"), reason=learning.get("reason"),
                            snapshot_id=learning.get("snapshot_id"), as_of=learning.get("as_of"),
                            coverage=learning.get("coverage"),
                            adopted_window_days=learning.get("adopted_window_days", 7),
                            counts=dict(learning.get("counts") or {}))
        line = learning_line(view, destination="telegram")
        if line:
            lines.append(line)
    if details_link:
        lines.append(f"Brief: {details_link}")
    return "\n".join(lines)[:MAX_CHARS]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deliver(*, spool, cycle_key: str, text: str, sender, destination_id: str, kill, paused,
            send_allowed) -> dict:
    """Send the copy at most once per cycle. ``send_allowed()`` re-checks the date/window at the
    moment of sending."""
    name = f"{cycle_key}.telegram"
    ledger = spool.read("outbox", name)
    if ledger and ledger["state"] in FINAL:
        return {"telegram": ledger["state"], "reason": ledger.get("reason")}
    if ledger and ledger["state"] == "dispatched":
        ledger.update(state="uncertain", reason="interrupted_after_dispatch", finished_at=_now())
        spool.put_state("outbox", name, ledger)
        return {"telegram": "uncertain", "reason": "interrupted_after_dispatch"}
    digest = hashlib.sha256(text.encode()).hexdigest()
    if ledger is None:
        spool.put_immutable("delivery", f"{cycle_key}.telegram-message", {"text": text, "sha256": digest})
        ledger = {"schema": SCHEMA, "cycle_key": cycle_key, "destination_id": destination_id,
                  "state": "prepared", "message_sha256": digest, "prepared_at": _now()}
        spool.put_state("outbox", name, ledger)
    else:
        text = spool.read("delivery", f"{cycle_key}.telegram-message")["text"]   # the prepared message, unchanged
    if kill():
        return {"telegram": "prepared", "reason": "killed_before_send"}
    if paused():
        return {"telegram": "prepared", "reason": "paused_before_send"}
    if not send_allowed():
        ledger.update(state="not_sent", reason="send_window_closed", finished_at=_now())
        spool.put_state("outbox", name, ledger)
        return {"telegram": "not_sent", "reason": "send_window_closed"}
    ledger.update(state="dispatched", dispatched_at=_now())
    spool.put_state("outbox", name, ledger)
    try:
        receipt = sender.send(destination_id, text)
    except NotSent as exc:
        ledger.update(state="not_sent", reason=exc.code, finished_at=_now())
        spool.put_state("outbox", name, ledger)
        return {"telegram": "not_sent", "reason": exc.code}
    except Exception:
        ledger.update(state="uncertain", reason="send_unconfirmed", finished_at=_now())
        spool.put_state("outbox", name, ledger)
        return {"telegram": "uncertain", "reason": "send_unconfirmed"}
    message_id = receipt.get("message_id") if isinstance(receipt, dict) else None
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        ledger.update(state="uncertain", reason="receipt_invalid", finished_at=_now())
        spool.put_state("outbox", name, ledger)
        return {"telegram": "uncertain", "reason": "receipt_invalid"}
    ledger.update(state="acknowledged", message_id=message_id, finished_at=_now())
    spool.put_state("outbox", name, ledger)
    return {"telegram": "acknowledged", "reason": None}


class TelegramBotSender:
    """Bot API ``sendMessage`` with conservative outcome classification. The token is read at call
    time and never logged; the chat id comes from the owner-approved binding, not from the token."""

    API = "https://api.telegram.org"

    def __init__(self, token_fn, timeout_s: float = 15.0, opener=urllib.request.urlopen):
        self.token_fn, self.timeout_s, self.opener = token_fn, timeout_s, opener

    def send(self, chat_id: str, text: str) -> dict:
        body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"{self.API}/bot{self.token_fn()}/sendMessage", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout_s) as resp:
                doc = json.loads(resp.read(65536))
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code != 429:
                raise NotSent(f"rejected_{exc.code}") from None     # refused before acceptance
            raise SendUncertain() from None                          # 429/5xx: may or may not have gone
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (ConnectionRefusedError, socket.gaierror)):
                raise NotSent("connect_failed") from None            # never reached the provider
            raise SendUncertain() from None
        except (TimeoutError, OSError, ValueError):
            raise SendUncertain() from None
        if not (isinstance(doc, dict) and doc.get("ok") is True and isinstance(doc.get("result"), dict)):
            raise SendUncertain()
        return {"message_id": doc["result"].get("message_id")}
