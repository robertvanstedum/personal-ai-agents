"""Spec 164 §12 release check for MC voice on the direct path, on the real portal (owner guard, CSRF, SQLite floor,
real api.run_mc_turn) with the real OpenClaw adapter over a stubbed native transport (FakeRuntime). The provider mint
is a stub. No provider, no Keychain, no microphone, no network."""
import json
import uuid
from pathlib import Path

import pytest

from floor_helpers import load_portal, write_headers  # noqa:F401
from floor_db_helpers import attach, floor_db, keyed  # noqa:F401
from test_mc_turns import FakeRuntime, _openclaw, _turn_on
from voice_budget_helpers import install as install_budget

from minimoi_portal.guild_ui.mc.openclaw import VOICE_CHANNEL_WORDS

API = "/guild-next/api/v1"
DIRECT = API + "/mc/voice/direct/api/realtime-voice"
ON = {"MINIMOI_MC_VOICE": "1", "MINIMOI_MC_VOICE_DIRECT": "1", "MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO": "2000000"}
COMPOSER = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/mc_voice.js"


def portal_with(load_portal, floor_db, monkeypatch, tmp_path, env):
    for name in ("MINIMOI_MC_VOICE", "MINIMOI_MC_VOICE_DIRECT", "MINIMOI_MC_MANAGED_REALTIME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    install_budget(monkeypatch, tmp_path)
    portal = load_portal()
    attach(portal, floor_db.store())
    runtime = FakeRuntime()
    _turn_on(portal, _openclaw(runtime))
    minted = []

    def mint(**kw):
        minted.append(kw)
        return {"provider": "openai", "client_secret": "ek_synthetic", "expires_at": 1, "model": "gpt-realtime-2.1"}
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", mint)
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    client = portal.owner()
    token = portal.csrf(client)
    return portal, runtime, minted, client, token


@pytest.fixture
def released(load_portal, floor_db, monkeypatch, tmp_path):
    return portal_with(load_portal, floor_db, monkeypatch, tmp_path, ON)


def headers(token, **kw):
    return {**write_headers(token, origin="https://dev.minimoi.ai"), "Content-Type": "application/json", **kw}


def conversation(client, token):
    made = client.post(API + "/conversations", json=keyed(), headers=write_headers(token))
    assert made.status_code == 200, made.get_json()
    return made.get_json()["conversation"]["id"]


def consult(client, token, sid, call_id="call_1", words="What is stuck on #12?", **extra):
    return client.post(DIRECT + "/consult", headers=headers(token, **extra), json={
        "session_id": sid, "call_id": call_id, "name": "consult", "owner_transcript_item": words,
        "arguments": {"question": "the model's paraphrase"}})


def test_the_surface_reports_the_direct_path_and_its_providers(released):
    _portal, _runtime, _minted, client, _token = released
    body = client.get(API + "/mc/voice/capabilities").get_json()
    direct = body["direct_voice"]
    assert body["ok"] is True and body["managed_realtime"] is None
    assert direct["path"] == "direct" and direct["persona"] == "master_craftsman" and direct["locale"] == "en-US"
    assert [p["provider"] for p in direct["providers"]] == ["openai", "xai"] and direct["default_provider"] == "openai"
    assert all(p["browser_direct_ephemeral_auth"] and not p["server_proxy_required"] for p in direct["providers"])
    for route in ("bootstrap_url", "consult_url", "end_url", "transcript_url", "summary_url", "summary_decision_url"):
        assert direct[route].startswith("/guild-next/api/v1/mc/voice/direct/api/realtime-voice/")
    assert direct["card_release"] == "0.1.18"


def test_one_bridged_consult_reaches_openclaw_and_returns_an_answered_receipt_and_misuse_has_one_effect_or_none(released):
    portal, runtime, minted, client, token = released
    cid = conversation(client, token)
    boot = client.post(DIRECT + "/bootstrap", headers=headers(token),
                       json={"persona": "master_craftsman", "provider": "openai", "conversation_id": cid})
    assert boot.status_code == 200, boot.get_json()
    sid = boot.get_json()["voice_session_id"]
    assert len(minted) == 1 and "OPERATING RULES" in minted[0]["instructions"]

    answered = consult(client, token, sid)
    assert answered.status_code == 200, answered.get_json()
    result = answered.get_json()
    assert result["ok"] is True and result["answered"] is True and result["receipt"]
    assert result["spoken"] == "Item 12 is in build."
    assert len(runtime.sent) == 1
    sent = runtime.sent[0]["body"]["messages"][0]["content"]
    assert sent.startswith("[Shop floor context: ") and json.dumps(VOICE_CHANNEL_WORDS) in sent
    assert "What is stuck on #12?" in sent and "paraphrase" not in sent and "object at" not in sent

    def effects():
        notes = client.get(API + f"/notes?conversation={cid}").get_json()
        return len(runtime.sent), len(notes["notes"]) + sum(1 for s in notes["voice_sessions"])
    before = effects()
    assert consult(client, token, sid).get_json() == result                       # replayed call_id: one effect
    assert consult(client, token, str(uuid.uuid4()), call_id="call_2").status_code == 403    # forged session
    assert consult(client, token, "vs-forged", call_id="call_3").status_code == 403
    no_csrf = {k: v for k, v in headers(token).items() if k != "X-CSRF-Token"}
    assert client.post(DIRECT + "/consult", headers=no_csrf, json={"session_id": sid, "call_id": "call_4",
                       "name": "consult", "owner_transcript_item": "Again?"}).status_code == 403
    assert effects() == before
    assert client.post(DIRECT + f"/session/{sid}/end", headers=headers(token)).get_json() == {"ok": True, "ended": True}
    late = consult(client, token, sid, call_id="call_5")
    assert late.status_code == 409 and late.get_json()["error"] == "session ended"     # late: none
    assert len(runtime.sent) == 1
    revealed = client.get(API + f"/notes?conversation={cid}").get_json()
    assert revealed["voice_sessions"][0]["state"] == "ended"
    kinds = [n["voice"]["kind"] for n in revealed["notes"] if "voice" in n]
    assert kinds == ["consult_request", "consult_note", "consult_reply", "consult_receipt"]


@pytest.mark.parametrize("env", [{k: v for k, v in ON.items() if k != "MINIMOI_MC_VOICE_DIRECT"},
                                 {k: v for k, v in ON.items() if k != "MINIMOI_MC_VOICE"}])
def test_with_the_direct_flag_off_its_routes_are_absent_and_the_composer_keeps_todays_relay(
        load_portal, floor_db, monkeypatch, tmp_path, env):
    """Production today (MINIMOI_MC_VOICE=1, the direct path held off, managed off) keeps today's chained relay until the
    direct path is switched on there (Spec 165 §9, §11); once direct or managed is offered, start() returns before it."""
    _portal, runtime, minted, client, token = portal_with(load_portal, floor_db, monkeypatch, tmp_path, env)
    capabilities = client.get(API + "/mc/voice/capabilities")
    relay = client.get(API + "/mc/voice/static/realtime-confer-controller.js")
    if env.get("MINIMOI_MC_VOICE") == "1":
        assert capabilities.get_json()["direct_voice"] is None and capabilities.get_json()["managed_realtime"] is None
        assert relay.status_code == 200 and b"ConferVoiceController" in relay.data      # the relay the composer starts
    else:
        assert capabilities.status_code == 404 and relay.status_code == 404
    assert client.post(DIRECT + "/bootstrap", headers=headers(token), json={}).status_code == 404
    assert client.post(DIRECT + "/consult", headers=headers(token), json={}).status_code == 404
    composer = COMPOSER.read_text()
    start = composer[composer.index("async function start()"):composer.index("const DIRECT_ENDED")]
    direct_first = start.index("if (direct) {await startDirect(mine,current);return;}")
    managed_next = start.index("if (managed) {await startManaged(mine,current);return;}")
    assert direct_first < managed_next < start.index("realtime-confer-controller.js")
    assert composer.count("realtime-confer-controller") == 1                    # only there, after both
    direct_page = COMPOSER.with_name("mc_voice_direct.js").read_text()          # the direct path's page code (CC_16)
    for chained in ("realtime-confer-controller", "ConferVoiceController", "/mc/voice/bootstrap", "/mc/voice/speech"):
        assert chained not in direct_page
    assert not runtime.sent and not minted


@pytest.mark.parametrize("env,xai", [(ON, True), ({k: v for k, v in ON.items() if k != "MINIMOI_MC_VOICE_DIRECT"}, False)])
def test_the_page_may_reach_xai_only_when_the_direct_path_offers_it(load_portal, floor_db, monkeypatch, tmp_path, env, xai):
    _portal, _runtime, _minted, client, _token = portal_with(load_portal, floor_db, monkeypatch, tmp_path, env)
    csp = client.get("/guild-next/guild/build").headers["Content-Security-Policy"]
    connect = next(part for part in csp.split(";") if part.strip().startswith("connect-src"))
    assert "https://api.openai.com" in connect and connect.count("https://api.openai.com") == 1
    assert ("wss://api.x.ai" in connect) is xai and "https://api.x.ai" not in connect
