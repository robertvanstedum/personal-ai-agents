"""A1's binding pass (A1_BINDING_BRIEF.md; Spec 165 §5.1 and §7 as amended for Robert's C2 decision of 28 Oct 2026, "A: tie
by construction") in the host core and the shared bootstrap, through the fake adapter of test_direct_host.py.

I6: on the direct path the model is given consult and save_note only on a provider eligible for its own actions: one that
names the committed item and lets the page create every response. Eligibility is one reviewed value in the core
(capabilities.MODEL_ACTIONS): OpenAI yes, xAI no until A4. On an eligible provider the session's turn detection lets the page
create every response (create_response false; the provider's barge-in still on), and the bootstrap's answer says so
(page_responses). On xAI the session has no action tool, its rules say every action goes through Check with the person, and
Check still works. I7: German and Portuguese pass nothing new, and their bootstrap is as before. No provider, no Keychain, no
microphone, no network."""
import pytest
from flask import Flask

from core.realtime_voice import bootstrap as shared, capabilities
from core.realtime_voice.bootstrap import create_bootstrap_blueprint
from core.realtime_voice.standards import conversation_turn_detection
from test_direct_host import BASE, OWNER, consult, rig  # noqa: F401


def xai_mint(monkeypatch, mints):
    def mint(**kw):
        mints.append({"provider": "xai", **kw})
        return {"provider": "xai", "ephemeral_token": "xai_synthetic", "expires_after_seconds": 300, "model": "grok-voice-latest",
                "session_config": {"turn_detection": kw["turn_detection"], "instructions": kw["instructions"],
                                   **({"tools": kw["tools"]} if kw.get("tools") else {})}}
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", mint)


def boot(r, provider):
    return r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "provider": provider,
                                                                    "conversation_id": "owner"})


def test_eligibility_is_one_reviewed_value_openai_yes_xai_no():
    assert capabilities.MODEL_ACTIONS == {"openai": True, "xai": False}
    assert capabilities.model_actions("openai") is True
    assert capabilities.model_actions("xai") is False
    assert capabilities.model_actions("claude") is False                 # anything not named is not eligible


def test_openai_direct_the_page_creates_every_response_and_the_model_has_its_tools(rig):
    r = rig
    answer = boot(r, "openai")
    assert answer.status_code == 200
    body = answer.get_json()
    assert body["page_responses"] is True                                # the page creates every response that may act
    minted = r.mints[0]
    vad = minted["turn_detection"]
    assert vad["create_response"] is False                               # the provider starts no response of its own
    assert vad["interrupt_response"] is True                             # barge-in still cancels a response (I5)
    assert vad["type"] == "server_vad" and vad["silence_duration_ms"] == 1200 and vad["prefix_padding_ms"] == 300
    assert [t["name"] for t in minted["tools"]] == ["consult", "save_note"]
    assert "your two tools are 'consult'" in minted["instructions"]
    assert "you have no tools" not in minted["instructions"]


def test_xai_direct_has_no_action_tool_its_rules_send_actions_to_check_and_check_still_works(rig, monkeypatch):
    r = rig
    xai_mints = []
    xai_mint(monkeypatch, xai_mints)
    answer = boot(r, "xai")
    assert answer.status_code == 200, answer.get_json()
    body = answer.get_json()
    assert "page_responses" not in body                                  # the provider creates its own responses, as before
    minted = xai_mints[0]
    assert minted.get("tools") is None and "tools" not in body["session_config"]
    assert minted["turn_detection"] == conversation_turn_detection("xai")   # xAI's turn detection unchanged
    text = minted["instructions"]
    assert "you have no tools" in text and "press Check with TP" in text and "your two tools" not in text
    assert "Shall I sum up?" in text                                     # the spoken end is unchanged
    # Check with the person is page-initiated, its id and words from one capture record: it still works on xAI.
    sid = body["voice_session_id"]
    done = consult(r, sid, call_id="host-1", words="What is on my agenda?", turn="t0")
    assert done.status_code == 200 and done.get_json()["outcome"] == "done"
    assert r.host.turns[-1][-1] == "What is on my agenda?"


def test_the_capabilities_metadata_names_which_offered_provider_may_act(rig):
    assert rig.metadata["model_actions"] == {"openai": True, "xai": False}


def test_a_host_persona_that_offers_tools_where_the_page_cannot_name_the_owner_is_refused(monkeypatch):
    """The bootstrap holds the rule too: with page_responses given, a persona carrying tools for a provider it does not name
    is refused before the provider is contacted."""
    app, mints = Flask("bind"), []
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", lambda **kw: mints.append(kw))
    tools = [{"type": "function", "name": "consult", "description": "x", "parameters": {"type": "object"}}]
    seen = []

    def persona(name, actions=True):
        seen.append(actions)
        return {"name": "P", "prompt_txt": "p", "scenes": {}, "voices": {}, "tools": tools, "instructions": "rules"}
    app.register_blueprint(create_bootstrap_blueprint(domain="t", locale="en-US", get_persona=persona,
                                                      is_production=lambda: True, owner=lambda: ("robert", "Robert"),
                                                      page_responses=capabilities.model_actions))
    answer = app.test_client().post("/api/realtime-voice/bootstrap", json={"persona": "p", "provider": "xai"})
    assert answer.status_code == 400 and answer.get_json()["error"] == "invalid tools"
    assert seen == [False] and mints == []


@pytest.mark.parametrize("provider", ["openai", "xai"])
def test_german_and_portuguese_pass_no_page_responses_their_bootstrap_is_as_before(monkeypatch, provider):
    """I7: a host that passes no page_responses (German, Portuguese) has get_persona called with the name alone, its turn
    detection unchanged, its tools as the persona gives them, and no new field in its answer."""
    app, mints, calls = Flask("lang"), [], []
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})

    def mint(**kw):
        mints.append(kw)
        return {"provider": provider, "client_secret": "s", "model": "m"}
    monkeypatch.setattr(f"core.realtime_voice.bootstrap.{'openai_realtime' if provider == 'openai' else 'xai_voice'}"
                        ".mint_ephemeral_credential", mint)

    def persona(*args, **kwargs):
        calls.append((args, kwargs))
        return {"name": "Frau Berger", "prompt_txt": "Bäckerin.", "scenes": {"bakery": "In der Bäckerei."},
                "voices": {"openai": "marin", "xai": "eve"}}
    app.register_blueprint(create_bootstrap_blueprint(domain="german", locale="de-AT", get_persona=persona,
                                                      is_production=lambda: True, owner=lambda: ("robert", "Robert")))
    answer = app.test_client().post("/api/realtime-voice/bootstrap",
                                    json={"persona": "frau_berger", "scene": "bakery", "provider": provider})
    assert answer.status_code == 200
    assert calls == [(("frau_berger",), {})]
    assert mints[0]["turn_detection"] == shared._default_turn_detection(provider) == conversation_turn_detection(provider)
    assert "create_response" not in mints[0]["turn_detection"] and mints[0]["tools"] is None
    assert "page_responses" not in answer.get_json()


def test_the_turn_standard_changes_only_for_a_caller_that_passes_interrupt_response():
    assert conversation_turn_detection("openai") == {"type": "server_vad", "prefix_padding_ms": 300, "silence_duration_ms": 1200}
    assert conversation_turn_detection("openai", create_response=False) == {        # CoS's chained and controlled use
        "type": "server_vad", "prefix_padding_ms": 300, "silence_duration_ms": 1200,
        "create_response": False, "interrupt_response": False}
    assert conversation_turn_detection("openai", create_response=False, interrupt_response=True)["interrupt_response"] is True
    assert conversation_turn_detection("xai") == {"type": "server_vad", "silence_duration_ms": 1200}
    assert conversation_turn_detection("xai", create_response=False) == {"type": "server_vad", "silence_duration_ms": 1200}


# ── The binding pass's independent review at 8eb30a10 (REVIEW_independent_A1_binding.md) ──────────────────────────────

def test_b_n7_on_a_provider_not_eligible_the_consult_route_runs_only_check(rig, monkeypatch):
    """Defence in depth (B N7): the session records its provider's eligibility at bootstrap. On xAI a call that is not the
    page's own Check ("host-…") runs nothing, and answers not done; Check still acts. On OpenAI the model's call acts."""
    r = rig
    xai_mint(monkeypatch, [])
    grok = boot(r, "xai").get_json()["voice_session_id"]
    refused = consult(r, grok, call_id="call_model", turn="t0").get_json()
    assert refused["outcome"] == "not_done" and refused["error"] == "check_only" and "Check with TP" in refused["spoken"]
    assert r.host.turns == []                                               # nothing ran
    assert consult(r, grok, call_id="host-7", turn="t0").get_json()["outcome"] == "done" and len(r.host.turns) == 1
    openai = boot(r, "openai").get_json()["voice_session_id"]
    assert consult(r, openai, call_id="call_model", turn="t0").get_json()["outcome"] == "done" and len(r.host.turns) == 2


def test_b_s3_end_keeps_the_owner_turns_not_acted_on_capture_ids_only_once_each(rig):
    """B S3 + N2: End may name his turns not acted on (a commit let go, a reply cancelled or overtaken before its call ran).
    The core keeps them once, capture ids only, de-duplicated and capped; a body that names none keeps nothing."""
    from core.realtime_voice.direct_host import NOT_ACTED_MAX
    r = rig
    sid = boot(r, "openai").get_json()["voice_session_id"]
    other = boot(r, "openai").get_json()["voice_session_id"]
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER,
                          json={"not_acted": ["t0", "t0", "../x", 7, "t3"] + [f"t{n}" for n in range(10, 100)]})
    assert ended.status_code == 200 and ended.get_json()["ended"] is True
    kept = {rid: rec for (s, rid), rec in r.store.kept_records.items() if s == sid}["not_acted"]
    assert kept["kind"] == "not_acted" and kept["owner_item_ids"][:2] == ["t0", "t3"]
    assert len(kept["owner_item_ids"]) == NOT_ACTED_MAX
    r.client.post(BASE + f"/session/{other}/end", headers=OWNER, json={})
    assert "not_acted" not in {rid for (s, rid) in r.store.kept_records if s == other}
