"""The Master Craftsman backend switch and connector contract (MC spec v0.7 §8,
carrying v0.5 §2; Robert's September 28 decision: "Master Craftsman should be
able to run on grok").

The Shop floor talks to Master Craftsman only through ``MasterCraftsmanBackend``:
``health()`` and ``turn(req, cancel)``. Which implementation answers is one
switch, ``MINIMOI_GUILD_MC``, read once when /guild-next mounts (staging only):

    off       (default, and any unknown value) Master Craftsman is off
    stub      scripted replies, never an agent; its rows are the stub's forever
    openclaw  Master Craftsman's own OpenClaw container, separate from CoS
              Agent A's (openclaw.py; spec v0.8)
    grok      a direct Grok backend, like CoS's COS_BACKEND_TYPE=grok. The seam
              is here; the implementation is NOT BUILT YET (grok.py) and says so

Honest states: an unavailable backend is "unavailable", never a stub, never a
fake answer. Only an ``answered`` result is ever kept as a reply, and the
author of a kept reply comes from the backend kind (``reply_author``): a stub
reply can never be stored as Master Craftsman's.

Dormant (separate-container plan, PR 1): the default is off, nothing is wired
to run a turn (``TURNS_WIRED`` is False; the owner route ``POST /mc/turns``
exists but always refuses), and no MC container or credential exists. The
Shop floor shows the state (``view``). Turn wiring is stage B of the plan.
"""
from __future__ import annotations

import abc
import logging
import threading
import time
from dataclasses import dataclass, field

from ..adapters.contract import now_iso
from ..stores import MASTER_CRAFTSMAN, Author

log = logging.getLogger(__name__)

SWITCH_VAR = "MINIMOI_GUILD_MC"
SWITCH_VALUES = ("off", "stub", "openclaw", "grok")
STATUSES = ("answered", "unavailable", "timeout_uncertain", "cancelled", "refused", "error",
            "duplicate_in_progress", "not_listening")
# Stub rows: author_kind "platform" (allowed by sql/001_floor_b1.sql), a label
# that says what it is, whatever the switch says later (spec v0.5 §2.3, H2).
MASTER_CRAFTSMAN_STUB = Author("master_craftsman_stub", "platform", "Master Craftsman stub · scripted")
REAL_KINDS = ("openclaw", "grok")
HEALTH_TTL_S = 60
# Turn wiring is stage B. Until then no surface sends a turn, whatever the state,
# and the owner route POST /mc/turns refuses every request.
TURNS_WIRED = False


@dataclass(frozen=True)
class TurnRequest:
    conversation_id: str
    text: str                                  # the stored, scrubbed note
    note_request_id: str
    context: dict = field(default_factory=dict)   # {about, item_ref, page}


@dataclass(frozen=True)
class TurnResult:
    status: str
    backend_kind: str
    text: str | None = None
    failure_class: str | None = None
    message: str = ""
    usage: dict | None = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown turn status {self.status!r}")
        if self.status == "answered" and not (self.text or "").strip():
            raise ValueError("an answered turn needs reply text")


@dataclass(frozen=True)
class Health:
    state: str                 # off | stub | ready | unavailable
    reason: str | None = None  # why unavailable: not_connected, not_ready, starting, not_built, ...
    observed_at: str = field(default_factory=now_iso)


class MasterCraftsmanBackend(abc.ABC):
    """One Master Craftsman runtime behind the Shop floor."""

    kind: str = "off"

    @abc.abstractmethod
    def health(self) -> Health: ...

    @abc.abstractmethod
    def turn(self, req: TurnRequest, cancel: threading.Event | None = None) -> TurnResult: ...


class OffBackend(MasterCraftsmanBackend):
    kind = "off"

    def health(self) -> Health:
        return Health("off")

    def turn(self, req, cancel=None) -> TurnResult:
        return TurnResult("refused", self.kind, failure_class="off", message="Master Craftsman is off")


class UnavailableBackend(MasterCraftsmanBackend):
    """The switch named a backend that could not be built: unavailable, never
    off-by-accident, never the stub."""

    def __init__(self, kind: str, reason: str):
        self.kind, self.reason = kind, reason

    def health(self) -> Health:
        return Health("unavailable", self.reason)

    def turn(self, req, cancel=None) -> TurnResult:
        return TurnResult("unavailable", self.kind, failure_class=self.reason)


class CachedHealth:
    """health() at most once per ``ttl`` seconds (spec v0.5 §2.2: cached 60 s)."""

    def __init__(self, backend: MasterCraftsmanBackend, ttl: float = HEALTH_TTL_S, clock=time.monotonic):
        self.backend, self.ttl, self.clock = backend, ttl, clock
        self._value: Health | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def get(self) -> Health:
        with self._lock:
            if self._value is None or self.clock() - self._at >= self.ttl:
                try:
                    self._value = self.backend.health()
                except Exception:   # a broken health check is "unavailable", never a crash
                    log.exception("master craftsman: health() failed")
                    self._value = Health("unavailable", "health_check_failed")
                self._at = self.clock()
            return self._value


def switch_value(environ) -> str:
    raw = str(environ.get(SWITCH_VAR, "") or "").strip().lower()
    if raw in ("", "0", "false", "no"):
        return "off"
    if raw not in SWITCH_VALUES:
        log.warning("%s=%r is not one of %s; Master Craftsman stays off", SWITCH_VAR, raw, ", ".join(SWITCH_VALUES))
        return "off"
    return raw


def backend_from_env(environ, *, http_get=None, http_post=None) -> MasterCraftsmanBackend:
    """Build the backend the switch names. Never raises: a backend that cannot
    be built is "unavailable", logged (an MC error never fails the /guild-next
    mount, and never turns into the stub)."""
    kind = switch_value(environ)
    try:
        if kind == "stub":
            from .stub import StubBackend
            return StubBackend()
        if kind == "openclaw":
            from .openclaw import OpenClawMasterCraftsman
            return OpenClawMasterCraftsman.from_env(environ, http_get=http_get, http_post=http_post)
        if kind == "grok":
            from .grok import GrokMasterCraftsman
            return GrokMasterCraftsman()
    except Exception:
        log.exception("master craftsman: the %s backend could not be built; it is unavailable", kind)
        return UnavailableBackend(kind, "misconfigured")
    return OffBackend()


# ── what the Shop floor shows ─────────────────────────────────────────────────

HEADER_OFF = "Master Craftsman is off · your messages are kept as notes"
HEADER_OFF_NO_NOTES = "Master Craftsman is off · notes unavailable, nothing you send is kept"
HEADER_NO_NOTES = "Master Craftsman can't take turns · notes unavailable, nothing you send is kept"
HEADER_STUB = "Master Craftsman stub · scripted replies, not an agent"
HEADER_LIVE = "Master Craftsman is live · your messages are kept as notes"
UNAVAILABLE_WHY = {
    "not_connected": "not connected yet",
    "not_ready": "its runtime is not answering",
    "starting": "starting, not checked yet",
    "not_built": "the Grok backend is not built yet",
    "model_gateway_down": "model gateway down",
    "cap_reached": "monthly cap reached",
    "rate_limited": "rate limit reached, try again shortly",
    "key_refused": "its model key was refused",
    "failed_check": "failed its last check",
    "caller_refused": "the runtime refused the Shop floor's token",
    "health_check_failed": "its health could not be read",
    "misconfigured": "its connection settings are invalid",
}
NOTES_TEXT = "On the record, your messages are kept as notes."


def view(health: Health, *, notes_ok: bool) -> dict:
    """The Shop floor's Master Craftsman state, header and notes line.

    ``state`` is one of off, stub, live, unavailable. ``turns`` says whether a
    turn may be sent: never while notes are down (a turn needs a kept note),
    never before stage 2 wires /mc/turns, never when off or unavailable."""
    if health.state == "off":
        state, header = "off", HEADER_OFF if notes_ok else HEADER_OFF_NO_NOTES
    elif health.state == "stub":
        state, header = "stub", HEADER_STUB
    elif health.state == "ready":
        state, header = "live", HEADER_LIVE
    else:
        why = UNAVAILABLE_WHY.get(health.reason or "", "your messages are kept as notes")
        state = "unavailable"
        header = f"Master Craftsman is unavailable · {why}"
    if not notes_ok and state != "off":
        header = HEADER_NO_NOTES
    replies = state in ("stub", "live") and TURNS_WIRED and notes_ok
    if state == "stub":
        notes = f"{NOTES_TEXT} " + ("Scripted stub replies, never Master Craftsman's." if replies
                                    else "Stub replies are not switched on yet.")
    elif state == "live":
        notes = f"{NOTES_TEXT} Master Craftsman {'replies on the record' if replies else 'does not reply yet'}."
    else:
        notes = f"{NOTES_TEXT} Master Craftsman does not reply."
    return {"state": state, "reason": health.reason if state == "unavailable" else None, "header": header,
            "notes_text": notes, "turns": replies, "observed_at": health.observed_at}


# ── keeping a reply ───────────────────────────────────────────────────────────

class NotAnAnswer(ValueError):
    """Only an ``answered`` result from a known backend is ever kept as a reply."""


def reply_author(result: TurnResult) -> Author:
    """Who a kept reply is from. Decided by the backend kind, never by the
    caller: a stub reply is the stub's, never Master Craftsman's."""
    if result.status != "answered":
        raise NotAnAnswer(f"a {result.status} turn is not an answer and is never kept as one")
    if result.backend_kind == "stub":
        return MASTER_CRAFTSMAN_STUB
    if result.backend_kind in REAL_KINDS:
        return MASTER_CRAFTSMAN
    raise NotAnAnswer(f"backend {result.backend_kind!r} cannot answer")


def keep_reply(floor_store, result: TurnResult, *, request_id: str, area=None, item_ref=None, page=None):
    """Keep an answered turn's reply as a note, with ``reply_author``'s author."""
    author = reply_author(result)
    return floor_store.add_note(request_id, result.text, author, area=area, item_ref=item_ref, page=page)
