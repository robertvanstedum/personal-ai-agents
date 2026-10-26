"""Tests for the production activation of the real Guild floor on minimoi.ai (two switches, exact host, owner guard).

Meant to prove: both switches and the exact host are required; the prototype is never mounted; Master Craftsman is held
off whatever MINIMOI_GUILD_MC says; the owner guard holds; /guild only redirects when MINIMOI_GUILD_PRIMARY is also on.
"""
from __future__ import annotations

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)

PROD = "https://minimoi.ai"


def test_new_managed_voice_stays_out_of_production_even_with_legacy_voice_approved():
    from minimoi_portal.guild_mounts import production_environ
    filtered=production_environ({'MINIMOI_GUILD_CHAT':'cos','MINIMOI_MC_VOICE':'1',
        'MINIMOI_GUILD_VOICE_PRODUCTION':'1','MINIMOI_MC_MANAGED_REALTIME':'1'})
    assert filtered['MINIMOI_MC_VOICE']=='1'
    assert 'MINIMOI_MC_MANAGED_REALTIME' not in filtered


def _rules(app, prefix):
    return [r.rule for r in app.url_map.iter_rules() if r.rule.startswith(prefix)]


def test_both_switches_and_the_exact_host_mount_only_the_real_floor(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    portal = load_portal(next_flag="1", proto_flag="1", base_url=PROD)
    assert portal.module.GUILD_MOUNTS == {"guild_proto": "refused_production", "guild_next": "on"}
    assert _rules(portal.app, "/guild-proto") == []
    assert portal.owner().get("/guild-proto/guild/build").status_code == 404
    assert portal.owner().get("/guild-next/guild/build").status_code == 200


def test_the_owner_guard_holds_in_production(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    portal = load_portal(next_flag="1", base_url=PROD)
    response = portal.client().get("/guild-next/guild/build")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert portal.guest().get("/guild-next/guild/build").status_code == 302


@pytest.mark.parametrize("next_flag,production_flag,base_url", [
    ("1", None, PROD),                              # the mount switch alone
    (None, "1", PROD),                              # the production switch alone
    ("1", "1", "https://www.minimoi.ai"),           # www is never a production host
    ("1", "1", "https://minimoi.ai.evil.example"),  # host must be exact
    ("1", "1", "https://staging.minimoi.ai"),
    ("1", "1", ""),
])
def test_one_switch_or_a_wrong_host_mounts_nothing(load_portal, monkeypatch, next_flag, production_flag, base_url):
    monkeypatch.delenv("MINIMOI_GUILD_PRODUCTION", raising=False)
    if production_flag:
        monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", production_flag)
    portal = load_portal(next_flag=next_flag, base_url=base_url)
    assert portal.module.GUILD_MOUNTS["guild_next"] in {"off", "refused_not_staging"}
    assert _rules(portal.app, "/guild-next") == []


def test_the_staging_allowlist_still_cannot_name_production(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_ALLOWED_HOSTS", "minimoi.ai")
    monkeypatch.delenv("MINIMOI_GUILD_PRODUCTION", raising=False)
    portal = load_portal(next_flag="1", base_url=PROD)
    assert portal.module.GUILD_MOUNTS["guild_next"] == "refused_not_staging"


def test_master_craftsman_is_held_off_in_production_whatever_the_environment_says(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    for name, value in (("MINIMOI_GUILD_MC", "stub"), ("MINIMOI_GUILD_MC_TURNS", "1"), ("MINIMOI_GUILD_MC_STREAM", "1")):
        monkeypatch.setenv(name, value)
    portal = load_portal(next_flag="1", base_url=PROD)
    assert portal.module.GUILD_MOUNTS["guild_next"] == "on"
    services = portal.app.extensions["guild_ui_next"]["services"]
    assert services.mc.kind == "off" and services.mc_turns is False and services.mc_stream is False


def test_guild_landing_redirects_only_when_primary_is_explicitly_on(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    monkeypatch.delenv("MINIMOI_GUILD_PRIMARY", raising=False)
    portal = load_portal(next_flag="1", base_url=PROD)
    assert portal.owner().get("/guild").status_code == 200            # today's landing stays
    monkeypatch.setenv("MINIMOI_GUILD_PRIMARY", "1")
    portal = load_portal(next_flag="1", base_url=PROD)
    response = portal.owner().get("/guild")
    assert response.status_code == 302 and response.headers["Location"].endswith("/guild-next/")


def test_staging_default_is_unchanged(load_portal, monkeypatch):
    monkeypatch.delenv("MINIMOI_GUILD_PRIMARY", raising=False)
    portal = load_portal(next_flag="1", base_url="https://dev.minimoi.ai")
    assert portal.owner().get("/guild").status_code == 302


# ── production-mode API behaviour (not the staging-only CSRF tests): who may call, who may write ─────────────────────────────

from domains.guild import queue_store as qs  # noqa: E402

from floor_helpers import write_headers  # noqa: E402

ITEM_URL = "/guild-next/api/v1/queue/items/12/status"


def _production(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    return load_portal(next_flag="1", base_url=PROD)


def test_production_api_anonymous_is_401_and_a_guest_is_403(load_portal, monkeypatch):
    portal = _production(load_portal, monkeypatch)
    anonymous = portal.client().get("/guild-next/api/v1/session")
    assert anonymous.status_code == 401 and anonymous.get_json()["error"] == "not_signed_in"
    guest = portal.guest().get("/guild-next/api/v1/session")
    assert guest.status_code == 403 and guest.get_json()["error"] == "not_allowed"
    assert portal.owner().get("/guild-next/api/v1/session").status_code == 200


def test_production_owner_write_without_the_token_is_403_csrf_and_writes_nothing(load_portal, monkeypatch):
    portal = _production(load_portal, monkeypatch)
    client = portal.owner()
    before = portal.queue_path.read_bytes()
    item = next(i for i in qs.QueueStore(str(portal.queue_path)).read_items() if i["id"] == 12)
    body = {"to": "done", "expect_item_digest": qs.item_digest(item), "idempotency_key": "k" * 16}
    refused = client.post(ITEM_URL, json=body, headers={"X-Record-Mode": "on_record"})
    assert refused.status_code == 403 and refused.get_json()["error"] == "csrf"
    wrong_origin = client.post(ITEM_URL, json=body,
                               headers=write_headers(portal.csrf(client), origin="https://evil.example"))
    assert wrong_origin.status_code == 403 and wrong_origin.get_json()["error"] == "csrf"
    assert portal.queue_path.read_bytes() == before and portal.journal() == []
    saved = client.post(ITEM_URL, json=body,
                        headers=write_headers(portal.csrf(client), origin=PROD, fetch_site="same-origin"))
    assert saved.status_code == 200 and saved.get_json()["result"] == "saved"


def test_production_flag_off_leaves_no_new_ui_and_no_api(load_portal, monkeypatch):
    """The fallback is exactly today's portal: with the production switch off the owner gets 404 on every new route."""
    monkeypatch.delenv("MINIMOI_GUILD_PRODUCTION", raising=False)
    portal = load_portal(next_flag="1", base_url=PROD)
    owner = portal.owner()
    for url in ("/guild-next/", "/guild-next/guild/build", "/guild-next/api/v1/session", "/guild-next/guild/ui-assets/tokens.css"):
        assert owner.get(url).status_code == 404, url
    assert owner.get("/guild").status_code == 200
    assert "/guild-next" not in (owner.get("/guild").headers.get("Location") or "")

def test_production_chat_is_off_unless_cos_is_explicitly_chosen(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    monkeypatch.setenv("COS_BACKEND", "http://cos-scheduler:8769")
    for value in (None, "", "mc", "stub", "openclaw", "1"):
        if value is None:
            monkeypatch.delenv("MINIMOI_GUILD_CHAT", raising=False)
        else:
            monkeypatch.setenv("MINIMOI_GUILD_CHAT", value)
        monkeypatch.setenv("MINIMOI_GUILD_MC", "stub")
        portal = load_portal(next_flag="1", base_url=PROD)
        services = portal.app.extensions["guild_ui_next"]["services"]
        assert services.mc.kind == "off" and services.mc_turns is False


def test_production_chat_cos_uses_the_chief_of_staff_over_the_existing_address(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    monkeypatch.setenv("COS_BACKEND", "http://cos-scheduler:8769")
    monkeypatch.setenv("MINIMOI_GUILD_CHAT", "cos")
    monkeypatch.setenv("MINIMOI_GUILD_MC", "openclaw")            # still ignored
    portal = load_portal(next_flag="1", base_url=PROD)
    services = portal.app.extensions["guild_ui_next"]["services"]
    assert services.mc.kind == "cos" and services.mc.base_url == "http://cos-scheduler:8769"
    assert services.mc_turns is True and services.mc_stream is False


@pytest.mark.parametrize('chat', ['', 'cos'])
def test_production_filters_voice_and_dictation_for_pages_csp_and_provider_routes(load_portal, monkeypatch, chat):
    from core.realtime_voice.providers import openai_realtime, openai_speech
    from minimoi_portal.guild_mounts import production_environ
    from minimoi_portal.guild_ui import mc_voice, dictation, mc_files, voice_release
    # Exercise the independent production filter even after shared voice ships.
    # The unpatched alpha readiness hold has separate HTTP/HTML/CSP tests.
    monkeypatch.setattr(voice_release, "SHARED_VOICE_READY", True)
    calls = []
    monkeypatch.setattr(openai_realtime, 'mint_confer_transcription_credential', lambda **kw: calls.append(kw))
    monkeypatch.setattr(openai_realtime, 'mint_transcription_credential', lambda **kw: calls.append(kw))
    monkeypatch.setattr(openai_speech, 'create_speech_stream', lambda **kw: calls.append(kw))
    monkeypatch.setenv('MINIMOI_GUILD_PRODUCTION', '1')
    monkeypatch.setenv('MINIMOI_GUILD_CHAT', chat)
    monkeypatch.setenv('MINIMOI_MC_VOICE', '1')
    monkeypatch.setenv('MINIMOI_DICTATION', '1')
    monkeypatch.setenv(mc_files.ENV, '/tmp/sample/agent_requests')
    admitted = production_environ({'MINIMOI_MC_VOICE': '1', 'MINIMOI_DICTATION': '1', mc_files.ENV: '/tmp/sample/agent_requests'})
    assert not mc_voice.enabled(admitted) and not dictation.enabled(admitted)
    assert mc_files.host_root(admitted) is None
    portal = load_portal(next_flag='1', base_url=PROD)
    client = portal.owner()
    for path in ('/guild-next/guild/build', '/guild-next/guild/rooms'):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-mc-voice="true"' not in html and 'data-dictation="true"' not in html
        assert 'api.openai.com' not in response.headers['Content-Security-Policy']
    prefix = '/guild-next/api/v1'
    for path in ('mc/voice/capabilities', 'mc/voice/speech/some-note-id-1',
                 'mc/voice/static/realtime-confer-controller.js', 'dictation/capabilities',
                 'dictation/static/realtime-memo-controller.js'):
        assert client.get(f'{prefix}/{path}').status_code == 404
    for path in ('mc/voice/bootstrap', 'dictation/bootstrap'):
        assert client.post(f'{prefix}/{path}', json={}).status_code == 404
    assert not calls
    # Same raw flags in the same process: staging admits them, production does
    # not inherit that mapping or change when another mount is registered.
    staging = load_portal(next_flag='1', base_url='https://dev.minimoi.ai')
    response = staging.owner().get('/guild-next/guild/build')
    html = response.get_data(as_text=True)
    assert 'data-mc-voice="true"' in html and 'data-dictation="true"' in html
    assert 'api.openai.com' in response.headers['Content-Security-Policy']
    assert mc_files.host_root(staging.app.extensions['guild_ui_next']['feature_environ']) == '/tmp/sample/agent_requests'
    assert client.get(f'{prefix}/mc/voice/capabilities').status_code == 404
    assert client.get(f'{prefix}/dictation/capabilities').status_code == 404
    assert not calls


def test_voice_beta_requires_explicit_production_gate_and_shared_cos_partner(load_portal,monkeypatch,tmp_path):
    from minimoi_portal.guild_mounts import production_environ
    from minimoi_portal.guild_ui import mc_files
    from voice_budget_helpers import install
    install(monkeypatch,tmp_path)
    flags={'MINIMOI_GUILD_VOICE_PRODUCTION':'1','MINIMOI_GUILD_CHAT':'cos','MINIMOI_MC_VOICE':'1','MINIMOI_DICTATION':'1',mc_files.ENV:'/tmp/synthetic/agent_requests'}
    admitted=production_environ(flags)
    assert admitted['MINIMOI_MC_VOICE']=='1' and admitted['MINIMOI_DICTATION']=='1'
    assert mc_files.ENV not in admitted
    assert 'MINIMOI_MC_VOICE' not in production_environ(dict(flags,MINIMOI_GUILD_CHAT=''))
    for name,value in flags.items():monkeypatch.setenv(name,value)
    monkeypatch.setenv('MINIMOI_GUILD_PRODUCTION','1')
    monkeypatch.setenv('COS_BACKEND','http://cos-scheduler:8769')
    portal=load_portal(next_flag='1',base_url=PROD)
    client=portal.owner()
    response=client.get('/guild-next/guild/build')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'data-mc-voice="true"' in html and 'data-dictation="true"' in html
    assert "media-src 'self' blob:" in response.headers['Content-Security-Policy']
    assert client.get('/guild-next/api/v1/mc/voice/capabilities').status_code==200
    assert client.get('/guild-next/api/v1/dictation/capabilities').status_code==200
    assert portal.app.test_client().get('/guild-next/api/v1/mc/voice/capabilities').status_code==401


@pytest.mark.parametrize("chat", [None, "", "mc", "openclaw", "stub", "cos"])
def test_production_shared_files_follow_only_existing_cos_selection(chat):
    from minimoi_portal.guild_mounts import production_environ, PRODUCTION_COS_FILES
    from minimoi_portal.guild_ui import mc_files
    config = {mc_files.ENV_COS_DIR: "/app/runtime/guild/mc-evidence", mc_files.ENV_COS_AGENT_DIR: "/minimoi/data/guild/mc-evidence",
              mc_files.ENV_COS_UID: "1000", mc_files.ENV_COS_GID: "1000", mc_files.ENV: "/tmp/agent_requests",
              "MINIMOI_GUILD_MC": "openclaw", "MINIMOI_GUILD_MC_STREAM": "1"}
    if chat is not None:
        config["MINIMOI_GUILD_CHAT"] = chat
    admitted = production_environ(config)
    assert set(PRODUCTION_COS_FILES).issubset(admitted) == (chat == "cos")
    assert mc_files.ENV not in admitted and "MINIMOI_GUILD_MC_STREAM" not in admitted
    if chat == "cos":
        assert admitted["MINIMOI_GUILD_MC"] == "cos" and admitted["MINIMOI_GUILD_MC_TURNS"] == "1"
    else:
        assert "MINIMOI_GUILD_MC" not in admitted and "MINIMOI_GUILD_MC_TURNS" not in admitted


@pytest.mark.parametrize("missing", ["MINIMOI_MC_COS_FILES_DIR", "MINIMOI_MC_COS_FILES_AGENT_DIR", "MINIMOI_MC_COS_FILES_UID", "MINIMOI_MC_COS_FILES_GID"])
def test_incomplete_production_shared_file_config_is_all_held_off(missing):
    from minimoi_portal.guild_mounts import production_environ, PRODUCTION_COS_FILES
    config = {"MINIMOI_GUILD_CHAT": "cos", "MINIMOI_MC_COS_FILES_DIR": "/app/runtime/guild/mc-evidence",
              "MINIMOI_MC_COS_FILES_AGENT_DIR": "/minimoi/data/guild/mc-evidence", "MINIMOI_MC_COS_FILES_UID": "1000", "MINIMOI_MC_COS_FILES_GID": "1000"}
    del config[missing]
    admitted = production_environ(config)
    assert set(PRODUCTION_COS_FILES).isdisjoint(admitted)
    assert admitted["MINIMOI_GUILD_MC"] == "cos"  # Existing text-only selection stays available.


MC_RELAY = "http://mc-relay:8790/v1"


def test_production_chat_mc_uses_the_mc_container_relay_only(load_portal, monkeypatch):
    """Master Craftsman's own container (spec_mc_agent_container §5): MINIMOI_GUILD_CHAT=mc, the exact relay and a token."""
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    monkeypatch.setenv("MINIMOI_GUILD_CHAT", "mc")
    monkeypatch.setenv("MC_RUNTIME_URL", MC_RELAY)
    monkeypatch.setenv("MC_RUNTIME_TOKEN", "synthetic-caller-token")
    monkeypatch.setenv("MC_RUNTIME_CHAT_MODEL", "openclaw/mc-chat")
    monkeypatch.setenv("MINIMOI_GUILD_MC", "stub")                 # still ignored: the chat partner decides
    portal = load_portal(next_flag="1", base_url=PROD)
    services = portal.app.extensions["guild_ui_next"]["services"]
    assert services.mc.kind == "openclaw" and services.mc_turns is True and services.mc_stream is True


@pytest.mark.parametrize("url,token", [
    ("http://host.lima.internal:18796/v1", "t"),          # the dev native relay
    ("http://mc-relay:8790/v1/../x", "t"),
    ("https://mc-relay:8790/v1", "t"),
    ("http://cos-agent-a:18789/v1", "t"),
    ("http://mc-relay:8791/v1", "t"),
    (MC_RELAY, ""),                                       # no caller token
    ("", "t"),
])
def test_production_chat_mc_with_any_other_relay_or_no_token_is_off(load_portal, monkeypatch, url, token):
    monkeypatch.setenv("MINIMOI_GUILD_PRODUCTION", "1")
    monkeypatch.setenv("MINIMOI_GUILD_CHAT", "mc")
    monkeypatch.setenv("MC_RUNTIME_URL", url)
    monkeypatch.setenv("MC_RUNTIME_TOKEN", token)
    portal = load_portal(next_flag="1", base_url=PROD)
    services = portal.app.extensions["guild_ui_next"]["services"]
    assert services.mc.kind == "off" and services.mc_turns is False


def test_production_chat_mc_admits_voice_like_cos_but_never_the_cos_shared_files():
    from minimoi_portal.guild_mounts import production_environ, PRODUCTION_COS_FILES
    flags = {"MINIMOI_GUILD_CHAT": "mc", "MC_RUNTIME_URL": MC_RELAY, "MC_RUNTIME_TOKEN": "t",
             "MINIMOI_GUILD_VOICE_PRODUCTION": "1", "MINIMOI_MC_VOICE": "1", "MINIMOI_DICTATION": "1",
             "MINIMOI_MC_MANAGED_REALTIME": "1", **{name: "/tmp/synthetic" for name in PRODUCTION_COS_FILES}}
    admitted = production_environ(flags)
    assert admitted["MINIMOI_MC_VOICE"] == "1" and admitted["MINIMOI_DICTATION"] == "1"
    assert "MINIMOI_MC_MANAGED_REALTIME" not in admitted
    assert not set(PRODUCTION_COS_FILES) & set(admitted)
    assert admitted["MINIMOI_GUILD_MC"] == "openclaw"
