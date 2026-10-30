"""C6, the agent door (spec v0.2 §3.8): T10, and T21's hung source that never stops an agent's notice."""
from __future__ import annotations

import hashlib
import threading

import pytest
from flask import Flask

from core.supervision.telegram import Bot
from minimoi_portal import action_waiting as notices
from minimoi_portal.action_waiting import door, sources as src, store as st
from minimoi_portal.action_waiting.service import Service
from minimoi_portal.guild_ui import action_waiting as mc
from aw_fakes import FAKE_TOKEN, Clock, FakeGuildDB, FakeTelegram
from aw_fakes import auth_dir, clock, make_settings, telegram, token_file

PYTEST_FIXTURES = (auth_dir, clock, make_settings, telegram, token_file,)                  # used by name below

CLAUDE = "c" * 43 + "-claude-test-token"
CODEX = "x" * 43 + "-codex-test-token"
TOKENS = {hashlib.sha256(CLAUDE.encode()).hexdigest(): "claude-code", hashlib.sha256(CODEX.encode()).hexdigest(): "codex"}


def build(make_settings, clock, telegram, *, door_clock=None, **over):
    settings = make_settings(agent_tokens=TOKENS, **over)
    store = st.Store(settings.root, clock=clock)
    sources = src.Sources(settings, store.digest, guild_db=FakeGuildDB(), clock=clock)
    svc = Service(settings, store=store, sources=sources, opener=telegram, clock=clock,
                  legacy_bot=lambda: Bot(FAKE_TOKEN, opener=FakeTelegram()), source_timeout=5)
    app = Flask(__name__)
    assert door.attach(app, svc, clock=door_clock or Clock(0.0))
    return svc, app.test_client()


@pytest.fixture
def world(make_settings, clock, telegram, auth_dir, monkeypatch):
    svc, client = build(make_settings, clock, telegram)
    monkeypatch.setattr(notices, "_SERVICE", svc)
    yield svc, client
    svc.sender.release()


def call(client, path, body, token=CLAUDE, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/internal/notices/v1/{path}", json=body, headers=headers, **kw)


WORDS = "Need your yes on merging PR #12 before I continue"


def test_tokens_gate_the_door(world):
    svc, client = world
    assert call(client, "raise", {"key": "k-000001", "words": WORDS}, token=None).status_code == 401
    assert call(client, "raise", {"key": "k-000001", "words": WORDS}, token="y" * 60).status_code == 401
    assert call(client, "raise", {"key": "k-000001", "words": WORDS}, token="short").status_code == 401
    assert svc.store.read()["items"] == {}


def test_the_agent_comes_from_the_token_never_the_body(world):
    svc, client = world
    r = call(client, "raise", {"key": "k-000001", "words": WORDS, "agent": "claude-code"}, token=CODEX)
    assert r.status_code == 200
    item = svc.store.read()["items"][r.get_json()["code"]]
    assert item["facts"]["agent"] == "codex" and item["key"] == Service.agent_key("codex", "k-000001")
    assert "k-000001" not in item["key"]                      # the caller's key is stored hashed (Codex B4)


def test_same_key_is_one_item_and_one_notice_conflicts_are_refused(world, telegram):
    svc, client = world
    first = call(client, "raise", {"key": "k-000001", "words": WORDS, "branch": "claude/x"}).get_json()
    again = call(client, "raise", {"key": "k-000001", "words": WORDS, "branch": "claude/x"}).get_json()
    assert first["code"] == again["code"] == "W-1" and first["new"] is True and again["new"] is False
    conflict = call(client, "raise", {"key": "k-000001", "words": "Something else entirely this time"})
    assert conflict.status_code == 409 and conflict.get_json() == {"error": "key_conflict"}
    svc.sweep()
    svc.sweep()
    assert len(telegram.sent) == 1 and "Claude Code is blocked on you · branch claude/x" in telegram.texts[0]


def test_resolve_is_idempotent_and_a_resolved_key_asks_again(world, telegram):
    svc, client = world
    call(client, "raise", {"key": "k-000001", "words": WORDS})
    svc.sweep()
    assert call(client, "resolve", {"key": "k-000001"}).get_json() == {"code": "W-1", "state": "resolved"}
    assert call(client, "resolve", {"key": "k-000001"}).get_json() == {"code": "W-1", "state": "resolved"}
    assert call(client, "resolve", {"key": "k-unknown"}).get_json() == {"code": None, "state": "not_found"}
    again = call(client, "raise", {"key": "k-000001", "words": WORDS}).get_json()
    assert again["code"] == "W-2" and again["new"] is True
    svc.sweep()
    assert len(telegram.sent) == 2 and "W-2" in telegram.texts[1]


def test_five_open_per_agent_then_429_and_stale_items_do_not_count(world, clock):
    svc, client = world
    for n in range(5):
        assert call(client, "raise", {"key": f"k-00000{n}", "words": WORDS + f" ({n})"}).status_code == 200
    sixth = call(client, "raise", {"key": "k-000009", "words": WORDS})
    assert sixth.status_code == 429 and sixth.get_json() == {"error": "too_many_open"}
    assert call(client, "raise", {"key": "k-000009", "words": WORDS}, token=CODEX).status_code == 200
    clock.advance(73 * 3600)
    svc.sweep()
    data = svc.store.read()
    assert all(i["stale"] and i["state"] == "open" for i in data["items"].values())   # never closed by itself
    assert call(client, "raise", {"key": "k-000009", "words": WORDS}).status_code == 200


def test_secrets_and_cards_are_scrubbed_everywhere(make_settings, clock, telegram, auth_dir, monkeypatch):
    svc, client = build(make_settings, clock, telegram, words_on_telegram=True)
    monkeypatch.setattr(notices, "_SERVICE", svc)
    planted = "Rotate ghp_ABCDEFGHIJKLMNOPQRSTUVWX and pay 4242 4242 4242 4242 then tell me"
    code = call(client, "raise", {"key": "k-000001", "words": planted}).get_json()["code"]
    svc.sweep()
    stored = svc.store.read()["items"][code]["words"]
    for where in (stored, mc.context_block("robert"), telegram.texts[0], mc.api_payload()["items"][0]["words"]):
        assert "ghp_" not in where and "4242 4242" not in where
        assert "credential removed" in where          # MC's block drops brackets from agents' words (no fake markers)
    svc.sender.release()


@pytest.mark.parametrize("body", [
    {"key": "short", "words": WORDS}, {"key": "k-000001", "words": "too short"},
    {"key": "k-000001", "words": "x" * 1501}, {"key": "k-000001", "words": "bell\x07 inside the words here"},
    {"key": "k-000001", "words": WORDS, "branch": "bad branch!"}, {"key": "k-000001", "words": WORDS, "session": "a b"},
    {"words": WORDS}, ["not", "an", "object"]])
def test_malformed_bodies_are_refused(world, body):
    svc, client = world
    assert call(client, "raise", body).status_code == 400
    assert svc.store.read()["items"] == {}


def test_newlines_are_allowed_in_words(world):
    _svc, client = world
    assert call(client, "raise", {"key": "k-000001", "words": "line one\nline two of it"}).status_code == 200


def test_body_bound_and_json_only(world):
    _svc, client = world
    big = {"key": "k-000001", "words": WORDS, "pad": "p" * 9000}
    assert call(client, "raise", big).status_code == 400
    r = client.post("/internal/notices/v1/raise", data="key=k", headers={"Authorization": f"Bearer {CLAUDE}"})
    assert r.status_code == 400


def test_rate_limit_per_address(make_settings, clock, telegram, auth_dir):
    svc, client = build(make_settings, clock, telegram)
    codes = [call(client, "resolve", {"key": "k-000001"}).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    svc.sender.release()


def test_delivery_answer_says_whether_robert_will_be_told(make_settings, clock, telegram, auth_dir, token_file):
    rec, client = build(make_settings, clock, telegram, mode="record")
    assert call(client, "raise", {"key": "k-000001", "words": WORDS}).get_json()["delivery"] == "record"
    rec.sender.release()
    on, client = build(make_settings, clock, telegram, root=rec.settings.root.parent / "on")
    on.sweep()                                                # a fresh heartbeat
    assert call(client, "raise", {"key": "k-000001", "words": WORDS}).get_json()["delivery"] == "pending"
    on.sweep()
    assert call(client, "raise", {"key": "k-000001", "words": WORDS}).get_json()["delivery"] == "told"
    token_file.unlink()
    assert call(client, "raise", {"key": "k-000002", "words": WORDS}).get_json()["delivery"] == "not_configured"
    on.sender.release()


def test_no_door_without_tokens_or_when_off(make_settings, tmp_path, monkeypatch):
    from minimoi_portal.action_waiting import install
    monkeypatch.setattr(notices, "_SERVICE", None)            # restored after; install() replaces it
    app = Flask(__name__)
    install(app, {"MINIMOI_ACTION_NOTICES": "record", "MINIMOI_ACTION_NOTICES_DIR": str(tmp_path / "n")},
            base_url="https://dev.minimoi.test")
    assert not any(r.rule.startswith("/internal/notices") for r in app.url_map.iter_rules())


def test_a_hung_source_never_stops_an_agent_notice(make_settings, clock, telegram, auth_dir, monkeypatch):
    """T21."""
    svc, client = build(make_settings, clock, telegram)
    svc.source_timeout = 0.3
    release, entered = threading.Event(), []

    def hung(snap):
        entered.append(1)
        release.wait(10)
    monkeypatch.setattr(svc.sources, "_guests", hung)
    call(client, "raise", {"key": "k-000001", "words": WORDS})
    svc.sweep()
    assert len(telegram.sent) == 1 and svc.coverage["guild"] == "unavailable"
    svc.sweep()
    assert len(entered) == 1                                  # the hung reader is not started a second time
    release.set()
    svc.sender.release()
