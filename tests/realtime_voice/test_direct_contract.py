"""The direct-voice contract (Spec 165 v0.2 §5, as the A1 fix round built it), run over every adapter: the test adapter of
test_direct_host.py (an in-memory store, not the Guild) and MC's real adapter on the synthetic Guild mount of
test_mc_voice_direct.py, and (A2) Chief of Staff's real adapter and file store (tests/cos/cos_voice_rig.py, a card copy
that also names a Grok voice, so the eligibility case runs on xAI too; her real card refuses xAI until the audition, which
tests/cos/test_cos_direct_voice.py checks). Spec 165 §12.1's no-copy by architecture, with tests/realtime_voice/test_no_copy.py:
whatever a host supplies, the core's behaviour is the same (review R1 P3-9, R2 S5). No provider, no Keychain, no microphone,
no network."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guild" / "shop_floor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cos"))

import cos_voice_rig as cos  # noqa: E402
import test_direct_host as fake  # noqa: E402
import test_mc_voice_direct as mc  # noqa: E402
from test_direct_host import rig  # noqa: E402,F401  (the fake host's fixture, for getfixturevalue)

HOSTS = ("test adapter", "MC", "CoS")


def driver(kind, request, monkeypatch, tmp_path):
    """One host, driven the same way: start, consult, post, end, summary, read; and what its backend saw."""
    if kind == "test adapter":
        r = request.getfixturevalue("rig")
        return SimpleNamespace(
            kind=kind, base=fake.BASE, headers=fake.OWNER, cid="owner", persona="test_person", short="TP",
            start=lambda: fake.start(r), turns=lambda: len(r.host.turns), effects=lambda: len(r.host.turns),
            summaries=lambda: [q["json"] for q in r.gateway.requests],
            event=lambda role, item, text, offset=1: fake.turn(r, role, item, text, offset),
            client=r.client, r=r)
    if kind == "CoS":
        r = cos.build(monkeypatch, tmp_path, voices={"openai": "cedar", "xai": "ara"})
        return SimpleNamespace(
            kind=kind, base=cos.BASE, headers=cos.OWNER, cid="owner", persona="chief_of_staff", short="CoS",
            start=lambda: cos.start(r), turns=lambda: len(r.turns), effects=lambda: cos.effects(r),
            summaries=lambda: [q["json"] for q in r.gateway.requests],
            event=lambda role, item, text, offset=1: cos.event(r, role, item, text, offset),
            client=r.client, r=r)
    r = mc.build(monkeypatch, tmp_path, dict(mc.ON))
    return SimpleNamespace(
        kind=kind, base=mc.BASE, headers=r.headers, cid=mc.CID, persona="master_craftsman", short="MC",
        start=lambda: mc.start(r)["voice_session_id"], turns=lambda: len(r.turns),
        effects=lambda: len(r.turns) + len(r.cos.calls),                  # MC's turns and the notes his platform saved
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


def test_one_effect_per_owner_turn_holds_from_save_note_to_check_and_from_check_to_save_note(host):
    """CX V2-1 (the settled rule): one effectful dispatch per (host, session, owner item) across both tools."""
    h = host
    sid = h.start()
    words = "Make a note that the kiln is hot"                       # a note command: either tool would save it
    model_first = ask(h, sid, "call_model", "t1", words=words, name="save_note").get_json()
    check_after = ask(h, sid, "host-1", "t1", words=words).get_json()
    check_first = ask(h, sid, "host-2", "t2", words=words).get_json()
    model_after = ask(h, sid, "call_model_2", "t2", words=words, name="save_note").get_json()
    assert check_after["same_turn"] is True and model_after["same_turn"] is True
    assert model_first.get("same_turn") is None and check_first.get("same_turn") is None
    assert h.effects() == 2                                            # one effect per owner item, whichever tool came first


def test_a_bare_confirmation_is_refused_in_writing_on_every_host_and_the_summary_yes_still_works(host):
    """Spec 165 v0.3 §6.4, D16 (a), G1: the core refuses a consult whose words are only a confirmation or a stop word, on
    every host, before anything is recorded or run. The yes to "Shall I sum up?" is a transcript turn, and still ends."""
    h = host
    sid = h.start()
    assert post(h, sid, [h.event("user", "t0", "We're done.")]).get_json()["propose_end"] is True
    refused = ask(h, sid, "call_1", "t1", words="Yes.").get_json()
    assert refused["outcome"] == "not_done" and refused["spoken"] == "Confirm that in writing." and h.effects() == 0
    read = h.client.get(h.base + f"/consult/{sid}/t1?conversation_id={h.cid}", headers=h.headers)
    assert read.status_code == 404                                       # never admitted
    assert ask(h, sid, "call_2", "t1", words="Yes.", name="save_note").get_json()["error"] == "confirmation"
    assert ask(h, sid, "call_3", "t2", words="Yes, what is stuck on #12?").get_json()["outcome"] == "done"
    assert h.turns() == 1                                                # not bare: it runs
    assert post(h, sid, [h.event("user", "t1", "Yes.", 3)]).get_json()["end_confirmed"] is True


# A1's binding pass (Spec 165 §7 as amended 28 Oct 2026, I6): eligibility is the core's one value, the same for every host. On
# OpenAI the page creates every response and the model has its two tools; on xAI the session has no action tool, its rules
# send every action through Check with the person, and Check still acts on his words.
@pytest.mark.parametrize("provider", ["openai", "xai"])
def test_each_host_gives_the_models_tools_only_where_the_provider_is_eligible(host, monkeypatch, provider):
    h = host
    minted = []

    def xai(**kw):
        minted.append(kw)
        return {"provider": "xai", "ephemeral_token": "xai_synthetic", "expires_after_seconds": 300, "model": "grok-voice-latest",
                "session_config": {}}
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", xai)
    answer = h.client.post(h.base + "/bootstrap", headers=h.headers,
                           json={"persona": h.persona, "provider": provider, "conversation_id": h.cid})
    assert answer.status_code == 200, answer.get_json()
    body = answer.get_json()
    session = minted[-1] if provider == "xai" else h.r.mints[-1]
    short = h.short
    if provider == "openai":
        assert body["page_responses"] is True and session["turn_detection"]["create_response"] is False
        assert [t["name"] for t in session["tools"]] == ["consult", "save_note"]
        assert "your two tools are 'consult'" in session["instructions"]
    else:
        assert "page_responses" not in body and not session.get("tools")
        assert "create_response" not in session["turn_detection"]
        assert f"press Check with {short}" in session["instructions"] and "your two tools" not in session["instructions"]
    check = ask(h, body["voice_session_id"], "host-1", "t0").get_json()           # Check with the person: page-initiated
    assert check["outcome"] == "done" and h.turns() == 1
