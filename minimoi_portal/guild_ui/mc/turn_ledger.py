"""The durable Master Craftsman turn ledger (issue #346, P1; sql/007_mc_turn_ledger.sql).

Every ledger-enabled turn is recorded and claimed BEFORE it is dispatched:

- one running turn per owner, across conversations, restarts and workers. A transaction-scoped
  advisory lock orders the decision, and a partial unique index is the backstop;
- a claim carries a random token and a generation. Only that token can extend the lease or
  finish the turn: no process id or worker name is authority;
- a claim whose lease runs out is "unknown": its outcome is not known and the backend may still
  be working. It is never replayed. It sets a persisted pause (barrier) that only an
  authenticated Continue naming that entry can lift;
- an entry that already exists is never dispatched again: a finished one returns its earlier
  content-free outcome, and a running one says so. A replay must match the entry exactly
  (owner, conversation, both ids, kind, files and topic items); anything else is refused;
- a turn refused before dispatch is finished "not_sent" (no pause). A turn that failed or whose
  outcome is unknown after dispatch pauses the owner's turns until Continue.

Nothing here stores a message, reply, file or topic text: only ids, states and outcomes. The
lease clock is the database's, so host clock skew cannot shorten or extend a claim.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import re
import secrets
import threading
from typing import Callable

from ..topics import ITEM_RE, REV_RE, TOPIC_RE
from ..topic_context import REFS_MAX
from ..turn_files import DOC_ID_RE, IDS_MAX

LEASE_S = 120                     # a claim without a heartbeat for this long is abandoned
QUEUE_MAX = 5                     # waiting turns per owner (issue #346 P2)
QUEUE_TTL_S = 24 * 3600           # a waiting turn not sent within this is expired (never sent)
HEARTBEAT_S = 30
TERMINAL = ("answered", "failed", "unknown", "not_sent", "cancelled", "expired")
FINISH_STATES = ("answered", "failed", "unknown", "not_sent")
KINDS = ("typed", "voice")


class LedgerUnavailable(Exception):
    """The ledger could not be read or written; nothing was dispatched."""


@dataclass(frozen=True)
class Claim:
    ledger_id: int
    token: str
    generation: int


@dataclass
class Admission:
    """What admit() decided. Only result == "claimed" may dispatch."""
    result: str                               # claimed | busy | paused | running | done | queued |
                                              # queue_pending | queue_full | not_next | gone
    position: int | None = None               # for a queued entry: 1 is next
    entry: dict | None = None                 # the owner's relevant entry (content-free)
    claim: Claim | None = None
    barrier: dict | None = None
    released: list = field(default_factory=list)   # entries just found abandoned (now unknown)


def _default_connect(url: str):
    import psycopg2
    return psycopg2.connect(url, connect_timeout=4, options="-c statement_timeout=4000")


_COLS = ("id, principal, conversation_id, note_request_id, turn_request_id, kind, attachment_ids, topic_refs, "
         "state, claim_generation, lease_until, queued_at, created_at, updated_at, outcome")


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json(value):
    return value if not isinstance(value, str) else json.loads(value)


def _entry(row) -> dict:
    (lid, principal, conv, note_id, turn_id, kind, files, refs, state, generation,
     lease, queued, created, updated, outcome) = row
    return {"id": int(lid), "principal": principal, "conversation_id": conv, "note_request_id": note_id,
            "turn_request_id": turn_id, "kind": kind, "attachment_ids": _json(files) or [],
            "topic_refs": _json(refs) or [], "state": state, "claim_generation": int(generation),
            "lease_until": _iso(lease), "queued_at": _iso(queued), "created_at": _iso(created),
            "updated_at": _iso(updated), "outcome": _json(outcome)}


def _barrier(row) -> dict | None:
    if row is None:
        return None
    principal, ledger_id, reason, created = row
    return {"principal": principal, "ledger_id": int(ledger_id), "reason": reason, "created_at": _iso(created)}


_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_WORD = re.compile(r"[a-z][a-z0-9_]{0,39}")
# Outcome fields: content-free by construction. Anything else is refused, never stored.
_OUTCOME = {
    "turn_id": _ID, "backend_turn_id": _ID, "request_id": _ID,
    "backend_kind": re.compile(r"(?:cos|openclaw|grok|platform|stub)"),
    "status": _WORD, "failure_class": _WORD, "reason": _WORD,
    "reply_note_id": int, "http_status": int, "history_saved": bool,
}
_OPERATION = {"type": _WORD, "saved": bool, "id": int, "repeated": bool, "reason": _WORD, "state": _WORD}


def _check(spec, key, value):
    rule = spec.get(key) if isinstance(key, str) else None
    if rule is None:
        raise ValueError(f"outcome field {key!r} is not allowed")
    if value is None:
        return
    if rule is int:
        if type(value) is not int:
            raise ValueError(f"outcome field {key!r} must be an integer")
    elif rule is bool:
        if type(value) is not bool:
            raise ValueError(f"outcome field {key!r} must be true or false")
    elif not isinstance(value, str) or not rule.fullmatch(value):
        raise ValueError(f"outcome field {key!r} is not an id or code")


def _clean_outcome(outcome) -> dict:
    """An allowlisted, content-free outcome: ids, codes, numbers and flags only. Refuses the rest."""
    if outcome is None:
        return {}
    if not isinstance(outcome, dict):
        raise ValueError("outcome must be a dict")
    out = {}
    for key, value in outcome.items():
        if key == "operation":
            if value is None:
                out[key] = None
                continue
            if not isinstance(value, dict):
                raise ValueError("operation must be a dict")
            for k, v in value.items():
                _check(_OPERATION, k, v)
            out[key] = dict(value)
            continue
        _check(_OUTCOME, key, value)
        out[key] = value
    return out


def _clean_files(attachment_ids) -> list:
    files = list(attachment_ids or [])
    if len(files) > IDS_MAX or len(set(files)) != len(files) or \
            not all(isinstance(f, str) and DOC_ID_RE.fullmatch(f) for f in files):
        raise ValueError(f"attachment ids are up to {IDS_MAX} distinct document ids")
    return files


def _clean_refs(topic_refs) -> list:
    refs = list(topic_refs or [])
    if len(refs) > REFS_MAX:
        raise ValueError(f"at most {REFS_MAX} topic items")
    out = []
    for r in refs:
        if not isinstance(r, dict) or set(r) - {"topic_id", "item_id", "rev"} or \
                not isinstance(r.get("topic_id"), str) or not TOPIC_RE.fullmatch(r["topic_id"]) or \
                not isinstance(r.get("item_id"), str) or not ITEM_RE.fullmatch(r["item_id"]) or \
                (r.get("rev") is not None and (not isinstance(r["rev"], str) or not REV_RE.fullmatch(r["rev"]))):
            raise ValueError("a topic item is {topic_id, item_id, rev} with valid ids")
        out.append({"topic_id": r["topic_id"], "item_id": r["item_id"], "rev": r.get("rev")})
    if len({(r["topic_id"], r["item_id"]) for r in out}) != len(out):
        raise ValueError("topic items must be distinct")
    return out


class TurnLedger:
    def __init__(self, database_url: Callable[[], str | None], *, connect: Callable | None = None,
                 lease_s: int = LEASE_S, worker: str | None = None):
        self._database_url = database_url
        self._connect = connect or _default_connect
        self.lease_s = int(lease_s)
        self.worker = (worker or "worker")[:80]

    # ── connection ────────────────────────────────────────────────────────────
    @contextmanager
    def _tx(self, principal: str | None = None):
        url = self._database_url()
        if not url:
            raise LedgerUnavailable("not_configured")
        try:
            conn = self._connect(url)
        except Exception as exc:
            raise LedgerUnavailable("unavailable") from exc
        try:
            with conn:
                with conn.cursor() as cur:
                    if principal is not None:
                        # One decision at a time per owner, across every worker.
                        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("mc_turn:" + principal,))
                    yield cur
        except LedgerUnavailable:
            raise
        except (ValueError, TypeError):
            raise
        except Exception as exc:
            raise LedgerUnavailable("unavailable") from exc
        finally:
            conn.close()

    @staticmethod
    def _release_abandoned(cur, principal: str) -> list:
        """Claims past their lease become unknown (never replayed) and set the owner's pause."""
        cur.execute("UPDATE guild.mc_turn_ledger SET state = 'unknown', claim_token = NULL, lease_until = NULL, "
                    "updated_at = now(), outcome = COALESCE(outcome, '{}'::jsonb) || '{\"reason\": \"lease_expired\"}'::jsonb "
                    "WHERE principal = %s AND state = 'claimed' AND lease_until < now() RETURNING " + _COLS, (principal,))
        released = [_entry(r) for r in cur.fetchall()]
        for e in released:
            cur.execute("INSERT INTO guild.mc_turn_barrier (principal, ledger_id, reason) VALUES (%s, %s, 'unknown') "
                        "ON CONFLICT (principal) DO NOTHING", (principal, e["id"]))
        return released

    # ── admission ─────────────────────────────────────────────────────────────
    @staticmethod
    def _validate(principal, conversation_id, note_request_id, turn_request_id, kind, attachment_ids, topic_refs):
        for name, value in (("principal", principal), ("conversation_id", conversation_id),
                            ("note_request_id", note_request_id), ("turn_request_id", turn_request_id)):
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError(f"{name} is required")
        if kind not in KINDS:
            raise ValueError("unknown turn kind")
        return _clean_files(attachment_ids), _clean_refs(topic_refs)

    @staticmethod
    def _expire_waiting(cur, principal: str) -> int:
        """A waiting turn not sent within QUEUE_TTL_S of being queued is expired (reason queue_expired).
        The waiting turns queued after it are then cancelled (reason earlier_expired): a later
        message must never run without the earlier one it followed, and they did not themselves
        wait that long. Neither was sent; neither sets a pause."""
        cur.execute("UPDATE guild.mc_turn_ledger SET state = 'expired', updated_at = now(), "
                    "outcome = COALESCE(outcome, '{}'::jsonb) || '{\"reason\": \"queue_expired\"}'::jsonb "
                    "WHERE principal = %s AND state = 'queued' AND queued_at < now() - make_interval(secs => %s) "
                    "RETURNING id", (principal, QUEUE_TTL_S))
        expired = [r[0] for r in cur.fetchall()]
        if not expired:
            return 0
        cur.execute("UPDATE guild.mc_turn_ledger SET state = 'cancelled', updated_at = now(), "
                    "outcome = COALESCE(outcome, '{}'::jsonb) || '{\"reason\": \"earlier_expired\"}'::jsonb "
                    "WHERE principal = %s AND state = 'queued' AND id > %s", (principal, min(expired)))
        return len(expired) + (cur.rowcount or 0)

    @staticmethod
    def _position(cur, principal: str, ledger_id: int) -> int:
        cur.execute("SELECT count(*) FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'queued' AND id <= %s",
                    (principal, ledger_id))
        return int(cur.fetchone()[0])

    def _existing(self, cur, principal, conversation_id, note_request_id, turn_request_id, kind, files, refs):
        """An entry already using these ids: its admission (never a new dispatch), or None."""
        cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE note_request_id = %s OR turn_request_id = %s "
                    "ORDER BY id LIMIT 1", (note_request_id, turn_request_id))
        row = cur.fetchone()
        if row is None:
            return None
        earlier = _entry(row)
        same = (earlier["principal"] == principal and earlier["conversation_id"] == conversation_id
                and earlier["note_request_id"] == note_request_id and earlier["turn_request_id"] == turn_request_id
                and earlier["kind"] == kind and earlier["attachment_ids"] == files and earlier["topic_refs"] == refs)
        if not same:
            # Never answer with an unrelated earlier outcome, and never dispatch it again.
            raise ValueError("request_mismatch: these ids were already used for a different turn")
        if earlier["state"] == "queued":
            return Admission("queued", entry=earlier, position=self._position(cur, principal, earlier["id"]))
        return Admission("running" if earlier["state"] == "claimed" else "done", entry=earlier)

    def admit(self, *, principal: str, conversation_id: str, note_request_id: str, turn_request_id: str,
              kind: str = "typed", attachment_ids: list | None = None, topic_refs: list | None = None) -> Admission:
        """Record and claim this turn before dispatch, or say why not. A refusal records nothing.
        While the owner has waiting turns, a new immediate turn is refused (queue_pending): it
        would jump the queue."""
        files, refs = self._validate(principal, conversation_id, note_request_id, turn_request_id, kind,
                                     attachment_ids, topic_refs)
        token = secrets.token_hex(16)
        with self._tx(principal) as cur:
            released = self._release_abandoned(cur, principal)
            self._expire_waiting(cur, principal)
            known = self._existing(cur, principal, conversation_id, note_request_id, turn_request_id, kind, files, refs)
            if known is not None:
                known.released = released
                return known
            cur.execute("SELECT principal, ledger_id, reason, created_at FROM guild.mc_turn_barrier WHERE principal = %s",
                        (principal,))
            barrier = _barrier(cur.fetchone())
            if barrier is not None:
                return Admission("paused", barrier=barrier, released=released)
            cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'claimed'",
                        (principal,))
            active = cur.fetchone()
            if active is not None:
                return Admission("busy", entry=_entry(active), released=released)
            cur.execute("SELECT count(*) FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'queued'", (principal,))
            if int(cur.fetchone()[0]) > 0:
                return Admission("queue_pending", released=released)
            cur.execute("INSERT INTO guild.mc_turn_ledger (principal, conversation_id, note_request_id, turn_request_id, "
                        "kind, attachment_ids, topic_refs, state, claim_token, claim_generation, claimed_by, lease_until) "
                        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, 'claimed', %s, 1, %s, "
                        "now() + make_interval(secs => %s)) RETURNING " + _COLS,
                        (principal, conversation_id, note_request_id, turn_request_id, kind, json.dumps(files),
                         json.dumps(refs), token, self.worker, self.lease_s))
            entry = _entry(cur.fetchone())
            return Admission("claimed", entry=entry, claim=Claim(entry["id"], token, entry["claim_generation"]),
                             released=released)

    # ── the queue (P2): waiting turns, sent strictly in order ─────────────────
    def enqueue(self, *, principal: str, conversation_id: str, note_request_id: str, turn_request_id: str,
                attachment_ids: list | None = None, topic_refs: list | None = None) -> Admission:
        """Record a typed turn as waiting (state queued). Owner-wide FIFO; at most QUEUE_MAX waiting.
        Nothing is dispatched here. An exact replay returns the same entry; a mismatch is refused."""
        files, refs = self._validate(principal, conversation_id, note_request_id, turn_request_id, "typed",
                                     attachment_ids, topic_refs)
        with self._tx(principal) as cur:
            released = self._release_abandoned(cur, principal)
            self._expire_waiting(cur, principal)
            known = self._existing(cur, principal, conversation_id, note_request_id, turn_request_id, "typed", files, refs)
            if known is not None:
                known.released = released
                return known
            cur.execute("SELECT count(*) FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'queued'", (principal,))
            if int(cur.fetchone()[0]) >= QUEUE_MAX:
                return Admission("queue_full", released=released)
            cur.execute("INSERT INTO guild.mc_turn_ledger (principal, conversation_id, note_request_id, turn_request_id, "
                        "kind, attachment_ids, topic_refs, state, queued_at) "
                        "VALUES (%s, %s, %s, %s, 'typed', %s::jsonb, %s::jsonb, 'queued', now()) RETURNING " + _COLS,
                        (principal, conversation_id, note_request_id, turn_request_id, json.dumps(files), json.dumps(refs)))
            entry = _entry(cur.fetchone())
            cur.execute("SELECT principal, ledger_id, reason, created_at FROM guild.mc_turn_barrier WHERE principal = %s",
                        (principal,))
            return Admission("queued", entry=entry, position=self._position(cur, principal, entry["id"]),
                             barrier=_barrier(cur.fetchone()), released=released)

    def claim_queued(self, principal: str, ledger_id: int) -> Admission:
        """Claim the owner's next waiting turn, only if it is this one and nothing is running or paused."""
        if type(ledger_id) is not int or ledger_id <= 0:
            raise ValueError("ledger_id is required")
        token = secrets.token_hex(16)
        with self._tx(principal) as cur:
            released = self._release_abandoned(cur, principal)
            self._expire_waiting(cur, principal)
            cur.execute("SELECT principal, ledger_id, reason, created_at FROM guild.mc_turn_barrier WHERE principal = %s",
                        (principal,))
            barrier = _barrier(cur.fetchone())
            if barrier is not None:
                return Admission("paused", barrier=barrier, released=released)
            cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'claimed'",
                        (principal,))
            active = cur.fetchone()
            if active is not None:
                return Admission("busy", entry=_entry(active), released=released)
            cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE id = %s AND principal = %s", (ledger_id, principal))
            row = cur.fetchone()
            if row is None or _entry(row)["state"] != "queued":
                return Admission("gone", entry=_entry(row) if row else None, released=released)
            cur.execute("SELECT min(id) FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'queued'", (principal,))
            if int(cur.fetchone()[0]) != ledger_id:
                return Admission("not_next", entry=_entry(row), position=self._position(cur, principal, ledger_id),
                                 released=released)
            cur.execute("UPDATE guild.mc_turn_ledger SET state = 'claimed', claim_token = %s, "
                        "claim_generation = claim_generation + 1, claimed_by = %s, "
                        "lease_until = now() + make_interval(secs => %s), updated_at = now() "
                        "WHERE id = %s AND state = 'queued' RETURNING " + _COLS,
                        (token, self.worker, self.lease_s, ledger_id))
            entry = _entry(cur.fetchone())
            return Admission("claimed", entry=entry, claim=Claim(entry["id"], token, entry["claim_generation"]),
                             released=released)

    def requeue(self, claim: Claim) -> bool:
        """A turn claimed FROM the queue was refused before anything was sent (MC unavailable, a
        transient store error): put it back at the head of the queue, same id and place, so no later
        message overtakes it. Only the current claim can; anything else returns False."""
        with self._tx() as cur:
            cur.execute("SELECT principal FROM guild.mc_turn_ledger WHERE id = %s", (claim.ledger_id,))
            row = cur.fetchone()
            if row is None:
                return False
        with self._tx(row[0]) as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET state = 'queued', claim_token = NULL, lease_until = NULL, "
                        "updated_at = now() WHERE id = %s AND state = 'claimed' AND claim_token = %s "
                        "AND claim_generation = %s AND queued_at IS NOT NULL RETURNING id",
                        (claim.ledger_id, claim.token, claim.generation))
            return cur.fetchone() is not None

    def cancel(self, principal: str, ledger_id: int) -> dict:
        """Cancel a waiting turn. Anything already claimed, finished or unknown cannot be cancelled."""
        if type(ledger_id) is not int or ledger_id <= 0:
            raise ValueError("ledger_id is required")
        with self._tx(principal) as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET state = 'cancelled', updated_at = now() "
                        "WHERE id = %s AND principal = %s AND state = 'queued' RETURNING id", (ledger_id, principal))
            if cur.fetchone() is not None:
                return {"result": "cancelled", "ledger_id": ledger_id}
            cur.execute("SELECT state FROM guild.mc_turn_ledger WHERE id = %s AND principal = %s", (ledger_id, principal))
            row = cur.fetchone()
        return {"result": "not_queued", "ledger_id": ledger_id, "state": row[0] if row else None}

    # ── while running ─────────────────────────────────────────────────────────
    def heartbeat(self, claim: Claim) -> bool:
        """Extend the lease; False when this claim no longer holds the turn (expired or finished)."""
        with self._tx() as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET lease_until = now() + make_interval(secs => %s), updated_at = now() "
                        "WHERE id = %s AND state = 'claimed' AND claim_token = %s AND claim_generation = %s "
                        "AND lease_until >= now() RETURNING id",
                        (self.lease_s, claim.ledger_id, claim.token, claim.generation))
            return cur.fetchone() is not None

    @contextmanager
    def held(self, claim: Claim, *, interval_s: float = HEARTBEAT_S):
        """Heartbeat in the background while the caller runs the turn."""
        stop = threading.Event()

        def beat():
            while not stop.wait(interval_s):
                try:
                    if not self.heartbeat(claim):
                        return
                except LedgerUnavailable:
                    continue
        thread = threading.Thread(target=beat, name=f"mc-ledger-{claim.ledger_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()

    def finish(self, claim: Claim, state: str, outcome: dict | None = None) -> bool:
        """Record the turn's outcome. Only the current claim can; a late finish after the lease was
        taken over changes nothing and returns False.

        not_sent: refused before anything was dispatched (no pause). failed or unknown: after
        dispatch, which pauses the owner's turns until an authenticated Continue."""
        if state not in FINISH_STATES:
            raise ValueError("unknown finish state")
        clean = _clean_outcome(outcome)
        with self._tx() as cur:
            cur.execute("SELECT principal FROM guild.mc_turn_ledger WHERE id = %s", (claim.ledger_id,))
            row = cur.fetchone()
            if row is None:
                return False
        principal = row[0]
        with self._tx(principal) as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET state = %s, claim_token = NULL, lease_until = NULL, "
                        "updated_at = now(), outcome = COALESCE(outcome, '{}'::jsonb) || %s::jsonb "
                        "WHERE id = %s AND state = 'claimed' AND claim_token = %s AND claim_generation = %s "
                        "RETURNING " + _COLS, (state, json.dumps(clean), claim.ledger_id, claim.token, claim.generation))
            done = cur.fetchone()
            if done is None:
                return False
            entry = _entry(done)
            if state in ("failed", "unknown"):
                cur.execute("INSERT INTO guild.mc_turn_barrier (principal, ledger_id, reason) VALUES (%s, %s, %s) "
                            "ON CONFLICT (principal) DO NOTHING", (principal, entry["id"], state))
            return True

    # ── owner view and Continue ───────────────────────────────────────────────
    def status(self, principal: str, *, recent: int = 10) -> dict:
        """The owner's running entry, pause, waiting entries in order and most recent entries (content-free)."""
        with self._tx(principal) as cur:
            released = self._release_abandoned(cur, principal)
            self._expire_waiting(cur, principal)
            cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'queued' "
                        "ORDER BY id", (principal,))
            waiting = [_entry(r) for r in cur.fetchall()]
            cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE principal = %s AND state = 'claimed'",
                        (principal,))
            running = cur.fetchone()
            cur.execute("SELECT principal, ledger_id, reason, created_at FROM guild.mc_turn_barrier WHERE principal = %s",
                        (principal,))
            barrier = _barrier(cur.fetchone())
            cur.execute("SELECT " + _COLS + " FROM guild.mc_turn_ledger WHERE principal = %s ORDER BY id DESC LIMIT %s",
                        (principal, max(1, min(int(recent), 50))))
            entries = [_entry(r) for r in cur.fetchall()]
        return {"running": _entry(running) if running else None, "pause": barrier,
                "queued": waiting, "recent": entries, "released": released}

    def continue_after(self, principal: str, ledger_id: int) -> dict:
        """Lift the owner's pause, only when it names this entry. The earlier work's outcome stays
        unknown: it may have run, or may still be running."""
        if type(ledger_id) is not int or ledger_id <= 0:
            raise ValueError("ledger_id is required")
        with self._tx(principal) as cur:
            cur.execute("DELETE FROM guild.mc_turn_barrier WHERE principal = %s AND ledger_id = %s RETURNING ledger_id, reason",
                        (principal, ledger_id))
            row = cur.fetchone()
        if row is None:
            return {"result": "not_paused"}
        return {"result": "continued", "ledger_id": int(row[0]), "reason": row[1],
                "message": ("Continuing. The earlier message's outcome is still unknown: it may have run, "
                            "or may still be running.") if row[1] == "unknown" else "Continuing after the failed message."}
