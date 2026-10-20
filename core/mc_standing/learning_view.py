"""P-G7: the learning line in the morning brief, read from the harness's count snapshot
(standing spec v0.3.1 §7.1 and P-15; harness v0.3.1 §14.6).

The harness (Codex) publishes a sanitized count snapshot; this module only validates and renders
it. It never sees statement text: the snapshot carries counts, coverage, ``as_of`` and, at most,
topic labels the publisher already authorized for the site conversation.

Proposed wire shape (``mc-learning-count-snapshot/1``; Codex owns the publisher and the final
schema, recorded here rather than silently assumed)::

    {schema, snapshot_id, principal, environment: "dev"|"prod", as_of: UTC "…Z",
     coverage: "complete"|"partial"|"unavailable", adopted_window_days: 1..31,
     counts: {pending_suggestions, adopted_in_window,
              repeated_corrections_confirmed, repeated_corrections_provisional},
     site_labels: [{kind: "repeated_correction"|"pending_suggestion", label}]   # optional, ≤5

Rules enforced here:
* Unknown is not zero. A missing, unreadable, malformed, foreign, future or stale snapshot gives
  a "learning counts unavailable" line, never silence and never a zero.
* Valid zero counts with complete coverage render nothing (a quiet day is not padded).
* Partial coverage is said out loud; confirmed and provisional repeats are kept apart.
* Labels appear only for the site destination and only when the caller permits them; any other
  destination (the optional Telegram copy) gets counts only.
* Reading the snapshot can never fail the brief: every fault becomes an unavailable view.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SCHEMA = "mc-learning-count-snapshot/1"
MAX_BYTES = 8192
MAX_COUNT = 100_000
MAX_LABELS = 5
DEFAULT_MAX_AGE = timedelta(hours=36)
CLOCK_SKEW = timedelta(minutes=5)
COUNT_KEYS = ("pending_suggestions", "adopted_in_window",
              "repeated_corrections_confirmed", "repeated_corrections_provisional")
_TOP_KEYS = {"schema", "snapshot_id", "principal", "environment", "as_of", "coverage",
             "adopted_window_days", "counts", "site_labels"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.'/&+-]{0,39}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class SnapshotInvalid(ValueError):
    pass


@dataclass(frozen=True)
class LearningView:
    status: str                       # ok | unavailable | stale | invalid | mismatch
    reason: str | None = None
    snapshot_id: str | None = None
    as_of: str | None = None
    coverage: str | None = None
    adopted_window_days: int = 7
    counts: dict = field(default_factory=dict)
    site_labels: tuple = ()

    @property
    def known(self) -> bool:
        return self.status == "ok"

    def record(self) -> dict:
        """What the brief payload keeps as evidence: identity, as_of, coverage and counts."""
        return {"status": self.status, "reason": self.reason, "snapshot_id": self.snapshot_id,
                "as_of": self.as_of, "coverage": self.coverage,
                "adopted_window_days": self.adopted_window_days, "counts": dict(self.counts)}


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        raise SnapshotInvalid("count_invalid")
    return value


def parse(doc, *, principal: str, environment: str, now: datetime,
          max_age: timedelta = DEFAULT_MAX_AGE) -> LearningView:
    """Strictly validate one snapshot document (bytes, str or dict)."""
    if isinstance(doc, (bytes, str)):
        raw = doc.encode() if isinstance(doc, str) else doc
        if len(raw) > MAX_BYTES:
            raise SnapshotInvalid("snapshot_too_large")
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            raise SnapshotInvalid("snapshot_not_json") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise SnapshotInvalid("schema_unknown")
    if set(doc) - _TOP_KEYS:
        raise SnapshotInvalid("unexpected_field")
    snapshot_id = doc.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not _ID.match(snapshot_id):
        raise SnapshotInvalid("snapshot_id_invalid")
    if doc.get("principal") != principal or doc.get("environment") != environment:
        return LearningView("mismatch", "principal_or_environment_mismatch", snapshot_id=snapshot_id)
    as_of = doc.get("as_of")
    if not isinstance(as_of, str) or not _TS.match(as_of):
        raise SnapshotInvalid("as_of_invalid")
    taken = datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if taken > now + CLOCK_SKEW:
        raise SnapshotInvalid("as_of_in_future")
    coverage = doc.get("coverage")
    if coverage not in ("complete", "partial", "unavailable"):
        raise SnapshotInvalid("coverage_invalid")
    window = doc.get("adopted_window_days", 7)
    if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 31:
        raise SnapshotInvalid("window_invalid")
    counts = doc.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(COUNT_KEYS):
        raise SnapshotInvalid("counts_invalid")
    counts = {k: _count(counts[k]) for k in COUNT_KEYS}
    labels = doc.get("site_labels", [])
    if not isinstance(labels, list) or len(labels) > MAX_LABELS:
        raise SnapshotInvalid("labels_invalid")
    clean = []
    for item in labels:
        if (not isinstance(item, dict) or set(item) != {"kind", "label"}
                or item["kind"] not in ("repeated_correction", "pending_suggestion")
                or not isinstance(item["label"], str) or not _LABEL.match(item["label"])):
            raise SnapshotInvalid("label_invalid")
        clean.append((item["kind"], item["label"]))
    base = dict(snapshot_id=snapshot_id, as_of=as_of, coverage=coverage, adopted_window_days=window)
    if coverage == "unavailable":
        return LearningView("unavailable", "publisher_coverage_unavailable", **base)
    if now - taken > max_age:
        return LearningView("stale", "snapshot_stale", **base)
    return LearningView("ok", None, counts=counts, site_labels=tuple(clean), **base)


def read_view(fetch, *, principal: str, environment: str, now: datetime,
              max_age: timedelta = DEFAULT_MAX_AGE) -> LearningView:
    """Never raises: the brief must not depend on learning data being available."""
    try:
        doc = fetch()
    except Exception:
        return LearningView("unavailable", "snapshot_fetch_failed")
    if doc is None:
        return LearningView("unavailable", "snapshot_missing")
    try:
        return parse(doc, principal=principal, environment=environment, now=now, max_age=max_age)
    except SnapshotInvalid as exc:
        return LearningView("invalid", str(exc))
    except Exception:
        return LearningView("invalid", "snapshot_unreadable")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def learning_line(view: LearningView | None, *, destination: str = "site",
                  labels_permitted: bool = False) -> str | None:
    """One line for brief section 3, or None. ``destination`` is "site" or "telegram"."""
    if view is None:
        return None                               # learning visibility not configured
    if not view.known:
        when = f" (last snapshot {view.as_of})" if view.status == "stale" and view.as_of else ""
        return f"Learning counts unavailable{when}."
    c = view.counts
    show_labels = destination == "site" and labels_permitted

    def labels(kind):
        names = [label for k, label in view.site_labels if k == kind] if show_labels else []
        return f" ({', '.join(repr(n) for n in names)})" if names else ""

    parts = []
    if c["pending_suggestions"]:
        parts.append(_plural(c["pending_suggestions"], "learning suggestion", "learning suggestions")
                     + " pending" + labels("pending_suggestion"))
    if c["adopted_in_window"]:
        span = "this week" if view.adopted_window_days == 7 else f"in the last {view.adopted_window_days} days"
        parts.append(_plural(c["adopted_in_window"], "practice", "practices") + f" adopted {span}")
    confirmed, provisional = c["repeated_corrections_confirmed"], c["repeated_corrections_provisional"]
    if confirmed:
        parts.append(_plural(confirmed, "repeated correction", "repeated corrections")
                     + labels("repeated_correction"))
    if provisional:
        parts.append(_plural(provisional, "possible repeated correction", "possible repeated corrections"))
    partial = view.coverage == "partial"
    if not parts:
        return "Learning: nothing new in the part checked (coverage partial)." if partial else None
    return "Learning: " + " · ".join(parts) + (" (coverage partial)." if partial else ".")
