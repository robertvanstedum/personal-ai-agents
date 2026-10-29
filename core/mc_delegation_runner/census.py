"""The agent census (host runtime spec v0.5 §6.6, R6.6-a to R6.6-g; Robert, 28 Oct: "I'd like to see all of them").

One record per agent instance: each child the runtime starts, each session a front-door connection or the fallback
door starts, each queued turn of the Codex desktop app (F1), and every subagent a source exposes. Each is a row in a
tree (``parent_id``), with where it runs, the work item it serves, its times, its state, and its usage as reported.

* **File first** (R6.6-c). Every source appends events to ``artifact_root/census/<day>.jsonl`` on its host (the UTC day
  of the event), fsynced, one ``event_id`` per line, under an exclusive ``flock`` on ``census/.lock`` (the runtime,
  ``fakework``, the connections, the desktop queue and the Claude Code hook are separate processes). Each line carries
  the SHA-256 of its own canonical form, as the pending records do. A line that does not check is counted as damaged,
  shown, and never sent; the rest of the file stays readable (a census never blocks contact or work, CEN-6). A torn
  last line is sealed with a newline before the next append, so it never swallows the next event.
* **Sent with the heartbeat** (R3.2-a). The runtime sends unsent events, oldest first, in bounded batches
  (``BATCH_MAX_EVENTS``, ``BATCH_MAX_BYTES``; the door's body limit holds both with room for the facts), each exactly
  as written (byte-faithful replay). The authority applies each once into ``guild.mc_delegation_events`` (no other
  table) and answers per event; the answer is appended as an ``ack`` line in the event's own day file. An event the
  authority rejects is acknowledged as rejected and never resent; a transport failure leaves every event unsent.
* **Events, never inferences** (R6.6-a). ``census.start``, ``census.update`` (activity or a state change),
  ``census.end``, and ``census.gap`` (the host's capture was interrupted: a runtime restart, or a wake from sleep).
  ``started_at``, ``last_activity_at`` and ``ended_at`` are event times. Usage is what the vendor reported, with
  ``complete``; unknown stays ``null``, never zero. No event carries content (prompts, completions, tool input),
  an environment value or a secret: every field is a checked name, id, digest, path, number or fixed word.
* **Derived at read time** (R6.6-b; ``Fold.rows``), in this order:
    an agent's own end                                   as recorded (``done``/``failed``, ``ended_at``)
    an ended parent (or ancestor), no end of its own     ``unknown: end_unobserved``, ``ended_at`` null
    its host not reachable                               ``unknown: host_unreachable`` (``host_stopped`` when the
                                                         host announced a stop), never ``running``
    frozen (it, or the runtime child it runs inside,     ``idle: frozen``
    was frozen after the latest gap)
    no event since its host's latest capture gap         ``unknown: capture_gap``, until its next event
    otherwise                                            its latest reported state
  A wake gap covers every agent of the host (the whole host was suspended). A restart gap covers the agents the
  runtime itself captures (its children and their subagents): the other sources write the file themselves and were
  not interrupted by the runtime's restart.
* **Seeing grants nothing** (R6.6-e): nothing here admits, claims, resumes or stops work, and no census answer is an
  authority transition. **Swarms are recorded, never limited** (R6.6-f). **No model is called** (R6.6-g).
* **Retention** (N3): an agent tree whose agents have all ended (their own end, or ``end_unobserved``) is dropped
  ``RETENTION_DAYS`` after its last end. Only census events and census files are ever dropped; the authority's own
  audit is never touched (``grant.step_bound`` rows enforce one grant operation per card step).
* **Honest children** (R6.6-c): a source that exposes no subagent events says ``children: not_exposed``, never "no
  children".

Stdlib only; Python 3.9-compatible. ``fakework`` and the Claude Code hook load this file by its path (``load``), so it
imports nothing from the package and uses no dataclass (a module loaded by path is not in ``sys.modules`` first).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

LINE_SCHEMA = 1
KINDS = ("census.start", "census.update", "census.end", "census.gap")
STATES = ("running", "idle", "done", "failed", "unknown")
START_STATES = ("running", "idle", "unknown")
END_STATES = ("done", "failed", "unknown")
CHILDREN = ("exposed", "not_exposed")
START_PATHS = ("runtime", "connection", "fallback")
PROFILES = ("unattended", "supervised", "desktop_app")
GAP_CAUSES = ("restart", "wake")
UNITS = ("usd", "usd_notional", "minutes", "test_units")
RETENTION_DAYS = 30                     # N3: census_retention_days, for ended agents
BATCH_MAX_EVENTS = 200                  # one heartbeat's census batch (R3.2-a)
BATCH_MAX_BYTES = 96 * 1024             # its serialized size; the door's body limit (160 KiB) keeps room for the facts
EVENT_MAX_BYTES = 8 * 1024              # one event, as sent
NAMES_MAX = 128                         # environment names kept in a launch summary, per list
FILE_MAX_BYTES = 64 * 1024 * 1024       # one day file; a larger one is not read (shown as damaged)
RUNTIME_SOURCE = "runtime.child"        # the runtime's own capture: a restart gap covers these trees
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_AT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$")
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,159}$")
_WORD = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_VENDOR = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
_ROUTE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$")
_LABEL = re.compile(r"^(subscription|test|api:[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*)$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_PATH = re.compile(r"^(/|~/)[^\x00-\x1f\x7f]{0,1022}$")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")
START_KEYS = ("agent_id", "parent_id", "vendor", "model", "role", "item_id", "state", "reason", "usage", "source",
              "verified", "children", "start_path", "route", "launch", "transcript_ref")
UPDATE_KEYS = ("agent_id", "state", "reason", "usage", "model", "source")
END_KEYS = ("agent_id", "state", "reason", "usage", "source")
GAP_KEYS = ("cause", "from", "to")
USAGE_KEYS = ("input_tokens", "output_tokens", "cost", "unit", "complete")
LAUNCH_KEYS = ("launch_id", "tool", "profile", "route", "binary", "policy_sha256", "env_names", "removed", "governed")
MERGED = ("parent_id", "vendor", "model", "role", "item_id", "source", "verified", "children", "start_path", "route",
          "launch", "transcript_ref")


class CensusRefused(ValueError):
    """An event the census will not record or apply: a fixed code, never the event's content."""


def _need(ok, code):
    if not ok:
        raise CensusRefused(code)


# ── time ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_at(value) -> datetime:
    """An event time: ISO 8601 with seconds and a zone, up to six fraction digits (the store's own rule, parsed here
    rather than by datetime.fromisoformat, whose rules differ between Python 3.9 and later)."""
    m = _AT.match(value) if type(value) is str and len(value) <= 40 else None
    _need(m is not None, "census_at_invalid")
    year, month, day, hour, minute, second, fraction, zone = m.groups()
    try:
        tz = timezone.utc if zone == "Z" else timezone(
            (1 if zone[0] == "+" else -1) * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                        int((fraction or "0").ljust(6, "0")), tzinfo=tz)
    except ValueError:
        raise CensusRefused("census_at_invalid") from None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso(value):
    return None if value is None else value.astimezone(timezone.utc).isoformat()


# ── the event shapes (the authority checks with these too) ────────────────────────────────────────────────────────
def _opt(value, check) -> bool:
    return value is None or check(value)


def _s(rx):
    return lambda v: type(v) is str and rx.match(v) is not None


def check_usage(u):
    """{input_tokens, output_tokens, cost, unit, complete}, each null when not reported (never read as zero)."""
    if u is None:
        return None
    _need(type(u) is dict and set(u) == set(USAGE_KEYS), "census_usage_invalid")
    for k in ("input_tokens", "output_tokens"):
        _need(u[k] is None or (type(u[k]) is int and 0 <= u[k] < 10 ** 12), "census_usage_invalid")
    c = u["cost"]
    _need(c is None or (type(c) in (int, float) and math.isfinite(c) and 0 <= c <= 10 ** 9), "census_usage_invalid")
    _need(u["unit"] is None or u["unit"] in UNITS, "census_usage_invalid")
    _need(c is None or u["unit"] is not None, "census_usage_unit_missing")       # a cost always says its unit
    _need(u["complete"] is None or type(u["complete"]) is bool, "census_usage_invalid")
    return u


def check_launch(rec):
    """The launch record's census form (R6.6-a ``launch``): names, digests and references only, never a value."""
    if rec is None:
        return None
    _need(type(rec) is dict and set(rec) == set(LAUNCH_KEYS), "census_launch_invalid")
    _need(_opt(rec["launch_id"], _s(_UUID)) and type(rec["tool"]) is str and _VENDOR.match(rec["tool"])
          and rec["profile"] in PROFILES and _opt(rec["route"], _s(_ROUTE)) and type(rec["governed"]) is bool
          and _opt(rec["policy_sha256"], _s(_SHA)), "census_launch_invalid")
    b = rec["binary"]
    _need(type(b) is dict and set(b) == {"path", "sha256", "pinned_by"} and _opt(b["path"], _s(_PATH))
          and _opt(b["sha256"], _s(_SHA)) and type(b["pinned_by"]) is str and _WORD.match(b["pinned_by"]),
          "census_launch_invalid")
    for k in ("env_names", "removed"):
        _need(type(rec[k]) is list and len(rec[k]) <= NAMES_MAX and all(type(n) is str and _NAME.match(n)
                                                                        for n in rec[k]), "census_launch_invalid")
    _need(not (rec["profile"] == "desktop_app" and rec["governed"]), "census_launch_desktop_governed")
    return rec


def check_transcript(t):
    """Where the session's own transcript is kept ({kept: true, session_id, path}), or {kept: false, reason}."""
    if t is None:
        return None
    _need(type(t) is dict and type(t.get("kept")) is bool, "census_transcript_invalid")
    if t["kept"]:
        _need(set(t) == {"kept", "session_id", "path"} and _s(_AGENT)(t["session_id"]) and _s(_PATH)(t["path"]),
              "census_transcript_invalid")
    else:
        _need(set(t) == {"kept", "reason"} and _s(_WORD)(t["reason"]), "census_transcript_invalid")
    return t


def _check_body(kind, b):
    if kind == "census.gap":
        _need(type(b) is dict and set(b) == set(GAP_KEYS) and b["cause"] in GAP_CAUSES, "census_body_invalid")
        if b["from"] is not None:
            parse_at(b["from"])
        parse_at(b["to"])
        return
    keys = {"census.start": START_KEYS, "census.update": UPDATE_KEYS, "census.end": END_KEYS}[kind]
    _need(type(b) is dict and set(b) == set(keys), "census_body_invalid")
    _need(_s(_AGENT)(b["agent_id"]) and _s(_WORD)(b["source"]) and _opt(b["reason"], _s(_WORD)), "census_body_invalid")
    check_usage(b["usage"])
    if kind == "census.start":
        _need(b["state"] in START_STATES and _opt(b["parent_id"], _s(_AGENT)) and b["parent_id"] != b["agent_id"]
              and _opt(b["vendor"], _s(_VENDOR)) and _opt(b["model"], _s(_MODEL)) and _opt(b["role"], _s(_ROLE))
              and _opt(b["item_id"], _s(_UUID)) and type(b["verified"]) is bool and b["children"] in CHILDREN
              and _opt(b["start_path"], lambda v: v in START_PATHS) and _opt(b["route"], _s(_LABEL)),
              "census_body_invalid")
        check_launch(b["launch"])
        check_transcript(b["transcript_ref"])
    elif kind == "census.update":
        _need(_opt(b["state"], lambda v: v in START_STATES) and _opt(b["model"], _s(_MODEL)), "census_body_invalid")
    else:
        _need(b["state"] in END_STATES, "census_body_invalid")
    _need(b["state"] != "unknown" or b["reason"] is not None, "census_unknown_without_reason")


def check_event(ev) -> dict:
    """One census event as written and as sent: {event_id, at, kind, body}, strictly; CensusRefused otherwise."""
    _need(type(ev) is dict and set(ev) == {"event_id", "at", "kind", "body"}, "census_event_invalid")
    _need(_s(_UUID)(ev["event_id"]) and ev["kind"] in KINDS, "census_event_invalid")
    parse_at(ev["at"])
    _check_body(ev["kind"], ev["body"])
    try:
        size = len(json.dumps(ev, separators=(",", ":")))
    except (TypeError, ValueError):
        raise CensusRefused("census_event_invalid") from None
    _need(size <= EVENT_MAX_BYTES, "census_event_too_large")
    return ev


# ── builders (every source writes through these) ──────────────────────────────────────────────────────────────────
def usage(*, input_tokens=None, output_tokens=None, cost=None, unit=None, complete=None) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost": cost, "unit": unit,
            "complete": complete}


def start(agent_id, *, source, verified, children, parent_id=None, vendor=None, model=None, role=None, item_id=None,
          state="running", reason=None, usage=None, start_path=None, route=None, launch=None,
          transcript_ref=None) -> dict:
    return {"agent_id": agent_id, "parent_id": parent_id, "vendor": vendor, "model": model, "role": role,
            "item_id": item_id, "state": state, "reason": reason, "usage": usage, "source": source,
            "verified": verified, "children": children, "start_path": start_path, "route": route, "launch": launch,
            "transcript_ref": transcript_ref}


def update(agent_id, *, source, state=None, reason=None, usage=None, model=None) -> dict:
    return {"agent_id": agent_id, "state": state, "reason": reason, "usage": usage, "model": model, "source": source}


def end(agent_id, *, source, state, reason=None, usage=None) -> dict:
    return {"agent_id": agent_id, "state": state, "reason": reason, "usage": usage, "source": source}


def gap(cause, to, frm=None) -> dict:
    return {"cause": cause, "from": frm, "to": to}


def kept(session_id, path) -> dict:
    return {"kept": True, "session_id": session_id, "path": path}


def not_kept(reason) -> dict:
    return {"kept": False, "reason": reason}


def route_label(name, entry) -> str | None:
    """R6.6-a ``route``: ``subscription``, ``api:<route>`` or ``test``, from the launch policy's entry for the route the
    item names (the exact route name stays in ``launch.route``)."""
    if not isinstance(entry, dict):
        return None
    if entry.get("test") or entry.get("kind") == "test":
        return "test"
    if entry.get("kind") == "api":
        return "api:" + name
    return "subscription" if entry.get("kind") == "subscription" else None


def _names(names) -> list:
    """At most NAMES_MAX names, sorted; a longer list keeps its first NAMES_MAX (never silently: see the summary)."""
    return sorted(set(n for n in names if isinstance(n, str) and _NAME.match(n)))[:NAMES_MAX]


def launch_summary(rec, *, governed=True) -> dict:
    """A ``minimoi.launch.v1`` record (launch.record) in the census's form: the profile, the binary's digest, the
    policy's digest, the names given and the names removed. Never a value."""
    b = rec.get("binary") or {}
    return {"launch_id": rec.get("launch_id"), "tool": rec.get("tool"), "profile": rec.get("profile"),
            "route": rec.get("route"), "binary": {"path": b.get("path"), "sha256": b.get("sha256"),
                                                  "pinned_by": b.get("pinned_by") or "unknown"},
            "policy_sha256": (rec.get("policy") or {}).get("sha256"), "env_names": _names(rec.get("env_names") or []),
            "removed": _names(rec.get("removed") or []), "governed": governed}


# ── hook payloads (Claude Code's hooks; fakework's spawn writes the same shapes) ─────────────────────────────────────
# SessionStart, PreToolUse and Stop are consumed in the repo already (spec R6.6-c): verified. These are not (N4).
SUBAGENT_HOOKS = ("SubagentStart", "SubagentStop")


def hook_events(payload, *, source, verified, subagents, vendor, start_path=None, route=None,
                subagent_verified=False) -> list:
    """The census events one hook payload means, as (kind, body). Only ids, the event's name and a transcript path
    are read; everything else in the payload (prompts, tool input, messages) is never looked at. ``subagents`` is
    False until a source's subagent events are confirmed against the installed version (N4): then a session says
    ``children: not_exposed`` and subagent payloads write nothing. ``verified`` is whether this source's session events
    are verified for the installed version; ``subagent_verified`` is the same for its subagent events (False for every
    vendor until confirmed; True only for the runtime's own instrument, fakework)."""
    if not isinstance(payload, dict):
        return []
    name, session = payload.get("hook_event_name"), payload.get("session_id")
    if not (type(session) is str and _AGENT.match(session)):
        return []
    if name == "SessionStart":
        path = payload.get("transcript_path")
        ref = kept(session, path) if type(path) is str and _PATH.match(path) else None
        return [("census.start", start(session, source=source, verified=verified, vendor=vendor, role="session",
                                       children="exposed" if subagents else "not_exposed", start_path=start_path,
                                       route=route, transcript_ref=ref))]
    if name == "PreToolUse":
        return [("census.update", update(session, source=source, state="running"))]
    if name == "Stop":
        return [("census.update", update(session, source=source, state="idle", reason="turn_ended"))]
    if name in SUBAGENT_HOOKS and subagents:
        agent = payload.get("agent_id")
        if not (type(agent) is str and _AGENT.match(agent)) or agent == session:
            return []
        if name == "SubagentStart":
            role = payload.get("agent_type")
            return [("census.start", start(agent, parent_id=session, source=source, verified=subagent_verified,
                                           vendor=vendor, role=role if type(role) is str and _ROLE.match(role) else None,
                                           children="not_exposed"))]
        return [("census.end", end(agent, source=source, state="done", reason="stop_observed"))]
    return []


# ── the day files ─────────────────────────────────────────────────────────────────────────────────────────────────
def _canon(doc) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sum(doc) -> str:
    return hashlib.sha256(_canon({k: v for k, v in doc.items() if k != "sum"})).hexdigest()


def _line(doc) -> bytes:
    doc = {**doc, "v": LINE_SCHEMA}
    doc["sum"] = _sum(doc)
    return _canon(doc) + b"\n"


def _wire(doc) -> dict:
    return {"event_id": doc["event_id"], "at": doc["at"], "kind": doc["kind"], "body": doc["body"]}


def _day_of(at) -> str:
    return parse_at(at).astimezone(timezone.utc).date().isoformat()


class CensusFile:
    """The census folder of one host: its writers append, the runtime reads it incrementally (only bytes it has not
    read), keeps the fold and the unsent events, and writes the authority's answers back as ack lines."""

    def __init__(self, root, *, clock=now_iso):
        self.root = Path(root)
        self._clock = clock
        self._mu = threading.RLock()
        self._offsets = {}            # file name -> bytes read (complete lines only)
        self._unsent = {}             # event_id -> wire event, in file order
        self._file_of = {}            # event_id -> file name
        self._agents_in = {}          # file name -> agent ids with an event in it
        self._files_of = {}           # agent id -> file names holding its events
        self._gaps_in = {}            # file name -> gap event ids in it
        self._unsent_in = {}          # file name -> count of unsent events
        self.damaged = {}             # file name -> lines that did not check (never sent)
        self.fold = Fold()

    # writing
    def _locked(self, exclusive=True):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return fd

    def _append_line(self, name, line):
        path = self.root / name
        new = not path.exists()
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = b"\n" + line                     # seal a torn last line: it never swallows this one
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if new:
            dfd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

    def append(self, kind, body, *, at=None, event_id=None) -> dict:
        """Write one event, checked, fsynced, before anything relies on it. Returns it as it will be sent."""
        ev = check_event({"event_id": event_id or str(uuid.uuid4()), "at": at or self._clock(), "kind": kind,
                          "body": body})
        line = _line({"type": "event", **ev})
        fd = self._locked()
        try:
            self._append_line(_day_of(ev["at"]) + ".jsonl", line)
        finally:
            os.close(fd)
        return ev

    def acknowledge(self, answers) -> int:
        """The authority's answer for each sent event: ``applied`` and ``already_applied`` stop its sending, and so does
        ``rejected`` (with its code; the same bytes would get the same answer). Anything else leaves it unsent."""
        done = 0
        fd = self._locked()
        try:
            with self._mu:
                for a in answers or []:
                    if not isinstance(a, dict) or a.get("state") not in ("applied", "already_applied", "rejected"):
                        continue
                    eid = a.get("event_id")
                    name = self._file_of.get(eid)
                    if name is None or eid not in self._unsent:
                        continue
                    ans = {"state": a["state"]}
                    if a["state"] == "rejected":
                        code = a.get("code")
                        ans["code"] = code if type(code) is str and _WORD.match(code) else "census_rejected"
                    self._append_line(name, _line({"type": "ack", "event_id": eid, "answer": ans}))
                    self._unsent.pop(eid, None)
                    self._unsent_in[name] = self._unsent_in.get(name, 1) - 1
                    done += 1
        finally:
            os.close(fd)
        return done

    # reading
    def _files(self) -> list:
        try:
            return sorted(n for n in os.listdir(self.root) if _DAY.match(n))
        except FileNotFoundError:
            return []

    def scan(self) -> int:
        """Read what was appended since the last scan (complete lines only). Returns the number of new lines."""
        if not self.root.exists():
            return 0
        fd = self._locked(exclusive=False)
        try:
            with self._mu:
                new = 0
                for name in self._files():
                    new += self._scan_file(name)
                return new
        finally:
            os.close(fd)

    def _scan_file(self, name) -> int:
        path, start = self.root / name, self._offsets.get(name, 0)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return 0
        if size > FILE_MAX_BYTES:
            self.damaged[name] = self.damaged.get(name, 0) + (1 if start == 0 else 0)
            self._offsets[name] = size
            return 0
        if size <= start:
            return 0
        with open(path, "rb") as fh:
            fh.seek(start)
            raw = fh.read(size - start)
        end = raw.rfind(b"\n")
        if end < 0:
            return 0                                      # a line still being written: read it next time
        self._offsets[name] = start + end + 1
        n = 0
        for line in raw[:end].split(b"\n"):
            n += 1
            self._take(name, line)
        return n

    def _take(self, name, line):
        try:
            doc = json.loads(line.decode("utf-8"))
            ok = type(doc) is dict and doc.get("v") == LINE_SCHEMA and doc.get("sum") == _sum(doc)
        except (ValueError, UnicodeDecodeError):
            ok = False
        if ok and doc.get("type") == "event" and set(doc) == {"v", "sum", "type", "event_id", "at", "kind", "body"}:
            try:
                ev = check_event(_wire(doc))
            except CensusRefused:
                ok = False
            else:
                if ev["event_id"] not in self._file_of:
                    self._file_of[ev["event_id"]] = name
                    self._unsent[ev["event_id"]] = ev
                    self._unsent_in[name] = self._unsent_in.get(name, 0) + 1
                    self.fold.apply(ev, None)
                    aid = ev["body"].get("agent_id")
                    if aid is not None:
                        self._agents_in.setdefault(name, set()).add(aid)
                        self._files_of.setdefault(aid, set()).add(name)
                    else:
                        self._gaps_in.setdefault(name, set()).add(ev["event_id"])
                return
        if ok and doc.get("type") == "ack" and set(doc) == {"v", "sum", "type", "event_id", "answer"}:
            if self._unsent.pop(doc["event_id"], None) is not None:
                self._unsent_in[name] = self._unsent_in.get(name, 1) - 1
            return
        if line.strip():
            self.damaged[name] = self.damaged.get(name, 0) + 1

    def batch(self, *, max_events=BATCH_MAX_EVENTS, max_bytes=BATCH_MAX_BYTES) -> list:
        """Unsent events, oldest first, exactly as written, within both bounds (always at least one when any is
        unsent: one event is at most EVENT_MAX_BYTES)."""
        self.scan()
        out, size = [], 2
        with self._mu:
            for ev in self._unsent.values():
                n = len(json.dumps(ev)) + 2
                if out and (len(out) >= max_events or size + n > max_bytes):
                    break
                out.append(ev)
                size += n
        return out

    def unsent(self) -> list:
        self.scan()
        with self._mu:
            return list(self._unsent.values())

    def rows(self, *, host_id, environment, reachable=True) -> list:
        """This host's own view of its census (R6.6-b), rebuilt from its file (after a restart, from the start)."""
        self.scan()
        with self._mu:
            return self.fold.rows(reachability={None: "reachable" if reachable else "unreachable"},
                                  environment=environment, host_override=host_id)

    def retain(self, *, now=None, days=RETENTION_DAYS) -> list:
        """N3 on the host: a day file older than ``days`` goes when nothing in it is unsent, it holds no host's latest
        capture gap, and every agent with an event in it belongs to a tree that ended more than ``days`` ago, whose
        other files go with it. Returns the files removed. Only census files are ever removed."""
        now = parse_at(now) if isinstance(now, str) else (now or datetime.now(timezone.utc))
        cutoff = now - timedelta(days=days)
        fd = self._locked()
        try:
            with self._mu:
                self._scan_locked()
                closed = self.fold.closed_trees(cutoff)
                latest = set(self.fold.gap_events.values())
                cand = {n for n in self._files()
                        if datetime(*map(int, _DAY.match(n).groups()), tzinfo=timezone.utc) + timedelta(days=1) <= cutoff
                        and not self._unsent_in.get(n) and not (self._gaps_in.get(n, set()) & latest)
                        and self._agents_in.get(n, set()) <= closed and not self.damaged.get(n)}
                changed = True
                while changed:                               # a tree's files go together, or none of them
                    changed = False
                    for n in sorted(cand):
                        if any(not self._files_of.get(a, set()) <= cand for a in self._agents_in.get(n, set())):
                            cand.discard(n)
                            changed = True
                for n in sorted(cand):
                    os.unlink(self.root / n)
                gone = set().union(*(self._agents_in.get(n, set()) for n in cand)) if cand else set()
                self.fold.forget(gone)
                for n in cand:
                    for d in (self._offsets, self._agents_in, self._gaps_in, self._unsent_in):
                        d.pop(n, None)
                for a in gone:
                    self._files_of.pop(a, None)
                self._file_of = {e: n for e, n in self._file_of.items() if n not in cand}
                return sorted(cand)
        finally:
            os.close(fd)

    def _scan_locked(self):
        for name in self._files():
            self._scan_file(name)


# ── the fold: events to rows, and the derived state ───────────────────────────────────────────────────────────────
def host_reachability(seen_at, announced, now, timeout_s) -> str:
    """R5.1-a at read time: ``reachable`` when the last contact is at most ``timeout_s`` old; ``stopping``/``stopped``
    when that contact announced it; otherwise ``unreachable``. ``unknown`` without a contact or a timeout."""
    if seen_at is None or timeout_s is None or now is None:
        return "unknown"
    if announced in ("stopping", "stopped"):
        return announced
    return "reachable" if (now - seen_at).total_seconds() <= timeout_s else "unreachable"


def _new(agent_id, host_id):
    return {"agent_id": agent_id, "parent_id": None, "vendor": None, "model": None, "role": None, "item_id": None,
            "source": None, "verified": None, "children": None, "start_path": None, "route": None, "launch": None,
            "transcript_ref": None, "usage": None, "host_id": host_id, "started_at": None, "last_activity_at": None,
            "ended_at": None, "state": None, "reason": None, "state_at": None, "usage_at": None, "placeholder": False}


class Fold:
    """Census events folded into one record per agent (each ``agent_id`` once, however many events), in any order:
    each value is the one from its latest event by event time. ``rows`` derives the state shown (R6.6-b)."""

    def __init__(self):
        self.agents = {}
        self.gaps = {}                 # (host_id, cause) -> latest gap end (host time)
        self.gap_events = {}           # (host_id, cause) -> the event id of that latest gap

    def apply(self, ev, host_id):
        at, kind, b = parse_at(ev["at"]), ev["kind"], ev["body"]
        if kind == "census.gap":
            key = (host_id, b["cause"])
            to = parse_at(b["to"])
            if key not in self.gaps or to >= self.gaps[key]:
                self.gaps[key], self.gap_events[key] = to, ev["event_id"]
            return
        rec = self.agents.get(b["agent_id"])
        if rec is None:
            rec = self.agents[b["agent_id"]] = _new(b["agent_id"], host_id)
        if rec["host_id"] is None:
            rec["host_id"] = host_id
        rec["last_activity_at"] = at if rec["last_activity_at"] is None else max(rec["last_activity_at"], at)
        if kind == "census.start":
            for k in MERGED:
                if b[k] is not None:
                    rec[k] = b[k]
            rec["started_at"] = at if rec["started_at"] is None else min(rec["started_at"], at)
        elif rec["source"] is None:
            rec["source"] = b["source"]
        if kind == "census.update" and b["model"] is not None:
            rec["model"] = b["model"]
        if b.get("usage") is not None and (rec["usage_at"] is None or at >= rec["usage_at"]):
            rec["usage"], rec["usage_at"] = b["usage"], at
        if rec["state_at"] is not None and at < rec["state_at"]:
            return                                             # an older state never overwrites a newer one
        if kind == "census.update":
            if b["state"] is None:
                return                                         # activity only: the state stands
            rec["state"], rec["reason"], rec["ended_at"] = b["state"], b["reason"], None
        elif kind == "census.end":
            rec["state"], rec["reason"], rec["ended_at"] = b["state"], b["reason"], at
        else:
            rec["state"], rec["reason"], rec["ended_at"] = b["state"], b["reason"], None
        rec["state_at"] = at

    def forget(self, agent_ids):
        for a in agent_ids:
            self.agents.pop(a, None)

    # derivation
    def _all(self) -> dict:
        """The agents, plus a placeholder for each parent named but never recorded (so every link resolves)."""
        out = dict(self.agents)
        for rec in self.agents.values():
            p = rec["parent_id"]
            if p is not None and p not in out:
                ph = _new(p, rec["host_id"])
                ph.update(state="unknown", reason="not_captured", placeholder=True, source="census.parent_only")
                out[p] = ph
        return out

    def _chain(self, agents, aid):
        """The agent and its ancestors, nearest first; a cycle in bad data stops the walk."""
        seen, out = set(), []
        while aid is not None and aid in agents and aid not in seen:
            seen.add(aid)
            out.append(agents[aid])
            aid = agents[aid]["parent_id"]
        return out

    def _closing(self, agents, aid):
        """When the agent's life closed: its own end, or the nearest ended ancestor's (end_unobserved); else None."""
        for rec in self._chain(agents, aid):
            if rec["ended_at"] is not None:
                return rec["ended_at"]
        return None

    def _gap(self, rec, root, host):
        g = [self.gaps.get((host, "wake"))]
        if root is not None and root["source"] == RUNTIME_SOURCE:
            g.append(self.gaps.get((host, "restart")))
        g = [x for x in g if x is not None]
        return max(g) if g else None

    def derive(self, agents, aid, reachability, host_override=None) -> tuple:
        rec = agents[aid]
        if rec["placeholder"]:
            return "unknown", "not_captured"
        if rec["ended_at"] is not None:
            return rec["state"], rec["reason"]
        chain = self._chain(agents, aid)
        if any(r["ended_at"] is not None for r in chain[1:]):
            return "unknown", "end_unobserved"
        host = rec["host_id"] if host_override is None else None
        reach = reachability.get(host, "unknown")
        if reach != "reachable":
            return "unknown", "host_stopped" if reach in ("stopping", "stopped") else "host_unreachable"
        root = chain[-1]
        for r in chain:                                      # frozen: it, or the runtime child it runs inside
            if r["placeholder"] or r["host_id"] != rec["host_id"]:
                break
            g = self._gap(r, root, r["host_id"])
            if r["state"] == "idle" and r["reason"] == "frozen" and (g is None or r["state_at"] >= g):
                return "idle", "frozen"
        g = self._gap(rec, root, rec["host_id"])
        if g is not None and (rec["last_activity_at"] is None or rec["last_activity_at"] < g):
            return "unknown", "capture_gap"
        if rec["state"] is None:
            return "unknown", "state_not_reported"
        return rec["state"], rec["reason"]

    def rows(self, *, reachability, environment, host_override=None) -> list:
        """The census as R7.6-b's ``agents`` (every R6.6-a field): one row per agent, the state derived now.
        ``reachability`` maps host_id to R5.1-a's word; ``host_override`` names the host for a host's own view."""
        agents = self._all()
        out = []
        for aid in sorted(agents, key=lambda a: (iso(agents[a]["started_at"] or agents[a]["last_activity_at"]) or "",
                                                 a)):
            rec = agents[aid]
            state, reason = self.derive(agents, aid, reachability, host_override)
            chain = self._chain(agents, aid)
            root = chain[-1]

            def inherited(key):
                return next((r[key] for r in chain if r[key] is not None), None)
            ended = rec["ended_at"] if state in END_STATES and rec["ended_at"] is not None and \
                reason != "end_unobserved" else None
            u = rec["usage"] or usage()
            out.append({
                "agent_id": aid, "parent_id": rec["parent_id"], "vendor": rec["vendor"], "model": rec["model"],
                "role": rec["role"], "host_id": host_override if host_override is not None else rec["host_id"],
                "environment": environment, "item_id": inherited("item_id"),
                "started_at": iso(rec["started_at"]), "last_activity_at": iso(rec["last_activity_at"]),
                "ended_at": iso(ended), "state": state, "reason": reason, "usage": dict(u),
                "source": rec["source"], "verified": rec["verified"], "children": rec["children"],
                "start_path": root["start_path"] if rec["parent_id"] is not None else rec["start_path"],
                "route": rec["route"] if rec["route"] is not None else root["route"],
                "launch": rec["launch"], "transcript_ref": rec["transcript_ref"]})
        return out

    def closed_trees(self, cutoff) -> set:
        """The agent ids of every tree whose agents have all closed (their own end, or end_unobserved), the last of
        them before ``cutoff``: what retention may drop. A tree with any open agent is kept whole."""
        agents = self._all()
        trees = {}
        for aid in self.agents:
            trees.setdefault(self._chain(agents, aid)[-1]["agent_id"], []).append(aid)
        out = set()
        for members in trees.values():
            closes = [self._closing(agents, a) for a in members]
            if all(c is not None for c in closes) and max(closes) < cutoff:
                out.update(members)
        return out
