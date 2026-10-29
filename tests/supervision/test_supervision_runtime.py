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

    apply = tell_outcomes = close_closed = deliver = drain = check_in = _boom


class OutboxFake:
    """The supervisor's outbox as Store keeps it (sql/003 supervision_outbox, P2-3): queued once per key, oldest first,
    sent only when marked so. ``fail_record`` makes the next n records of a send fail, as a database error would."""
    fail_record = 0

    @property
    def outbox(self):
        if "_outbox" not in self.__dict__:
            self._outbox = {}
        return self._outbox

    def queue_notices(self, channel, items):
        fresh = []
        for i in items:
            if i["key"] not in self.outbox:
                self.outbox[i["key"]] = {"key": i["key"], "channel": channel, "kind": i["kind"], "text": i["text"],
                                         "sent": False, "attempts": 0, "last_error": None, "n": len(self.outbox)}
                fresh.append(i["key"])
        return fresh

    def outbox_unsent(self, limit=50):
        return sorted((dict(r) for r in self.outbox.values() if not r["sent"]), key=lambda r: r["n"])[:limit]

    def outbox_sent(self, keys):
        if self.fail_record:
            self.fail_record -= 1
            raise ConnectionError("database_down")
        for k in keys:
            self.outbox[k]["sent"] = True

    def outbox_failed(self, keys, code):
        for k in keys:
            self.outbox[k]["attempts"] += 1
            self.outbox[k]["last_error"] = code


class TickStore(OutboxFake):
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
    assert len(told) == 1 and "apply:boom, close:boom, deliver:boom, drain:boom, tell:boom" in told[0]   # once a day
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
    assert items == [{"key": "runtime-stalled:x:g1:t", "kind": "stalled", "text": "x stalled", "drained": True}]
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


class WatchStore(OutboxFake):
    def __init__(self, rows):
        self.rows = rows

    def watch_rows(self):
        return self.rows

    def watch_transition(self, host, to, since, notice):
        """As Store's: the row's change and the notice queued together, or neither when the row moved already."""
        r = next(r for r in self.rows if r["host_id"] == host)
        if (to == "silent" and r["silent_since"] is not None) or (to == "back" and r["silent_since"] != since):
            return False
        r["silent_since"] = since if to == "silent" else None
        self.queue_notices("staging_watch", [notice])
        return True


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


# ── Codex part 2 P2-3: a notice whose send fails is never consumed; it waits in the outbox until it goes out ─────
class Flaky(Bot):
    """A bot whose sends fail while ``down`` (or for the next ``fail`` sends), as Telegram does now and then."""

    def __init__(self, fail=0):
        super().__init__()
        self.fail, self.down, self.attempts = fail, False, 0

    def send(self, chat_id, text, *, reply_to=None):
        self.attempts += 1
        if self.down or self.fail:
            self.fail = max(0, self.fail - 1)
            raise ConnectionError("telegram_unreachable")
        return super().send(chat_id, text, reply_to=reply_to)


class WatchTickStore(WatchStore, TickStore):
    def __init__(self, rows):
        WatchStore.__init__(self, rows)
        TickStore.__init__(self)


def watched(row, bot):
    store = WatchTickStore([row])
    return store, M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER,
                               watch={"timeout_s": 90, "hosts": ["staging-1"]})


def staging_row():
    return {"host_id": "staging-1", "last_at": NOW - timedelta(seconds=30), "now": NOW, "silent_since": None,
            "view": {"authority_answering": True, "sweeper_age_s": 4, "lapsed_always_on": 0}}


def test_p2_3_an_outage_and_a_return_whose_sends_fail_are_each_told_later_once():
    row, bot = staging_row(), Flaky(fail=1)
    store, sup = watched(row, bot)
    row["now"] = NOW + timedelta(seconds=120)                 # silent past the timeout
    sup.watch_tick()                                          # the send fails: nothing consumed
    assert bot.sent == [] and store.outbox_unsent()[0]["attempts"] == 1
    assert store.outbox_unsent()[0]["last_error"] == "telegram_unreachable"
    sup.watch_tick()
    sup.watch_tick()
    assert [t.split("\n")[3].split(" has been")[0] for t, _ in bot.sent] == ["• Staging staging-1"]   # once
    row["last_at"] = row["now"] - timedelta(seconds=5)        # it beats again
    bot.fail = 1
    sup.watch_tick()                                          # the return's send fails
    assert len(bot.sent) == 1
    sup.watch_tick()
    sup.watch_tick()
    assert len(bot.sent) == 2 and "Staging staging-1 is back" in bot.sent[1][0]
    assert store.outbox_unsent() == [] and len(store.outbox) == 2


def test_p2_3_an_outage_that_ends_while_telegram_is_down_is_still_told_then_its_return():
    row, bot = staging_row(), Flaky()
    store, sup = watched(row, bot)
    bot.down = True
    row["now"] = NOW + timedelta(seconds=120)
    sup.watch_tick()
    row["last_at"] = row["now"] - timedelta(seconds=5)
    sup.watch_tick()
    assert bot.sent == [] and [r["key"].split(":")[0] for r in store.outbox_unsent()] == \
        ["staging-silent", "staging-back"]
    bot.down = False
    sup.watch_tick()
    (text, _), = bot.sent                                     # both, in order, in one message
    assert text.index("has been silent") < text.index("is back")
    sup.watch_tick()
    assert len(bot.sent) == 1


def test_p2_3_a_restart_after_a_failed_send_tells_it_and_a_failed_record_never_repeats_it():
    row, bot = staging_row(), Flaky(fail=1)
    store, sup = watched(row, bot)
    row["now"] = NOW + timedelta(seconds=120)
    sup.watch_tick()                                          # failed; then the supervisor restarts
    again = M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER,
                         watch={"timeout_s": 90, "hosts": ["staging-1"]})
    store.fail_record = 1                                     # its send succeeds, its record fails once
    again.watch_tick()
    again.watch_tick()                                        # held in memory: never sent twice by this process
    assert len(bot.sent) == 1 and store.outbox_unsent() == []  # and recorded at the next tick


def test_p2_3_a_runtime_link_error_whose_send_fails_is_told_later_once_a_day():
    store, bot = TickStore(), Flaky(fail=1)
    sup = M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER)
    sup._runtime_failed(["apply:synthetic_failure"])          # its send fails
    assert bot.sent == []
    sup._runtime_failed([])                                   # the next tick, no new error: told now
    sup._runtime_failed(["apply:synthetic_failure"])          # the same error again today: not again
    assert [t for t, _ in bot.sent] == ["MC: the runtime link failed (apply:synthetic_failure). Today's approvals "
                                        "and check-ins go on; the runtime's decisions and notices wait until it works "
                                        "again."]


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


# ── the closure check's follow-up (REVIEW_independent_S1-G.md, "Closure check") ──
class FlakyBot(Bot):
    """The approvals bot whose next ``fail`` sends fail, as a Telegram error would."""

    def __init__(self, fail=0):
        super().__init__()
        self.fail = fail

    def send(self, chat_id, text, *, reply_to=None):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("telegram_down")
        return super().send(chat_id, text, reply_to=reply_to)


class Authority:
    """The authority's side the runtime link reads: its outbox and its decisions' status (and resolve, scripted)."""
    environment = "dev"

    def __init__(self, notices=(), status=None, resolve=None):
        self.notices, self.status, self._resolve, self.reads = list(notices), dict(status or {}), resolve, []

    def notices_since(self, after_seq=0, *, hours=24):
        self.reads.append((after_seq, hours))
        return [n for n in self.notices if n["seq"] > after_seq][:500]

    def decisions_status(self, ids):
        return {i: self.status.get(i, {"open": False, "answer": "unknown_decision"}) for i in ids}

    def resolve(self, did, answer, *, by, receipt):
        return self._resolve(did, answer, receipt)


def outbox(seq, key):
    return {"seq": seq, "notice": {"key": key, "kind": "blocked", "rule": "blocked", "text": f"{key} told"}}


def fresh_doc():
    return {"schema": "minimoi.runtime.state.v1", "environment": "dev", "as_of": NOW.isoformat(), "view_age_s": 5,
            "notices": []}


class AlertStore:
    """The once-per-key memory (guild.supervision_alerts); ``fail_record`` makes the next records fail."""

    def __init__(self):
        self.alerts, self.fail_record = set(), 0

    def told_keys(self, keys):
        return {k for k in keys if k in self.alerts}

    def new_alert_keys(self, keys):
        if self.fail_record:
            self.fail_record -= 1
            raise RuntimeError("database_down")
        fresh = [k for k in keys if k not in self.alerts]
        self.alerts.update(keys)
        return fresh


def link_for(authority):
    return RuntimeLink(authority, owner="robert", view_bound_s=60, source=fresh_doc)


def test_c_sf1_a_rule_notice_is_told_only_once_its_message_went_out():
    auth = Authority([outbox(1, "runtime-blocked:a"), outbox(2, "runtime-overdue:b")])
    store, bot = AlertStore(), FlakyBot(fail=1)
    link = link_for(auth)
    assert link.drain(store, bot, OWNER) == []                      # Telegram fails: nothing is marked told
    assert store.alerts == set() and link.take_errors() == ["drain_send:telegram_down"]
    assert link.drain(store, bot, OWNER) == ["runtime-blocked:a", "runtime-overdue:b"]     # next tick: it goes out
    assert auth.reads == [(0, None), (0, None)]                     # the cursor never passed an untold notice
    assert len(bot.sent) == 1 and "runtime-blocked:a told" in bot.sent[0][0]
    assert store.alerts == {"runtime-blocked:a", "runtime-overdue:b"}
    assert link.drain(store, bot, OWNER) == [] and len(bot.sent) == 1                       # never twice (DUP-7)
    assert auth.reads[-1] == (2, None)
    # A database error recording what went out: never sent again, recorded at the next tick.
    auth.notices.append(outbox(3, "runtime-lapsed:c"))
    store.fail_record = 1
    assert link.drain(store, bot, OWNER) == ["runtime-lapsed:c"] and "runtime-lapsed:c" not in store.alerts
    assert link.take_errors() == ["drain_record:database_down"]
    assert link.drain(store, bot, OWNER) == [] and "runtime-lapsed:c" in store.alerts and len(bot.sent) == 2
    # A notice whose send failed, then a restart: the new link reads the outbox from the start, whatever a notice's
    # age, and tells only what was never told.
    auth.notices.append(outbox(4, "runtime-held:d"))
    bot.fail = 1
    assert link.drain(store, bot, OWNER) == []
    restarted = link_for(auth)
    assert restarted.drain(store, bot, OWNER) == ["runtime-held:d"]
    assert auth.reads[-1] == (0, None) and len(bot.sent) == 3 and "runtime-held:d told" in bot.sent[-1][0]
    assert link_for(auth).drain(store, bot, OWNER) == [] and len(bot.sent) == 3


def test_c_sf1_a_backlog_goes_out_whole_a_few_messages_a_tick():
    from core.supervision import runtime as R
    auth = Authority([outbox(i, f"runtime-blocked:{i:02d}") for i in range(1, 36)])
    store, bot = AlertStore(), FlakyBot()
    link = link_for(auth)
    assert len(link.drain(store, bot, OWNER)) == R.NOTICES_PER_MESSAGE * R.MESSAGES_PER_TICK
    assert len(bot.sent) == R.MESSAGES_PER_TICK and all(len(t) <= R.MESSAGE_CHARS for t, _ in bot.sent)
    assert len(link.drain(store, bot, OWNER)) == 5                   # the rest at the next tick, oldest first
    assert len(store.alerts) == 35 and link.drain(store, bot, OWNER) == []


def test_c_sf1_the_check_in_lists_the_runtimes_notices_and_never_marks_them_told():
    class L:
        def check_in(self):
            return [{"key": "runtime-blocked:a", "kind": "blocked", "text": "a blocked", "drained": True}], [], [], []

    class GH:
        def open_prs(self):
            return []

        def runs(self):
            return []
    store, bot = TickStore(), TickBot()
    summary, new = M.Supervisor(store, bot, GH(), chat_id=OWNER, user_id=OWNER, runtime=L()).checkin(now=NOW)
    assert new == [] and store.alerts == set() and bot.sent == []
    assert {"kind": "blocked", "text": "a blocked"} in summary["open_items"]


class RtStore:
    """The supervision store's runtime rows, as the tables answer them."""

    def __init__(self, rows):
        self.rows = rows

    def _row(self, i):
        return next(r for r in self.rows if r["id"] == i)

    def runtime_answered(self):
        return [r for r in self.rows if r["state"] in ("approved", "declined") and not r.get("delivered")]

    def mark_applied(self, i):
        self._row(i)["delivered"] = True

    def runtime_untold(self):
        return [r for r in self.rows if r["state"] in ("approved", "declined") and r.get("delivered")
                and not r.get("announced")]

    def mark_announced(self, i):
        self._row(i)["announced"] = True

    def batch_of(self, i):
        return 77

    def runtime_rows(self, did):
        return [r for r in self.rows if r["requester"]["decision_id"] == did]


DID = "00000007-0000-4000-8000-000000000000"


def answered(**over):
    return dict(row(7, "Admit card C-7 step review: the card's approval no longer admits it. Yes admits it."),
                state="approved", receipt={"channel": "telegram", "chat_id": OWNER, "message_id": 5001, "text": "yes"},
                **over)


def test_c_n4_a_lost_outcome_message_goes_out_at_the_next_tick_once():
    store = RtStore([answered(delivered=True)])
    link = link_for(Authority())
    link._outcomes[7] = {"state": "resolved", "action": "admit"}
    bot = FlakyBot(fail=1)
    assert link.tell_outcomes(store, bot, OWNER) == [] and not store.rows[0].get("announced")
    assert link.take_errors() == ["tell:telegram_down"]
    assert link.tell_outcomes(store, bot, OWNER) == ["D-7"]
    assert bot.sent == [("Applied: D-7 approved: the step is admitted under its own approval and queued.", 77)]
    assert link.tell_outcomes(store, bot, OWNER) == [] and len(bot.sent) == 1


def test_c_n1_an_answer_applied_before_a_restart_is_told_as_applied():
    """The authority applied Robert's yes; the supervisor died before marking it applied. After the restart the
    authority answers already_resolved: its records are read again, and the closing turn is his."""
    turn = f"telegram:{OWNER}:5001"
    for closed_by, expected in ((turn, "Applied: D-7 approved: the step is admitted under its own approval and "
                                       "queued."),
                                ("guild:c-1:n-9", "Not applied: the runtime had already closed D-7 (approved); "
                                                  "nothing was done.")):
        auth = Authority(status={DID: {"open": False, "answer": "approved", "action": "admit", "turn_id": closed_by}},
                         resolve=lambda did, answer, receipt: {"state": "already_resolved", "answer": "approved",
                                                                "action": "admit"})
        store, bot, link = RtStore([answered()]), Bot(), link_for(auth)
        assert [o["state"] for _, o in link.apply(store, bot, OWNER)] == \
            ["resolved" if closed_by == turn else "already_resolved"]
        link.tell_outcomes(store, bot, OWNER)
        assert bot.sent == [(expected, 77)]
    # A restart between applying and telling: the outcome is read again from the authority's records.
    auth = Authority(status={DID: {"open": False, "answer": "approved", "action": "admit", "turn_id": turn}})
    store, bot = RtStore([answered(delivered=True)]), Bot()
    assert link_for(auth).tell_outcomes(store, bot, OWNER) == ["D-7"]
    assert bot.sent[0][0].startswith("Applied: D-7 approved")


def test_c_n4_a_lost_closing_message_leaves_the_d_code_open_and_goes_out_at_the_next_tick():
    from core.supervision.runtime import closed_reason
    rows = [row(7, "Admit card C-7 step review: the card's approval no longer admits it. Yes admits it.")]

    class S(RtStore):
        closed = []

        def runtime_open(self):
            return [r for r in self.rows if r["state"] in ("new", "sent")]

        def close_runtime(self, i):
            self._row(i)["state"] = "expired"
            self.closed.append(i)
            return True
    store = S(rows)
    st = {"open": False, "answer": "withdrawn", "code": "item_ended", "item_state": "completed"}
    link, bot = link_for(Authority(status={DID: st})), FlakyBot(fail=1)
    assert link.close_closed(store, bot, OWNER) == [] and store.closed == []
    assert link.close_closed(store, bot, OWNER) == [("D-7", "its item has ended (completed)")]
    assert bot.sent == [("D-7 is no longer open: its item has ended (completed). Nothing to answer.", 77)]
    assert link.close_closed(store, bot, OWNER) == [] and len(bot.sent) == 1
    assert closed_reason(st) == "its item has ended (completed)"
