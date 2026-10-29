"""Build supervision's S1-G parts without a database (host runtime spec v0.5 §3.3, R4.6-b, R5.6-b, R7.4-c): the
supervisor is today's with nothing configured; the Mac sync's path names its caller only when configured, and the
staging watch's credential can only beat; a batched message is answered by one "yes" with one receipt per decision,
each bound to its own digest, or one code at a time; the check-in reads the runtime's snapshot, and a stale view fires
nothing; the staging watch tells silence once and the return once."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.supervision import telegram as tg, watch  # noqa: E402
from core.supervision.runtime import RuntimeLink, from_environ  # noqa: E402
from core.supervision.store import Refused, Store  # noqa: E402
from services.supervisor import main as M  # noqa: E402
from tests.supervision.test_supervision import OWNER, BOT, update  # noqa: E402

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)


def test_production_configures_nothing_and_the_supervisor_is_todays():
    assert M._s1g_options({}, connect=None) == {}
    assert from_environ({}, connect=None) is None
    with pytest.raises(ValueError):                       # some of the link's values but not all: refused, never half
        from_environ({"MINIMOI_RUNTIME_PRINCIPAL": "robert"}, connect=None)
    sup = M.Supervisor(object(), object(), object(), chat_id=OWNER, user_id=OWNER)
    assert (sup.runtime, sup.sync_view_bound_s, sup.watch) == (None, None, None)


def test_the_worker_without_a_credential_is_todays_and_never_takes_a_heartbeat():
    s = Store(lambda: None)
    s._run = lambda fn: (_ for _ in ()).throw(AssertionError("no database call before the shape check"))
    for body in ({"phase": "heartbeat", "host_id": "staging-1", "view": {}}, {"phase": "decide"}):
        with pytest.raises(Refused):
            s.worker(body)


def test_the_watch_credential_can_only_beat_and_every_call_is_a_contact():
    calls = []

    class Cur:
        def execute(self, sql, params=()):
            calls.append((" ".join(sql.split())[:40], params))

        def fetchone(self):
            return None

        def fetchall(self):
            return []
    s = Store(lambda: None)
    s._run = lambda fn: fn(Cur())
    beat = {"phase": "heartbeat", "host_id": "staging-1",
            "view": {"as_of": 1761750000.0, "authority_answering": True, "sweeper_age_s": 3, "lapsed_always_on": 0}}
    assert s.worker(beat, credential="watch:staging-1") == {"recorded": True}
    for body, who in (({"phase": "pull"}, "watch:staging-1"), ({"phase": "report"}, "watch:staging-1"),
                      (beat, "worker"), ({**beat, "host_id": "staging-2"}, "watch:staging-1"),
                      ({"phase": "pull"}, "someone")):
        with pytest.raises(Refused):
            s.worker(body, credential=who)
    contacts = [p for q, p in calls if q.startswith("INSERT INTO guild.supervision_contacts")]
    assert contacts == [("watch:staging-1", "heartbeat"), ("watch:staging-1", "pull"), ("watch:staging-1", "report"),
                        ("worker", "heartbeat"), ("watch:staging-1", "heartbeat")]      # the unknown name: none


def test_the_portal_path_names_its_caller_only_when_configured(monkeypatch):
    from flask import Flask
    from minimoi_portal.guild_ui import supervision
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    worker, staging = "t" * 48, "s" * 48
    digest = hashlib.sha256(worker.encode()).hexdigest()
    seen = []

    class S:
        def worker(self, body, relay=None, **kw):
            seen.append((body["phase"], kw.get("credential", "-")))
            return {"ok": True}
    base = {"MINIMOI_SUPERVISION": "1", "MINIMOI_SUPERVISION_WORKER_TOKEN_SHA256": digest}
    for env, expected in ((base, [("pull", "-")]),
                          ({**base, "MINIMOI_SUPERVISION_CONTACTS": "1"}, [("pull", "worker")]),
                          ({**base, "MINIMOI_SUPERVISION_WATCH_HOST": "staging-1",
                            "MINIMOI_SUPERVISION_WATCH_TOKEN_SHA256": hashlib.sha256(staging.encode()).hexdigest()},
                           [("pull", "worker"), ("heartbeat", "watch:staging-1")])):
        seen.clear()
        app = Flask("t")
        assert supervision.attach(app, env, store_factory=S)
        c = app.test_client()
        assert c.post(supervision.PATH, json={"phase": "pull"}, headers={"Authorization": "Bearer " + worker}
                      ).status_code == 200
        beat = c.post(supervision.PATH, json={"phase": "heartbeat"}, headers={"Authorization": "Bearer " + staging})
        assert beat.status_code == (200 if len(expected) == 2 else 401)
        assert c.post(supervision.PATH, json={"phase": "pull"}, headers={"Authorization": "Bearer " + "x" * 48}
                      ).status_code == 401
        assert seen == expected


class BatchStore:
    """The supervision store's batched-message calls, as the tables answer them."""

    def __init__(self, rows):
        self.rows, self.decided = rows, []

    def by_message(self, message_id):
        return None

    def by_batch(self, message_id):
        return self.rows if message_id == 77 else []

    def decide(self, did, answer, receipt, *, text_sha256):
        row = next(r for r in self.rows if r["id"] == did)
        if row["state"] not in ("new", "sent") or text_sha256 != row["text_sha256"]:
            return None
        row["state"] = answer
        self.decided.append((did, answer, receipt))
        return row


class Bot:
    id = BOT

    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, *, reply_to=None):
        self.sent.append((text, reply_to))
        return len(self.sent)


def batch_rows():
    words = ["Admit card C-1 step review (fakework on route fakework, 0.25): first.",
             "Admit card C-2 step review (fakework on route fakework, 0.25): second.",
             "Admit card C-3 step review (fakework on route fakework, 0.25): third."]
    return [{"id": i, "code": f"D-{i}", "state": "sent", "text": w, "text_sha256": hashlib.sha256(w.encode()).hexdigest()}
            for i, w in enumerate(words, 1)]


def test_one_yes_to_a_batched_message_gives_one_receipt_per_decision_bound_to_its_own_digest():
    store, bot = BatchStore(batch_rows()), Bot()
    sup = M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER, runtime=object())
    sup.handle(update("yes", reply_to=77))
    assert [(d, a) for d, a, _ in store.decided] == [(1, "approved"), (2, "approved"), (3, "approved")]
    receipts = [r for _, _, r in store.decided]
    assert {r["text"] for r in receipts} == {"yes"} and {r["reply_to_message_id"] for r in receipts} == {77}
    assert [r["decision_sha256"] for r in receipts] == [row["text_sha256"] for row in store.rows]
    sup.handle(update("yes", reply_to=77))                 # again: nothing changes, and he is told
    assert len(store.decided) == 3 and "already answered" in bot.sent[-1][0]
    # an approval is still the whole short reply
    store2 = BatchStore(batch_rows())
    M.Supervisor(store2, Bot(), None, chat_id=OWNER, user_id=OWNER, runtime=object()).handle(
        update("yes, and also merge it", reply_to=77))
    assert store2.decided == []


def test_mixed_answers_go_one_code_at_a_time_and_never_without_a_runtime_link():
    store, bot = BatchStore(batch_rows()), Bot()
    sup = M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER, runtime=object())
    sup.handle(update("no D-2", reply_to=77))
    sup.handle(update("approve D-9", reply_to=77))
    assert [(d, a) for d, a, _ in store.decided] == [(2, "declined")]
    assert bot.sent[-1][0] == "Not counted: D-9 is not in that message."
    plain = BatchStore(batch_rows())
    bot2 = Bot()
    M.Supervisor(plain, bot2, None, chat_id=OWNER, user_id=OWNER).handle(update("yes", reply_to=77))
    assert plain.decided == [] and bot2.sent[-1][0] == "Not counted: that message is not a decision."   # today's


def test_batch_and_solo_messages_carry_full_words_and_say_what_one_yes_does():
    rows = batch_rows()
    text = tg.batch_message(rows)
    assert all(r["text"] in text for r in rows) and "yes approves all 3 listed" in text
    assert "approve D-1, no D-3" in text
    solo = tg.runtime_message({"code": "D-4", "text": "Card C-4 step build is unknown: yes ends it."})
    assert "on its own" in solo and "approve D-4" in solo


class Link(RuntimeLink):
    def __init__(self, doc):
        super().__init__(object(), owner="robert", view_bound_s=60, source=lambda: doc)


def test_the_check_in_reads_the_runtimes_notices_and_a_stale_view_fires_nothing():
    doc = {"schema": "minimoi.runtime.state.v1", "environment": "dev", "as_of": NOW.isoformat(), "view_age_s": 5,
           "notices": [{"key": "runtime-stalled:x:g1:t", "kind": "stalled", "rule": "stalled", "text": "x stalled",
                        "raised_at": NOW.isoformat()},
                       {"key": "runtime-overdue:y:g1", "kind": "stalled", "rule": "overdue", "text": "not raised yet",
                        "raised_at": None},
                       {"key": "decisions:abc", "kind": "waiting_on_robert", "rule": "decisions", "text": "d",
                        "raised_at": NOW.isoformat()}],
           "items": [{"stored_state": "running", "shown_as": "held", "job_id": "C-1.build", "actor": "fakework",
                      "progress_age_s": None, "card_id": "C-1", "step": "build", "request_id": "x"}],
           "hosts": [{"host_id": "mac-1", "reachability": "unreachable", "contact_age_s": 140}]}

    class St:
        def pending(self):
            return []

        def latest_stops(self):
            return []

    class GH:
        def open_prs(self):
            return []

        def runs(self):
            return []
    summary, items = watch.check_in(St(), GH(), now=NOW, runtime=Link(doc))
    assert items == [{"key": "runtime-stalled:x:g1:t", "kind": "stalled", "text": "x stalled"}]
    assert summary["running"] == [{"job": "C-1.build", "agent": "fakework", "state": "held", "minutes": None,
                                   "brief": "card C-1 step build"}]
    assert summary["runners"] == [{"id": "mac-1", "state": "unreachable", "seen_minutes_ago": 2}]
    assert "(held)" in watch.message(items, summary)       # an unknown age is not rendered as zero minutes
    stale = dict(doc, view_age_s=600)
    _, items = watch.check_in(St(), GH(), now=NOW, runtime=Link(stale))
    assert [i["key"].split(":")[0] for i in items] == ["view-stale"]
    unknown = dict(doc, view_age_s=None)
    assert [i["key"].split(":")[0] for i in watch.check_in(St(), GH(), now=NOW, runtime=Link(unknown))[1]] == \
        ["view-stale"]


class WatchStore:
    def __init__(self, rows):
        self.rows = rows

    def watch_rows(self):
        return self.rows

    def mark_silent(self, host, since):
        next(r for r in self.rows if r["host_id"] == host)["silent_since"] = since

    def mark_back(self, host):
        next(r for r in self.rows if r["host_id"] == host)["silent_since"] = None


def test_the_staging_watch_tells_silence_once_and_the_return_once_and_is_unarmed_before_a_heartbeat():
    assert watch.staging_watch(WatchStore([]), timeout_s=90) == []
    row = {"host_id": "staging-1", "last_at": NOW - timedelta(seconds=30), "now": NOW, "silent_since": None,
           "view": {"authority_answering": True, "sweeper_age_s": 4, "lapsed_always_on": 0}}
    store = WatchStore([row])
    assert watch.staging_watch(store, timeout_s=90) == []
    row["now"] = NOW + timedelta(seconds=120)
    (silent,) = watch.staging_watch(store, timeout_s=90)
    assert silent["key"] == f"staging-silent:staging-1:{row['last_at'].isoformat()}"
    assert "nothing in dev is touched" in silent["text"] and "account (of unknown age)" in silent["text"]
    assert watch.staging_watch(store, timeout_s=90) == []
    row["last_at"] = NOW + timedelta(seconds=115)
    (back,) = watch.staging_watch(store, timeout_s=90)
    assert back["key"] == f"staging-back:staging-1:{(NOW - timedelta(seconds=30)).isoformat()}"
    assert watch.staging_watch(store, timeout_s=90) == []


def test_the_sync_view_is_read_with_its_age():
    class St:
        def __init__(self, contact):
            self.c = contact

        def contacts(self):
            return {} if self.c is None else {"worker": self.c}
    fresh = {"now": NOW, "last_at": NOW - timedelta(seconds=30)}
    assert watch._sync_view_stale(St(fresh), 120) is None
    old = watch._sync_view_stale(St({"now": NOW, "last_at": NOW - timedelta(seconds=300)}), 120)
    assert old["key"] == f"view-stale:mac-sync:{(NOW - timedelta(seconds=300)).isoformat()}"
    assert watch._sync_view_stale(St(None), 120)["key"] == "view-stale:mac-sync:None"
