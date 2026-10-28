"""The direct-voice contract (Spec 165 v0.2 §5, as the A1 fix round built it), run over every adapter: the test adapter of
test_direct_host.py (an in-memory store, not the Guild) and MC's real adapter on the synthetic Guild mount of
test_mc_voice_direct.py. Spec 165 §12.1's no-copy by architecture, with tests/realtime_voice/test_no_copy.py: whatever a host
supplies, the core's behaviour is the same (review R1 P3-9, R2 S5). A2 adds the CoS adapter to HOSTS. No provider, no
Keychain, no microphone, no network."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guild" / "shop_floor"))

import test_direct_host as fake  # noqa: E402
import test_mc_voice_direct as mc  # noqa: E402
from test_direct_host import rig  # noqa: E402,F401  (the fake host's fixture, for getfixturevalue)

HOSTS = ("test adapter", "MC")


def driver(kind, request, monkeypatch, tmp_path):
    """One host, driven the same way: start, consult, post, end, summary, read; and what its backend saw."""
    if kind == "test adapter":
        r = request.getfixturevalue("rig")
        return SimpleNamespace(
            kind=kind, base=fake.BASE, headers=fake.OWNER, cid="owner",
            start=lambda: fake.start(r), turns=lambda: len(r.host.turns),
            summaries=lambda: [q["json"] for q in r.gateway.requests],
            event=lambda role, item, text, offset=1: fake.turn(r, role, item, text, offset),
            client=r.client, r=r)
    r = mc.build(monkeypatch, tmp_path, dict(mc.ON))
    return SimpleNamespace(
        kind=kind, base=mc.BASE, headers=r.headers, cid=mc.CID,
        start=lambda: mc.start(r)["voice_session_id"], turns=lambda: len(r.turns),
        summaries=lambda: [p.request for p in r.cos.prompts],
        event=lambda role, item, text, offset=1: mc.turn(r, role, item, text, offset),
        client=r.client, r=r)


@pytest.fixture(params=HOSTS)
def host(request, monkeypatch, tmp_path):
    return driver(request.param, request, monkeypatch, tmp_path)


def ask(h, sid, call_id, item, words="What is stuck on #12?", name="consult"):
    return h.client.post(h.base + "/consult", headers=h.headers, json={
        "session_id": sid, "call_id": call_id, "name": name, "owner_transcript_item": words, "owner_item_id": item,
        "arguments": {"question": "the model's paraphrase"}})


def post(h, sid, events, **body):
    return h.client.post(h.base + f"/session/{sid}/transcript", headers=h.headers, json={"events": events, **body})


def test_one_owner_turn_is_one_effect_whichever_tool_or_call_asks(host):
    h = host
    sid = h.start()
    first = ask(h, sid, "call_1", "t1").get_json()
    retry = ask(h, sid, "call_1", "t1").get_json()
    check = ask(h, sid, "host-2", "t1").get_json()
    other = ask(h, sid, "call_3", "t1", name="save_note").get_json()
    assert h.turns() == 1 and first["outcome"] == "done" and retry == first
    assert check["same_turn"] is True and other["same_turn"] is True and other["first_tool"] == "consult"
    assert ask(h, sid, "call_4", "t2").get_json()["outcome"] == "done" and h.turns() == 2     # his next words: new turn


def test_the_same_turn_with_other_words_is_refused(host):
    h = host
    sid = h.start()
    ask(h, sid, "call_1", "t1")
    refused = ask(h, sid, "call_2", "t1", words="Delete everything.")
    assert refused.status_code == 409 and refused.get_json()["error"] == "turn_mismatch" and h.turns() == 1


def test_every_answer_is_one_of_four_outcomes(host):
    h = host
    sid = h.start()
    assert ask(h, sid, "call_1", "t1").get_json()["outcome"] in ("done", "not_done", "pending", "unknown")
    h.client.post(h.base + f"/session/{sid}/end", headers=h.headers, json={})
    late = ask(h, sid, "call_2", "t2")
    assert late.status_code == 409 and late.get_json()["outcome"] == "not_done"


def test_reconciliation_reads_the_stored_outcome_and_runs_nothing(host):
    h = host
    sid = h.start()
    done = ask(h, sid, "call_1", "t1").get_json()
    got = h.client.get(h.base + f"/consult/{sid}/t1?conversation_id={h.cid}", headers=h.headers).get_json()
    assert got["outcome"] == "done" and got["receipt"] == done["receipt"] and h.turns() == 1


def test_the_close_counts_what_the_server_does_not_hold_and_refuses_later_posts(host):
    h = host
    sid = h.start()
    post(h, sid, [h.event("user", "t0", "Kept words.")])
    ended = h.client.post(h.base + f"/session/{sid}/end", headers=h.headers,
                          json={"last_item_id": "t1", "unacknowledged": ["t0", "t1"]}).get_json()
    assert ended["capture"] == "incomplete" and ended["missing"] == 1
    assert post(h, sid, [h.event("assistant", "t2", "Too late.")]).status_code == 409


def test_keep_talking_withdraws_the_proposal(host):
    h = host
    sid = h.start()
    assert post(h, sid, [h.event("user", "t0", "We're done.")]).get_json()["propose_end"] is True
    assert h.client.post(h.base + f"/session/{sid}/keep_talking", headers=h.headers, json={}).status_code == 200
    assert "end_confirmed" not in post(h, sid, [h.event("user", "t1", "Yes.", 3)]).get_json()


def test_the_summary_is_a_plain_gateway_completion_with_no_tools(host):
    h = host
    sid = h.start()
    post(h, sid, [h.event("user", "t0", "We looked at #12. We're done.")])
    answer = h.client.post(h.base + f"/session/{sid}/summary", headers=h.headers, json={"conversation_id": h.cid}).get_json()
    (sent,) = h.summaries()
    assert answer["state"] == "proposed" and sent["model"] == "minimoi-voice-summary"
    assert not {"tools", "tool_choice", "user"} & set(sent)


def test_a_summary_attempt_is_recorded_once_and_a_second_ask_repeats_its_proposal(host):
    """CX V2-5: the attempt's identity lives in the host's store, and a later ask, from any device, gets the same proposal."""
    h = host
    sid = h.start()
    post(h, sid, [h.event("user", "t0", "We looked at #12. We're done.")])
    asked = lambda: h.client.post(h.base + f"/session/{sid}/summary", headers=h.headers,  # noqa: E731
                                  json={"conversation_id": h.cid}).get_json()
    first, second = asked(), asked()
    assert first["state"] == "proposed" and second["repeated"] is True and second["written"] == first["written"]
    assert len(h.summaries()) == 1
