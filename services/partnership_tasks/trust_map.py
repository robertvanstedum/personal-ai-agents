"""The Q06 trust map's checkpoint section (spec v1.1 Addendum 1, AC02).

It states, from evidence and never from the plan:
- the intended independent-checkpoint cadence, labelled an assumption until custody and verification are evidenced;
- the last verified, independently retained checkpoint and what it covers (through which journal line);
- the current unanchored interval (journal lines and time since that checkpoint);
- each missed checkpoint, so a gap widens visibly. A missed checkpoint never reports the intended cadence as
  achieved protection;
- verification frequency and delay, stated separately from the cadence;
- the fixed limits of what a checkpoint can show.

Only a checkpoint that is both independently retained (outside the writer's control) and verified against the
journal counts as an anchor. A co-located, recomputable hash does not.
"""
from __future__ import annotations

LIMITS = (
    "A checkpoint can expose later alteration of the history it covers, when compared with trustworthy retained "
    "evidence.",
    "It cannot prove that events were truthful when first recorded.",
    "It cannot detect a rewrite of history that has not yet been anchored.",
    "Co-located recomputable hashes do not resist a privileged rewrite.",
    "No maximum time to detect tampering is claimed: detection depends on when verification actually runs.",
)


def trust_map(*, intended_cadence_s: int | None, checkpoints: list[dict], journal_head_seq: int, now: float,
              verification_interval_s: int | None = None, live_custody_evidenced: bool = False) -> dict:
    """``checkpoints``: [{"through_seq", "created_at", "independent": bool, "verified": bool, "verified_at"}].
    Times are epoch seconds. Only independent and verified checkpoints anchor."""
    anchors = sorted((c for c in checkpoints if c.get("independent") is True and c.get("verified") is True),
                     key=lambda c: (c["through_seq"], c["created_at"]))
    last = anchors[-1] if anchors else None
    since = last["created_at"] if last else None
    covered = last["through_seq"] if last else 0
    elapsed = None if since is None else max(0.0, now - since)
    missed = 0
    if intended_cadence_s and elapsed is not None:
        missed = int(elapsed // intended_cadence_s)
    if last is None:
        status = "never_anchored"
    elif missed >= 1:
        status = "gap_widening"
    else:
        status = "within_intended_cadence"
    unanchored_lines = max(0, journal_head_seq - covered)
    return {
        "intended_cadence": {
            "seconds": intended_cadence_s,
            "standing": "evidenced" if live_custody_evidenced and last is not None else "assumption",
        },
        "last_verified_independent_checkpoint": None if last is None else {
            "through_seq": last["through_seq"], "created_at": last["created_at"], "verified_at": last.get("verified_at")},
        "unanchored": {"from_seq": covered + 1 if unanchored_lines else None, "to_seq": journal_head_seq,
                       "lines": unanchored_lines, "seconds": elapsed},
        "missed_checkpoints": missed,
        "status": status,
        "protection_claimed": status == "within_intended_cadence" and live_custody_evidenced,
        "not_counted": [{"through_seq": c.get("through_seq"),
                         "why": "not independent" if c.get("independent") is not True else "not verified"}
                        for c in checkpoints if c not in anchors],
        "verification": {"interval_seconds": verification_interval_s,
                         "stated": verification_interval_s is not None},
        "limits": list(LIMITS),
    }
