"""Master Craftsman jobs: work that runs in the background, in the conversation that asked for it (overnight build, step 3).

A chat turn is conversational speed. A JOB is work that may take minutes: it is started once on the relay, shown in the
conversation as a live line with Stop, and posts its result there. Everything that matters is a file, so a page reload or
a portal restart never loses a job and nothing is ever run twice.

The contract this module keeps (amendment B1 and B2):

* **Lifecycle.** queued → dispatching → running → completed | failed | stopped | unknown, plus not_started for a job the
  relay is known never to have run. A job is CLAIMED (state written and fsynced) before the relay is called, so a crash
  between the two leaves ``dispatching``, never a lost job. The relay dedupes on the job id; a start is never retried; an
  ambiguous start is settled by asking the relay for the job's status (found → follow it; never heard of → not_started;
  unreachable too long → unknown). Nothing is replayed.
* **At most once.** A job id comes from its kept note, so one note makes one job. The result is posted as a note under a
  deterministic request id, from a result frozen in the job file first, so a crash and a retry cannot post it twice or
  post two different texts.
* **Honest ends.** completed (the relay said so; "verified" is claimed only for files the relay checked), failed, stopped
  (confirmed by the relay) versus a stop that was only requested, and unknown (this page lost track). A refusal is a
  valid completed answer.
* **Bounds.** One running job per conversation, two overall, three waiting, a 30 minute deadline, a 64 KB result.
* **The task is the owner's own words**: the kept note, verbatim (scrubbed like any note sent out). A model's text is only
  ever a *proposal* to run one (``parse_proposal``), decided by the server.
* **Private.** A job starts only from an on-the-record note; a conversation that goes off the record afterwards does not
  stop a running job. Every call is owner-checked by the caller; a job belongs to its principal.
* **The switch.** All of it is behind ``MINIMOI_GUILD_JOBS`` (off by default and in production)."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from . import job_inbox
from .mc.job_relay import RESULT_MAX_CHARS

log = logging.getLogger("guild_ui.jobs")

JOBS_VAR = "MINIMOI_GUILD_JOBS"
FLAG_ON = ("1", "true", "on", "yes")
JOBS_FOLDER = "jobs"
CONV_RE = re.compile(r"^c-[0-9a-f]{12}$")
JOB_RE = re.compile(r"^j-[0-9a-f]{16}$")
STATES = ("queued", "dispatching", "running", "completed", "failed", "stopped", "unknown", "not_started")
TERMINAL = frozenset({"completed", "failed", "stopped", "unknown", "not_started"})
ACTIVE = frozenset({"queued", "dispatching", "running"})
ALLOWED = {"queued": {"dispatching", "stopped", "not_started"},
           "dispatching": {"queued", "running", "completed", "failed", "stopped", "unknown", "not_started"},
           "running": {"completed", "failed", "stopped", "unknown"}}
AGENT = "mc-agent"
TITLE_MAX = 80
RESULT_CUT_NOTE = "\n\n[The result was cut at 64 KB; the rest was not kept.]"
NOTE_SHOWN_MAX = 3600           # a conversation note holds at most 4000 characters (the floor database's limit)
NOTE_SHOWN_CUT = "\n\n[Showing the first part. The full result ({n:,} characters) is kept with this job: use \"Open full result\".]"


def jobs_enabled(environ) -> bool:
    return str(environ.get(JOBS_VAR, "") or "").strip().lower() in FLAG_ON


@dataclass(frozen=True)
class Limits:
    per_conversation: int = 1
    overall: int = 2
    queue: int = 3
    deadline_s: float = 30 * 60
    stop_grace_s: float = 60
    unreachable_grace_s: float = 300
    dispatch_grace_s: float = 120
    busy_attempts: int = 10


MESSAGES = {
    "queued": "Waiting for a free place to run. Nothing has started yet.",
    "dispatching": "Starting. Master Craftsman has been asked to run this job; this page has not heard back yet.",
    "running": "Running in the background. You can keep talking here; say stop to cancel it.",
    "stop_requested": "Stop requested. The relay has not confirmed it yet, so the job may still be working.",
    "completed": "Done. The result is posted in this conversation.",
    "failed": "The job failed. Nothing was run again.",
    "stopped": "Stopped: the relay confirmed it cancelled the run. A shell command it started may still be running.",
    "stopped_before_start": "Stopped before it started. Nothing was run.",
    "unknown": ("Outcome unknown: this page lost track of the job. It may still be running, or it may have finished or failed. "
                "Nothing was cancelled or run again. Check before asking again."),
    "not_started": "Not started: the relay never ran this job, so nothing was changed. You can ask again.",
    "writes_verified": "The relay checked the files it recognised the run writing: they exist, with the sizes and fingerprints shown. A shell command can also change files in ways it cannot see.",
    "writes_unverified": "The relay did not confirm every file it recognised the run writing. Check them before relying on them.",
    "no_writes": "The relay recognised no file writes in this run's tool calls. A shell command can change files in ways it cannot see.",
    "flagged": "{n} tool call{s} touched an area the job rules forbid. They are marked in the list below; check what they did.",
    "audit_unavailable": "The run record could not be read, so there is no list of tool calls for this job.",
    "audit": ("A list of the tool calls recorded for this run. It is not a complete record of everything on the Mac, and the "
              "limits on what Master Craftsman may read or write are instructions to it, not enforced."),
    "empty_result": "The job finished but returned no text, so there is no answer to keep.",
}
ERROR_WORDS = {"relay_unreachable": "the relay could not be reached", "relay_lost_job": "the relay no longer knows this job",
               "stop_unconfirmed": "a requested stop was never confirmed", "deadline": "the 30 minute limit was reached",
               "refused": "the relay refused to start it", "relay_busy": "the relay stayed busy", "note_missing": "the kept note could not be read",
               "empty_result": "no result text came back", "not_connected": "Master Craftsman is not connected"}


# ── the owner's words: commands and proposals, decided by the server ──────────────────────────────

_STOP_WORDS = {"stop", "stop the job", "stop job", "stop it", "cancel the job", "cancel job", "cancel it", "stop the running job"}
_FORCE = re.compile(r"\brun (?:this|it|that) as a job\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.!?\"'`]+", " ", (text or "").lower())).strip()


def interpret(text: str) -> str | None:
    """'stop' when the message is only a stop request, 'run_job' when the owner asks for a job, else None. Deterministic:
    no model decides either."""
    n = _norm(text)
    if n in _STOP_WORDS:
        return "stop"
    if _FORCE.search(n):
        return "run_job"
    return None


_PROPOSAL = re.compile(r"^JOB: ([^\x00-\x1f\x7f]{1,%d})$" % TITLE_MAX)


def parse_proposal(text: str | None, *, ended_cleanly: bool) -> dict | None:
    """A conversation-lane reply may PROPOSE a job by starting with the single line ``JOB: <title>``. Only the first
    complete line of a cleanly ended stream counts; a title past 80 characters, control characters, a prefix not at the
    start, or any later ``JOB:`` line are text, not a trigger. At most one proposal per turn. The server still decides:
    the task is always the owner's own message, never anything the model wrote."""
    if not ended_cleanly or not isinstance(text, str) or not text:
        return None
    first, _, rest = text.partition("\n")
    m = _PROPOSAL.match(first.rstrip("\r"))
    if not m or not m.group(1).strip():
        return None
    return {"title": m.group(1).strip(), "rest": rest.lstrip("\n")}


def shown_in_note(text: str) -> str:
    """The part of a result that goes in the conversation note: all of it when it fits, else the first part cut at a
    paragraph or word, with a line that says the rest is kept with the job."""
    if len(text) <= NOTE_SHOWN_MAX:
        return text
    cut = text[:NOTE_SHOWN_MAX]
    for sep in ("\n\n", "\n", " "):
        at = cut.rfind(sep)
        if at > NOTE_SHOWN_MAX // 2:
            cut = cut[:at]
            break
    return cut.rstrip() + NOTE_SHOWN_CUT.format(n=len(text))


def job_id_for(note_id: str) -> str:
    return "j-" + hashlib.sha256(("job:" + note_id).encode("utf-8")).hexdigest()[:16]


def result_request_id(job_id: str) -> str:
    return "jobres-" + hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:40]


def default_title(task: str) -> str:
    line = re.sub(r"\s+", " ", (task or "").strip())
    line = re.sub(r"(?i)\brun (?:this|it|that) as a job\b[.!?]*", "", line, count=1) if line else line
    line = re.sub(r"[^\w .,:;()'/&+\-]", "", line).strip(" .,:;-") or "Job"
    if len(line) <= TITLE_MAX:
        return line
    cut = line[:TITLE_MAX - 1]
    at = cut.rfind(" ")
    return (cut[:at] if at >= TITLE_MAX // 2 else cut).rstrip(" .,:;-") + "…"


# ── the store: one JSON file per job ──────────────────────────────────────────────────────────────

class JobGone(LookupError):
    pass


class BadTransition(RuntimeError):
    pass


class _Skip(Exception):
    """Raised inside a mutation to leave the job as it is."""


class JobStore:
    def __init__(self, folder: str | None):
        self.root = os.path.join(folder, JOBS_FOLDER) if folder else None

    def _dir(self, cid):
        if not self.root or not CONV_RE.fullmatch(str(cid)):
            raise JobGone(cid)
        return os.path.join(self.root, cid)

    def _path(self, cid, jid):
        if not JOB_RE.fullmatch(str(jid)):
            raise JobGone(jid)
        return os.path.join(self._dir(cid), f"{jid}.json")

    @contextmanager
    def _lock(self, cid, jid):
        d = self._dir(cid)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(d, f"{jid}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read(self, cid, jid):
        try:
            with open(self._path(cid, jid), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def _write(self, cid, jid, job):
        path = self._path(cid, jid)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def create(self, job: dict) -> tuple[dict, bool]:
        """(job, created). A job id that already exists is returned as it is."""
        cid, jid = job["conversation_id"], job["id"]
        with self._lock(cid, jid):
            existing = self._read(cid, jid)
            if existing is not None:
                return existing, False
            self._write(cid, jid, job)
            return job, True

    def get(self, cid, jid):
        try:
            return self._read(cid, jid)
        except (json.JSONDecodeError, OSError, JobGone):
            return None

    def mutate(self, cid, jid, fn):
        """Apply ``fn`` to the job under its lock, write it, return it. ``fn`` may raise _Skip to change nothing (the
        job as it stands is returned)."""
        with self._lock(cid, jid):
            job = self._read(cid, jid)
            if job is None:
                raise JobGone(jid)
            try:
                fn(job)
            except _Skip:
                return job
            self._write(cid, jid, job)
            return job

    def _result_path(self, cid, jid):
        if not JOB_RE.fullmatch(str(jid)):
            raise JobGone(jid)
        return os.path.join(self._dir(cid), f"{jid}.result.txt")

    def write_result(self, cid, jid, text: str) -> None:
        path = self._result_path(cid, jid)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def read_result(self, cid, jid) -> str | None:
        try:
            with open(self._result_path(cid, jid), encoding="utf-8") as f:
                return f.read(RESULT_MAX_CHARS + len(RESULT_CUT_NOTE) + 16)
        except (FileNotFoundError, JobGone):
            return None

    def list(self, cid) -> list:
        try:
            names = sorted(n for n in os.listdir(self._dir(cid)) if JOB_RE.fullmatch(n[:-5]) and n.endswith(".json"))
        except (FileNotFoundError, JobGone):
            return []
        rows = [j for j in (self.get(cid, n[:-5]) for n in names) if j]
        return sorted(rows, key=lambda j: (j.get("created_at", 0), j["id"]), reverse=True)

    def active(self) -> list:
        out = []
        if not self.root or not os.path.isdir(self.root):
            return out
        for cid in sorted(os.listdir(self.root)):
            if CONV_RE.fullmatch(cid):
                out.extend(j for j in self.list(cid) if j.get("state") in ACTIVE)
        return sorted(out, key=lambda j: (j.get("created_at", 0), j["id"]))


def _set_state(job: dict, new: str, now: float):
    old = job["state"]
    if new not in ALLOWED.get(old, ()):
        raise BadTransition(f"{old} -> {new}")
    job["state"] = new
    job["t_" + new] = now
    if new in TERMINAL:
        job["finished_at"] = now


# ── the manager ───────────────────────────────────────────────────────────────────────────────────

class JobManager:
    """Starts, follows and ends jobs. ``tick()`` is the whole engine: one pass that polls what is running, dispatches what
    is waiting, enforces deadlines and clears old inboxes. A background ticker calls it every few seconds; tests call it
    directly with a fake clock. It is safe to call from several workers (each change is under the job's file lock)."""

    def __init__(self, folder: str, relay, *, conversations, floor_for, scrub=lambda t: t, clock=time.time,
                 limits: Limits = Limits(), touch=None):
        self.folder = folder
        self.relay = relay
        self.store = JobStore(folder)
        self.conversations = conversations
        self.floor_for = floor_for
        self.scrub = scrub
        self.clock = clock
        self.limits = limits
        self.touch = touch or (lambda conv, principal: None)
        self._tick_lock = threading.Lock()
        self.wake = threading.Event()

    # ── starting ──
    def start(self, conv: dict, principal: str, note: dict, note_id: str, doc_ids=None, title: str | None = None):
        """(job, refusal). A refusal is (code, plain message) and means nothing was created and nothing was sent."""
        cid = conv.get("id", "")
        if not CONV_RE.fullmatch(str(cid)):
            return None, ("not_a_conversation", "Open a conversation to run a job; the old Shop floor thread does not run jobs.")
        if not self.relay.connected:
            return None, ("not_connected", "Master Craftsman is not connected, so no job was started.")
        jid = job_id_for(note_id)
        existing = self.store.get(cid, jid)
        if existing is not None:
            return existing, None
        if sum(1 for j in self.store.active() if j["state"] == "queued") >= self.limits.queue:
            return None, ("queue_full", "Three jobs are already waiting. Wait for one to finish, then ask again. Nothing was started.")
        files = []
        if doc_ids:
            try:
                manifest = job_inbox.stage(self.conversations, self.folder, cid, principal, jid, list(doc_ids))
            except job_inbox.InboxRefused as exc:
                if exc.code == "exists" and self.store.get(cid, jid) is not None:      # a racing start of the same note
                    return self.store.get(cid, jid), None
                return None, (exc.code, exc.message + " Nothing was started.")
            files = [{"doc_id": f["doc_id"], "name": f["name"], "bytes": f["bytes"], "sha256": f["sha256"], "path": f["path"]}
                     for f in manifest["files"]]
        now = self.clock()
        job = {"id": jid, "conversation_id": cid, "principal": principal, "note_id": note_id, "state": "queued",
               "title": (title or default_title(note.get("text", ""))), "task_sha256": hashlib.sha256((note.get("text") or "").encode()).hexdigest(),
               "created_at": now, "t_queued": now, "updated_at": now, "files": files, "inbox": bool(files), "attempts": 0,
               "deadline_s": self.limits.deadline_s, "stop_requested_at": None, "stop_outcome": None, "tools": [], "tools_truncated": False,
               "writes": [], "error_class": None, "result_note_id": None, "result_truncated": False, "result_chars": 0}
        job, created = self.store.create(job)
        if not created and files:
            job_inbox.remove_inbox(self.folder, jid)       # a racing duplicate staged nothing of its own
        self.wake.set()
        return job, None

    # ── the engine ──
    def tick(self) -> None:
        if not self._tick_lock.acquire(blocking=False):
            return
        try:
            jobs = self.store.active()
            for j in jobs:
                if j["state"] in ("dispatching", "running"):
                    self._guard(self._poll, j)
            jobs = self.store.active()
            running = [j for j in jobs if j["state"] in ("dispatching", "running")]
            for j in [j for j in jobs if j["state"] == "queued"]:           # oldest first (active() is sorted)
                per_conv = sum(1 for r in running if r["conversation_id"] == j["conversation_id"])
                if len(running) >= self.limits.overall or per_conv >= self.limits.per_conversation:
                    continue
                started = self._guard(self._dispatch, j)
                if started:
                    running.append(started)
            keep = {j["id"] for j in self.store.active()}
            try:
                job_inbox.sweep(self.folder, keep=keep, now=self.clock())
            except OSError:
                pass
        finally:
            self._tick_lock.release()

    def _guard(self, fn, job):
        try:
            return fn(job)
        except JobGone:
            return None
        except Exception:
            log.exception("job %s: %s failed; it is retried on the next pass", job.get("id"), getattr(fn, "__name__", "step"))
            return None

    def _dispatch(self, job):
        cid, jid, now = job["conversation_id"], job["id"], self.clock()

        def claim(j):
            if j["state"] != "queued":
                raise _Skip
            j["attempts"] = j.get("attempts", 0) + 1
            _set_state(j, "dispatching", now)
        claimed = self.store.mutate(cid, jid, claim)                # written and fsynced BEFORE the relay hears of it
        if claimed["state"] != "dispatching" or claimed.get("t_dispatching") != now:
            return None
        try:
            note = self._note(claimed)
        except Exception:                                   # the note store is down: nothing was sent, so try again later
            def later(j):
                if j["state"] != "dispatching":
                    raise _Skip
                if j.get("attempts", 0) >= self.limits.busy_attempts:
                    self._end(j, "not_started", "note_missing")
                else:
                    _set_state(j, "queued", self.clock())
            self.store.mutate(cid, jid, later)
            return None
        if note is None:                                    # the note really is gone, or is not the owner's: this job cannot run
            self.store.mutate(cid, jid, lambda j: self._end(j, "not_started", "note_missing"))
            return None
        spec = {"job_id": jid, "agent": AGENT, "task": self.scrub(note["text"]), "deadline_s": claimed["deadline_s"],
                "inbox": ({"job_id": jid, "files": [{"name": f["path"], "sha256": f["sha256"]} for f in claimed["files"]]} if claimed["files"] else None)}
        outcome, detail = self.relay.start(spec)
        t = self.clock()
        if outcome in ("accepted", "duplicate"):
            return self.store.mutate(cid, jid, lambda j: self._running(j, t))
        if outcome == "busy":
            def back(j):
                if j["state"] != "dispatching":
                    raise _Skip
                if j.get("attempts", 0) >= self.limits.busy_attempts:
                    self._end(j, "not_started", "relay_busy")
                else:
                    _set_state(j, "queued", t)
            self.store.mutate(cid, jid, back)
            return None
        if outcome == "refused":
            self.store.mutate(cid, jid, lambda j: self._end(j, "not_started", "refused" if detail.get("reason") != "not_connected" else "not_connected"))
            return None
        # ambiguous: it may have started. Left in dispatching; the next pass asks the relay for its status.
        self.store.mutate(cid, jid, lambda j: j.__setitem__("ambiguous_since", j.get("ambiguous_since") or t))
        return self.store.get(cid, jid)

    def _running(self, j, t):
        if j["state"] != "dispatching":
            raise _Skip
        _set_state(j, "running", t)
        j["unreachable_since"] = None

    def _end(self, j, state, error_class=None):
        if j["state"] in TERMINAL:
            raise _Skip
        _set_state(j, state, self.clock())
        if error_class:
            j["error_class"] = error_class

    def _note(self, job):
        note = self._floor(job).get_note(job["note_id"])
        if not note or note.get("who") != job["principal"] or note.get("author_kind") != "owner":
            return None
        return note

    def _floor(self, job):
        conv = self.conversations.get(job["conversation_id"], job["principal"])
        return self.floor_for(conv)

    def _poll(self, job):
        cid, jid, now = job["conversation_id"], job["id"], self.clock()
        kind, st = self.relay.status(jid)
        lim = self.limits
        if kind == "ok":
            return self._apply(job, st, now)
        if kind == "not_found":
            if job["state"] == "dispatching":
                self.store.mutate(cid, jid, lambda j: self._end(j, "not_started") if j["state"] == "dispatching" else None)
            else:
                self.store.mutate(cid, jid, lambda j: self._end(j, "unknown", "relay_lost_job"))
            return
        # unreachable
        def lost(j):
            if j["state"] not in ("dispatching", "running"):
                raise _Skip
            since = j.get("unreachable_since") or (j.get("ambiguous_since") if j["state"] == "dispatching" else None) or now
            j["unreachable_since"] = since
            grace = lim.dispatch_grace_s if j["state"] == "dispatching" else lim.unreachable_grace_s
            if now - since > grace:
                self._end(j, "unknown", "relay_unreachable")
        self.store.mutate(cid, jid, lost)
        self._enforce_time(self.store.get(cid, jid), now)

    def _enforce_time(self, job, now):
        if not job or job["state"] != "running":
            return
        if job.get("stop_requested_at") and now - job["stop_requested_at"] > self.limits.stop_grace_s:
            self.store.mutate(job["conversation_id"], job["id"], lambda j: self._end(j, "unknown", "stop_unconfirmed") if j["state"] == "running" else None)
        elif not job.get("stop_requested_at") and now - (job.get("t_running") or now) > job["deadline_s"]:
            self._request_stop(job, reason="deadline")

    def _apply(self, job, st, now):
        cid, jid = job["conversation_id"], job["id"]

        def snapshot(j):
            j["tools"], j["tools_truncated"], j["writes"] = st["tools"], st["tools_truncated"], st["writes"]
            j["audit_available"] = st.get("audit_available", True)
            j["last_status_at"], j["elapsed_s"], j["heartbeat_age_s"] = now, st["elapsed_s"], st["heartbeat_age_s"]
            j["unreachable_since"] = None
        state = st["state"]
        if state == "running":
            def running(j):
                if j["state"] in TERMINAL:
                    raise _Skip
                if j["state"] == "dispatching":
                    _set_state(j, "running", now)
                snapshot(j)
            job = self.store.mutate(cid, jid, running)
            self._enforce_time(job, now)
            return job
        if state == "completed":
            return self._finalize_completed(job, st, now, snapshot)
        if state in ("failed", "stopped", "unknown"):
            def end(j):
                if j["state"] in TERMINAL:
                    raise _Skip
                snapshot(j)
                if state == "stopped":
                    j["stop_outcome"] = "confirmed"
                self._end(j, state, st["error_class"] if state != "stopped" else None)
            return self.store.mutate(cid, jid, end)

    def _finalize_completed(self, job, st, now, snapshot):
        cid, jid = job["conversation_id"], job["id"]
        text = st["result_text"] if isinstance(st["result_text"], str) else ""
        if not text.strip():
            def empty(j):
                if j["state"] in TERMINAL:
                    raise _Skip
                snapshot(j)
                self._end(j, "failed", "empty_result")
            return self.store.mutate(cid, jid, empty)
        truncated = len(text) > RESULT_MAX_CHARS
        frozen = (text[:RESULT_MAX_CHARS] + RESULT_CUT_NOTE) if truncated else text

        def freeze(j):                                              # 1: the text is decided and saved first
            if j["state"] in TERMINAL:
                raise _Skip
            snapshot(j)
            if "result_pending" not in j:
                self.store.write_result(cid, jid, frozen)           # the whole result, kept with the job
                j["result_pending"] = shown_in_note(frozen)         # what the conversation note will say
                j["result_truncated"], j["result_chars"], j["result_in_note_cut"] = truncated, len(frozen), len(frozen) > len(j["result_pending"])
        job = self.store.mutate(cid, jid, freeze)
        if job["state"] in TERMINAL:
            return job
        from .stores import MASTER_CRAFTSMAN
        note = self._note(job)
        ctx = (note or {}).get("context") or {}
        floor = self._floor(job)
        kept = floor.add_note(result_request_id(jid), job["result_pending"], MASTER_CRAFTSMAN, area=ctx.get("area"),   # 2: posted once
                              item_ref=ctx.get("item_ref"), page=ctx.get("page"))
        if getattr(kept, "outcome", None) != "kept" or not getattr(kept, "value", None):
            log.warning("job %s: the result could not be kept (%s); it is retried on the next pass", jid, getattr(kept, "outcome", None))
            return job
        note_id = kept.value.get("request_id") or kept.value.get("id")

        def done(j):                                                # 3: only now is it completed
            if j["state"] in TERMINAL:
                raise _Skip
            j["result_note_id"] = note_id
            j.pop("result_pending", None)
            _set_state(j, "completed", now)
        job = self.store.mutate(cid, jid, done)
        try:
            self.touch(self.conversations.get(cid, job["principal"]), job["principal"])
        except Exception:
            pass
        return job

    # ── stopping ──
    def stop(self, cid, principal, jid):
        """(job, outcome). outcome: 'stopped_before_start' | 'confirmed' | 'requested' | 'already_finished' | 'not_found'."""
        job = self.store.get(cid, jid) if JOB_RE.fullmatch(str(jid)) and CONV_RE.fullmatch(str(cid)) else None
        if job is None or job["principal"] != principal:
            return None, "not_found"
        if job["state"] in TERMINAL:
            return job, "already_finished"
        now = self.clock()
        if job["state"] == "queued":
            def never(j):
                if j["state"] != "queued":
                    raise _Skip
                j["stop_outcome"] = "never_started"
                j["stop_requested_at"] = now
                _set_state(j, "stopped", now)
            job = self.store.mutate(cid, jid, never)
            if job["state"] == "stopped":
                if job.get("inbox"):
                    job_inbox.remove_inbox(self.folder, jid)
                return job, "stopped_before_start"
            if job["state"] in TERMINAL:
                return job, "already_finished"
        return self._request_stop(job, reason="owner")

    def _request_stop(self, job, *, reason):
        cid, jid, now = job["conversation_id"], job["id"], self.clock()

        def mark(j):
            if j["state"] in TERMINAL:
                raise _Skip
            j["stop_requested_at"] = j.get("stop_requested_at") or now
            j["stop_reason"] = j.get("stop_reason") or reason
        job = self.store.mutate(cid, jid, mark)
        if job["state"] in TERMINAL:
            return job, "already_finished"
        answer = self.relay.stop(jid)
        if answer == "confirmed":
            self._guard(self._poll, self.store.get(cid, jid))        # one last read of the audit, if the relay still has it

            def confirm(j):
                if j["state"] in TERMINAL:
                    raise _Skip
                j["stop_outcome"] = "confirmed"
                _set_state(j, "stopped", self.clock())
            return self.store.mutate(cid, jid, confirm), "confirmed"
        if answer == "not_running":
            self._guard(self._poll, self.store.get(cid, jid))        # it already ended: take its real end
            return self.store.get(cid, jid), "already_finished" if self.store.get(cid, jid)["state"] in TERMINAL else "requested"
        if answer == "not_found" and job["state"] == "dispatching":
            self.store.mutate(cid, jid, lambda j: self._end(j, "not_started") if j["state"] == "dispatching" else None)
            return self.store.get(cid, jid), "already_finished"
        return self.store.get(cid, jid), "requested"                  # requested, unreachable, or not_found while running: never claimed as confirmed

    # ── what the browser sees ──
    def view(self, job: dict) -> dict:
        now = self.clock()
        state = job["state"]
        stop_req = bool(job.get("stop_requested_at"))
        started = job.get("t_running") or job.get("t_dispatching")
        end = job.get("finished_at")
        elapsed = int((end or now) - started) if started else 0
        writes = job.get("writes") or []
        verified = (all(w.get("verified") and w.get("exists") for w in writes) if writes else None) if state == "completed" else None
        if state == "stopped" and job.get("stop_outcome") == "never_started":
            message = MESSAGES["stopped_before_start"]
        elif state in ("running", "dispatching") and stop_req:
            message = MESSAGES["stop_requested"]
        else:
            message = MESSAGES.get(state, "")
        if state in ("failed", "unknown", "not_started") and job.get("error_class"):
            why = ERROR_WORDS.get(job["error_class"], job["error_class"])
            if state == "failed":
                message = f"The job failed: {why}. Nothing was run again."
            elif state == "not_started":
                message = f"Not started: {why}. Nothing was changed. You can ask again."
            else:
                message = message + f" ({why}.)"
        flagged = sum(1 for t in job.get("tools") or [] if t.get("flag"))
        if state == "completed":
            message += " " + (MESSAGES["no_writes"] if verified is None else MESSAGES["writes_verified"] if verified else MESSAGES["writes_unverified"])
        if flagged and state in TERMINAL:
            message += " " + MESSAGES["flagged"].format(n=flagged, s="" if flagged == 1 else "s")
        if state in TERMINAL and job.get("audit_available") is False:
            message += " " + MESSAGES["audit_unavailable"]
        return {"id": job["id"], "conversation_id": job["conversation_id"], "title": job.get("title") or "Job", "state": state,
                "message": message, "created_at": job.get("created_at"), "elapsed_s": max(0, elapsed), "active": state in ACTIVE,
                "can_stop": state in ACTIVE, "stop_requested": stop_req, "stop_outcome": job.get("stop_outcome"),
                "files": [{"name": f["name"], "bytes": f["bytes"]} for f in job.get("files") or []],
                "tools": job.get("tools") or [], "tools_truncated": bool(job.get("tools_truncated")), "flagged": flagged,
                "audit_available": job.get("audit_available") is not False,
                "writes": [{k: w.get(k) for k in ("path", "exists", "bytes", "sha256", "verified")} for w in writes],
                "writes_verified": verified, "audit_note": MESSAGES["audit"], "enforced": False,
                "result_note_id": job.get("result_note_id"), "result_truncated": bool(job.get("result_truncated")),
                "result_full": bool(job.get("result_in_note_cut")), "result_chars": job.get("result_chars") or 0,
                "error_class": job.get("error_class")}

    def views(self, cid, principal, limit=20) -> list:
        return [self.view(j) for j in self.store.list(cid) if j.get("principal") == principal][:limit]


class Ticker(threading.Thread):
    """Calls ``manager.tick()`` every few seconds, and at once when a job is started. Never raises."""

    def __init__(self, manager: JobManager, interval_s: float = 3.0):
        super().__init__(name="guild-jobs-ticker", daemon=True)
        self.manager, self.interval_s = manager, interval_s
        self._stop_flag = threading.Event()

    def run(self):
        while not self._stop_flag.is_set():
            try:
                self.manager.tick()
            except Exception:
                log.exception("job ticker pass failed")
            self.manager.wake.wait(self.interval_s)
            self.manager.wake.clear()

    def stop(self):
        self._stop_flag.set()
        self.manager.wake.set()


__all__ = ["JOBS_VAR", "jobs_enabled", "Limits", "JobManager", "JobStore", "Ticker", "interpret", "parse_proposal",
           "job_id_for", "result_request_id", "MESSAGES", "TERMINAL", "ACTIVE", "STATES", "CONV_RE", "JOB_RE", "AGENT"]
