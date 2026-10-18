"""B3 corrections to the scheduled delivery ledger (#305).

One delivery per day at a time (nonblocking exclusive lock across admission,
sends and the final record); the ledger directory and its parent are durable
before any request; a 5xx is uncertain, never "definitely not sent"; redirects
are never followed; only an exactly valid record for today proves delivery.
Fake Telegram only: no provider, network or real send.
"""

import fcntl
import json
import os
import threading
from datetime import datetime, timezone

import pytest

from core.telegram import delivery_ledger as dl

TODAY = datetime.now(timezone.utc).date().isoformat()


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


OK = Reply(200, {"ok": True})


def _briefing_text():
    from domains.curator.curator_rss_v2 import format_output
    return format_output([{"hash_id": "h1", "title": "Synthetic 1", "source": "Example", "link": "https://example.com/1",
                           "summary": "s", "category": "other", "score": 9.0, "final_score": 9.0,
                           "method": "synthetic", "published": datetime(2026, 10, 16, 12, tzinfo=timezone.utc)}])


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    from core.telegram import telegram_bot as tb
    out = tmp_path / "curator_output.txt"
    out.write_text(_briefing_text())
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(out))
    monkeypatch.setattr(tb, "DELIVERY_DIR", tmp_path / "data" / "curator" / "telegram_delivery")
    (tmp_path / "data" / "curator").mkdir(parents=True)
    tb.events = []

    def script(*replies, gate=None, posting=None):
        queue = list(replies)

        def post(url, json=None, timeout=None, **kwargs):
            tb.events.append(("post", kwargs))
            if posting is not None:
                posting.set()
            if gate is not None:
                gate.wait(10)
            reply = queue.pop(0) if queue else OK
            if isinstance(reply, Exception):
                raise reply
            return reply
        monkeypatch.setattr(tb.requests, "post", post)
    script()
    tb.script = script
    return tb


def _record(tb):
    return json.loads((tb.DELIVERY_DIR / f"{TODAY}.json").read_text())


def _posts(tb):
    return [e for e in tb.events if e[0] == "post"]


# (1) one delivery per day at a time

def test_a_concurrent_second_delivery_sends_nothing(bot):
    gate, posting, results = threading.Event(), threading.Event(), {}
    bot.script(gate=gate, posting=posting)
    first = threading.Thread(target=lambda: results.setdefault("first", bot.deliver_briefing("t", "c")), daemon=True)
    first.start()
    try:
        # Bounded wait: if the first worker fails before posting, this fails instead of hanging.
        assert posting.wait(10), "first delivery never reached its first post"
        assert bot.deliver_briefing("t", "c") == 4   # busy: no admission, no send
        assert len(_posts(bot)) == 1
    finally:
        gate.set()
        first.join(10)
    assert not first.is_alive()
    assert results["first"] == 0 and _record(bot)["state"] == "acknowledged"
    assert len(_posts(bot)) == _record(bot)["planned"]   # every message sent exactly once


def test_an_externally_held_lock_refuses_without_touching_the_ledger(bot):
    bot.DELIVERY_DIR.mkdir(parents=True)
    fd = os.open(bot.DELIVERY_DIR / f"{TODAY}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert bot.deliver_briefing("t", "c") == 4
        assert _posts(bot) == [] and not (bot.DELIVERY_DIR / f"{TODAY}.json").exists()
    finally:
        os.close(fd)
    assert (bot.DELIVERY_DIR / f"{TODAY}.lock").exists()     # never removed


# (2) durable directory before any request

def test_directory_and_parent_are_fsynced_before_the_first_request(bot, monkeypatch):
    real = bot._fsync_dir
    monkeypatch.setattr(bot, "_fsync_dir", lambda p: (bot.events.append(("fsync", str(p))), real(p)))
    assert not bot.DELIVERY_DIR.exists()
    assert bot.deliver_briefing("t", "c") == 0
    first_post = next(i for i, e in enumerate(bot.events) if e[0] == "post")
    synced_before = {e[1] for e in bot.events[:first_post] if e[0] == "fsync"}
    assert {str(bot.DELIVERY_DIR), str(bot.DELIVERY_DIR.parent)} <= synced_before


def test_a_directory_that_cannot_be_made_durable_sends_nothing(bot, monkeypatch):
    def broken():
        raise OSError("read-only file system")
    monkeypatch.setattr(bot, "_durable_delivery_dir", broken)
    assert bot.deliver_briefing("t", "c") == 1 and _posts(bot) == []


# (3) 5xx is uncertain; only an explicit 4xx is definite; no redirects

@pytest.mark.parametrize("status", [500, 502, 503])
def test_first_message_5xx_is_held_as_uncertain(bot, status):
    bot.script(Reply(status, {"ok": False}))
    assert bot.deliver_briefing("t", "c") == 3
    assert _record(bot)["state"] == "uncertain" and _record(bot)["reason"] == f"http_{status}"
    assert bot.deliver_briefing("t", "c") == 3 and len(_posts(bot)) == 1    # held, never resent


@pytest.mark.parametrize("status", [400, 403, 429])
def test_first_message_explicit_4xx_is_definitely_not_sent(bot, status):
    bot.script(Reply(status, {"ok": False}))
    assert bot.deliver_briefing("t", "c") == 1
    assert _record(bot)["state"] == "not_sent" and _record(bot)["http_status"] == status


def test_requests_never_follow_redirects(bot):
    bot.script(Reply(302, {"ok": True}))
    assert bot.deliver_briefing("t", "c") == 3 and _record(bot)["state"] == "uncertain"
    assert all(kwargs.get("allow_redirects") is False for _e, kwargs in _posts(bot))


# (4) only an exactly valid record for today proves delivery

def _valid_ack():
    return {"format": dl.FORMAT, "date": TODAY, "state": "acknowledged", "started_at": "2026-10-16T13:00:00+00:00",
            "finished_at": "2026-10-16T13:00:05+00:00", "planned": 3, "accepted": 3, "briefing_sha256": "a" * 64}


@pytest.mark.parametrize("forged", [
    {"state": "acknowledged"},
    {**_valid_ack(), "date": "2026-10-15"},
    {**_valid_ack(), "format": "other/1"},
    {**_valid_ack(), "accepted": 2},
    {**_valid_ack(), "planned": True, "accepted": True},
    {**_valid_ack(), "briefing_sha256": "nothex"},
    {**_valid_ack(), "unexpected": 1},
    {**_valid_ack(), "finished_at": "yesterday"},
    {k: v for k, v in _valid_ack().items() if k != "started_at"},
    [],
])
def test_a_malformed_or_wrong_day_record_holds_and_never_proves_delivery(bot, forged, monkeypatch, tmp_path):
    bot.DELIVERY_DIR.mkdir(parents=True)
    path = bot.DELIVERY_DIR / f"{TODAY}.json"
    path.write_text(json.dumps(forged))
    assert dl.classify(forged, TODAY) == "invalid"
    assert bot.deliver_briefing("t", "c") == 3 and _posts(bot) == []       # bot: hold, nothing sent
    monkeypatch.setenv("CURATOR_DELIVERY_DIR", str(bot.DELIVERY_DIR))
    monkeypatch.chdir(tmp_path)
    assert dl.cron_state(TODAY) == "hold"                                  # wrapper: hold, not delivered


def test_a_valid_acknowledgement_is_the_only_proof(bot, monkeypatch, tmp_path):
    bot.DELIVERY_DIR.mkdir(parents=True)
    (bot.DELIVERY_DIR / f"{TODAY}.json").write_text(json.dumps(_valid_ack()))
    assert bot.deliver_briefing("t", "c") == 0 and _posts(bot) == []
    monkeypatch.setenv("CURATOR_DELIVERY_DIR", str(bot.DELIVERY_DIR))
    monkeypatch.chdir(tmp_path)
    assert dl.cron_state(TODAY) == "delivered"


@pytest.mark.parametrize("replies,expected", [
    ((), "acknowledged"),
    ((Reply(401, {"ok": False}),), "not_sent"),
    ((OK, Reply(403, {"ok": False})), "partial"),
    ((OK, Reply(503, {"ok": False})), "uncertain"),
])
def test_every_record_the_bot_writes_passes_the_shared_rules(bot, replies, expected):
    bot.script(*replies)
    bot.deliver_briefing("t", "c")
    assert dl.classify(_record(bot), TODAY) == expected
