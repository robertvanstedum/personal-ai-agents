"""The export seam: a day of the Workshop journal as a memory-shelf Bundle (v0.6 section 11; v0.7 Unit 4).

Fixture-first and read-only. This module reads journal events and builds the existing shelf ``Bundle`` through the existing
``render`` and ``sessions`` APIs; it registers nothing in the shelf (no provider, chair, normalizer or index policy) and writes
nothing to any shelf. The registration the shelf needs is listed in ``REGISTRATION`` so the cutover review can read exactly
what is being asked for.

Rules fixed here, each with a test:

- **Day key:** the local calendar date (America/Chicago) of the helper's ``recorded_at``, never caller time. Events of a day are
  exported in journal (``seq``) order, so a repeated wall-clock hour at the end of daylight saving time orders correctly.
- **Source key:** ``workshop:<workshop-id>:<stream>:<YYYY-MM-DD>``; provider ``workshop``, origin ``workshop-export``, kind
  ``session``, session class ``handoff``, chair ``Workshop`` (an aggregate, like ``Rooms``).
- **Coordination, structurally:** every turn is speaker ``coordination`` with the participant in ``who``. Nothing is relabelled
  human or assistant to be searchable; the shelf's own dialogue filter then leaves the day out of ordinary retrieval.
- **Prefix identity:** ``source_hash`` is the SHA-256 of the exact canonical bytes of the exported events (a validated complete
  prefix), never of a live file. ``source_revision`` is the highest journal sequence included, so the shelf refuses an older
  prefix and a conflicting one by itself.
- **Exclusions:** health and delivery events are counted, not exported (policy ``workshop-day-v1``); events citing excluded
  evidence are exported without it and counted.
- **Cadence:** pure functions decide when a changed day may be exported (60 s coalescing, 15-minute reconciliation, at most 96
  automatic editions a day, then ``export_deferred_budget``); nothing here runs a timer.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.workshop_journal import strictjson

TIMEZONE = "America/Chicago"
POLICY_ID = "workshop-day-v1"
PROVIDER, ORIGIN, CHAIR, SESSION_CLASS, NORMALIZER = "workshop", "workshop-export", "Workshop", "handoff", 1
EXCLUDED_KINDS = ("health", "delivery")
MATERIAL_KINDS = ("request", "result", "decision", "needs_you", "done", "next", "blocked", "release", "claim")
COALESCE_SECONDS, RECONCILE_SECONDS, MAX_EDITIONS_PER_DAY = 60, 15 * 60, 96

# What the shelf has to register before it will accept this bundle, as the cutover review needs it. Nothing here is applied.
REGISTRATION = {
    "record.CHAIRS": "add 'Workshop'",
    "sessions.NORMALIZER_VERSION": {"workshop": NORMALIZER},
    "sessions.RETRIEVAL_MIN_NORMALIZER": {"workshop": NORMALIZER},
    "memory_index.policy": "no change: coordination turns stay out of ordinary dialogue retrieval; only the provider floor is added",
}


class ShelfUnavailable(RuntimeError):
    """The memory shelf code is not importable from this tree (it lives on the memory branch until the cutover)."""


def day_key(recorded_at: str, zone: str = TIMEZONE) -> str:
    moment = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d")


def source_key(workshop_id: str, stream: str, day: str) -> str:
    return f"{PROVIDER}:{workshop_id}:{stream}:{day}"


def approval_fingerprint(*, root: str, workshop_id: str, stream: str, selection_policy_id: str = POLICY_ID,
                         never_copy: tuple[str, ...] = ()) -> str:
    """What an owner approval of this source binds: kind, root, workshop, stream, selection policy and never-copy list.
    The format normalizer version is deliberately not part of it: a format-only upgrade is a reviewed reprocess, whereas a
    change of scope or privacy selection invalidates the approval."""
    return hashlib.sha256(strictjson.canonical_bytes({
        "kind": "workshop", "root": root, "workshop": workshop_id, "stream": stream, "selection_policy_id": selection_policy_id,
        "never_copy": sorted(never_copy)})).hexdigest()


@dataclass
class DayExport:
    day: str
    rows: list = field(default_factory=list)              # exported journal events, in seq order
    excluded: Counter = field(default_factory=Counter)    # reason -> count
    first_seq: int = 0
    through_seq: int = 0
    prefix: bytes = b""
    prefix_sha256: str = ""
    artifacts: dict = field(default_factory=dict)         # retained hash -> number of citing events

    @property
    def event_count(self) -> int:
        return len(self.rows)

    def listing(self) -> dict:
        """Counts only: day, sequence range, sizes, hashes and exclusion counts. No titles, no narrative."""
        return {"day": self.day, "first_seq": self.first_seq, "through_seq": self.through_seq, "events": self.event_count,
                "prefix_bytes": len(self.prefix), "prefix_sha256": self.prefix_sha256, "artifacts": len(self.artifacts),
                "excluded": dict(sorted(self.excluded.items()))}


def day_export(events: list[dict], day: str, *, through_seq: int | None = None, zone: str = TIMEZONE) -> DayExport:
    """The validated prefix of one local day, up to ``through_seq`` (default: all), in journal order."""
    out = DayExport(day)
    for ev in events:
        if ev.get("v") != 2:
            continue                                              # v1 rows carry no recorded time; they are never exported
        if through_seq is not None and ev["seq"] > through_seq:
            break
        if day_key(ev["recorded_at"], zone) != day:
            continue
        if ev["kind"] in EXCLUDED_KINDS:
            out.excluded[f"kind:{ev['kind']}"] += 1
            continue
        refs = [r for r in ev.get("refs") or [] if r.get("availability") != "excluded"]
        if len(refs) != len(ev.get("refs") or []):
            out.excluded["excluded_reference"] += len(ev["refs"]) - len(refs)
            ev = {**ev, "refs": refs}
        out.rows.append(ev)
        out.first_seq = out.first_seq or ev["seq"]
        out.through_seq = ev["seq"]
        for r in refs:
            if r.get("type") == "artifact" and r.get("sha256") and r.get("availability") == "retained":
                out.artifacts[r["sha256"]] = out.artifacts.get(r["sha256"], 0) + 1
    out.excluded = Counter({k: v for k, v in out.excluded.items() if v})
    out.prefix = b"".join(strictjson.canonical_bytes(r) + b"\n" for r in out.rows)
    out.prefix_sha256 = hashlib.sha256(out.prefix).hexdigest()
    return out


def days_in(events: list[dict], zone: str = TIMEZONE) -> list[str]:
    return sorted({day_key(e["recorded_at"], zone) for e in events if e.get("v") == 2})


# ── when a changed day may be exported ───────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DayState:
    """What the exporter has already done for one day (kept by the caller; nothing here persists it)."""
    exported_through_seq: int = 0
    exported_sha256: str = ""
    editions: int = 0
    last_export_at: datetime | None = None


def _when(ev: dict) -> datetime:
    return datetime.fromisoformat(ev["recorded_at"].replace("Z", "+00:00"))


def decide(export: DayExport, state: DayState, now: datetime, *, closed: bool = False) -> tuple[str, str]:
    """(action, reason): ``export``, ``wait`` or ``deferred``. Pure; the caller supplies the clock.

    - nothing new since the last export → wait (``unchanged``);
    - a day that has ended exports whenever it changed (it is final);
    - more than the daily budget of automatic editions → ``export_deferred_budget``, events stay durable in the journal;
    - a material event (request, result, decision, question, done...) exports once the oldest unexported one is a coalescing
      window old (the batch is at most 60 seconds, however busy the day is); anything else (receipts, progress) waits for the
      15-minute reconciliation."""
    if export.through_seq <= state.exported_through_seq or export.prefix_sha256 == state.exported_sha256:
        return "wait", "unchanged"
    if state.editions >= MAX_EDITIONS_PER_DAY and not closed:
        return "deferred", "export_deferred_budget"
    if closed:
        return "export", "day_closed"
    new = [r for r in export.rows if r["seq"] > state.exported_through_seq]
    material = [r for r in new if r["kind"] in MATERIAL_KINDS]
    if material:                                           # batch for at most the coalescing window, even while activity continues
        oldest = min(_when(r) for r in material)
        return ("export", "material_boundary") if (now - oldest).total_seconds() >= COALESCE_SECONDS else ("wait", "coalescing")
    since = None if state.last_export_at is None else (now - state.last_export_at).total_seconds()
    if since is None or since >= RECONCILE_SECONDS:
        return "export", "reconciliation"
    return "wait", "reconciliation_not_due"


# ── the bundle ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _shelf():
    try:
        from core.memory_shelf import bundle, render, sessions
    except ImportError as exc:                                  # the shelf is on the memory branch until the cutover
        raise ShelfUnavailable("core.memory_shelf is not importable from this tree") from exc
    return bundle, render, sessions


def _token(value: str) -> str:
    return "".join(c if c.isalnum() or c in "_:.+-" else "_" for c in str(value))[:64]


def build_bundle(workshop_id: str, stream: str, export: DayExport):
    """The shelf ``Bundle`` for this validated prefix. Raises ShelfUnavailable if the shelf code is absent. Writes nothing."""
    bundle_mod, _render, sessions = _shelf()
    if not export.rows:
        raise ValueError("an empty day is not exported")
    first_at = export.rows[0]["recorded_at"]
    key = source_key(workshop_id, stream, export.day)
    parsed = sessions.Parsed(PROVIDER, CHAIR, source_id=key.split(":", 1)[1], started=first_at, identity="source", designatable=False,
                             session_class=SESSION_CLASS, normalizer=NORMALIZER)
    parsed.lines = len(export.rows)
    actors: dict[str, int] = {}
    for ev in export.rows:
        attrs = {"who": _token(ev["actor"]), "seq": ev["seq"], "kind": ev["kind"], "rid": ev["event_id"], "ing": ev["recorded_at"],
                 "ts": ev["at"], "class": SESSION_CLASS}
        if ev.get("in_reply_to"):
            attrs["reply"] = ev["in_reply_to"]
        if ev.get("supersedes"):
            attrs["corrects"] = ev["supersedes"]
        if ev.get("recipients"):
            attrs["to"] = _token("+".join(ev["recipients"]))
        actors[ev["actor"]] = actors.get(ev["actor"], 0) + 1
        parsed.add(sessions.COORDINATION, f"{ev['kind']}: {ev['text']}", ev["seq"], f"workshop:{ev['kind']}", attrs)
    parsed.participants = [{"id": a, "kind": "human" if a == "robert" else "agent", "role": "participant", "label": a}
                           for a in sorted(actors)]
    parsed.references = [{"kind": "artifact", "target": sha, "label": f"retained document cited by {n} event(s)", "reference_id": sha[:16]}
                         for sha, n in sorted(export.artifacts.items())]
    parsed.manifest = {"workshop": workshop_id, "stream": stream, "day": export.day, "timezone": TIMEZONE, "through_seq": export.through_seq,
                       "source_revision": export.through_seq, "selection_policy_id": POLICY_ID, "events": export.event_count,
                       "excluded": dict(sorted(export.excluded.items())), "artifacts": len(export.artifacts)}
    parsed.coverage = {"seen": {"events": export.event_count + sum(export.excluded.values())}, "taken": {"events": export.event_count},
                       "gap": {"events": 0}, "flags": []}
    return bundle_mod.from_parsed(parsed, export.prefix_sha256, len(export.prefix), key=key, title=f"Workshop {export.day}",
                                  origin=ORIGIN, created=bundle_mod.utc(first_at),
                                  ledger_key=bundle_mod.ledger_key_for(PROVIDER, key), kind="session")
