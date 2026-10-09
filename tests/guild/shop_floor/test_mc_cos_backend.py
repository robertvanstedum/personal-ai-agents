"""The Chief of Staff as the Shop floor's conversation partner (mc/cos.py): honest identity, one POST per note, no fallback,
no Private claim, no documents, no stub."""
from __future__ import annotations

import pytest
import requests

from minimoi_portal.guild_ui.mc import (Health, TurnRequest, UnavailableBackend, backend_from_env, reply_author, view)
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
from minimoi_portal.guild_ui.stores import CHIEF_OF_STAFF, MASTER_CRAFTSMAN

BASE = "http://cos-scheduler:8769"
REQ = TurnRequest(conversation_id="floor:robert:2026-10-09", text="How is the build going?", note_request_id="note-1",
                  correlation_id="t1", context={"about": "build"})


class Resp:
    def __init__(self, status=200, payload=None, raises=False):
        self.status_code, self._payload, self._raises = status, payload, raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


def _backend(post=None, get=None):
    calls = {"post": [], "get": []}

    def fake_post(url, json=None, timeout=None, **kw):
        calls["post"].append((url, json, timeout))
        if isinstance(post, Exception):
            raise post
        return post or Resp(200, {"reply": "Build is on track."})

    def fake_get(url, timeout=None, **kw):
        calls["get"].append(url)
        if isinstance(get, Exception):
            raise get
        return get or Resp(200, {})

    return ChiefOfStaffBackend(BASE, http_get=fake_get, http_post=fake_post), calls


def test_the_switch_builds_it_from_the_portals_existing_cos_address():
    backend = backend_from_env({"MINIMOI_GUILD_MC": "cos", "COS_BACKEND": BASE})
    assert isinstance(backend, ChiefOfStaffBackend) and backend.kind == "cos" and backend.base_url == BASE


@pytest.mark.parametrize("environ", [{"MINIMOI_GUILD_MC": "cos"}, {"MINIMOI_GUILD_MC": "cos", "COS_BACKEND": ""},
                                     {"MINIMOI_GUILD_MC": "cos", "COS_BACKEND": "cos-scheduler:8769"}])
def test_without_a_cos_address_it_is_unavailable_never_the_stub(environ):
    backend = backend_from_env(environ)
    assert isinstance(backend, UnavailableBackend) and backend.kind == "cos"
    assert backend.health().state == "unavailable" and backend.turn(REQ).status == "unavailable"


def test_one_post_to_chat_with_its_own_session_and_the_note_id_and_nothing_else():
    backend, calls = _backend()
    result = backend.turn(REQ)
    assert (result.status, result.backend_kind, result.text) == ("answered", "cos", "Build is on track.")
    (url, body, timeout), = calls["post"]
    assert url == f"{BASE}/chat" and timeout == 120
    assert set(body) == {"text", "channel", "conversation_id", "request_id"}
    assert body["text"] == REQ.text and body["request_id"] == "note-1" and body["channel"] == "api_text"
    assert body["conversation_id"].startswith("guild-") and body["conversation_id"] != "owner"
    assert "floor:robert" not in body["conversation_id"]                  # a hash, not the Shop floor's ids
    again, _ = _backend()
    assert again.turn(REQ).text == result.text and backend._session("a") != backend._session("b")


def test_a_reply_is_kept_as_the_chief_of_staffs_never_master_craftsmans():
    backend, _ = _backend()
    author = reply_author(backend.turn(REQ))
    assert author == CHIEF_OF_STAFF and author != MASTER_CRAFTSMAN and author.label == "Chief of Staff"


@pytest.mark.parametrize("post,status", [
    (Resp(200, {"reply": ""}), "error"), (Resp(200, {"reply": "  "}), "error"), (Resp(200, {}), "error"),
    (Resp(200, None, raises=True), "error"), (Resp(400, {"reply": "bad"}), "error"), (Resp(500, {"reply": "CoS error"}), "error"),
    (Resp(503, {}), "unavailable"), (Resp(502, {}), "unavailable"),
    (requests.Timeout(), "timeout_uncertain"), (requests.ConnectionError(), "unavailable"),
], ids=["empty", "blank", "no-reply", "not-json", "400", "500", "503", "502", "timeout", "connection"])
def test_nothing_but_a_real_reply_is_an_answer_and_a_failure_is_never_retried(post, status):
    backend, calls = _backend(post=post)
    result = backend.turn(REQ)
    assert result.status == status and result.text is None
    assert len(calls["post"]) == 1                                              # one dispatch per note
    with pytest.raises(Exception):
        reply_author(result)


def test_a_private_question_is_refused_and_nothing_is_sent():
    backend, calls = _backend()
    result = backend.turn(TurnRequest(conversation_id="private:x:y", text="secret", note_request_id="p1",
                                      context={"mode": "private"}))
    assert result.status == "refused" and result.failure_class == "private_unsupported"
    assert calls["post"] == [] and backend.supports_private is False and backend.accepts_files is False


def test_health_reads_cos_health_and_never_a_model():
    backend, calls = _backend(get=Resp(200, {}))
    assert backend.health().state == "ready" and calls["get"] == [f"{BASE}/health"] and calls["post"] == []
    down, _ = _backend(get=requests.ConnectionError())
    assert down.health() == Health("unavailable", "not_ready", observed_at=down.health().observed_at)
    sick, _ = _backend(get=Resp(500, {}))
    assert sick.health().state == "unavailable"


def test_the_floor_says_chief_of_staff_not_master_craftsman():
    live = view(Health("ready", reachable=True), notes_ok=True, turns_on=True, name="Chief of Staff")
    assert live["state"] == "live" and live["turns"] is True
    assert "Chief of Staff" in live["header"] and "Master Craftsman" not in live["header"] + live["notes_text"]
    down = view(Health("unavailable", "not_ready"), notes_ok=True, turns_on=True, name="Chief of Staff")
    assert "Chief of Staff is unavailable" in down["header"] and "Master Craftsman" not in down["header"]
    still_mc = view(Health("ready", reachable=True), notes_ok=True, turns_on=True)
    assert "Master Craftsman" in still_mc["header"]


# ── through the real Shop floor routes (owner guard, CSRF, floor store) ───────────────────────────────────────────────────────

from minimoi_portal.guild_ui.mc import CachedHealth  # noqa: E402

from floor_db_helpers import floor_db, floored, keyed  # noqa: E402,F401  (pytest fixtures)
from floor_helpers import load_portal, write_headers  # noqa: E402,F401

API = "/guild-next/api/v1"


def _cos_turned(portal, post=None):
    backend, calls = _backend(post=post)
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    return calls


def _keep(client, token, text):
    r = client.post(f"{API}/notes", json=keyed(text=text), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["note"]


def test_a_kept_note_is_answered_by_the_chief_of_staff_and_kept_as_hers(floored):
    calls = _cos_turned(floored)
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    assert client.get(f"{API}/floor").get_json()["mc_header"].startswith("Chief of Staff is live")
    note = _keep(client, token, "How is the build going?")
    r = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record"},
                    headers=write_headers(token))
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "answered" and body["backend_kind"] == "cos"
    assert body["reply_note"]["text"] == "Build is on track."
    assert body["reply_note"]["who"] == "chief_of_staff" and body["reply_note"]["author_label"] == "Chief of Staff"
    assert "Master Craftsman" not in body["mc_header"] and body["mc_header"].startswith("Chief of Staff is live")
    assert len(calls["post"]) == 1 and calls["post"][0][1]["text"] == "How is the build going?"
    assert [row["author"] for row in db.rows("floor_messages")] == ["robert", "chief_of_staff"]


def test_a_down_chief_of_staff_answers_nothing_and_keeps_only_the_note(floored):
    calls = _cos_turned(floored, post=requests.ConnectionError())
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    note = _keep(client, token, "hello")
    body = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record"},
                       headers=write_headers(token)).get_json()
    assert body["status"] == "unavailable" and body["reply_note"] is None
    assert [row["author"] for row in db.rows("floor_messages")] == ["robert"] and len(calls["post"]) == 1


def test_a_private_question_is_refused_over_the_chief_of_staff_and_nothing_is_sent(floored):
    calls = _cos_turned(floored)
    client = floored.owner()
    token = floored.csrf(client)
    r = client.post(f"{API}/mc/private", json={"text": "off the record?", "session": "s" * 20},
                    headers=write_headers(token, mode="off_record"))
    assert r.status_code == 409 and r.get_json()["error"] == "private_unsupported"
    assert calls["post"] == []


# ── what the page SHOWS: the partner's name in every visible place, and the unsupported controls refused up front ─────────────

import re  # noqa: E402
from pathlib import Path  # noqa: E402

JS = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui" / "static" / "js"


def _visible(html):
    html = re.sub(r"(?s)<(script|style)\b.*?</\1>", "", html)
    html = re.sub(r"(?s)<!--.*?-->", "", html)
    return html


def test_the_page_names_the_chief_of_staff_and_disables_private(floored):
    _cos_turned(floored)
    html = floored.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert 'data-mc-name="Chief of Staff"' in html and 'data-mc-private="false"' in html and 'data-mc-files="false"' in html
    seen = _visible(html)
    assert 'class="mc-title">Chief of Staff<' in html and "Ask Master Craftsman" not in html
    assert 'data-private-off' in html and "Private is not available" in html
    assert "Master Craftsman" not in re.findall(r'<h2 class="mc-title">([^<]*)<', html)[0]
    assert "Master Craftsman" not in re.findall(r'<h1 class="hero-name">([^<]*)<', html)[0]
    assert "Master Craftsman" not in re.findall(r'data-mc-record[^>]*title="([^"]*)"', html)[0]


def test_the_page_stays_master_craftsman_when_that_is_the_partner(floored):
    html = floored.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert 'data-mc-name="Master Craftsman"' in html and 'data-mc-private="true"' in html and 'data-private-off' not in html


def test_server_wording_carries_the_partners_name_but_never_a_kept_text(floored):
    _cos_turned(floored, post=requests.ConnectionError())
    client = floored.owner()
    token = floored.csrf(client)
    note = _keep(client, token, "Is Master Craftsman there?")                    # the user's own words must stay as typed
    body = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record"},
                       headers=write_headers(token)).get_json()
    assert "Chief of Staff" in body["message"] and "Master Craftsman" not in body["message"]
    assert note["text"] == "Is Master Craftsman there?"
    private = client.post(f"{API}/mc/private", json={"text": "x", "session": "s" * 20},
                          headers=write_headers(token, mode="off_record")).get_json()
    assert "Chief of Staff" in private["message"] and "Master Craftsman" not in private["message"]
    kept = client.get(f"{API}/floor").get_json()
    assert "Master Craftsman" not in kept["mc_header"]


def test_the_composer_refuses_private_and_documents_before_anything_is_kept_sent_or_locked():
    js = (JS / "conversation.js").read_text()
    send = js[js.index("async function sendNote("):]
    assert send.index("dataset.mcPrivate === 'false'") < send.index("await askPrivate(text, input)")      # before a Private question is sent
    refusal = send.index("dataset.mcFiles === 'false'")
    # before Send is locked (sendingNote / send.disabled), the tray is taken, or the note is kept: a refusal must never leave Send disabled
    assert refusal < send.index("sendingNote = true;") < send.index("send.disabled = true;") < send.index("takeTray()") < send.index("apiPost('/notes'")
    assert "window.guildTopicContext" in send[refusal:send.index("sendingNote = true;")]                 # topic items count as documents


def test_ask_about_this_is_off_not_promised_when_the_partner_reads_no_documents():
    js = (JS / "workshop_topic.js").read_text()
    assert "const askOff = () => document.body.dataset.mcFiles === 'false';" in js
    assert js.count("askOff()") >= 5                                                  # both menus, both buttons, and askAbout itself
    ask = js[js.index("function askAbout(id) {"):]
    assert ask.index("if (askOff())") < ask.index("window.guildTopicContext = [")     # nothing is added to the next message
