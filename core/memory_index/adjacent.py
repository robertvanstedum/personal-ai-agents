"""The adjacent slot (v0.5 D3): exactly one clearly labelled result from somewhere else.

After the two ranked lists are built, one more hit is chosen from the *meaning* neighbours the lists did not use. It is
the designed-in channel for surprise: an old record that relates in a way keyword search never would. Rules:

* never a record that already has a hit in either list; never mixed into them; at most one;
* it must come from a **different area** than the ranked hits, in this order of preference (``basis``):
  ``other_source`` (a provider none of the hits came from), ``other_kind`` (a record kind none of them is),
  ``older_thread`` (the same source and kind, but a different record created **at least 30 days before every hit**:
  it is older, not merely different in age; a newer record never carries this label);
  within a basis the nearest neighbour wins, so the result is related, just not the obvious answer;
* it must be close enough to be defensible: cosine distance at most ``max_distance`` (``MOI_ADJACENT_MAX_DISTANCE``,
  default ``DEFAULT_MAX_DISTANCE``; **provisional**: not yet measured on real data, tuned against Robert's own queries
  at M4 acceptance, so a change here is a parameter change, not a design change);
* it passes the same pointing rule (the resolver) as every other hit, and the same caller gates. A candidate that fails
  is reported under ``unresolved`` with list ``adjacent`` and the next one is tried;
* with no hits there is nothing to be adjacent to, and with the embedder down there are no meaning neighbours: the slot
  says so (``{"none": reason}``), it is never filled with a keyword hit.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

from core.memory_index import policy, resolver

DEFAULT_MAX_DISTANCE = 0.6
OLDER_THREAD_DAYS = 30
BASES = ("other_source", "other_kind", "older_thread")


def valid_distance(value) -> bool:
    """A usable cosine-distance cutoff: a finite number from 0 to 2 (the range of cosine distance). NaN, infinity,
    negatives, values above 2, booleans and non-numbers are not (NaN would pass every ``score > limit`` test)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0.0 <= value <= 2.0


def max_distance() -> float:
    """``MOI_ADJACENT_MAX_DISTANCE`` if it is a valid cutoff, else ``DEFAULT_MAX_DISTANCE``."""
    try:
        value = float(os.environ.get("MOI_ADJACENT_MAX_DISTANCE", DEFAULT_MAX_DISTANCE))
    except ValueError:
        return DEFAULT_MAX_DISTANCE
    return value if valid_distance(value) else DEFAULT_MAX_DISTANCE


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _facts(con, record_ids) -> dict[str, dict]:
    ids = sorted(set(record_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = con.execute(f"SELECT record_id, provider, kind, created FROM records WHERE record_id IN ({marks})", ids).fetchall()
    return {r[0]: {"provider": r[1], "kind": r[2], "created": _when(r[3])} for r in rows}


def _basis(facts: dict, top: list[dict]) -> str | None:
    if facts["provider"] not in {t["provider"] for t in top}:
        return BASES[0]
    if facts["kind"] not in {t["kind"] for t in top}:
        return BASES[1]
    made = facts["created"]
    if made is not None and all(t["created"] is not None and (t["created"] - made) >= timedelta(days=OLDER_THREAD_DAYS)
                                for t in top):
        return BASES[2]                              # the candidate is at least 30 days OLDER than every hit
    return None


def pick(con, shelf_root, pool: list[dict], hits: list[dict], caller: policy.Caller, unresolved: list,
         parsed: dict | None = None, *, limit: float | None = None) -> dict:
    """The adjacent hit (the resolver's hit plus ``label``, ``basis``, ``distance``) or ``{"none": reason}``."""
    if not hits:
        return {"none": "no_results"}
    limit = limit if limit is not None and valid_distance(limit) else max_distance()
    used = {h["record_id"] for h in hits}
    facts = _facts(con, used | {r["record_id"] for r in pool})
    top = [facts[r] for r in used if r in facts]
    if not top:
        return {"none": "no_results"}
    ranked = []
    for row in pool:                                                  # nearest first
        score = row["score"]
        if row["record_id"] in used or not isinstance(score, (int, float)) or not math.isfinite(score) or score > limit:
            continue                                  # a missing, NaN or infinite distance is never "close enough"
        if caller.scopes is not None and row["scope"] not in caller.scopes:
            continue
        info = facts.get(row["record_id"])
        basis = _basis(info, top) if info else None
        if basis:
            ranked.append((BASES.index(basis), row["score"], row, basis))
    if not ranked:
        return {"none": "nothing_close_enough_elsewhere"}
    for _, _, row, basis in sorted(ranked, key=lambda t: (t[0], t[1], t[2]["chunk_id"])):
        hit, bad = resolver.resolve(shelf_root, row, caller, "adjacent", parsed)
        if bad:
            unresolved.append(bad)
            continue
        return {**hit, "label": "Adjacent", "basis": basis, "distance": row["score"]}
    return {"none": "nothing_close_enough_elsewhere"}


__all__ = ["pick", "max_distance", "valid_distance", "DEFAULT_MAX_DISTANCE", "BASES", "OLDER_THREAD_DAYS"]
