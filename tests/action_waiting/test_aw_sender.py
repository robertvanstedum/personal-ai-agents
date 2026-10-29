"""C3, words and the sender (spec v0.2 §3.5–3.6): T2, T3, T4, T9 (send side), T11, T12, T14, T15, T16."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from core.supervision.telegram import Bot
from minimoi_portal import auth
from minimoi_portal.action_waiting import sources as src, store as st, words
from minimoi_portal.action_waiting.service import Service
from minimoi_portal.action_waiting.settings import MESSAGE_MAX
from utils import credential_scrub, payment_scrub
from aw_fakes import FAKE_TOKEN, FakeGuildDB, FakeTelegram
from aw_fakes import auth_dir, clock, make_settings, telegram, token_file

PYTEST_FIXTURES = (auth_dir, clock, make_settings, telegram, token_file,)                  # used by name in the tests below


def make(make_settings, clock, telegram, legacy=None, db=None, decisions=None, decision_state=None, **over):
    settings = make_settings(**over)
    store = st.Store(settings.root, clock=clock)
    db = db if db is not None else FakeGuildDB()
    rows = decisions if decisions is not None else {"value": []}
    sources = src.Sources(settings, store.digest, guild_db=db, decision_rows=lambda: rows["value"],
                          decision_state=decision_state or (lambda did: "new"), clock=clock)
    legacy = legacy or FakeTelegram()
    svc = Service(settings, store=store, sources=sources, opener=telegram, clock=clock,
                  legacy_bot=lambda: Bot(FAKE_TOKEN, opener=legacy), source_timeout=5)
    svc.db, svc.rows = db, rows
    return svc


def items(svc):
    return svc.store.read()["items"]


@pytest.fixture
def svc(make_settings, clock, telegram, auth_dir):
    s = make(make_settings, clock, telegram)
    yield s
    s.sender.release()


def test_one_new_item_is_told_once_with_its_link(svc, telegram):
    svc.db.add(41, "ann@x.test")
    svc.sweep()
    assert len(telegram.sent) == 1
    text = telegram.texts[0]
    assert text.splitlines()[0] == "[dev] MiniMoi · action waiting · W-1"
    assert "Guest access request · Meu Português · since" in text
    assert "Open in MC: https://dev.minimoi.test/guild-next/guild/build?waiting=W-1" in text
    assert "Acts on: https://dev.minimoi.test/guild/operate" in text
    assert items(svc)["W-1"]["delivery"]["state"] == "told"
    svc.sweep()
    svc.sweep()
    assert len(telegram.sent) == 1                           # told once


def test_restart_before_send_sends_exactly_once(make_settings, clock, telegram, auth_dir):
    """T2: the item is committed, the process 'dies' before sending, a new sender sends it once."""
    first = make(make_settings, clock, telegram)
    code = first.record_guest("ann@x.test", pending={"token": "t" * 32, "requested_at": "2026-10-29T19:00:00+00:00"})
    assert code == "W-1" and telegram.sent == []
    first.sender.release()                                    # the process dies; nothing in memory survives
    second = make(make_settings, clock, telegram)
    (auth_dir / "pending.json").write_text(json.dumps({"pending": [
        {"token": "t" * 32, "email": "ann@x.test", "requested_at": "2026-10-29T19:00:00+00:00"}]}))
    second.sweep()
    second.sweep()
    assert len(telegram.sent) == 1 and items(second)["W-1"]["delivery"]["state"] == "told"
    second.sender.release()


def _fail_store_commits(monkeypatch, store, flag):
    real = store._atomic_write

    def write(name, payload, mode=0o600):
        if flag["on"] and name == st.STORE:
            raise OSError("disk full")
        return real(name, payload, mode)
    monkeypatch.setattr(store, "_atomic_write", write)


def test_sent_but_unrecorded_is_never_resent_by_this_process(make_settings, clock, telegram, auth_dir, monkeypatch):
    """T3, same process: the held send is recorded later, never sent again."""
    svc = make(make_settings, clock, telegram)
    svc.record_guest("ann@x.test", guild={"id": 41, "requested_at": "2026-10-29T19:00:00+00:00"})
    svc.db.add(41, "ann@x.test", hour=19, day=29)
    svc.db.rows[41]["requested_at"] = datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc)
    flag = {"on": False}
    _fail_store_commits(monkeypatch, svc.store, flag)
    svc.sweep()                                               # nothing new to commit; the send happens
    assert len(telegram.sent) == 1 and items(svc)["W-1"]["delivery"]["state"] == "told"
    # Now a second item whose record commit fails after Telegram accepted it.
    svc.record_guest("bo@x.test", guild={"id": 42, "requested_at": "2026-10-29T19:00:00+00:00"})
    svc.db.add(42, "bo@x.test")
    svc.db.rows[42]["requested_at"] = datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc)
    flag["on"] = True
    svc.sweep()
    assert len(telegram.sent) == 2 and "W-2" in svc.sender._held
    svc.sweep()
    svc.sweep()
    assert len(telegram.sent) == 2                            # held: never resent
    flag["on"] = False
    svc.sweep()
    assert len(telegram.sent) == 2 and items(svc)["W-2"]["delivery"]["state"] == "told" and not svc.sender._held
    svc.sender.release()


def test_sent_but_unrecorded_repeats_exactly_once_after_a_restart(make_settings, clock, telegram, auth_dir,
                                                                  monkeypatch):
    """T3, restart: the one accepted duplicate (spec §1), and only one."""
    svc = make(make_settings, clock, telegram)
    svc.sweep()                                               # an empty sweep records the source files' identity
    svc.record_guest("bo@x.test", guild={"id": 42, "requested_at": "2026-10-29T19:00:00+00:00"})
    svc.db.add(42, "bo@x.test")
    svc.db.rows[42]["requested_at"] = datetime(2026, 10, 29, 19, 0, tzinfo=timezone.utc)
    flag = {"on": True}
    _fail_store_commits(monkeypatch, svc.store, flag)
    svc.sweep()
    assert len(telegram.sent) == 1 and items(svc)["W-1"]["delivery"]["state"] == "pending"
    svc.sender.release()                                      # crash: the held set is lost
    again = make(make_settings, clock, telegram, db=svc.db)
    again.sweep()
    again.sweep()
    assert len(telegram.sent) == 2 and items(again)["W-1"]["delivery"]["state"] == "told"
    again.sender.release()


def test_failed_send_is_kept_retried_with_backoff_and_never_leaks_the_token(svc, telegram, clock, caplog):
    """T4."""
    caplog.set_level(logging.DEBUG)
    telegram.fail = urllib.error.URLError(f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage")
    svc.db.add(41, "ann@x.test")
    svc.sweep()
    d = items(svc)["W-1"]["delivery"]
    assert (d["state"], d["attempts"], d["last_code"]) == ("failing", 1, "telegram_sendMessage_failed:URLError")
    assert svc.health()["reasons"] == ["failing"] and svc.sender_view()["state"] == "failing"
    for name in (st.STORE, st.STATE, st.EVENTS):
        blob = (svc.store.root / name).read_text()
        assert FAKE_TOKEN not in blob and "api.telegram.org" not in blob
    assert FAKE_TOKEN not in caplog.text and "api.telegram.org" not in caplog.text
    tries = len(telegram.urls)
    clock.advance(29)
    svc.sweep()
    assert len(telegram.urls) == tries                        # still inside the 30 s backoff
    telegram.fail = None
    clock.advance(2)
    svc.sweep()
    svc.sweep()
    assert len(telegram.sent) == 1 and items(svc)["W-1"]["delivery"]["state"] == "told"
    assert svc.sender_view()["state"] == "ok"


def test_backoff_doubles_to_thirty_minutes(svc, telegram, clock):
    telegram.fail = RuntimeError("boom")
    svc.db.add(41, "ann@x.test")
    gaps = []
    for _ in range(9):
        svc.sweep()
        d = items(svc)["W-1"]["delivery"]
        gaps.append(round(d["next_try_at"] - clock()))
        clock.advance(d["next_try_at"] - clock())
    assert gaps[:7] == [30, 60, 120, 240, 480, 960, 1800] and gaps[-1] == 1800
    assert items(svc)["W-1"]["delivery"]["last_code"] == "telegram_sendMessage_failed:RuntimeError"


def test_bad_token_file_is_not_configured_and_after_the_fix_told_once(svc, telegram, token_file):
    """T4, the dev check's step 6: a world-readable token file is refused; fixed, the item is told once."""
    os.chmod(token_file, 0o644)
    svc.db.add(41, "ann@x.test")
    svc.sweep()
    assert telegram.sent == [] and svc.sender_view() == {"state": "not_configured", "since": None,
                                                         "last_ok": None, "reason": "token_file_permissions"}
    assert "not_configured" in svc.health()["reasons"]
    os.chmod(token_file, 0o600)
    svc.sweep()
    svc.sweep()
    assert len(telegram.sent) == 1


@pytest.mark.parametrize("change,reason", [
    ({"env": "prod"}, "env_mismatch"), ({"chat_id": None}, "chat_id_invalid"), ({"token_file": None}, "token_file_unset"),
    ({"bot_id": 7000000002}, "bot_id_mismatch"), ({"token_file": "/nonexistent/bot-token"}, "token_file_missing")])
def test_sender_configuration_is_checked(make_settings, clock, telegram, auth_dir, change, reason):
    svc = make(make_settings, clock, telegram, **change)
    svc.db.add(41, "ann@x.test")
    svc.sweep()
    assert telegram.sent == [] and svc.sender_view()["reason"] == reason
    svc.sender.release()


def test_record_mode_stores_and_never_sends(make_settings, clock, telegram, auth_dir):
    svc = make(make_settings, clock, telegram, mode="record")
    svc.db.add(41, "ann@x.test")
    svc.sweep()
    assert telegram.sent == [] and len(st.open_items(svc.store.read())) == 1
    assert svc.health() == {"healthy": False, "reasons": ["record_only"]}
    svc.sender.release()


def test_an_item_resolved_before_its_first_send_is_never_sent(make_settings, clock, telegram, auth_dir):
    rec = make(make_settings, clock, telegram, mode="record")
    rec.db.add(41, "ann@x.test")
    rec.sweep()
    rec.db.rows[41]["status"] = "rejected"
    rec.sweep()
    rec.sender.release()
    on = make(make_settings, clock, telegram, db=rec.db)
    on.sweep()
    assert telegram.sent == []
    on.sender.release()


def test_one_sender_per_folder(make_settings, clock, telegram, auth_dir):
    """T14."""
    a = make(make_settings, clock, telegram)
    b = make(make_settings, clock, telegram, db=a.db)
    a.db.add(41, "ann@x.test")
    assert a.sender.holds_lock() and not b.sender.holds_lock()
    b.sweep()
    assert telegram.sent == [] and len(st.open_items(b.store.read())) == 1   # b records, never sends
    a.sweep()
    b.sweep()
    assert len(telegram.sent) == 1
    a.sender.release()
    assert b.sender.holds_lock()
    b.sender.release()


def test_reminder_once_after_24h_none_after_resolve_none_with_zero(make_settings, clock, telegram, auth_dir):
    """T15."""
    svc = make(make_settings, clock, telegram)
    svc.db.add(41, "ann@x.test")
    svc.db.add(42, "bo@x.test")
    svc.sweep()
    assert len(telegram.sent) == 1                            # two items, one grouped message
    clock.advance(23 * 3600)
    svc.sweep()
    assert len(telegram.sent) == 1
    svc.db.rows[42]["status"] = "granted"                     # W-2 answered before its reminder
    clock.advance(3600 + 1)
    svc.sweep()
    assert len(telegram.sent) == 2
    assert telegram.texts[1].startswith("[dev] MiniMoi · still waiting (24 h) · W-1")
    clock.advance(48 * 3600)
    svc.sweep()
    assert len(telegram.sent) == 2                            # never twice
    svc.sender.release()
    never = make(make_settings, clock, FakeTelegram(), remind_hours=0.0, root=svc.settings.root.parent / "never")
    never.db.add(51, "cy@x.test")
    never.sweep()
    clock.advance(72 * 3600)
    never.sweep()
    assert len(never.sender.opener.sent) == 1
    never.sender.release()


def test_grouping_five_in_one_message_twelve_in_two(make_settings, clock, telegram, auth_dir):
    """T16."""
    svc = make(make_settings, clock, telegram)
    for i in range(5):
        svc.db.add(100 + i, f"g{i}@x.test")
    svc.sweep()
    assert len(telegram.sent) == 1
    text = telegram.texts[0]
    assert text.splitlines()[0] == "[dev] MiniMoi · 5 actions waiting" and text.endswith("?waiting=all")
    ids = {i["delivery"]["message_id"] for i in items(svc).values()}
    assert len(ids) == 1 and all(i["delivery"]["state"] == "told" for i in items(svc).values())
    svc.sender.release()
    twelve = make(make_settings, clock, FakeTelegram(), root=svc.settings.root.parent / "other")
    for i in range(12):
        twelve.db.add(200 + i, f"h{i}@x.test")
    twelve.sweep()
    sent = twelve.sender.opener.texts
    assert [t.splitlines()[0] for t in sent] == ["[dev] MiniMoi · 10 actions waiting", "[dev] MiniMoi · 2 actions waiting"]
    twelve.sender.release()


def test_decision_grace_reread_and_by_supervisor(make_settings, clock, telegram, auth_dir):
    """T9, send side: a new D-code is told only after 180 s, and only if still new when re-read."""
    created = datetime.fromtimestamp(clock(), timezone.utc)
    states = {14: "new", 15: "new"}
    rows = {"value": [
        {"id": 14, "requester": {"agent": "codex"}, "text": "Merge PR #9 now please", "state": "new",
         "created_at": created, "expires_at": created + timedelta(hours=24)},
        {"id": 15, "requester": {"agent": "claude-code"}, "text": "Deploy the bundle please", "state": "new",
         "created_at": created, "expires_at": created + timedelta(hours=24)}]}
    svc = make(make_settings, clock, telegram, decisions=rows, decision_state=lambda did: states[did],
               supervision=True)
    svc.sweep()
    assert telegram.sent == []
    states[15] = "sent"                                       # the supervisor sent D-15 meanwhile
    clock.advance(181)
    svc.sweep()
    assert len(telegram.sent) == 1 and "Decision D-14 from Codex · answer in MC: approve D-14 / no D-14" in telegram.texts[0]
    by_code = {i["facts"]["decision"]: i for i in items(svc).values()}
    assert by_code["D-15"]["delivery"]["state"] == "by_supervisor"
    assert "Merge PR" not in telegram.texts[0]                # words stay in MC unless D8 is on
    svc.sweep()
    assert len(telegram.sent) == 1
    svc.sender.release()


# ---- T11, T12: what a notice may contain -----------------------------------------------------------------------------

def _all_renderings(svc):
    data = svc.store.read()
    out = []
    for item in data["items"].values():
        out += [words.single(svc.settings, item), words.single(svc.settings, item, reminder=True),
                words.direct(svc.settings, item, recorded=True), words.direct(svc.settings, item, recorded=False)]
    out.append(words.group(svc.settings, list(data["items"].values())))
    return out


@pytest.mark.parametrize("words_on", [False, True])
def test_no_secret_and_no_personal_data_in_any_notice(make_settings, clock, telegram, auth_dir, words_on):
    """T11: every kind, every rendering, with secrets planted in every free field a source has."""
    created = datetime.fromtimestamp(clock(), timezone.utc)
    rows = {"value": [{"id": 14, "requester": {"agent": "codex", "branch": "codex/x", "session": "abcdef123456"},
                       "text": "password: hunter2 and ghp_ABCDEFGHIJKLMNOPQRSTUV card 4242 4242 4242 4242",
                       "state": "new", "created_at": created, "expires_at": created + timedelta(hours=1)}]}
    svc = make(make_settings, clock, telegram, decisions=rows, supervision=True, words_on_telegram=words_on)
    svc.db.add(41, "ann.secret@x.test", name="Ann password=hunter2")
    auth.create_pending("Bo token: abcdef0123456789", "bo.private@x.test", "temp-Pass-123")
    pending_token = auth.load_pending()[0]["token"]
    auth._write_json("guests.json", {"guests": [{"username": "guest_ab12cd34", "display_name": "Cy Hidden",
                                                 "email": "cy@x.test", "expires_at": "2020-01-01T00:00:00+00:00"}]})
    auth.create_reset_request("guest_ab12cd34", "Cy Hidden", "cy@x.test")
    svc.raise_agent("claude-code", {"key": "blocked-on-merge-1", "words":
                    f"Need the {FAKE_TOKEN} rotated; mail ann.secret@x.test; sk-ant-abcdefghijklmnop", "branch": "claude/x"})
    clock.advance(181)
    svc.sweep()
    texts = _all_renderings(svc) + telegram.texts
    assert len(telegram.sent) >= 1
    for text in texts:
        # Nothing left that a scrub would remove (a removed marker after "password:" is not a secret).
        assert credential_scrub.scrub(text)[1] is False and payment_scrub.scrub(text) == text, text
        if not words_on:
            assert credential_scrub.SECRET.search(text) is None, text
        for banned in (FAKE_TOKEN, "hunter2", "ann.secret", "bo.private", "cy@x.test", "Ann", "Cy Hidden",
                       "temp-Pass-123", pending_token, "4242 4242", "ghp_", "sk-ant-", "<b>", "<code>"):
            assert banned not in text, (banned, text)
    for body in telegram.sent:
        assert "parse_mode" not in body
    reset = next(i for i in items(svc).values() if i["kind"] == "password_reset")
    assert "account expired: renew first" in words.single(svc.settings, reset)
    svc.sender.release()


def test_length_bound_keeps_the_header_and_links(make_settings, clock, telegram, auth_dir):
    """T12."""
    svc = make(make_settings, clock, telegram, words_on_telegram=True)
    svc.raise_agent("codex", {"key": "long-words-0001", "words": ("x" * 99 + " ") * 15})
    data = svc.store.read()
    item = next(iter(data["items"].values()))
    item = dict(item, words="y" * 10_000)
    text = words.single(svc.settings, item)
    assert len(text) <= MESSAGE_MAX and "?waiting=W-1" in text and text.splitlines()[0].endswith("W-1")
    assert text.rstrip("”").endswith(words.CUT)
    many = [dict(item, code=f"W-{n}", facts=dict(item["facts"], branch="b" * 120, session="s" * 96))
            for n in range(1, 11)]
    assert len(words.group(svc.settings, many)) <= MESSAGE_MAX
    svc.sender.release()
