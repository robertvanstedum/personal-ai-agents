"""The shared voice budget: a durable, concurrent, daily admission cap on paid speech (text-to-speech and transcription sessions) before any provider call.

What it is. Every paid speech call that goes through this module first RESERVES a conservative allowance against today's cap. The cap is a count of reserved allowance,
not a bill: the provider's real charge is not read back, and the page must say so. A day is the America/Chicago calendar day (DST included), so the allowance renews at
local midnight and survives restarts, because the counter lives in the database, not in memory.

How admission is atomic. One row per day holds the reserved total; admission is a single conditional UPDATE (``reserved + amount <= cap``) in the same transaction as the
audit entry. Postgres serialises concurrent updaters on that row and re-checks the condition after the lock, SQLite has one writer at a time; either way two callers (threads,
processes, or the portal and the cos-scheduler container) can never both pass the same last dollar.

What it does not do, on purpose:
  * It never takes a rebate from the client. Nothing here is reachable from a browser; a reservation is released only by server code, and only when the provider definitely
    rejected the request (an HTTP 4xx answer). A timeout, a dropped connection or a 5xx may have been billed, so the allowance stays reserved. Nothing retries.
  * It does not claim a hard billing cap from a browser timer. A browser-to-provider session cannot be stopped from here and the provider allows a session to run up to
    60 minutes, so a session reserves the provider-bound exposure (rate x 60 minutes), not the page's own 15-minute timer. The allowances are labelled estimates of
    provider exposure; the provider's real charge is never read back.
  * It never stores audio, text, transcripts or a raw identity: an entry holds the day, time, domain, kind, a short one-way hash of the principal, and micro-dollars.
  * It fails closed: if the ledger cannot be read or written, the call is refused (503) and the provider is not contacted.

Activation. MC voice and dictation REQUIRE it (they call ``admit(..., required=True)``, so they cannot run uncapped). Existing Chief of Staff voice routes call it with
``required=False`` and are unchanged until ``MINIMOI_VOICE_BUDGET=1`` is set in that service's environment.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
MICRO = 1_000_000
ENV_ENFORCE = "MINIMOI_VOICE_BUDGET"            # "1": existing (not-required) callers are gated too
ENV_DSN = "MINIMOI_VOICE_BUDGET_DSN"            # "sqlite:////abs/path.db" (tests/local) or a Postgres URL; default: the platform DATABASE_URL
LIMIT_ID = "voice.daily_cap_usd"                # in core.limits; the default and hard bounds live there
DEFAULT_CAP_USD = 20.0

# Reserved allowances. They are estimates of the provider's exposure, labelled "reserved allowance" everywhere and never shown as cost. Sources (fetched by Codex,
# 13 Oct 2026): developers.openai.com/api/docs/pricing (gpt-live-transcribe $0.017/min, gpt-4o-transcribe and whisper-1 $0.006/min, gpt-4o-mini-tts priced in tokens,
# about $0.015/min of speech) and developers.openai.com/api/docs/guides/realtime-conversations (a Realtime session lasts up to 60 minutes). Re-check before changing.
REALTIME_SESSION_MAX_MINUTES = 60                # the provider's own maximum; a browser-held session cannot be stopped from here, whatever the page timer says
TRANSCRIPTION_LIVE_USD_PER_MINUTE = 0.017        # gpt-live-transcribe, the model the shared transcription credential asks for: the published rate, with NO safety multiple
TTS_ALLOWANCE_USD_PER_1K_CHARS = 0.05            # ~1000 chars is ~1.1 min of speech; the published estimate is ~$0.015/min, so this is ~3x for speech only
UPLOAD_TRANSCRIPTION_USD_PER_MINUTE = 0.006      # whisper-1
UPLOAD_ASSUMED_BYTES_PER_SECOND = 750            # ~6 kbps, a very low bitrate: a file's duration cannot be read from its size, so this is an assumption, not a bound
UPLOAD_MAX_BYTES = 5 * 1024 * 1024               # refused above this when the budget is enforced
UPLOAD_TRANSCRIPTION_MIN_USD = 0.02
LABEL = "reserved allowance: the provider's exposure estimate, counted when a session or reading starts; not a bill and not a guaranteed spending limit"


class VoiceBudgetError(RuntimeError):
    status = 503
    public = "Voice is paused: the daily voice allowance could not be checked."


class BudgetExhausted(VoiceBudgetError):
    status = 429

    def __init__(self, day: str, reserved: int, cap: int, asked: int):
        self.day, self.reserved, self.cap, self.asked = day, reserved, cap, asked
        self.public = (f"Today's voice allowance is used up (${reserved / MICRO:.2f} of ${cap / MICRO:.2f} reserved; "
                       f"{LABEL}). It renews at midnight Chicago time.")
        super().__init__(self.public)


class BudgetUnavailable(VoiceBudgetError):
    status = 503

    def __init__(self, why: str = "ledger unavailable"):
        self.why = why
        self.public = "Voice is paused: the daily voice allowance could not be checked, so nothing was sent to the speech provider."
        super().__init__(self.public)


def micro(usd: float) -> int:
    return int(round(float(usd) * MICRO))


def tts_allowance_micro(text: str) -> int:
    return max(micro(0.01), -(-len(text or "") * micro(TTS_ALLOWANCE_USD_PER_1K_CHARS) // 1000))


def session_allowance_micro(minutes: float = REALTIME_SESSION_MAX_MINUTES) -> int:
    """A transcription session: the published live-transcription rate times the provider's maximum session length (60 min = $1.02), not the page's 15-minute timer."""
    return micro(TRANSCRIPTION_LIVE_USD_PER_MINUTE) * max(1, int(-(-float(minutes) // 1)))


def upload_allowance_micro(nbytes: int) -> int:
    """An uploaded clip: ESTIMATED from its size at a very low assumed bitrate. The clip's real duration is not read, so this is an estimate and not a spend bound."""
    minutes = max(1, -(-int(nbytes) // (UPLOAD_ASSUMED_BYTES_PER_SECOND * 60)))
    return max(micro(UPLOAD_TRANSCRIPTION_MIN_USD), minutes * micro(UPLOAD_TRANSCRIPTION_USD_PER_MINUTE))


def chicago_day(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(CHICAGO).date().isoformat()


def enforced(environ=None) -> bool:
    return (os.environ if environ is None else environ).get(ENV_ENFORCE) == "1"


def effective_cap_usd(environ=None) -> float:
    """The cap in force now: the limits registry's value for ``voice.daily_cap_usd`` (default $20, hard-bounded), read from the registry file. A missing or damaged file is the default, never 'no cap'."""
    from core.limits import Limits
    environ = os.environ if environ is None else environ
    return Limits(environ.get("MINIMOI_LIMITS_DIR", "/app/runtime/guild/limits")).value(LIMIT_ID)


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS voice_budget_day (
        day TEXT PRIMARY KEY,
        cap_micro BIGINT NOT NULL CHECK (cap_micro >= 0),
        reserved_micro BIGINT NOT NULL DEFAULT 0 CHECK (reserved_micro >= 0),
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS voice_budget_entry (
        id TEXT PRIMARY KEY,
        day TEXT NOT NULL REFERENCES voice_budget_day(day),
        at TEXT NOT NULL,
        domain TEXT NOT NULL,
        kind TEXT NOT NULL,
        who TEXT NOT NULL,
        micro BIGINT NOT NULL CHECK (micro > 0),
        state TEXT NOT NULL CHECK (state IN ('reserved', 'released')))""",
    "CREATE INDEX IF NOT EXISTS voice_budget_entry_day ON voice_budget_entry (day)",
)


def default_dsn(environ=None) -> str | None:
    environ = os.environ if environ is None else environ
    if environ.get(ENV_DSN):
        return environ[ENV_DSN]
    try:
        from core.get_secret import get_secret
        return get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)
    except Exception:
        return None


class Reservation:
    def __init__(self, budget, rid: str, day: str, amount: int, kind: str, domain: str):
        self._budget, self.id, self.day, self.micro, self.kind, self.domain = budget, rid, day, amount, kind, domain
        self.released = False

    @property
    def usd(self) -> float:
        return self.micro / MICRO

    def release(self) -> None:
        """Server-side only, and only after the provider definitely rejected the request. Never reachable from a browser."""
        if self.released:
            return
        self.released = self._budget._release(self)


class _Noop(Reservation):
    def __init__(self):
        super().__init__(None, "", "", 0, "", "")

    def release(self) -> None:
        return


def definite_rejection(exc: BaseException) -> bool:
    """True only when the provider answered with a 4xx: it refused before doing billable work. Timeouts, resets and 5xx are NOT definite (they may have been billed)."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status <= 499


class VoiceBudget:
    def __init__(self, dsn: str | None = None, *, clock=None, cap_usd=None, environ=None):
        self._dsn = dsn
        self._environ = environ
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cap_usd = cap_usd or (lambda: effective_cap_usd(environ))

    # ── plumbing ──
    def _connect(self):
        dsn = self._dsn or default_dsn(self._environ)
        if not dsn:
            raise BudgetUnavailable("no database configured")
        if dsn.startswith("sqlite:///"):
            conn = sqlite3.connect(dsn[len("sqlite:///"):], timeout=10, isolation_level=None)
            return conn, "?", True
        import psycopg2
        # The application role owns Guild, not public. Both portal and CoS
        # must address the same ledger regardless of their default search path.
        conn = psycopg2.connect(dsn, connect_timeout=4,
                               options="-c statement_timeout=4000 -c search_path=guild,pg_catalog")
        return conn, "%s", False

    @contextlib.contextmanager
    def _tx(self):
        try:
            conn, ph, lite = self._connect()
        except BudgetUnavailable:
            raise
        except Exception as exc:
            raise BudgetUnavailable(type(exc).__name__) from None
        try:
            if lite:
                conn.execute("BEGIN IMMEDIATE")

            def run(sql, params=()):
                cur = conn.cursor()
                cur.execute(sql.replace("?", ph), params)
                return cur
            yield run
            conn.commit() if not lite else conn.execute("COMMIT")
        except VoiceBudgetError:
            with contextlib.suppress(Exception):
                conn.rollback() if not lite else conn.execute("ROLLBACK")
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback() if not lite else conn.execute("ROLLBACK")
            raise BudgetUnavailable(type(exc).__name__) from None
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    def ensure_schema(self) -> None:
        """Explicit one-time creation (the operator runs ``python -m core.realtime_voice.budget --init``); never done implicitly on a request."""
        with self._tx() as run:
            if not (self._dsn or default_dsn(self._environ) or "").startswith("sqlite:///"):
                # IF NOT EXISTS still checks database CREATE privileges on
                # PostgreSQL. The live role only needs its existing schema.
                if run("SELECT 1 FROM pg_namespace WHERE nspname = 'guild'").fetchone() is None:
                    run("CREATE SCHEMA guild")
            for statement in SCHEMA:
                run(statement)

    # ── admission ──
    def reserve(self, *, kind: str, domain: str, principal: str, amount_micro: int) -> Reservation:
        if amount_micro <= 0:
            raise ValueError("a reservation needs a positive amount")
        now = self._clock()
        day = chicago_day(now)
        cap_now = micro(self._cap_usd())
        who = hashlib.sha256(("voice-budget:" + str(principal)).encode()).hexdigest()[:16]
        rid = uuid.uuid4().hex
        refused = None
        with self._tx() as run:
            run("INSERT INTO voice_budget_day (day, cap_micro, reserved_micro, created_at) VALUES (?, ?, 0, ?) ON CONFLICT (day) DO NOTHING",
                (day, cap_now, now.astimezone(timezone.utc).isoformat()))
            # Persist lower limits even if this call is refused. Higher limits
            # apply tomorrow. Admission compares against the row itself so a
            # concurrent PostgreSQL writer cannot leave us using a stale cap.
            run("UPDATE voice_budget_day SET cap_micro = ? WHERE day = ? AND cap_micro > ?", (cap_now, day, cap_now))
            took = run("UPDATE voice_budget_day SET reserved_micro = reserved_micro + ? WHERE day = ? AND reserved_micro + ? <= cap_micro",
                       (amount_micro, day, amount_micro))
            if took.rowcount != 1:
                cap, reserved = run("SELECT cap_micro, reserved_micro FROM voice_budget_day WHERE day = ?", (day,)).fetchone()
                refused = BudgetExhausted(day, int(reserved), int(cap), amount_micro)
            else:
                run("INSERT INTO voice_budget_entry (id, day, at, domain, kind, who, micro, state) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved')",
                    (rid, day, now.astimezone(timezone.utc).isoformat(), str(domain)[:40], str(kind)[:40], who, amount_micro))
        # Raise after commit: rolling back a refusal would undo a lowered cap.
        if refused is not None:
            raise refused
        return Reservation(self, rid, day, amount_micro, kind, domain)

    def _release(self, res: Reservation) -> bool:
        with self._tx() as run:
            flipped = run("UPDATE voice_budget_entry SET state = 'released' WHERE id = ? AND state = 'reserved'", (res.id,))
            if flipped.rowcount != 1:
                return True
            run("UPDATE voice_budget_day SET reserved_micro = reserved_micro - ? WHERE day = ? AND reserved_micro >= ?", (res.micro, res.day, res.micro))
        return True

    def status(self) -> dict:
        """Today's position, read-only. Raises BudgetUnavailable if the ledger cannot be read."""
        now = self._clock()
        day = chicago_day(now)
        cap_now = micro(self._cap_usd())
        with self._tx() as run:
            row = run("SELECT cap_micro, reserved_micro FROM voice_budget_day WHERE day = ?", (day,)).fetchone()
        cap = min(int(row[0]), cap_now) if row else cap_now
        reserved = int(row[1]) if row else 0
        return {"day": day, "timezone": "America/Chicago", "cap_usd": cap / MICRO, "reserved_usd": reserved / MICRO,
                "remaining_usd": max(0, cap - reserved) / MICRO, "label": LABEL}


_default: VoiceBudget | None = None


def default_budget() -> VoiceBudget:
    global _default
    if _default is None:
        _default = VoiceBudget()
    return _default


@contextlib.contextmanager
def admit(*, kind: str, domain: str, principal: str, amount_micro: int, required: bool, budget: VoiceBudget | None = None, environ=None):
    """Reserve before the provider call; keep the reservation unless the provider definitely rejected it.

    ``required=True`` (MC voice, dictation): always enforced. ``required=False`` (existing routes): enforced only when ``MINIMOI_VOICE_BUDGET=1``.
    Raises BudgetExhausted (429) or BudgetUnavailable (503) BEFORE the body runs, so the provider is never contacted. An exception from the body that is a definite
    provider rejection releases the allowance; anything else (timeout, 5xx, a client that hangs up mid-stream) leaves it reserved. Nothing here retries."""
    if not required and not enforced(environ):
        yield _Noop()
        return
    res = (budget or default_budget()).reserve(kind=kind, domain=domain, principal=principal, amount_micro=amount_micro)
    try:
        yield res
    except BaseException as exc:
        if definite_rejection(exc):
            with contextlib.suppress(Exception):
                res.release()
        raise


def public_status(*, budget: VoiceBudget | None = None) -> dict:
    """What a capabilities response may show: today's cap and reserved allowance, honestly labelled, or an unavailable marker. Never raises."""
    try:
        return {"ok": True, **(budget or default_budget()).status()}
    except VoiceBudgetError as exc:
        return {"ok": False, "error": exc.public, "label": LABEL}


if __name__ == "__main__":      # pragma: no cover - operator entry point
    import sys
    if "--init" in sys.argv:
        VoiceBudget().ensure_schema()
        print("voice budget tables ready")
    else:
        print(public_status())
