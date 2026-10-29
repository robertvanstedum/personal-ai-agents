"""A4-3 (A4_FIX_BRIEF.md item 1): owner notes and reminders are the Chief of Staff's whichever runtime Master Craftsman
chats through. MC on OpenClaw used to refuse every owner note ("Notes and reminders need the Chief of Staff platform"),
typed or spoken, because owner_actions.execute was handed services.mc. Now it is handed owner_actions.cos_for(services):
MC on CoS keeps that backend; otherwise the mount's own CoS operation client (mc/cos.py operations_from_env), built from
COS_BACKEND, or None without it (UNAVAILABLE, as before). The real portal (owner guard, CSRF, SQLite floor), the real
OpenClaw adapter over a fake runtime, and a fake CoS /chat route. No network, no model call, no provider."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from minimoi_portal.guild_ui import owner_actions as oa
from minimoi_portal.guild_ui.mc import CachedHealth
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend, operations_from_env

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_owner_actions import CosRoute, Resp
from test_mc_turns import FakeRuntime, _openclaw
from test_topic_context import _topic_item

API = "/guild-next/api/v1"
COS = "http://cos-scheduler:8769"


# ── the client and the choice ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("environ", [{}, {"COS_BACKEND": ""}, {"COS_BACKEND": "cos-scheduler:8769"},
                                     {"COS_BACKEND": "ftp://cos-scheduler"}])
def test_no_cos_backend_gives_no_operation_client(environ):
    assert operations_from_env(environ) is None


def test_the_operation_client_is_the_cos_backends_own_operation_over_cos_backend():
    route = CosRoute()
    client = operations_from_env({"COS_BACKEND": COS + "/"}, http_post=route.post)
    assert isinstance(client, ChiefOfStaffBackend) and client.kind == "cos" and client.base_url == COS
    out = client.operation("/note buy milk", conversation_id="floor-1", request_id="00000000-0000-4000-8000-000000000001")
    assert out["state"] == "ok" and out["operation"]["type"] == "note_save"
    sent = route.sent[0]
    assert sent["text"] == "/note buy milk" and sent["channel"] == "api_text" and sent["conversation_id"].startswith("guild-")


def test_mc_on_cos_keeps_its_own_backend_and_never_reads_the_separate_client():
    mc, other = SimpleNamespace(kind="cos"), object()
    assert oa.cos_for(SimpleNamespace(mc=mc, cos_operations=other)) is mc
    assert oa.cos_for(SimpleNamespace(mc=mc)) is mc                          # a bundle built before A4: unchanged


@pytest.mark.parametrize("kind", ["openclaw", "grok", "stub", "off"])
def test_mc_on_another_runtime_uses_the_cos_client_or_nothing(kind):
    client = object()
    assert oa.cos_for(SimpleNamespace(mc=SimpleNamespace(kind=kind), cos_operations=client)) is client
    assert oa.cos_for(SimpleNamespace(mc=SimpleNamespace(kind=kind), cos_operations=None)) is None
    assert oa.cos_for(SimpleNamespace(mc=SimpleNamespace(kind=kind))) is None


# ── typed, on the real portal, MC on OpenClaw ─────────────────────────────────────────────────────────────────────────

def _on_openclaw(portal, cos_operations):
    runtime = FakeRuntime()
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.mc = _openclaw(runtime)
    services.mc_health = CachedHealth(services.mc)
    services.mc_turns = True
    services.cos_operations = cos_operations
    return services, runtime


def _keep(client, token, text):
    r = client.post(f"{API}/notes", json=keyed(text=text), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["note"]


def _ask(client, token, note_id):
    return client.post(f"{API}/mc/turns", json={"note_request_id": note_id, "record_mode": "on_record"},
                       headers=write_headers(token, mode="on_record"))


def test_typed_note_with_mc_on_openclaw_saves_through_cos_with_its_receipt_and_never_reaches_mc(floored):
    route = CosRoute()
    _services, runtime = _on_openclaw(floored, ChiefOfStaffBackend(COS, http_post=route.post))
    client = floored.owner(); token = floored.csrf(client)
    note = _keep(client, token, "make a note that the kiln needs a new seal")
    body = _ask(client, token, note["request_id"]).get_json()
    assert [s["text"] for s in route.sent] == ["/note the kiln needs a new seal"]       # the owner's words only
    assert body["backend_kind"] == "platform" and body["operation"]["type"] == "cos_note"
    assert body["operation"]["saved"] is True and body["operation"]["operation_id"] == route.sent[0]["request_id"]
    assert body["reply_note"]["text"] == "Saved to Chief of Staff notes: the kiln needs a new seal"
    assert not runtime.sent                                                              # no model was asked


def test_a_retry_of_the_same_typed_turn_on_openclaw_is_the_same_operation(floored):
    route = CosRoute()
    _on_openclaw(floored, ChiefOfStaffBackend(COS, http_post=route.post))
    client = floored.owner(); token = floored.csrf(client)
    note = _keep(client, token, "/note the spare key is in the blue box")
    first = _ask(client, token, note["request_id"]).get_json()
    again = _ask(client, token, note["request_id"]).get_json()
    assert [s["text"] for s in route.sent] == ["/note the spare key is in the blue box"] * 2
    assert route.sent[0]["request_id"] == route.sent[1]["request_id"]                   # one operation id per turn
    assert first["operation"]["repeated"] is False and again["operation"]["repeated"] is True
    assert first["operation"]["operation_id"] == again["operation"]["operation_id"]
    assert first["reply_note"]["text"] == again["reply_note"]["text"]


def test_typed_reminder_on_openclaw_stays_behind_its_switch(floored, monkeypatch):
    route = CosRoute()
    _on_openclaw(floored, ChiefOfStaffBackend(COS, http_post=route.post))
    monkeypatch.setattr(oa, "reminders_enabled", lambda: False)
    client = floored.owner(); token = floored.csrf(client)
    note = _keep(client, token, "Remind me in 20 minutes to check the oven")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["message"] == oa.REMINDERS_OFF and not route.sent


def test_typed_note_with_attached_context_on_openclaw_is_still_refused_before_cos(floored):
    route = CosRoute()
    _services, runtime = _on_openclaw(floored, ChiefOfStaffBackend(COS, http_post=route.post))
    client = floored.owner(); token = floored.csrf(client)
    tid, iid = _topic_item(client, token)
    note = _keep(client, token, "make a note that the glaze is ready")
    body = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record",
                                                "topic_context": [{"topic_id": tid, "item_id": iid}]},
                       headers=write_headers(token, mode="on_record")).get_json()
    assert body["message"] == oa.WITH_CONTEXT and body["operation"] == {"type": "cos_note", "saved": False}
    assert not route.sent and not runtime.sent


def test_no_cos_backend_on_openclaw_is_unavailable_exactly_as_before(floored):
    _services, runtime = _on_openclaw(floored, None)
    client = floored.owner(); token = floored.csrf(client)
    note = _keep(client, token, "/note buy milk")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["message"] == oa.UNAVAILABLE and body["operation"] == {"type": "cos_note", "saved": False}
    assert not runtime.sent


def test_mc_on_cos_is_unchanged_and_the_separate_client_is_never_called(floored):
    used, unused = CosRoute(), CosRoute()
    services = floored.app.extensions["guild_ui_next"]["services"]
    services.mc = ChiefOfStaffBackend(COS, http_post=used.post, http_get=lambda *a, **k: Resp(200, {}))
    services.mc_health = CachedHealth(services.mc)
    services.mc_turns = True
    services.cos_operations = ChiefOfStaffBackend("http://elsewhere:1", http_post=unused.post)
    client = floored.owner(); token = floored.csrf(client)
    note = _keep(client, token, "Make a note that the gate code is 4411")
    body = _ask(client, token, note["request_id"]).get_json()
    assert [s["text"] for s in used.sent] == ["/note the gate code is 4411"] and not unused.sent
    assert body["operation"]["saved"] is True


# ── the mounts build it from COS_BACKEND only ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cos_backend", [COS, None])
def test_the_staging_mount_with_mc_on_openclaw_builds_the_cos_client_only_where_cos_is_configured(
        load_portal, monkeypatch, cos_backend):
    monkeypatch.setenv("MINIMOI_GUILD_MC", "openclaw")
    if cos_backend is None:
        monkeypatch.delenv("COS_BACKEND", raising=False)
    else:
        monkeypatch.setenv("COS_BACKEND", cos_backend)
    services = load_portal(next_flag="1").app.extensions["guild_ui_next"]["services"]
    assert services.mc.kind == "openclaw"
    chosen = oa.cos_for(services)
    if cos_backend is None:
        assert services.cos_operations is None and chosen is None
    else:
        assert chosen is services.cos_operations and chosen.base_url == COS and chosen.kind == "cos"


# ── spoken, on MC's direct voice path, MC on OpenClaw ─────────────────────────────────────────────────────────────────

def _voice_on_openclaw(monkeypatch, tmp_path, cos_operations):
    """test_mc_voice_direct's synthetic mount (real routes, consult ledger, typed-turn route code), with MC's services on
    OpenClaw: services.mc is not CoS, and the CoS operation client is the one under test."""
    from test_mc_voice_direct import ON, build
    r = build(monkeypatch, tmp_path, dict(ON))
    r.config["services"] = SimpleNamespace(mc=SimpleNamespace(kind="openclaw"), mc_turns=True, cos_operations=cos_operations)
    return r


def test_spoken_note_with_mc_on_openclaw_saves_through_cos_with_its_receipt(monkeypatch, tmp_path):
    from test_mc_voice_direct import Cos, call, start
    cos = Cos()
    r = _voice_on_openclaw(monkeypatch, tmp_path, cos)
    sid = start(r)["voice_session_id"]
    saved = call(r, sid, call_id="note-1", name="save_note", words="Make a note that the kiln needs a new seal").get_json()
    assert saved["saved"] is True and saved["receipt"], saved
    assert saved["spoken"] == "Saved to Chief of Staff notes: the kiln needs a new seal"
    consulted = call(r, sid, call_id="note-2", words="make a note that the glaze is ready").get_json()   # by consult too
    assert consulted["saved"] is True and consulted["receipt"] and "not saved" not in consulted["spoken"].lower()
    again = call(r, sid, call_id="note-1", name="save_note", words="Make a note that the kiln needs a new seal").get_json()
    assert again == saved                                                        # a retry: the same answer, no second run
    assert cos.calls == ["/note the kiln needs a new seal", "/note the glaze is ready"]
    assert not r.turns                                                           # no model was asked


def test_spoken_note_with_mc_on_openclaw_and_no_cos_is_not_saved(monkeypatch, tmp_path):
    from minimoi_portal.guild_ui import mc_voice_direct as direct
    from test_mc_voice_direct import call, start
    r = _voice_on_openclaw(monkeypatch, tmp_path, None)
    sid = start(r)["voice_session_id"]
    said = call(r, sid, call_id="note-1", name="save_note", words="Make a note that the kiln needs a new seal").get_json()
    assert said["saved"] is False and said["receipt"] is None and said["spoken"] == direct.NOT_SAVED
    assert said["outcome"] == "not_done" and not r.turns
