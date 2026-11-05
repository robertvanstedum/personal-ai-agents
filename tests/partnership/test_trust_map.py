"""Addendum 1 AC02 (synthetic): the trust map reports checkpoint coverage from evidence, a missed checkpoint widens
the gap, and the intended cadence is never reported as achieved protection without evidence."""
from services.partnership_tasks.trust_map import LIMITS, trust_map

DAY = 86400


def cp(seq, at, independent=True, verified=True):
    return {"through_seq": seq, "created_at": at, "independent": independent, "verified": verified,
            "verified_at": at + 60 if verified else None}


def test_never_anchored_is_said_plainly():
    m = trust_map(intended_cadence_s=DAY, checkpoints=[], journal_head_seq=40, now=1000)
    assert m["status"] == "never_anchored" and m["last_verified_independent_checkpoint"] is None
    assert m["unanchored"] == {"from_seq": 1, "to_seq": 40, "lines": 40, "seconds": None}
    assert m["intended_cadence"]["standing"] == "assumption" and m["protection_claimed"] is False


def test_within_cadence_still_claims_nothing_without_live_custody():
    m = trust_map(intended_cadence_s=DAY, checkpoints=[cp(30, 0)], journal_head_seq=40, now=DAY / 2)
    assert m["status"] == "within_intended_cadence" and m["protection_claimed"] is False
    assert m["unanchored"]["from_seq"] == 31 and m["unanchored"]["lines"] == 10
    live = trust_map(intended_cadence_s=DAY, checkpoints=[cp(30, 0)], journal_head_seq=40, now=DAY / 2,
                     live_custody_evidenced=True)
    assert live["protection_claimed"] is True and live["intended_cadence"]["standing"] == "evidenced"


def test_a_missed_checkpoint_widens_the_gap_and_drops_the_claim():
    one = trust_map(intended_cadence_s=DAY, checkpoints=[cp(30, 0)], journal_head_seq=50, now=1.5 * DAY,
                    live_custody_evidenced=True)
    three = trust_map(intended_cadence_s=DAY, checkpoints=[cp(30, 0)], journal_head_seq=90, now=3.2 * DAY,
                      live_custody_evidenced=True)
    assert one["status"] == three["status"] == "gap_widening"
    assert (one["missed_checkpoints"], three["missed_checkpoints"]) == (1, 3)
    assert three["unanchored"]["lines"] > one["unanchored"]["lines"]
    assert three["unanchored"]["seconds"] > one["unanchored"]["seconds"]
    assert one["protection_claimed"] is three["protection_claimed"] is False


def test_colocated_or_unverified_checkpoints_never_anchor():
    m = trust_map(intended_cadence_s=DAY, checkpoints=[cp(80, 10, independent=False), cp(70, 5, verified=False),
                                                       cp(20, 0)], journal_head_seq=90, now=100)
    assert m["last_verified_independent_checkpoint"]["through_seq"] == 20
    assert {n["why"] for n in m["not_counted"]} == {"not independent", "not verified"}


def test_verification_and_limits_are_stated_separately():
    m = trust_map(intended_cadence_s=DAY, checkpoints=[], journal_head_seq=1, now=0)
    assert m["verification"] == {"interval_seconds": None, "stated": False}
    assert m["limits"] == list(LIMITS) and any("truthful" in x for x in m["limits"])
    assert trust_map(intended_cadence_s=DAY, checkpoints=[], journal_head_seq=1, now=0,
                     verification_interval_s=3600)["verification"]["stated"] is True
