"""The A1 fix round (A1_FIX_BRIEF.md; Spec 165 v0.2 §4.4, §5) in the host core, through the fake adapter of
test_direct_host.py (an in-memory store, not the Guild). Each test fails on the tree before its item's commit. No provider,
no Keychain, no microphone, no network."""
from test_direct_host import BASE, OWNER, consult, rig, start  # noqa: F401


def bare(r, sid, **fields):
    return r.client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "call_1", "name": "consult",
                                                                 "owner_transcript_item": "What is on my agenda?", **fields})


# ── Item 1: the owner turn is named by its capture id, owner_item_id (§5.1) ─────────────────────────────────────────────

def test_a_consult_names_its_owner_turn_by_capture_id_and_nothing_else_is_accepted(rig):
    r = rig
    sid = start(r)
    assert bare(r, sid, owner_turn="u1").status_code == 400                       # A1's turn count is not an identity
    assert bare(r, sid, owner_item_id="u1").status_code == 400                    # not a capture id
    assert bare(r, sid, owner_item_id="t").status_code == 400
    assert not r.host.turns
    answer = bare(r, sid, owner_item_id="t3")
    assert answer.status_code == 200 and len(r.host.turns) == 1
