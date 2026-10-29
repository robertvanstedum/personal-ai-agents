"""Host runtime P1-a in the supervisor (no database): runtime decisions are Telegram notices Robert answers in MiniMoi
unless the reply path is switched on (Robert, 29 Oct); the authority's own timers run here only when all three of
their values are set, with the link; the outbox's messages are bounded by length (review N-B); and the first
switch-on's bounded baseline (3b, Codex's P1 brief review S2) silences only ended history, never what waits on Robert.
With nothing configured, the supervisor is today's (test_supervision_runtime.py's own proof, and the golden check-in)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.supervision import switch_on, telegram as tg, watch  # noqa: E402
from core.supervision.runtime import RuntimeLink  # noqa: E402
from services.supervisor import main as M  # noqa: E402
from tests.supervision.test_supervision import OWNER, update  # noqa: E402
from tests.supervision.test_supervision_runtime import BatchStore, Bot, TickBot, TickStore, batch_rows, solo_row  # noqa: E402

PROD = ROOT / "core" / "mc_delegation_dispatcher" / "authority.prod.json"
LINK = {"MINIMOI_RUNTIME_AUTHORITY_FILE": str(PROD), "MINIMOI_RUNTIME_PRINCIPAL": "robert",
        "MINIMOI_RUNTIME_OWNER": "robert", "MINIMOI_RUNTIME_VIEW_BOUND_S": "120"}


def _connect():
    raise AssertionError("no database")


# ── answered in MiniMoi ──
def test_production_takes_runtime_answers_in_minimoi_unless_the_reply_path_is_on_link_or_no_link():
    assert M._s1g_options({}, _connect) == {}                                        # nothing set: today's
    assert "loops" not in M._s1g_options(LINK, _connect)
    # main() passes runtime_answers(environ) whether or not the link is on (review SF4): off unless "1".
    for environ in ({}, LINK, {"MINIMOI_RUNTIME_TELEGRAM_ANSWERS": "0"}):
        assert M.runtime_answers(environ) is False
    assert M.runtime_answers({"MINIMOI_RUNTIME_TELEGRAM_ANSWERS": "1"}) is True
    import inspect
    assert "runtime_answers=runtime_answers(os.environ)" in inspect.getsource(M.main)


def test_a_decision_message_becomes_a_notice_with_its_own_words_and_where_to_answer():
    solo = dict(solo_row(), code="D-4")
    for text, codes in ((tg.runtime_message(solo), ["D-4"]), (tg.batch_message(batch_rows()), ["D-1", "D-3"])):
        assert tg.REPLY_HERE in text
        notice = tg.answer_in_minimoi(text)
        head = text.partition(tg.REPLY_HERE)[0]
        assert notice.startswith(head) and tg.REPLY_HERE not in notice                  # the words kept, word for word
        assert "Answer in MiniMoi" in notice and all(c in notice for c in codes) and "not counted" in notice
    for other in ("Applied: D-1 approved: admitted.", "D-2 is no longer open (withdrawn); nothing changed.", None):
        assert tg.answer_in_minimoi(other) == other


def test_a_telegram_reply_to_a_runtime_decision_is_not_counted_while_answers_are_taken_in_minimoi():
    store, bot = BatchStore(batch_rows(), solo=solo_row()), Bot()
    sup = M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER, runtime=object(), runtime_answers=False)
    sup.handle(update("yes", reply_to=77))                                              # the batched message
    sup.handle(update("yes", reply_to=88))                                              # one sent alone
    assert store.decided == []
    assert [t for t, _ in bot.sent] == [tg.not_counted_here(["D-1", "D-2", "D-3"]), tg.not_counted_here(["D-4"])]
    on = M.Supervisor(store, Bot(), None, chat_id=OWNER, user_id=OWNER, runtime=object(), runtime_answers=True)
    on.handle(update("yes", reply_to=77))                                               # the reply path, on
    assert [d for d, *_ in store.decided] == [1, 2, 3]


def test_the_runtime_tick_sends_decisions_as_notices_through_its_bot():
    class Link:
        errors = []

        def __init__(self):
            self.bots = []

        def _record(self, store, bot, chat_id):
            self.bots.append(bot)
            bot.send(chat_id, tg.runtime_message(dict(solo_row(), code="D-4")))

        apply = tell_outcomes = close_closed = drain = lambda self, *a: []

        def deliver(self, store, bot, chat_id):
            self._record(store, bot, chat_id)

        def take_errors(self):
            return []
    link, bot = Link(), Bot()
    sup = M.Supervisor(TickStore(), bot, None, chat_id=OWNER, user_id=OWNER, runtime=link, runtime_answers=False)
    sup.runtime_tick()
    assert isinstance(link.bots[0], M.NoticeBot) and "Answer in MiniMoi" in bot.sent[-1][0]
    link2, bot2 = Link(), Bot()
    M.Supervisor(TickStore(), bot2, None, chat_id=OWNER, user_id=OWNER, runtime=link2).runtime_tick()
    assert link2.bots[0] is bot2 and tg.REPLY_HERE in bot2.sent[-1][0]                 # built directly: S1-G's path


# ── the authority's timers ──
def test_the_timers_run_only_with_all_three_values_and_the_link(tmp_path):
    loops = {"MINIMOI_RUNTIME_STATE_DIR": str(tmp_path / "authority"), "MINIMOI_RUNTIME_ENDPOINT_ID": "prod-authority",
             "MINIMOI_RUNTIME_HOST_ID": "prod-ec2-1"}
    opts = M._s1g_options({**LINK, **loops}, _connect)
    al = opts["loops"]
    assert (al.endpoint_id, al.host_id, al.state_dir) == ("prod-authority", "prod-ec2-1", tmp_path / "authority")
    assert al.store is opts["runtime"].authority and al.store.sweeps
    # Anything incomplete or unusable is refused by its code and left off, never a crash that takes today's
    # approvals down (review SF5).
    for environ, code in (({**LINK, "MINIMOI_RUNTIME_STATE_DIR": "/x"}, "authority_loops_refused:authority_loops_incomplete"),
                          (loops, "authority_loops_refused:authority_loops_incomplete"),          # never without the link
                          ({**LINK, **loops, "MINIMOI_RUNTIME_HOST_ID": "a/b"},
                           "authority_loops_refused:authority_loops_invalid")):
        out, refused = M.runtime_options(environ, _connect)
        assert "loops" not in out and refused == [code], (refused, code)
    v1 = tmp_path / "v1.json"
    v1.write_text(json.dumps({**json.loads(PROD.read_text()), "protocols": [1]}))
    out, refused = M.runtime_options({**LINK, **loops, "MINIMOI_RUNTIME_AUTHORITY_FILE": str(v1)}, _connect)
    assert out == {} and refused == ["runtime_link_refused:authority_not_version_2",
                                     "authority_loops_refused:authority_loops_incomplete"]
    out, refused = M.runtime_options({"MINIMOI_RUNTIME_PRINCIPAL": "robert"}, _connect)     # a partial link
    assert out == {} and refused == ["runtime_link_refused:runtime_link_incomplete"]
    # the link's store has production's grant catalogue (the sink's effect among it, as the file admits test routes)
    assert "fakework.sink_post" in set(opts["runtime"].authority._grants._catalogue)


# ── the outbox bounded by length ──
def test_the_outbox_never_sends_a_message_telegram_would_cut():
    rows = [{"key": f"staging-silent:h{i}", "channel": "staging_watch", "kind": "error", "text": "x" * 900}
            for i in range(8)]
    chunks = list(M._outbox_chunks("staging_watch", rows))
    assert [len(c) for c in chunks] == [3, 3, 2]
    assert all(len(watch.outbox_message("staging_watch", c)) <= M.OUTBOX_MESSAGE_CHARS for c in chunks)
    short = [dict(r, text="short") for r in rows] + [dict(rows[0], key="k9", text="short")] * 3
    assert [len(c) for c in M._outbox_chunks("staging_watch", short)] == [10, 1]       # still at most ten
    store, bot = TickStore(), TickBot()
    store.queue_notices("staging_watch", rows)
    M.Supervisor(store, bot, None, chat_id=OWNER, user_id=OWNER).tell_outbox()
    assert len(bot.sent) == 3 and all(len(t) <= M.OUTBOX_MESSAGE_CHARS for t, _ in bot.sent)


# ── 3b: the first switch-on ──
class Authority:
    """The authority's outbox and items, as notices_since and live_items read them."""

    def __init__(self, notices, live=()):
        self.notices, self.live = notices, set(live)

    def notices_since(self, after_seq=0, *, hours=24):
        return [n for n in self.notices if n["seq"] > after_seq][:500]


def _n(seq, key, rule, kind):
    return {"seq": seq, "at": "2026-10-29T12:00:00+00:00",
            "notice": {"key": key, "rule": rule, "kind": kind, "text": f"notice {key}"}}


ENDED, LIVE = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
HISTORY = [_n(1, f"runtime-stalled:{ENDED}:g1:x", "stalled", "stalled"),
           _n(2, f"runtime-overdue:{ENDED}:g1", "overdue", "stalled"),
           _n(3, f"runtime-drift:{LIVE}:over_estimate", "drift", "stalled"),
           _n(4, f"runtime-unknown:{ENDED}:g1", "unknown", "waiting_on_robert"),
           _n(5, f"delegation-ended:{ENDED}", "ended", "error"),
           _n(6, "runtime-sweeper-stale:2026-10-29T11:00:00+00:00", "sweeper_stale", "error"),
           _n(7, "runtime-ready:C-1", "ready", "waiting_on_robert"),
           _n(8, "decisions:abc", "decisions", "waiting_on_robert")]


def test_the_baseline_silences_only_ended_history_up_to_its_cutoff():
    notices = switch_on.raised(Authority(HISTORY))
    assert [n["key"] for n in notices][-1] == "runtime-ready:C-1"                         # decision notices left out
    plan = switch_on.plan_baseline(notices, {LIVE}, cutoff=6)
    assert {b["key"] for b in plan["baselined"]} == {f"runtime-stalled:{ENDED}:g1:x", f"runtime-overdue:{ENDED}:g1"}
    kept = {k["key"]: k["why"] for k in plan["kept"]}
    assert kept == {f"runtime-drift:{LIVE}:over_estimate": "item_live",
                    f"runtime-unknown:{ENDED}:g1": "actionable:unknown",
                    f"delegation-ended:{ENDED}": "actionable:ended",
                    # a sweeper outage's key names a time, not an item: never baselined (Codex's P1-a review S1)
                    "runtime-sweeper-stale:2026-10-29T11:00:00+00:00": "actionable:sweeper_stale",
                    "runtime-ready:C-1": "after_cutoff"}


def test_a_first_switch_on_tells_no_stale_burst_and_still_tells_what_waits_and_what_comes_after(tmp_path, monkeypatch):
    class Auth(Authority):
        def __init__(self, notices, live=()):
            super().__init__(notices, live)
            self.environment = "prod"

        def snapshot(self):
            return {"schema": "minimoi.runtime.state.v1", "view_age_s": 0, "notices": [], "authority": {}}

    def link_for(auth):
        return RuntimeLink(auth, owner="robert", view_bound_s=120, source=auth.snapshot)

    # Fresh: production's authority has no history, so the first switch-on tells nothing at all.
    store, bot = TickStore(), Bot()
    store.told_keys = lambda keys: {k for k in keys if k in store.alerts}
    assert link_for(Auth([])).drain(store, bot, OWNER) == [] and bot.sent == []
    # With history: the baseline first, then the link's first tick tells only what was kept.
    auth = Auth(HISTORY[:6])
    monkeypatch.setattr(switch_on, "live_items", lambda a: a.live | {LIVE})
    if True:
        out = switch_on.baseline(auth, store, record=tmp_path / "baseline.json")
        assert (out["cutoff_seq"], out["baselined"], out["newly_marked"]) == (6, 2, 2)
        record = json.loads((tmp_path / "baseline.json").read_text())
        assert {b["why"] for b in record["baselined"]} == {"historical:stalled", "historical:overdue"}
        sent = link_for(auth).drain(store, bot, OWNER)
        assert set(sent) == {f"runtime-drift:{LIVE}:over_estimate", f"runtime-unknown:{ENDED}:g1",
                             f"delegation-ended:{ENDED}", "runtime-sweeper-stale:2026-10-29T11:00:00+00:00"}
        auth.notices.append(_n(9, "runtime-stalled:33333333-3333-4333-8333-333333333333:g1:y", "stalled", "stalled"))
        assert link_for(auth).drain(store, Bot(), OWNER) == [
            "runtime-stalled:33333333-3333-4333-8333-333333333333:g1:y"]               # after the cutoff: told
        with pytest.raises(FileExistsError):
            switch_on.baseline(auth, store, record=tmp_path / "baseline.json")         # a record is never overwritten


def test_the_switch_on_commands_need_the_link_configuration_and_print_no_value(capsys):
    assert switch_on.main(["nonsense"], environ={}) == 2
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        switch_on.main(["check"], environ={})
    with pytest.raises(SystemExit, match="MINIMOI_RUNTIME_AUTHORITY_FILE"):
        switch_on.main(["rollback-check"], environ={"DATABASE_URL": "postgresql://unused"})


def test_the_rollback_check_names_every_live_item_and_open_effect():
    class Cur:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, sql, args=()):
            self.last = sql

        def fetchall(self):
            return self.rows

    class Auth:
        def __init__(self):
            self.cur = Cur([{"id": LIVE}, {"id": ENDED}])

        def _tx(self):
            import contextlib
            return contextlib.nullcontext(self.cur)

        def _open_receipts(self, cur, rid):
            return [{"operation_id": "op-1", "state": "unknown"}] if rid == ENDED else []

    auth = Auth()
    out = switch_on.rollback_blockers(auth)
    assert out["open_effects"] == [{"item_id": ENDED, "operations": ["op-1"]}]
    assert sorted(out["live"]) == sorted([LIVE, ENDED])                       # the fake's live query returns both


def test_a_digest_names_the_one_authority_file():
    from core.mc_delegation_dispatcher.production import file_digest
    assert file_digest(PROD) == "sha256:" + hashlib.sha256(PROD.read_bytes()).hexdigest()
