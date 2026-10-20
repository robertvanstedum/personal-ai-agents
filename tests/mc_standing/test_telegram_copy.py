"""Optional Telegram copy of the brief (standing §7.1–§7.2): counts only, sending ledger, no
automatic resend of an uncertain send, never affects the site brief."""
import io
import json
import socket
import urllib.error
from datetime import timedelta
from types import SimpleNamespace

import pytest

from core.mc_standing import runner, telegram_copy as tc
from core.mc_standing.spool import Spool
from tests.mc_standing.test_learning_view import PRINCIPAL, snap
from tests.mc_standing.test_spool_findings_runner import NOW, FakeIngress, _status, run

KEY = "prod.2026-10-21.p0"


class Sender:
    def __init__(self, outcome="ok"):
        self.outcome, self.sent = outcome, []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        if self.outcome == "rejected":
            raise tc.NotSent("rejected_403")
        if self.outcome == "uncertain":
            raise tc.SendUncertain()
        if self.outcome == "bad_receipt":
            return {"message_id": None}
        return {"message_id": 4242}


def deliver(spool, sender, text="hello", allowed=True, **kw):
    return tc.deliver(spool=spool, cycle_key=KEY, text=text, sender=sender, destination_id="chat-owner",
                      kill=kw.get("kill", lambda: False), paused=kw.get("paused", lambda: False),
                      send_allowed=lambda: allowed)


def ledger(spool):
    return spool.read("outbox", f"{KEY}.telegram")


def obs(state, label="w"):
    """compose() reads only the state; the label proves no watch identity leaks into the copy."""
    return SimpleNamespace(state=state, watch_id=label)


# ── compose ─────────────────────────────────────────────────────────────────

def test_compose_is_counts_only_with_the_protected_link():
    text = tc.compose([obs("healthy", "a"), obs("failed", "guest-summary-robert"), obs("unknown", "c")],
                      environment="prod", local_date="2026-10-21",
                      learning={"status": "ok", "coverage": "complete", "adopted_window_days": 7,
                                "counts": {"pending_suggestions": 2, "adopted_in_window": 0,
                                           "repeated_corrections_confirmed": 1,
                                           "repeated_corrections_provisional": 0}},
                      details_link="https://minimoi.ai/app/guild/briefs/x")
    assert text.splitlines() == [
        "MC operations brief, production, 2026-10-21: 1 needs attention, 1 could not be checked, 1 healthy.",
        "Learning: 2 learning suggestions pending · 1 repeated correction.",
        "Brief: https://minimoi.ai/app/guild/briefs/x"]
    assert "guest" not in text and "robert" not in text


def test_compose_quiet_day():
    assert tc.compose([obs("healthy", "a"), obs("healthy", "b")], environment="dev", local_date="2026-10-21") == \
        "MC operations brief, dev, 2026-10-21: all 2 checks healthy."


# ── ledger ──────────────────────────────────────────────────────────────────

def test_acknowledged_once_and_never_resent(tmp_path):
    spool, s = Spool(tmp_path), Sender()
    assert deliver(spool, s) == {"telegram": "acknowledged", "reason": None}
    assert ledger(spool)["message_id"] == 4242 and ledger(spool)["state"] == "acknowledged"
    assert deliver(spool, s)["telegram"] == "acknowledged" and len(s.sent) == 1


@pytest.mark.parametrize("outcome,state", [("uncertain", "uncertain"), ("bad_receipt", "uncertain"),
                                           ("rejected", "not_sent")])
def test_uncertain_and_not_sent_are_final_for_the_cycle(tmp_path, outcome, state):
    spool, s = Spool(tmp_path), Sender(outcome)
    assert deliver(spool, s)["telegram"] == state
    s.outcome = "ok"
    assert deliver(spool, s)["telegram"] == state and len(s.sent) == 1


def test_crash_after_dispatch_is_uncertain_not_resent(tmp_path):
    spool = Spool(tmp_path)

    class Crash(BaseException):
        pass

    class Crashing(Sender):
        def send(self, chat_id, text):
            self.sent.append(text)
            raise Crash()
    with pytest.raises(Crash):
        deliver(spool, Crashing())
    assert ledger(spool)["state"] == "dispatched"
    s = Sender()
    assert deliver(spool, s) == {"telegram": "uncertain", "reason": "interrupted_after_dispatch"}
    assert s.sent == []


def test_paused_or_killed_before_send_keeps_it_prepared_then_sends_the_same_message(tmp_path):
    spool, s = Spool(tmp_path), Sender()
    assert deliver(spool, s, text="first", paused=lambda: True)["reason"] == "paused_before_send"
    assert deliver(spool, s, text="first", kill=lambda: True)["reason"] == "killed_before_send"
    assert s.sent == [] and ledger(spool)["state"] == "prepared"
    deliver(spool, s, text="changed later")
    assert s.sent == [("chat-owner", "first")]              # the prepared message, unchanged


def test_closed_window_is_not_sent(tmp_path):
    spool, s = Spool(tmp_path), Sender()
    assert deliver(spool, s, allowed=False) == {"telegram": "not_sent", "reason": "send_window_closed"}
    assert s.sent == []


# ── sender classification ──────────────────────────────────────────────────

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))


@pytest.mark.parametrize("raised,expected", [
    (_http_error(403), tc.NotSent), (_http_error(400), tc.NotSent),
    (_http_error(429), tc.SendUncertain), (_http_error(502), tc.SendUncertain),
    (urllib.error.URLError(ConnectionRefusedError()), tc.NotSent),
    (urllib.error.URLError(socket.gaierror()), tc.NotSent),
    (urllib.error.URLError(TimeoutError()), tc.SendUncertain), (TimeoutError(), tc.SendUncertain)])
def test_sender_classifies_outcomes_conservatively(raised, expected):
    def opener(req, timeout):
        raise raised
    with pytest.raises(expected):
        tc.TelegramBotSender(lambda: "T" * 40, opener=opener).send("chat", "x")


def test_sender_success_and_malformed_success():
    seen = {}

    def ok(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return FakeResp(json.dumps({"ok": True, "result": {"message_id": 7}}).encode())
    assert tc.TelegramBotSender(lambda: "T" * 40, opener=ok).send("chat", "x") == {"message_id": 7}
    assert seen["body"] == {"chat_id": "chat", "text": "x", "disable_web_page_preview": True}
    with pytest.raises(tc.SendUncertain):
        tc.TelegramBotSender(lambda: "T" * 40, opener=lambda r, timeout: FakeResp(b'{"ok": false}')).send("c", "x")


# ── cycle integration ──────────────────────────────────────────────────────

def test_copy_follows_verified_site_delivery_and_names_each_channel(tmp_path):
    _status(tmp_path / "src")
    s = Sender()
    spool, res = run(tmp_path, FakeIngress(), learning_source=lambda: snap(), learning_principal=PRINCIPAL,
                     details_link="https://minimoi.ai/b", telegram={"sender": s, "destination_id": "chat-owner"})
    assert res["state"] == "delivered" and res["channels"] == {"site": "delivered", "telegram": "acknowledged"}
    text = s.sent[0][1]
    assert "Learning: 2 learning suggestions pending" in text and "website" not in text
    assert text.endswith("Brief: https://minimoi.ai/b")
    _, again = run(tmp_path, FakeIngress(), spool=spool, telegram={"sender": s, "destination_id": "chat-owner"})
    assert again["channels"]["telegram"] == "acknowledged" and len(s.sent) == 1


def test_no_copy_until_the_site_brief_is_delivered(tmp_path):
    _status(tmp_path / "src")
    s = Sender()
    spool, res = run(tmp_path, FakeIngress(mode="defer"), telegram={"sender": s, "destination_id": "c"})
    assert res["channels"] == {"site": "delivery_pending", "telegram": "waiting_for_site"} and s.sent == []


def test_a_failed_copy_never_changes_the_site_result(tmp_path):
    _status(tmp_path / "src")

    class Broken:
        def send(self, chat_id, text):
            raise RuntimeError("boom")
    spool, res = run(tmp_path, FakeIngress(), telegram={"sender": Broken(), "destination_id": "c"})
    assert res["state"] == "delivered" and res["channels"] == {"site": "delivered", "telegram": "uncertain"}
    assert spool.read("cycles", res["cycle_key"])["state"] == "delivered"


def test_without_telegram_configured_nothing_changes(tmp_path):
    _status(tmp_path / "src")
    _, res = run(tmp_path, FakeIngress())
    assert "channels" not in res
