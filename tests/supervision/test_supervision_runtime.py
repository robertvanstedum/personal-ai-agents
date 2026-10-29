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
    """The supervision store's runtime-message calls, as the tables answer them: a batched message (77) carrying
    D-1…D-3, and D-4 sent alone (message 88)."""

    def __init__(self, rows, solo=None):
        self.rows, self.solo, self.decided = rows, solo, []

    def by_message(self, message_id):
        return self.solo if (self.solo is not None and message_id == 88) else None

    def by_batch(self, message_id):
        return self.rows if message_id == 77 else []

    def decide(self, did, answer, receipt, *, text_sha256):
        row = next(r for r in self.rows + ([self.solo] if self.solo else []) if r["id"] == did)
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


def row(i, words, *, solo=False, state="sent"):
    return {"id": i, "code": f"D-{i}", "state": state, "text": words, "text_sha256": hashlib.sha256(words.encode()).hexdigest(),
            "requester": {"agent": "runtime", "decision_id": f"0000000{i}-0000-4000-8000-000000000000", "solo": solo}}


def batch_rows():
    return [row(i, f"Admit card C-{i} step review (fakework on route fakework, 0.25): the card's own approval no longer "
                   "admits it. Yes admits it; no ends the card.") for i in (1, 2, 3)]


def solo_row():
    """As Store.by_message returns it: the table's row, with no ``code`` (the batched rows' by_batch adds one)."""
    out = row(4, "Card C-4 step build is unknown: yes ends it; no leaves it waiting.", solo=True)
    del out["code"]
    return out


def sup_for(store, bot, runtime=object()):
    return M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER, runtime=runtime)


def test_one_yes_to_a_batched_message_gives_one_receipt_per_decision_bound_to_its_own_digest():
    store, bot = BatchStore(batch_rows()), Bot()
    sup = sup_for(store, bot)
    sup.handle(update("yes", reply_to=77))
    assert [(d, a) for d, a, _ in store.decided] == [(1, "approved"), (2, "approved"), (3, "approved")]
    receipts = [r for _, _, r in store.decided]
    assert {r["text"] for r in receipts} == {"yes"} and {r["reply_to_message_id"] for r in receipts} == {77}
    assert [r["decision_sha256"] for r in receipts] == [r["text_sha256"] for r in store.rows]
    sup.handle(update("yes", reply_to=77))                 # again: nothing changes, and he is told
    assert len(store.decided) == 3 and bot.sent[-1][0] == "D-3 is no longer open (already approved); nothing changed."
    store2 = BatchStore(batch_rows())                      # an approval is still the whole short reply
    sup_for(store2, Bot()).handle(update("yes, and also merge it", reply_to=77))
    assert store2.decided == []


@pytest.mark.parametrize("words", ["wait", "hold", "not now", "stop", "hold on, thinking", "don't"])
@pytest.mark.parametrize("where", ["batched", "solo"])
def test_sf1_a_holding_reply_declines_nothing_and_says_so(words, where):
    store, bot = BatchStore(batch_rows(), solo_row()), Bot()
    sup_for(store, bot).handle(update(words, reply_to=77 if where == "batched" else 88))
    assert store.decided == []
    assert bot.sent[-1][0].startswith("Nothing changed: ") and "still wait" in bot.sent[-1][0]


@pytest.mark.parametrize("words", ["no, only D-2", "no D-2 but yes to the others", "no thanks, not that one",
                                   "nope, later maybe", "maybe", "yes but only D-1"])
@pytest.mark.parametrize("where", ["batched", "solo"])
def test_sf1_a_partial_or_mixed_reply_is_not_counted_and_names_the_per_code_form(words, where):
    store, bot = BatchStore(batch_rows(), solo_row()), Bot()
    sup_for(store, bot).handle(update(words, reply_to=77 if where == "batched" else 88))
    assert store.decided == []
    assert bot.sent[-1][0].startswith("Not counted for ") and "one code at a time (approve D-" in bot.sent[-1][0]


@pytest.mark.parametrize("where", ["batched", "solo"])
def test_sf1_only_the_whole_short_no_declines(where):
    store, bot = BatchStore(batch_rows(), solo_row()), Bot()
    sup_for(store, bot).handle(update("No.", reply_to=77 if where == "batched" else 88))
    expected = [(1, "declined"), (2, "declined"), (3, "declined")] if where == "batched" else [(4, "declined")]
    assert [(d, a) for d, a, _ in store.decided] == expected


def test_mixed_answers_go_one_code_at_a_time_and_never_without_a_runtime_link():
    store, bot = BatchStore(batch_rows()), Bot()
    sup = sup_for(store, bot)
    sup.handle(update("no D-2", reply_to=77))
    sup.handle(update("hold D-3", reply_to=77))           # "hold" one code: nothing changes
    sup.handle(update("approve D-9", reply_to=77))
    assert [(d, a) for d, a, _ in store.decided] == [(2, "declined")]
    assert bot.sent[-1][0] == "Not counted: D-9 is not in that message."
    plain = BatchStore(batch_rows())
    bot2 = Bot()
    M.Supervisor(plain, bot2, None, chat_id=OWNER, user_id=OWNER).handle(update("yes", reply_to=77))
    assert plain.decided == [] and bot2.sent[-1][0] == "Not counted: that message is not a decision."   # today's


def test_n2_a_solo_decision_is_never_answered_by_a_batched_reply():
    rows = batch_rows()[:2] + [row(5, "An unknown resolution that must arrive alone: yes ends it.", solo=True)]
    store, bot = BatchStore(rows), Bot()
    sup_for(store, bot).handle(update("yes", reply_to=77))
    assert [d for d, _, _ in store.decided] == [1, 2]
    sup_for(store, bot).handle(update("approve D-5", reply_to=77))
    assert bot.sent[-1][0] == "Not counted: D-5 arrives on its own; answer it in its own message."


def test_todays_agent_asks_keep_todays_grammar():
    """Not a runtime row: "wait" still declines an agent's ask, word for word as today (telegram.parse_answer)."""
    ask = {"id": 9, "state": "sent", "text": "Merge PR #1?", "text_sha256": "a" * 64,
           "requester": {"agent": "claude-code", "session": "s"}}

    class S(BatchStore):
        def by_message(self, message_id):
            return ask if message_id == 99 else None

        def decide(self, did, answer, receipt, *, text_sha256):
            self.decided.append((did, answer))
            return ask
    store = S([])
    M.Supervisor(store, Bot(), None, chat_id=OWNER, user_id=OWNER).handle(update("wait", reply_to=99))
    assert store.decided == [(9, "declined")]


def test_batch_and_solo_messages_carry_full_words_and_say_what_one_yes_does():
    rows = batch_rows()
    text = tg.batch_message(rows)
    assert all(r["text"] in text for r in rows) and "just “yes” approves all 3 listed" in text
    assert "“wait” changes nothing" in text and "approve D-1, no D-3" in text
    solo = tg.runtime_message({"code": "D-4", "text": "Card C-4 step build is unknown: yes ends it."})
    assert "on its own" in solo and "approve D-4" in solo and "“wait” changes nothing" in solo


def test_sf2_robert_is_told_what_his_answer_did():
    from core.supervision.runtime import outcome_text, closed_reason
    r = {"code": "D-7", "state": "approved"}
    assert outcome_text(r, {"state": "resolved", "action": "admit"}).startswith("Applied: D-7 approved: the step is")
    assert "already moved on (cancelled)" in outcome_text(r, {"state": "moot", "item_state": "cancelled"})
    assert "generation 3" in outcome_text(r, {"state": "stale", "generation_now": 3, "fresh_decision_id": "x"})
    assert "already closed D-7 (withdrawn)" in outcome_text(r, {"state": "already_resolved", "answer": "withdrawn"})
    assert outcome_text(r, {"state": "refused", "code": "grant_capability_unavailable", "reissued": "D-9"}) == \
        "Not applied: D-7 was refused (grant_capability_unavailable); nothing was done. It still waits, as D-9."
    assert closed_reason({"answer": "withdrawn", "code": "grant_revoked"}) == "the runtime withdrew it (grant_revoked)"
    assert closed_reason({"answer": "superseded"}) == "a newer question replaced it"


class FailingLink:
    """A runtime link whose every step fails, as a missing table or a database error would make it."""

    def __init__(self):
        self.errors = []

    def take_errors(self):
        out, self.errors = self.errors, []
        return out

    def _boom(self, *a, **k):
        raise RuntimeError("boom")

    apply = close_closed = deliver = drain = check_in = _boom


class TickStore:
    def __init__(self):
        self.alerts, self.checkins = set(), []

    def pending(self):
        return []

    def unannounced(self):
        return []

    def offset(self):
        return 0

    def set_offset(self, value):
        pass

    def latest_stops(self):
        return []

    def delegations(self):
        return {"jobs": [], "runners": []}

    def new_alert_keys(self, keys):
        fresh = [k for k in keys if k not in self.alerts]
        self.alerts.update(keys)
        return fresh

    def record_checkin(self, summary, told, telegram):
        self.checkins.append((summary, told))
        return {"id": len(self.checkins)}


class TickBot(Bot):
    def updates(self, offset, *, wait=25):
        return []


def test_sf3_a_failing_runtime_link_never_stops_the_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "HEALTH", tmp_path / "alive")
    store, bot = TickStore(), TickBot()

    class GH:
        def open_prs(self):
            return []

        def runs(self):
            return []
    sup = M.Supervisor(store, bot, GH(), chat_id=OWNER, user_id=OWNER, runtime=FailingLink())
    sup.tick()
    sup.next_checkin = 0
    sup.tick()
    assert (tmp_path / "alive").exists()                   # the health file is written
    assert len(store.checkins) == 2                        # today's check-ins went on
    told = [t for t, _ in bot.sent if t.startswith("MC: the runtime link failed")]
    assert len(told) == 1 and "apply:boom, close:boom, deliver:boom, drain:boom" in told[0]   # once per key, a day
    assert any("runtime link unreadable" in e for e in store.checkins[0][0]["errors"])


def test_staging_watch_is_armed_by_the_credential_alone():
    digest = "a" * 64
    on = M._s1g_options({"MINIMOI_SUPERVISION_WATCH_TOKEN_SHA256": digest,
                         "MINIMOI_SUPERVISION_WATCH_HOST": "staging-1"}, connect=None)
    assert on == {"watch": {"timeout_s": 90, "hosts": ["staging-1"]}}           # N3's 90 s by default
    assert M._s1g_options({"MINIMOI_SUPERVISION_WATCH_HOST": "staging-1",
                           "MINIMOI_SUPERVISION_WATCH_TIMEOUT_S": "45"}, connect=None) == {}   # removed: disarmed


def test_sf7_a_contact_that_cannot_be_written_never_costs_the_call():
    class Cur:
        def execute(self, sql, params=()):
            if "supervision_contacts" in sql:
                raise RuntimeError("relation guild.supervision_contacts does not exist")

        def fetchone(self):
            return None

        def fetchall(self):
            return []
    s = Store(lambda: None)
    s._run = lambda fn: fn(Cur())
    assert s.worker({"phase": "pull"}, credential="worker") == {"decisions": [], "nudges": []}


def test_n14_the_runtime_key_space_is_reserved():
    from core.supervision.store import clean_ask
    with pytest.raises(Refused):
        clean_ask({"request_key": "rt-00000000-0000-4000-8000-000000000000", "agent": "codex", "session": "sess-1",
                   "text": "Approve the thing please"})
    assert clean_ask({"request_key": "ask-12345678", "agent": "codex", "session": "sess-1",
                      "text": "Approve the thing please"})["request_key"] == "ask-12345678"


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
    stale_row = {"host_id": "staging-9", "last_at": NOW - timedelta(seconds=999), "now": NOW, "silent_since": None,
                 "view": {}}
    assert watch.staging_watch(WatchStore([stale_row]), timeout_s=90, hosts=["staging-1"]) == []   # disarmed
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


def test_a_solo_runtime_reply_is_answered_from_the_row_by_message_returns():
    """Found by the fix round's PostgreSQL run: by_message's row has no ``code``; the reply is still answered."""
    store, bot = BatchStore(batch_rows(), solo_row()), Bot()
    sup_for(store, bot).handle(update("yes", reply_to=88))
    assert [(d, a) for d, a, _ in store.decided] == [(4, "approved")]
    sup_for(store, bot).handle(update("yes", reply_to=88))
    assert bot.sent[-1][0] == "D-4 is no longer open (already approved); nothing changed."
