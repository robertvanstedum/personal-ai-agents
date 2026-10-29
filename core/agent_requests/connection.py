"""The connection manager (MC front door; SCAFFOLDING until a real idle-to-MC round trip has proved a provider).

One deterministic pass (``step``) per agent: ask the provider whether its configured, explicitly identified session is reachable, and, only when it is IDLE and a
request is waiting, hand it ONE notice. No model is called by the manager, and a pass with nothing to deliver makes no provider call except a cheap ``probe``.

Rules (design 2026-10-08 sections 7 and 12; Codex's acceptance criteria of 11 Oct):
* **Transport acceptance is not a receipt.** ``accepted`` means the transport took the notice. Only the agent's own ``receipt`` (written by the existing helper) proves
  pickup, and only its ``result`` answers. The manager never writes either.
* **Never resend a possibly accepted turn.** The intent is written (and fsynced) BEFORE the provider is called. After a crash an intent with no outcome becomes ``unknown`` and
  is not resent; an ``accepted`` notice with no receipt is not resent either (a provider that can deduplicate by correlation id may declare it and gets one resend of the same
  correlation). Only a definite ``refused``/``failed`` (not taken) is retried, on the backoff schedule, at most five times.
* **Busy queues.** A busy session is never interrupted or steered; the request waits and is delivered after its turn.
* **One owner, no silent fresh session.** An exclusive lease per agent (a held file lock) refuses a second manager; the configured session id is recorded and a different
  one is refused unless adoption is explicit and recorded.
* **Honest listening.** The ``.armed`` marker is touched only while the provider reports idle or busy, so the portal's "listening" means a live session, not a hope.
* The notice carries validated tokens only (request id, sender, recipient, how to read, receipt and report); never the request's own words.
* **The census** (host runtime spec v0.5 §6.6; ``core/mc_delegation_runner/census.py``): given the host runtime's census folder, the manager writes the session's
  root row at each start, beside its launch record and before the spawn (``start_path: connection``, its route, the launch record's names and digests, and where
  the vendor keeps the transcript), and a state change each time a pass sees one (running, idle, offline, unknown). It never writes content. The census never
  gates the session (CEN-6, R6.6-e): a row that cannot be written now is tried again each pass, and the gap is named in status.json (``census``) meanwhile.
Standard library only. Nothing here starts a session: a provider's ``start`` is called only by an operator action (scripts/agent_connection.py --start)."""
from __future__ import annotations

import fcntl
import json
from datetime import datetime
import os
import re
import secrets
import shlex
import sys
import time
import uuid
from dataclasses import dataclass

from core.agent_requests import ledger as L

BACKOFF_S = (2, 10, 30, 120, 300)
MAX_ATTEMPTS = 5
USAGE_LOG_MAX = 4 * 1024 * 1024            # a day's usage file; larger fails closed (usage_log_damaged)
MAX_HISTORY_BYTES = 64 * 1024 * 1024        # the complete attempt history is always read; beyond this the manager says "too large" and stops, it never reads a prefix
GAP_NOTICE_S = 120          # a pass this long after the last one means the host slept or the process was suspended
PROBE_STATES = ("offline", "idle", "busy", "unknown")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,99}$")       # any provider's plain session/thread id (Claude: a UUID; Codex: its thread id)


class LeaseHeld(RuntimeError):
    """Another manager already owns this agent's connection."""


class StateUnreadable(RuntimeError):
    """A file the manager depends on exists but cannot be read or understood (unreadable, corrupt, or too large to read completely). Fail closed: only a MISSING file means "new";
    treating damage as empty history could replay work the session already accepted. The pass reports ``blocked`` and delivers nothing until an operator repairs or archives it."""


class IdentityMismatch(RuntimeError):
    """The configured session is not the one recorded for this agent. A fresh session is never started silently."""


@dataclass
class Probe:
    state: str
    session_ref: str = ""
    attention: str = ""          # a plain reason the owner should look (identity mismatch, an approval the manager will not give, a failed turn); never silent


@dataclass
class Delivery:
    status: str          # accepted | refused | failed | unknown
    native_ref: str = ""
    reason: str = ""


class Provider:
    """What a transport must offer. ``probe`` must NOT call a model. ``deliver`` hands one notice over and says honestly what happened; when it cannot tell whether the
    notice was taken it answers ``unknown``. A provider that can deduplicate a repeated correlation id sets ``dedupes_by_correlation``."""
    name = "provider"
    dedupes_by_correlation = False

    def probe(self) -> Probe:
        raise NotImplementedError

    def deliver(self, notice: str, correlation: str) -> Delivery:
        raise NotImplementedError

    def close(self) -> None:
        return None

    # The launch record (host runtime spec v0.5 R6.7-d step 4): a provider that starts its own process hands each
    # start's record to its manager, which appends it to connection/<agent>/launch.jsonl (the census seam, S1-F). A
    # record made before a manager is attached (Codex's first --create) is kept and handed over on attach. A sink that
    # fails raises, and the start is refused before any spawn. Names and digests only, never a value.
    launch_sink = None

    def attach_launch_sink(self, sink) -> None:
        self.launch_sink = sink
        pending = self.__dict__.setdefault("_unsent_launches", [])
        while pending:
            sink(pending[0])
            pending.pop(0)

    def _launched(self, record: dict) -> None:
        if self.launch_sink is None:
            self.__dict__.setdefault("_unsent_launches", []).append(record)
        else:
            self.launch_sink(record)


DEFAULT_HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "agent_requests.py")


def helper_commands(*, root: str, agent: str, session_id: str, request_id: str, python: str, helper: str) -> dict[str, list[str]]:
    """The exact argv for every helper call the session makes, with an absolute interpreter, an absolute helper and the manager's OWN ledger root on every one of them:
    a manager on an isolated ledger can never send its session to the default one."""
    base = [python, helper, "--root", root]
    return {"show": base + ["show", request_id],
            "receipt": base + ["receipt", request_id, "--as", agent, "--session", session_id],
            "result": base + ["result", request_id, "--as", agent, "--session", session_id],
            "needs-you": base + ["needs-you", request_id, "--as", agent, "--session", session_id]}


def read_identity(root: str, agent: str) -> dict:
    """The recorded identity for an agent without taking the lease: {} when none exists, StateUnreadable when the file is damaged (never treated as absent)."""
    path = os.path.join(root, "connection", agent, "identity.json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(64 * 1024 + 1)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise StateUnreadable(f"identity.json could not be read ({type(exc).__name__})") from None
    try:
        doc = json.loads(raw.decode("utf-8")) if len(raw) <= 64 * 1024 else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        raise StateUnreadable("identity.json is damaged; it was not overwritten")
    return doc


def connection_notice(request: dict, *, agent: str, session_id: str, root: str, python: str = sys.executable, helper: str = DEFAULT_HELPER) -> str:
    """The notice handed to the session. Tokens only (never the request's words); the request itself is read with the helper, as DATA."""
    cmds = {k: " ".join(shlex.quote(x) for x in v) for k, v in helper_commands(root=root, agent=agent, session_id=session_id, request_id=request["request_id"],
                                                                              python=python, helper=helper).items()}
    who = ("handed to you by Robert himself from a Rooms meeting (created by a click in his signed-in session; the request text records his words and the scope he set)"
           if request.get("origin") == "owner_handoff_from_room" else "agent-authored, not a message or approval from Robert")
    return (f"REQUEST {request['request_id']} from {request['from']} to {request['to']} · {who}"
            f" · read it with: {cmds['show']} · acknowledge pickup with: {cmds['receipt']} · when done, report with: {cmds['result']} <your report>"
            f" (or, for a question to the owner, {cmds['needs-you']} <question>). Run them in this order, and never skip the acknowledgement.")


def _safe_dir(path: str) -> None:
    if os.path.islink(path):
        raise L.Refused("unsafe", "The connection folder is a link; nothing was written.")
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.path.islink(path) or not os.path.isdir(path):
        raise L.Refused("unsafe", "The connection folder is not a folder; nothing was written.")


class ConnectionManager:
    def __init__(self, root: str, agent: str, provider: Provider, *, session_id: str, clock=time.time, helper: str = DEFAULT_HELPER, python: str = sys.executable,
                 adopt_identity: bool = False, limits=None, census_dir: str | None = None):
        if agent not in L.CONNECTED:
            raise L.Refused("invalid", "A connection is for claude-code or codex.")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise L.Refused("invalid", "Name the session this connection is for (a session id). A fresh session is never started silently.")
        if not os.path.isabs(root) or not os.path.isabs(helper) or not os.path.isabs(python) or not os.path.isfile(helper) or not os.access(python, os.X_OK):
            raise L.Refused("invalid", "The ledger root, the helper and the interpreter must be absolute paths that exist; there is no fallback to a default ledger.")
        self.root, self.agent, self.provider, self.session_id, self.clock, self.helper, self.python = root, agent, provider, session_id, clock, helper, python
        self.limits = limits                                    # the limits registry (core.limits.Limits), or None: no daily ceiling is enforced then
        if census_dir is not None and (not isinstance(census_dir, str) or not os.path.isabs(census_dir)):
            raise L.Refused("invalid", "The census folder must be an absolute path.")
        from core.mc_delegation_runner.census import CensusFile
        self.census = None if census_dir is None else CensusFile(census_dir)   # the host runtime's census folder, or None: no row (today's behaviour)
        self._census_state = None                               # the state last written to the census for this session
        self._census_pending = None                             # the root row's event while it waits to be written (tried again each pass)
        self._census_gap = None                                 # {status, reason, since} while the census cannot be written: shown in status.json
        self.instance = secrets.token_hex(6)
        self._last_pass, self._last_error, self._anchors, self._restart_info = None, "", {}, {}
        self.dir = os.path.join(root, "connection", agent)
        _safe_dir(os.path.join(root, "connection"))
        _safe_dir(self.dir)
        self._lock_fd = os.open(os.path.join(self.dir, "lease.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        for attempt in range(4):                                # a watcher's ownership probe holds the lock for an instant: retry before concluding someone else owns it
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if attempt == 3:
                    os.close(self._lock_fd)
                    self._lock_fd = None
                    raise LeaseHeld(f"The {agent} connection is owned by another manager.") from None
                time.sleep(0.05)
        try:
            self._claim_identity(adopt_identity)
            if hasattr(provider, "usage_sink"):
                provider.usage_sink = self._record_usage        # each cost the provider sees is written down the moment it arrives
                provider.session_total_hint = self._last_cumulative     # a resumed session continues from the last total on record
            if hasattr(provider, "attach_launch_sink"):
                provider.attach_launch_sink(self._record_launch)        # each start's launch record, before its spawn
            self._write_json("lease.json", {"agent": agent, "instance": self.instance, "pid": os.getpid(), "session_id": session_id,
                                             "provider": provider.name, "started_at": self._now()})
        except Exception:
            self.close()
            raise

    # ── small files ──
    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))

    def _write_json(self, name: str, doc: dict) -> None:
        path, tmp = os.path.join(self.dir, name), os.path.join(self.dir, f".{name}.{secrets.token_hex(3)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _read_json(self, name: str):
        """None only when the file does not exist. Anything else wrong with an existing file raises StateUnreadable."""
        try:
            with open(os.path.join(self.dir, name), "rb") as fh:
                raw = fh.read(64 * 1024 + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StateUnreadable(f"{name} could not be read ({type(exc).__name__})") from None
        try:
            doc = json.loads(raw[: 64 * 1024].decode("utf-8")) if len(raw) <= 64 * 1024 else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            raise StateUnreadable(f"{name} is damaged or too large; it was not overwritten")
        return doc

    def _claim_identity(self, adopt: bool) -> None:
        have = self._read_json("identity.json")
        same = bool(have) and have.get("session_id") == self.session_id
        if have and not same and not adopt:
            raise IdentityMismatch(f"{self.agent} is bound to session {have.get('session_id')}; {self.session_id} was not adopted.")
        history = (have or {}).get("history", [])
        if have and not same:
            history = history + [{"session_id": have.get("session_id"), "retired_at": self._now()}]
        doc = {"agent": self.agent, "session_id": self.session_id, "history": history[-20:]}
        if same and have.get("created"):
            doc["created"] = True                              # the session already exists: it is resumed, never created again
        self._write_json("identity.json", doc)

    def close(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def _record_launch(self, rec: dict) -> None:
        """Append one launch record (names and digests only) to connection/<agent>/launch.jsonl, fsynced, and with a census, the session's root row
        (spec v0.5 R6.6-c, R6.7-g). Raises when either cannot be written, so the start is refused."""
        line = json.dumps(rec, sort_keys=True)
        fd = os.open(os.path.join(self.dir, "launch.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        if self.census is not None:
            from core.mc_delegation_runner import census as C
            try:
                self._census_pending = {"event_id": str(uuid.uuid4()), "at": C.now_iso(), "body": self._census_root(rec)}
            except Exception as exc:                            # a row that cannot even be made: shown, the start goes on
                self._census_gap = {"status": "unwritable", "reason": str(exc)[:64] or type(exc).__name__, "since": self._now()}
                return
            self._census_state = ("running", None)
            self._census_try()

    def _census_try(self) -> None:
        """Write the root row that waits, the same event each try; the start never waits on it (it never gates the session)."""
        if self.census is None or self._census_pending is None:
            return
        ev = self._census_pending
        try:
            self.census.append("census.start", ev["body"], at=ev["at"], event_id=ev["event_id"], wait_s=0.2)
        except Exception as exc:
            reason = "lock_busy" if type(exc).__name__ == "CensusBusy" else (str(exc)[:64] if isinstance(exc, ValueError) else f"os_error_{getattr(exc, 'errno', 0) or 0}")
            self._census_gap = {"status": "deferred" if reason == "lock_busy" else "unwritable", "reason": reason, "since": (self._census_gap or {}).get("since") or self._now()}
            return
        self._census_pending, self._census_gap = None, None

    # The vendor and where its own transcript is kept, per agent: the memory shelf reads these folders (core/memory_shelf/config.py, sessions.py).
    _CENSUS_VENDOR = {"claude-code": ("anthropic", "~/.claude/projects/*/{session}.jsonl"), "codex": ("openai", "~/.codex/sessions/**/rollout-*.jsonl")}

    def _census_root(self, rec: dict) -> dict:
        from core.mc_delegation_runner import census as C, launch_env
        vendor, path = self._CENSUS_VENDOR[self.agent]
        return C.start(self.session_id, source="connection.start", verified=True, children="not_exposed", vendor=vendor, role="session",
                       start_path="connection", route=C.route_label(rec.get("route"), launch_env.ROUTES.get(rec.get("route"))),
                       launch=C.launch_summary(rec), transcript_ref=C.kept(self.session_id, path.format(session=self.session_id)))

    def _census_seen(self, state: str) -> None:
        """A pass's probe, as the session's census state, written only when it changed: busy is running, idle idle, offline idle (no process serves the
        session until it is resumed), anything else unknown. A census that cannot be written never costs the pass."""
        if self.census is None or self._census_state is None:
            return
        self._census_try()
        if self._census_pending is not None:
            return                                              # its root row first; a state without it would be a row with no start
        now = {"busy": ("running", None), "idle": ("idle", None), "offline": ("idle", "offline")}.get(state, ("unknown", "probe_unknown"))
        if now == self._census_state:
            return
        from core.mc_delegation_runner import census as C
        try:
            self.census.append("census.update", C.update(self.session_id, source="connection.probe", state=now[0], reason=now[1]))
            self._census_state = now
        except Exception:
            pass

    # ── attempts (append-only; the INTENT is written before the provider is called) ──
    def _attempts_path(self) -> str:
        return os.path.join(self.dir, "attempts.jsonl")

    def _append_attempt(self, rec: dict) -> None:
        line = json.dumps(rec, sort_keys=True)
        fd = os.open(self._attempts_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def _attempts(self) -> dict[str, list[dict]]:
        """The COMPLETE attempt history (never a prefix), or StateUnreadable. A missing file is a new history. An unreadable file, a file past MAX_HISTORY_BYTES, or an interior
        line that is not a valid record fails closed (delivering on a damaged history could replay accepted work). A torn FINAL line (no trailing newline: a crash mid-write) is
        safe to ignore, because the intent line is always written, whole, before anything is delivered."""
        path = self._attempts_path()
        try:
            size = os.stat(path).st_size
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise StateUnreadable(f"attempts.jsonl could not be read ({type(exc).__name__})") from None
        if size > MAX_HISTORY_BYTES:
            raise StateUnreadable("attempts.jsonl is too large to read completely; archive it")
        out: dict[str, list[dict]] = {}
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise StateUnreadable(f"attempts.jsonl could not be read ({type(exc).__name__})") from None
        lines = data.split(b"\n")
        torn = lines.pop() if lines and lines[-1] != b"" else None            # the last piece has no newline after it: possibly torn
        for raw in lines:
            if raw == b"":
                continue
            try:
                rec = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise StateUnreadable("attempts.jsonl has a damaged line inside it; it was not guessed at") from None
            if not isinstance(rec, dict) or not L.valid_request_id(rec.get("request_id")) or rec.get("status") not in ("dispatching", "accepted", "refused", "failed", "unknown", "attention"):
                raise StateUnreadable("attempts.jsonl has an invalid record inside it; it was not guessed at")
            out.setdefault(rec["request_id"], []).append(rec)
        if torn is not None:
            try:
                rec = json.loads(torn.decode("utf-8"))
                if isinstance(rec, dict) and L.valid_request_id(rec.get("request_id")) and rec.get("status") in ("dispatching", "accepted", "refused", "failed", "unknown", "attention"):
                    out.setdefault(rec["request_id"], []).append(rec)         # a complete record that only lacks its newline counts
            except ValueError:
                pass
        return out

    def _recover(self, attempts: dict[str, list[dict]]) -> None:
        """A crash between the intent and its outcome left a ``dispatching`` record: the notice may or may not have been taken. It becomes ``unknown`` and is not resent."""
        for rid, recs in attempts.items():
            last = recs[-1]
            if last.get("status") == "dispatching":
                rec = {"request_id": rid, "attempt_id": last["attempt_id"], "at": self.clock(), "status": "unknown", "reason": "crash_before_outcome"}
                self._append_attempt(rec)
                recs.append(rec)
                try:
                    L.record_delivery(self.root, rid, adapter=self.provider.name, state="unknown", attempt_id=last["attempt_id"], reason="crash_before_outcome")
                except L.Refused:
                    pass

    def _plan(self, rid: str, all_recs: list[dict]) -> tuple[str, str]:
        """(``send``|``wait``|``stop``, why) for one unreceipted request. Attention records are flags for the owner; they never decide whether to resend."""
        flagged = any(r.get("status") == "attention" for r in all_recs)
        recs = [r for r in all_recs if r.get("status") != "attention"]
        if not recs:
            return "send", "first"
        last = recs[-1]
        status = last.get("status")
        if status in ("accepted", "unknown"):
            if status == "unknown" and self.provider.dedupes_by_correlation and sum(1 for r in recs if r.get("status") == "unknown") == 1:
                return "send", "resend_same_correlation"
            why = "awaiting_receipt" if status == "accepted" else "unknown_needs_reconcile"
            return "stop", "needs_attention" if flagged else why
        failures = sum(1 for r in recs if r.get("status") in ("refused", "failed"))
        if failures >= MAX_ATTEMPTS:
            return "stop", "attempts_exhausted"
        now, at = self.clock(), last.get("at", 0)
        if at > now:                                            # the wall clock was set back since that attempt: measure from when this process first saw it, not from the future
            at = self._anchors.setdefault((rid, last.get("attempt_id")), now)
        if now < at + BACKOFF_S[min(failures, len(BACKOFF_S)) - 1]:
            return "wait", "backoff"
        return "send", "retry"

    def _surface_attention(self, attempts: dict[str, list[dict]], state: str, attention: str) -> None:
        """Nothing taken by the session may sit silently unfinished. For every request this manager delivered that is still open (not answered, not closed, not waiting on the
        owner's own answer to a question the agent asked), record ONE attention event per reason, visible in the ledger and so to MC and the owner:
          * the session is idle again (its turn ended) and there is no receipt          -> turn_ended_without_receipt
          * the session is idle again and it acknowledged but never answered           -> acknowledged_without_result
          * the session went offline (crash, restart) before answering                  -> session_offline_before_result
          * the provider reports a reason to look (an approval it will not give, a denied tool, a failed turn, an identity mismatch)
        The request stays open and is never resent on its own."""
        for rid, recs in attempts.items():
            if not any(r.get("status") in ("accepted", "unknown", "attention") for r in recs):
                continue                                          # this manager never handed it over
            doc = L.load(self.root, rid)
            if doc is None or doc["closed"] is not None or doc["needs_you"] is not None:
                continue                                          # answered, cancelled, or waiting for the owner: nothing to flag
            stage = doc["stage"]
            reason = attention
            if not reason and state == "idle":
                reason = "turn_ended_without_receipt" if stage == "queued" else "acknowledged_without_result"
            elif not reason and state == "offline" and stage in ("acknowledged", "working"):
                reason = "session_offline_before_result"
            if not reason or any(r.get("status") == "attention" and r.get("reason") == reason for r in recs):
                continue
            attempt_id = next((r["attempt_id"] for r in reversed(recs) if r.get("attempt_id")), "none")
            rec = {"request_id": rid, "attempt_id": attempt_id, "at": self.clock(), "status": "attention", "reason": reason}
            self._append_attempt(rec)
            recs.append(rec)
            try:
                L.record_delivery(self.root, rid, adapter=self.provider.name, state="needs_attention", attempt_id=attempt_id, reason=reason)
            except L.Refused:
                pass

    def identity(self) -> dict:
        return self._read_json("identity.json") or {}

    # ── spend (#296): append-only usage records, one file per Chicago day; the day's total is always recomputed from them, so it is the same after any restart ──
    def _today(self) -> str:
        """The accounting day: America/Chicago midnight to midnight (DST-correct, whatever the host's own zone), as the shared spec says."""
        try:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(self.clock(), ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
        except Exception:
            return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    def _usage_file(self, day: str) -> str:
        return os.path.join(self.dir, f"usage-{day}.jsonl")

    def _record_usage(self, rec: dict) -> None:
        """Append one cost record (called by the provider the moment a result arrives). The event id is the child process plus its result number, so the same result
        can never be counted twice, and a new process starts its own baseline."""
        day = self._today()
        line = {"event_id": f"{rec.get('child')}:{rec.get('seq')}", "at": self._now(), "day": day, **rec}
        if rec.get("unknown"):                                 # the hold is written FIRST and survives a restart of the child, the manager and the day: only a recorded resolution lifts it
            self._write_hold({"since": self._now(), "event_id": line["event_id"], "reason": "a result arrived without a readable cost", "recycled": False})
        # Every cost record is bracketed by a durable "pending" marker: written BEFORE the append, removed only AFTER the append is durable. If the append fails (or the process dies
        # in between) the marker stays, so a fresh manager still holds new work: a cost that may be missing from the records is never read as zero.
        marker = self._pending_file()
        made_marker = not os.path.lexists(marker)
        if made_marker:
            self._write_pending({"since": self._now(), "event_id": line["event_id"]})
        fd = os.open(self._usage_file(day), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, (json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        if made_marker:
            try:
                os.unlink(marker)
            except FileNotFoundError:
                pass

    def _pending_file(self) -> str:
        return os.path.join(self.dir, "usage-pending.json")

    def _write_pending(self, doc: dict) -> None:
        tmp = self._pending_file() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, json.dumps(doc, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._pending_file())

    def _hold_file(self) -> str:
        return os.path.join(self.dir, "usage-hold.json")

    def _write_hold(self, doc: dict) -> None:
        tmp = self._hold_file() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, json.dumps(doc, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._hold_file())

    def _read_hold(self) -> dict | None:
        """The standing unknown-usage hold, or None. A hold file that exists but cannot be read is still a hold (fail closed)."""
        try:
            with open(self._hold_file(), "rb") as fh:
                doc = json.loads(fh.read(64 * 1024).decode("utf-8"))
            return doc if isinstance(doc, dict) else {"reason": "unreadable hold record"}
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeDecodeError):
            return {"reason": "unreadable hold record"}

    def _note_event(self, kind: str) -> None:
        """A non-cost event on the day's file (a restart before the cap), kept with the same identity discipline."""
        self._record_usage({"child": f"event-{kind}", "seq": int(self.clock() * 1000), "cumulative_usd": None, "delta_usd": 0.0, "unknown": False, "kind": kind})

    def _usage_files(self) -> list[str]:
        try:
            return sorted(os.path.join(self.dir, n) for n in os.listdir(self.dir) if re.fullmatch(r"usage-\d{4}-\d{2}-\d{2}\.jsonl", n))
        except OSError:
            return []

    @staticmethod
    def _cum_of(line: bytes):
        try:
            rec = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        v = rec.get("cumulative_usd") if isinstance(rec, dict) else None
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0 else None

    def _last_cumulative(self, before_day: str | None = None):
        """The last session total on record (newest file first), or None. With ``before_day`` only earlier days count."""
        for path in reversed(self._usage_files()):
            if before_day and os.path.basename(path)[6:16] >= before_day:
                continue
            try:
                size = os.path.getsize(path)
                with open(path, "rb") as fh:
                    if size > USAGE_LOG_MAX:                          # the LATEST record is at the end: read the real tail (never a prefix), and drop the cut first line
                        fh.seek(size - USAGE_LOG_MAX)
                        raw = fh.read().split(b"\n", 1)[-1]
                    else:
                        raw = fh.read()
            except OSError:
                continue
            for line in reversed(raw.splitlines()):
                v = self._cum_of(line)
                if v is not None:
                    return v
        return None

    def _day_totals(self) -> dict:
        """The day so far, from the records: dollars (Claude's estimate), results without a readable cost, and restarts before the cap. A damaged line is skipped and counted."""
        seen, spent, unknown, recycles, bad = set(), 0.0, 0, 0, 0
        baseline_unknown = False                           # the first total of the history could not be measured from anything: the day's spend EXCLUDES that part (approximate)
        try:
            with open(self._usage_file(self._today()), "rb") as fh:
                raw = fh.read(USAGE_LOG_MAX + 1)
            if len(raw) > USAGE_LOG_MAX:                       # more than we read is more than we can count: never a valid prefix taken for the whole day
                raw, bad = raw[:USAGE_LOG_MAX], 1
        except FileNotFoundError:
            raw = b""
        except OSError:
            raw, bad = b"", 1
        prev = self._last_cumulative(before_day=self._today())          # the session total at the end of the last earlier day (None: nothing earlier on record)
        for line in raw.splitlines():
            try:
                rec = json.loads(line.decode("utf-8"))
                eid = rec["event_id"]
            except (ValueError, KeyError, UnicodeDecodeError, TypeError):
                bad += 1
                continue
            if eid in seen:
                continue
            seen.add(eid)
            if rec.get("kind") == "recycle":
                recycles += 1
            elif rec.get("kind") == "unknown_resolved":
                baseline_unknown = False                    # the owner reconciled it on the record
            elif rec.get("unknown"):
                unknown += 1
            else:
                cum = rec.get("cumulative_usd")
                if isinstance(cum, (int, float)) and not isinstance(cum, bool) and cum == cum and cum >= 0:
                    # The day's spend is the CHANGE in the session total, measured from the last total seen (not each record's stored difference, which older records got wrong).
                    base = prev if prev is not None else (rec.get("origin_usd") if isinstance(rec.get("origin_usd"), (int, float)) and not isinstance(rec.get("origin_usd"), bool) else None)
                    if base is None or rec.get("baseline_unknown"):
                        baseline_unknown = True             # no earlier total and no recorded origin: what came before this total cannot be separated, so it is NOT claimed as zero
                    spent += 0.0 if base is None else (cum - base if cum >= base else float(cum))
                    prev = float(cum)
                else:
                    try:
                        spent += max(0.0, float(rec.get("delta_usd") or 0.0))
                    except (TypeError, ValueError):
                        bad += 1
        return {"spent_usd": round(spent, 6), "unknown": unknown, "recycles": recycles, "damaged_lines": bad, "baseline_unknown": baseline_unknown}

    def _limit(self, limit_id: str, default):
        if self.limits is None:
            return default
        try:
            return self.limits.value(limit_id)
        except Exception:
            return default

    def _usage(self) -> tuple[dict, str, float]:
        """(usage for status.json, the reason new work is held or "", today's spend). A provider without cost tracking reports nothing and is never held."""
        if not hasattr(self.provider, "usage_sink"):
            return {}, "", 0.0
        totals = self._day_totals()
        ceiling = self._limit("claude.daily_ceiling_usd", None)
        reserve = self._limit("claude.daily_headroom_fraction", 0.1)
        line = None if ceiling is None else ceiling * (1.0 - (reserve if isinstance(reserve, (int, float)) else 0.1))
        usage = {**self.provider.usage(), "spent_today_usd": round(totals["spent_usd"], 4), "unknown_results_today": totals["unknown"], "recycles_today": totals["recycles"],
                 "daily_ceiling_usd": ceiling, "daily_hold_line_usd": None if line is None else round(line, 4), "day": self._today(),
                 "applied": {"claude.session_cap_usd": self.provider.usage().get("process_cap_usd"), "claude.daily_ceiling_usd": ceiling, "claude.daily_headroom_fraction": reserve},
                 "damaged_usage_lines": totals["damaged_lines"], "baseline_unknown": totals["baseline_unknown"]}
        hold = ""
        if ceiling is not None and totals["baseline_unknown"] and self._read_hold() is None:
            self._write_hold({"since": self._now(), "event_id": "baseline-unknown", "reason": "the session's earlier cost is not on record, so today's total may be understated against an enabled ceiling", "recycled": True})
        standing = self._read_hold()
        if standing is None and os.path.lexists(self._pending_file()):
            standing = {"reason": "a cost record may be missing (an append did not finish)", "pending": True}
        if standing is not None or self.provider.usage_unknown():
            hold = "usage_unknown"                                  # a result without a readable cost: new work waits until an explicit resolution is recorded (survives restarts and the day)
            usage["unknown_hold"] = {k: standing.get(k) for k in ("since", "reason", "event_id", "recycled")} if standing else {"reason": "a result without a readable cost"}
        elif totals["damaged_lines"]:
            hold = "usage_log_damaged"
        elif line is not None and totals["spent_usd"] >= line:
            hold = f"daily_ceiling_reached:{totals['spent_usd']:.2f} of {ceiling:g}"
        return usage, hold, totals["spent_usd"]

    def note_restart(self, attempts: int, error: str, next_in_s: float = 0.0) -> None:
        """Truthful supervisor status for the owner: how many times the provider's process has been restarted without success, and why."""
        self._restart_info = {"restart_attempts": attempts, "last_restart_error": error[:160], "next_restart_in_s": int(next_in_s), "restart_noted_at": self._now()}
        try:
            doc = self._read_json("status.json") or {}
        except StateUnreadable:
            doc = {}
        doc.update(self._restart_info)
        self._write_json("status.json", doc)

    def mark_created(self) -> None:
        """The persistent session now exists: later starts must RESUME this same session, never create a first one again."""
        ident = self.identity()
        ident["created"] = True
        self._write_json("identity.json", ident)

    # ── one pass ──
    def step(self) -> dict:
        """One pass that never raises: a ledger or disk failure is reported in the result and in status.json, nothing is delivered on a pass that could not record its
        intent, and the next pass tries again. A gap in wall-clock time between passes (the laptop slept, the process was suspended) is noted for one pass; nothing is
        replayed, because pending work is derived from the files, not from events."""
        now = self.clock()
        gap = now - self._last_pass if self._last_pass is not None else 0.0
        self._last_pass = now
        try:
            out = self._step()
            out["gap_s"] = int(gap) if gap > GAP_NOTICE_S else 0
            out["error"] = ""
            self._last_error = ""
            return out
        except Exception as exc:                                  # disk full, a permission failure, a corrupt file: truthful and survivable
            self._last_error = f"blocked:{exc}"[:160] if isinstance(exc, StateUnreadable) else f"{type(exc).__name__}"
            try:
                L.unarm(self.root, self.agent)                  # a manager that cannot do its job never advertises a listener
            except Exception:
                pass
            try:
                self._write_json("status.json", {"agent": self.agent, "session_id": self.session_id, "provider": self.provider.name, "state": "unknown", "session_ref": "",
                                                 "attention": "", "error": self._last_error, "updated_at": self._now(), "instance": self.instance})
            except Exception:
                pass
            return {"state": "unknown", "delivered": None, "held": {}, "attention": "", "gap_s": int(gap) if gap > GAP_NOTICE_S else 0, "error": self._last_error}

    def _step(self) -> dict:
        try:
            probe = self.provider.probe()
            state = probe.state if probe.state in PROBE_STATES else "unknown"
            ref = probe.session_ref
        except Exception:
            probe, state, ref = Probe("unknown"), "unknown", ""
        if ref and ref != self.session_id:                      # the provider is talking to a different session than the one configured: fail closed
            probe = Probe("unknown", ref, f"identity_mismatch:{ref[:60]}")
            state = "unknown"
        self._census_seen(state)
        usage, hold, spent = self._usage()
        attention = probe.attention
        if hold and not attention:
            attention = hold                                    # shown to the owner; requests wait and are not lost
        self._write_json("status.json", {"agent": self.agent, "session_id": self.session_id, "provider": self.provider.name, "state": state,
                                          "session_ref": ref, "attention": attention, "error": "", "updated_at": self._now(), "instance": self.instance,
                                          "usage": usage, **self._restart_info,
                                          **({} if self.census is None else {"census": self._census_gap or {"status": "ok"}})})
        attempts = self._attempts()                            # read and VALIDATE the history first: a damaged one raises here, before anything advertises availability
        if state in ("idle", "busy"):
            L.arm(self.root, self.agent)                       # listening means a live session, nothing else, and only the CONFIGURED one
        else:
            L.unarm(self.root, self.agent)                     # not reachable now: the marker must not keep saying "listening"
        self._recover(attempts)
        self._surface_attention(attempts, state, probe.attention)
        summary = {"state": state, "delivered": None, "held": {}, "attention": attention}
        if state != "idle":
            return summary                                      # offline, unknown or busy: the requests stay queued; nothing is interrupted
        if getattr(self.provider, "recycle_due", lambda: False)():
            # The process has used enough of its cap that one more turn could trip it: start a fresh one on the SAME session (a fresh cap). Nothing is delivered this pass.
            try:
                self.provider.recycle()
                self._note_event("recycle")
                summary["recycled"] = True
            except Exception as exc:
                summary["attention"] = f"recycle_failed:{type(exc).__name__}"
            return summary
        if hold == "usage_unknown":
            # A result came without a readable cost: new work is refused (nothing is counted as zero). The process is replaced ONCE so no further cost goes unseen, but the hold
            # stays until it is resolved on the record (resolve_usage_hold); a restart of the child or the manager never lifts it.
            standing = self._read_hold()
            if standing is not None and not standing.get("recycled") and standing.get("event_id"):
                try:
                    self.provider.recycle()
                    self._note_event("recycle")
                    summary["recycled"] = True
                    self._write_hold({**standing, "recycled": True})
                except Exception as exc:
                    summary["attention"] = f"recycle_failed:{type(exc).__name__}"
                return summary
            for doc in L.pending(self.root, self.agent):
                summary["held"][doc["request"]["request_id"]] = "usage_unknown"
            return summary
        if hold:
            for doc in L.pending(self.root, self.agent):
                summary["held"][doc["request"]["request_id"]] = hold.split(":")[0]
            return summary                                      # at today's reserve line or damaged records: requests wait (and are not lost); nothing is delivered
        for doc in L.pending(self.root, self.agent):           # oldest first; a receipt or a close removes a request from this list
            rid = doc["request"]["request_id"]
            verdict, why = self._plan(rid, attempts.get(rid, []))
            if verdict != "send":
                summary["held"][rid] = why
                continue
            attempt_id = f"a-{uuid.uuid4().hex[:12]}"
            correlation = rid                                   # stable across attempts: a provider that can deduplicate uses it
            notice = connection_notice(doc["request"], agent=self.agent, session_id=self.session_id, root=self.root, python=self.python, helper=self.helper)
            self._append_attempt({"request_id": rid, "attempt_id": attempt_id, "at": self.clock(), "status": "dispatching"})   # if this cannot be written, nothing is delivered
            try:
                got = self.provider.deliver(notice, correlation)
                status = got.status if got.status in ("accepted", "refused", "failed", "unknown") else "unknown"
                native, reason = got.native_ref, got.reason
            except Exception as exc:                            # the provider blew up: we cannot tell whether it was taken
                status, native, reason = "unknown", "", f"provider_error:{type(exc).__name__}"
            try:
                self._append_attempt({"request_id": rid, "attempt_id": attempt_id, "at": self.clock(), "status": status, "native_ref": native, "reason": reason})
            except OSError:
                pass                                            # the intent is on disk: the next pass reads it as an unknown outcome and never resends it
            try:
                L.record_delivery(self.root, rid, adapter=self.provider.name, state=status, attempt_id=attempt_id, native_ref=native, reason=reason)
            except L.Refused:
                pass
            summary["delivered"] = {"request_id": rid, "status": status}
            break                                               # one notice per pass: the session is busy from here on
        return summary


RESTART_BACKOFF_S = (1, 2, 5, 10, 30, 60, 120, 300)
STABLE_UP_S = 30.0          # a restarted provider must stay reachable this long before its failure count is cleared (a spawn that dies at once is a failure, not a recovery)


def run_forever(manager: "ConnectionManager", stop, *, interval: float = 1.0, max_backoff: float = 30.0, sleep=time.sleep, on_pass=None, restart=None,
                initial_failures: int = 0) -> None:
    """The manager's loop. It never exits on an error: a failed pass is reported (status.json carries the error) and retried with a bounded backoff; it ends only when ``stop``
    (an Event) is set. Closing a browser, sleeping the host or losing the provider does not stop it.

    ``restart`` (an operator-supplied callable that RESUMES the same saved session, never creates a first one) is called when a pass finds the provider offline: a dead child is
    restarted on a bounded backoff (1, 2, 5, 10, 30, 60, 120, then every 300 s). The failure count is cleared only after the provider has stayed reachable for ``STABLE_UP_S``; a
    child that is spawned and dies at once counts as another failure, so a crash loop backs off instead of retrying every second. status.json says how many tries have failed and why.

    ``initial_failures`` counts starts that already failed before the loop (the operator CLI's first start, refused by the launch contract): the loop continues that count
    and its backoff, so a refused first start is retried exactly as a refused restart is."""
    failures = max(0, int(initial_failures))
    next_restart = manager.clock() + RESTART_BACKOFF_S[min(failures - 1, len(RESTART_BACKOFF_S) - 1)] if failures else 0.0
    delay, started_at = interval, None
    while not stop.is_set():
        out = manager.step()
        now = manager.clock()
        state = out.get("state")
        if restart is not None:
            if started_at is not None and state in ("idle", "busy") and now - started_at >= STABLE_UP_S:
                failures, started_at = 0, None
                manager.note_restart(0, "")
            elif started_at is not None and state == "offline":                       # it was spawned but did not stay up: that is a failed start
                failures, started_at = failures + 1, None
                wait = RESTART_BACKOFF_S[min(failures - 1, len(RESTART_BACKOFF_S) - 1)]
                next_restart = now + wait
                manager.note_restart(failures, "the provider exited right after it was started", wait)
            if state == "offline" and started_at is None and now >= next_restart:
                try:
                    restart()
                    started_at = now                                                   # not yet a success: it has to stay up
                    manager.note_restart(failures, "", 0)
                except Exception as exc:
                    failures += 1
                    wait = RESTART_BACKOFF_S[min(failures - 1, len(RESTART_BACKOFF_S) - 1)]
                    next_restart = now + wait
                    try:
                        manager.note_restart(failures, f"{type(exc).__name__}: {exc}", wait)
                    except Exception:
                        pass
        if on_pass is not None:
            on_pass(out)
        delay = min(max_backoff, max(delay * 2, interval)) if out.get("error") else interval
        stop.wait(delay) if hasattr(stop, "wait") else sleep(delay)


def resolve_usage_hold(root: str, agent: str, note: str, by: str = "owner") -> bool:
    """Lift a standing unknown-usage hold, on the record: the resolution is appended to today's usage file (who, why) and then the hold file is removed. True when a hold was lifted.
    Nothing else ever lifts it. The note must say what was reconciled."""
    if not isinstance(note, str) or not note.strip():
        raise ValueError("say what was reconciled")
    folder = os.path.join(root, "connection", agent)
    hold, pending = os.path.join(folder, "usage-hold.json"), os.path.join(folder, "usage-pending.json")
    if not os.path.lexists(hold) and not os.path.lexists(pending):
        return False
    from zoneinfo import ZoneInfo
    day = datetime.fromtimestamp(time.time(), ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    line = {"event_id": f"resolve:{int(time.time() * 1000)}", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "day": day, "kind": "unknown_resolved", "unknown": False,
            "delta_usd": 0.0, "by": by, "note": note.strip()[:300]}
    fd = os.open(os.path.join(folder, f"usage-{day}.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, (json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    for path in (hold, pending):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    return True
