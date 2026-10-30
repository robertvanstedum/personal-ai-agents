"""Fakes and fixtures for the action-waiting notices tests: a fake Telegram opener, a fake guild table, a clock.

No network, provider, Telegram or Keychain call is made anywhere in these tests. There is deliberately no conftest.py
here (tests/cos does ``from conftest import …``; a second module named conftest could shadow it): test modules import
the fixtures from this module, as tests/guild/shop_floor/floor_helpers.py does.
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone

import pytest

from minimoi_portal import auth
from minimoi_portal.action_waiting.settings import Settings


FAKE_TOKEN = "7000000001:" + "Ab3_" * 8 + "xyz"           # shape only; never a real bot
CHAT = 8379221702
BASE = "https://dev.minimoi.test"


class FakeTelegram:
    """Stands in for urllib's opener inside core.supervision.telegram.Bot. Records every request body; can fail."""

    def __init__(self):
        self.sent = []
        self.urls = []
        self.fail = None                                   # an exception instance to raise, or None
        self.next_id = 500

    def __call__(self, req, timeout=None):
        self.urls.append(req.full_url)
        if self.fail is not None:
            raise self.fail
        body = json.loads(req.data.decode())
        self.sent.append(body)
        self.next_id += 1
        return io.BytesIO(json.dumps({"ok": True, "result": {"message_id": self.next_id}}).encode())

    @property
    def texts(self):
        return [b["text"] for b in self.sent]


class FakeGuildDB:
    """guild.guest_requests rows for open_with_coverage's select."""

    def __init__(self, rows=()):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.fail = False

    def add(self, i, email, *, status="requested", domain="portuguese", day=29, hour=14, reason="", name="Ann Guest"):
        self.rows[i] = {"id": i, "name": name, "email": email, "domain": domain, "status": status, "reason": reason,
                        "requested_at": datetime(2026, 10, day, hour, 2, tzinfo=timezone.utc)}

    def query(self, sql, params=()):
        if self.fail:
            raise RuntimeError("db down")
        assert "status='requested'" in sql and "reason" in sql, sql
        return sorted((dict(r) for r in self.rows.values() if r["status"] == "requested"),
                      key=lambda r: r["requested_at"])


class Clock:
    def __init__(self, t=1_761_750_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def auth_dir(tmp_path, monkeypatch):
    d = tmp_path / "auth"
    d.mkdir()
    (d / "pending.json").write_text(json.dumps({"pending": []}))
    monkeypatch.setattr(auth, "AUTH_DIR", d)
    return d


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "secrets" / "bot-token"
    p.parent.mkdir()
    p.write_text(FAKE_TOKEN + "\n")
    os.chmod(p, 0o600)
    return p


@pytest.fixture
def make_settings(tmp_path, token_file):
    def make(**over):
        values = dict(mode="on", env="dev", root=tmp_path / "notices", token_file=str(token_file), chat_id=CHAT,
                      owner="robert", base_url=BASE, sweep_seconds=60, remind_hours=24.0)
        values.update(over)
        return Settings(**values)
    return make


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def telegram():
    return FakeTelegram()


@pytest.fixture
def legacy():
    """The legacy bot for the safe direct notice, faked."""
    return FakeTelegram()


def build_service(make_settings, clock, telegram, legacy=None, db=None, decisions=None, decision_state=None, **over):
    """A Service on a temp folder with fake Telegram, a fake guild table and fake decision rows."""
    from core.supervision.telegram import Bot
    from minimoi_portal.action_waiting import sources as src, store as st
    from minimoi_portal.action_waiting.service import Service
    settings = make_settings(**over)
    store = st.Store(settings.root, clock=clock)
    db = db if db is not None else FakeGuildDB()
    rows = decisions if decisions is not None else {"value": []}
    sources = src.Sources(settings, store.digest, guild_db=db, decision_rows=lambda: rows["value"],
                          decision_state=decision_state or (lambda did: "new"), clock=clock)
    legacy = legacy or FakeTelegram()
    svc = Service(settings, store=store, sources=sources, opener=telegram, clock=clock,
                  legacy_bot=lambda: Bot(FAKE_TOKEN, opener=legacy), source_timeout=5)
    svc.db, svc.rows, svc.legacy = db, rows, legacy
    return svc
