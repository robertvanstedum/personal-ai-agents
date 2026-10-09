"""Master Craftsman jobs, the engine (overnight build, step 3; amendment B1 and B2).

Real stores (conversations, notes, job files), a fake relay speaking the v2 job protocol that can misbehave in every way a
real one might, and a fake clock. What the owner is told is checked against what the relay actually received. No model,
no network."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fake_job_relay import FakeJobRelay
from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_upload_reading import _doc, _new

from minimoi_portal.guild_ui import job_inbox, jobs as J
from minimoi_portal.guild_ui.conversations import conversations_of
from minimoi_portal.guild_ui.mc.job_relay import JobRelay


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class World:
    """One owner, one conversation, a manager over a real job folder, a fake relay and a fake clock."""

    def __init__(self, portal, tmp_path, limits=None):
        self.portal = portal
        self.client = portal.owner()
        self.token = portal.csrf(self.client)
        self.services = portal.app.extensions["guild_ui_next"]["services"]
        self.convs = conversations_of(self.services)
        self.folder = str(tmp_path / "guild-data")
        os.makedirs(self.folder)
        self.relay = FakeJobRelay()
        self.clock = Clock()
        self.limits = limits or J.Limits()
        self.manager = self.make_manager()
        self.cid = _new(self.client, self.token)
        self.principal = json.loads((Path(self.convs.dir) / f"{self.cid}.json").read_text())["principal"]
        self.conv = self.convs.get(self.cid, self.principal)

    def make_manager(self):
        base = self.services.floor

        def floor_for(conv):
            nf = (conv or {}).get("notes_floor")
            return base if not nf or nf == base.floor else base.for_floor(nf)
        relay = JobRelay("http://relay/v1", "tok", http_get=self.relay.get, http_post=self.relay.post)
        return J.JobManager(self.folder, relay, conversations=self.convs, floor_for=floor_for,
                            scrub=lambda t: t, clock=self.clock, limits=self.limits)

    def note(self, text="Compare my new resume to my old resume.", cid=None):
        n = _keep(self.client, self.token, text, conversation_id=cid or self.cid)
        return n

    def start(self, text="Compare my new resume to my old resume.", doc_ids=None, cid=None):
        n = self.note(text, cid)
        conv = self.convs.get(cid or self.cid, self.principal)
        job, refusal = self.manager.start(conv, self.principal, n, n["request_id"], doc_ids)
        return job, refusal, n

    def go(self, text="Compare my new resume to my old resume.", **kw):
        self.clock.advance(1)                                                      # jobs are ordered by when they were asked for
        job, refusal, n = self.start(text, **kw)
        assert refusal is None, refusal
        return job

    def get(self, job):
        return self.manager.store.get(job["conversation_id"], job["id"])

    def notes(self):
        return self.portal.extra["floor"].rows("floor_messages")


@pytest.fixture
def w(turned, tmp_path):
    return World(turned, tmp_path)


def state(w, job):
    return w.get(job)["state"]


# ── the happy path ───────────────────────────────────────────────────────────────

def test_a_job_is_queued_then_dispatched_once_with_the_owners_words_and_runs(w):
    job = w.go("Please compare my new resume to my old resume.")
    assert job["state"] == "queued" and w.relay.starts == []                      # nothing has left yet
    w.manager.tick()
    [sent] = w.relay.starts
    assert sent["job_id"] == job["id"] and sent["agent"] == "mc-agent" and sent["inbox"] is None
    assert sent["task"] == "Please compare my new resume to my old resume." and sent["deadline_s"] == 1800
    assert state(w, job) == "running"
    w.manager.tick()
    w.manager.tick()
    assert len(w.relay.starts) == 1                                               # polling never starts it again


def test_completion_posts_the_result_once_in_the_conversation_and_the_job_is_done(w):
    job = w.go()
    w.manager.tick()
    w.relay.progress(job["id"], 42, tools=[{"name": "read", "target": "docs/a.md", "ok": True}])
    w.clock.advance(42)
    w.manager.tick()
    v = w.manager.view(w.get(job))
    assert v["state"] == "running" and v["elapsed_s"] == 42 and v["tools"] == [{"name": "read", "target": "docs/a.md", "ok": True}]
    w.relay.finish(job["id"], "The new resume is stronger in two places.", tools=[{"name": "read", "target": "docs/a.md", "ok": True}])
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "completed" and done["result_note_id"] and "result_pending" not in done
    replies = [r for r in w.notes() if r["author_kind"] == "agent"]
    assert [r["text"] for r in replies] == ["The new resume is stronger in two places."] and replies[0]["author"] == "master_craftsman"
    w.manager.tick()
    w.manager.tick()
    assert len([r for r in w.notes() if r["author_kind"] == "agent"]) == 1         # a finished job posts nothing more


def test_one_note_makes_one_job_however_many_times_it_is_started(w):
    n = w.note()
    conv = w.convs.get(w.cid, w.principal)
    a, _ = w.manager.start(conv, w.principal, n, n["request_id"])
    b, _ = w.manager.start(conv, w.principal, n, n["request_id"])
    assert a["id"] == b["id"] and len(w.manager.store.list(w.cid)) == 1
    w.manager.tick()
    w.manager.tick()
    assert len(w.relay.starts) == 1


def test_the_job_is_claimed_on_disk_before_the_relay_hears_of_it(w):
    seen = []
    job = w.go()
    w.relay.on_start = lambda body: seen.append(w.manager.store.get(job["conversation_id"], job["id"])["state"])
    w.manager.tick()
    assert seen == ["dispatching"]


# ── an ambiguous or failed start is settled by asking, never by sending again ─────

def test_a_start_whose_answer_was_lost_but_which_arrived_is_followed_not_replayed(w):
    job = w.go()
    w.relay.start_mode = "ambiguous_received"
    w.manager.tick()
    assert state(w, job) == "dispatching" and len(w.relay.starts) == 1
    w.relay.start_mode = "ok"
    w.manager.tick()
    assert state(w, job) == "running" and len(w.relay.starts) == 1                   # found by status; no second start


def test_a_start_that_never_arrived_ends_as_not_started_and_is_never_sent_later(w):
    job = w.go()
    w.relay.start_mode = "ambiguous_lost"
    w.manager.tick()
    assert state(w, job) == "dispatching"
    w.relay.start_mode = "ok"
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "not_started" and w.relay.starts == []
    w.manager.tick()
    assert w.relay.starts == [] and "nothing was changed" in w.manager.view(done)["message"]


def test_a_relay_that_stays_unreachable_after_an_ambiguous_start_ends_as_unknown_not_not_started(w):
    job = w.go()
    w.relay.start_mode = "down"
    w.manager.tick()
    w.clock.advance(60)
    w.manager.tick()
    assert state(w, job) == "dispatching"
    w.clock.advance(70)
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "unknown" and done["error_class"] == "relay_unreachable"
    assert "Outcome unknown" in w.manager.view(done)["message"]


def test_a_busy_relay_puts_the_job_back_in_the_queue_and_it_runs_when_there_is_room(w):
    job = w.go()
    w.relay.start_mode = "busy"
    w.manager.tick()
    assert state(w, job) == "queued" and w.relay.starts == []
    w.relay.start_mode = "ok"
    w.manager.tick()
    assert state(w, job) == "running" and len(w.relay.starts) == 1


def test_a_relay_that_stays_busy_gives_up_after_ten_tries_as_not_started(w):
    job = w.go()
    w.relay.start_mode = "busy"
    for _ in range(12):
        w.manager.tick()
    done = w.get(job)
    assert done["state"] == "not_started" and done["error_class"] == "relay_busy" and done["attempts"] == 10


def test_a_refused_start_is_not_started(w):
    job = w.go()
    w.relay.start_mode = "refuse"
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "not_started" and done["error_class"] == "refused" and w.relay.starts == []


def test_a_relay_that_already_knows_the_job_id_is_followed_and_nothing_runs_twice(w):
    job = w.go()
    w.relay.jobs[job["id"]] = {"state": "running", "elapsed_s": 5, "heartbeat_age_s": 1, "result": None, "tools": [], "writes": [], "error_class": None}
    w.manager.tick()
    assert state(w, job) == "running" and w.relay.starts == []


def test_a_note_that_is_gone_when_the_job_is_dispatched_is_not_started(w, monkeypatch):
    job = w.go()
    monkeypatch.setattr(w.manager, "_note", lambda j: None)
    w.manager.tick()
    assert w.get(job)["state"] == "not_started" and w.get(job)["error_class"] == "note_missing" and w.relay.starts == []


# ── survives a portal restart ────────────────────────────────────────────────────

def test_a_new_portal_process_picks_up_a_running_job_without_starting_it_again(w):
    job = w.go()
    w.manager.tick()
    assert len(w.relay.starts) == 1
    again = w.make_manager()                                                      # a restart: nothing in memory
    w.relay.progress(job["id"], 90)
    again.tick()
    assert again.view(again.store.get(job["conversation_id"], job["id"]))["state"] == "running"
    w.relay.finish(job["id"], "done after the restart")
    again.tick()
    assert again.store.get(job["conversation_id"], job["id"])["state"] == "completed" and len(w.relay.starts) == 1


def test_a_restart_between_claim_and_answer_asks_the_relay_instead_of_sending_again(w):
    job = w.go()
    w.relay.start_mode = "ambiguous_received"
    w.manager.tick()                                                              # "crashed" with the job in dispatching
    w.relay.start_mode = "ok"
    again = w.make_manager()
    again.tick()
    assert again.store.get(job["conversation_id"], job["id"])["state"] == "running" and len(w.relay.starts) == 1


# ── the result is posted at most once ────────────────────────────────────────────

def test_a_failure_to_keep_the_result_is_retried_and_posts_exactly_one_note(w, monkeypatch):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "the answer")
    floor = w.manager._floor(w.get(job))
    real = floor.add_note
    calls = []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
            raise FloorStoreUnavailable("down")
        return real(*a, **kw)
    monkeypatch.setattr(type(floor), "add_note", lambda self, *a, **kw: flaky(*a, **kw))
    w.manager.tick()
    assert state(w, job) == "running" and w.get(job)["result_pending"] == "the answer"      # decided and saved, not yet posted
    w.manager.tick()
    assert state(w, job) == "completed"
    assert [r["text"] for r in w.notes() if r["author_kind"] == "agent"] == ["the answer"]


def test_a_crash_after_posting_but_before_completing_does_not_post_again_or_change_the_text(w, monkeypatch):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "first text")
    real = w.manager.store.mutate
    state_box = {"armed": True}

    def crash_on_done(cid, jid, fn):
        probe = dict(w.manager.store.get(cid, jid))
        try:
            fn(probe)
        except J._Skip:
            return real(cid, jid, fn)
        except Exception:
            return real(cid, jid, fn)
        if state_box["armed"] and probe.get("state") == "completed":
            state_box["armed"] = False
            raise RuntimeError("crash before the job was marked completed")
        return real(cid, jid, fn)
    monkeypatch.setattr(w.manager.store, "mutate", crash_on_done)
    w.manager.tick()                                                              # the note is posted, then the 'crash'
    assert state(w, job) == "running" and len([r for r in w.notes() if r["author_kind"] == "agent"]) == 1
    w.relay.jobs[job["id"]]["result"] = "a different text on the second read"
    w.manager.tick()
    assert state(w, job) == "completed"
    assert [r["text"] for r in w.notes() if r["author_kind"] == "agent"] == ["first text"]


def test_an_empty_result_is_a_failure_not_an_answer(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "   ")
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "failed" and done["error_class"] == "empty_result" and not [r for r in w.notes() if r["author_kind"] == "agent"]


def test_a_refusal_is_a_valid_saved_answer(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "I can't do that: it would mean reading a key file, which I am not allowed to open.")
    w.manager.tick()
    assert state(w, job) == "completed" and [r["text"] for r in w.notes() if r["author_kind"] == "agent"][0].startswith("I can't do that")


def test_a_long_result_shows_its_first_part_in_the_note_and_keeps_the_whole_with_the_job(w):
    job = w.go()
    w.manager.tick()
    paragraphs = "\n\n".join(f"Paragraph {i}: " + "word " * 60 for i in range(40))
    w.relay.finish(job["id"], paragraphs)
    w.manager.tick()
    done = w.get(job)
    [note] = [r for r in w.notes() if r["author_kind"] == "agent"]
    assert len(note["text"]) <= 4000 and note["text"].startswith("Paragraph 0:")                         # fits the note limit
    assert note["text"].endswith('use "Open full result".]') and f"{len(paragraphs):,} characters" in note["text"]
    assert w.manager.store.read_result(w.cid, job["id"]) == paragraphs                                    # nothing lost
    v = w.manager.view(done)
    assert v["result_full"] is True and v["result_chars"] == len(paragraphs) and v["result_truncated"] is False


def test_a_result_over_64_kb_is_cut_there_and_says_so_in_the_kept_file(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "x " * (40 * 1024))
    w.manager.tick()
    done = w.get(job)
    kept = w.manager.store.read_result(w.cid, job["id"])
    assert done["result_truncated"] and kept.endswith("the rest was not kept.]") and len(kept) <= 64 * 1024 + 80
    assert w.manager.view(done)["result_truncated"] is True


def test_a_short_result_is_posted_whole(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "Short and complete.")
    w.manager.tick()
    assert [r["text"] for r in w.notes() if r["author_kind"] == "agent"] == ["Short and complete."]
    assert w.manager.view(w.get(job))["result_full"] is False


@pytest.mark.parametrize("relay_state,expected", [("failed", "failed"), ("unknown", "unknown")])
def test_the_relays_own_failed_and_unknown_ends_are_kept_as_they_are(w, relay_state, expected):
    job = w.go()
    w.manager.tick()
    w.relay.jobs[job["id"]].update(state=relay_state, error_class="agent_crashed")
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == expected and done["error_class"] == "agent_crashed" and not [r for r in w.notes() if r["author_kind"] == "agent"]


# ── stop ─────────────────────────────────────────────────────────────────────────

def test_a_stop_the_relay_confirmed_ends_the_job_as_stopped_and_says_what_that_means(w):
    job = w.go()
    w.manager.tick()
    got, outcome = w.manager.stop(w.cid, w.principal, job["id"])
    assert outcome == "confirmed" and got["state"] == "stopped" and got["stop_outcome"] == "confirmed" and w.relay.stops == [job["id"]]
    message = w.manager.view(got)["message"]
    assert message.startswith("Stopped: the relay confirmed") and "may still be running" in message
    w.manager.tick()
    assert state(w, job) == "stopped" and not [r for r in w.notes() if r["author_kind"] == "agent"]


def test_a_stop_the_relay_only_acknowledged_is_requested_not_confirmed_until_it_is(w):
    job = w.go()
    w.manager.tick()
    w.relay.stop_mode = "requested"
    got, outcome = w.manager.stop(w.cid, w.principal, job["id"])
    assert outcome == "requested" and got["state"] == "running"
    v = w.manager.view(got)
    assert v["stop_requested"] and v["stop_outcome"] is None and "has not confirmed" in v["message"] and v["can_stop"]
    w.relay.jobs[job["id"]]["state"] = "stopped"
    w.manager.tick()
    assert state(w, job) == "stopped" and w.get(job)["stop_outcome"] == "confirmed"


def test_a_stop_that_is_never_confirmed_ends_as_unknown_after_a_minute(w):
    job = w.go()
    w.manager.tick()
    w.relay.stop_mode = "unreachable"
    got, outcome = w.manager.stop(w.cid, w.principal, job["id"])
    assert outcome == "requested" and got["state"] == "running"
    w.relay.status_mode = "ok"
    w.clock.advance(70)
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "unknown" and done["error_class"] == "stop_unconfirmed"


def test_a_stop_for_a_job_that_had_already_finished_takes_its_real_end(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "it finished just as I pressed stop")
    w.relay.stop_mode = "not_running"
    got, outcome = w.manager.stop(w.cid, w.principal, job["id"])
    assert outcome == "already_finished" and got["state"] == "completed"
    assert [r["text"] for r in w.notes() if r["author_kind"] == "agent"] == ["it finished just as I pressed stop"]


def test_stopping_a_job_that_is_still_waiting_starts_nothing_and_clears_its_files(w):
    d = _doc(w.client, w.token, w.cid, "files for later", "later.txt")
    first = w.go("first job")
    second = w.go("second job, with a file", doc_ids=[d])
    w.manager.tick()                                                              # first runs; second waits (one per conversation)
    assert state(w, first) == "running" and state(w, second) == "queued"
    assert os.path.isdir(job_inbox.inbox_path(w.folder, second["id"]))
    got, outcome = w.manager.stop(w.cid, w.principal, second["id"])
    assert outcome == "stopped_before_start" and got["state"] == "stopped" and got["stop_outcome"] == "never_started"
    assert second["id"] not in {s["job_id"] for s in w.relay.starts} and not os.path.exists(job_inbox.inbox_path(w.folder, second["id"]))
    assert "before it started" in w.manager.view(got)["message"]


def test_stopping_while_the_start_is_still_unsettled_makes_a_late_start_refused(w):
    job = w.go()
    w.relay.start_mode = "ambiguous_lost"
    w.manager.tick()
    assert state(w, job) == "dispatching"
    got, outcome = w.manager.stop(w.cid, w.principal, job["id"])
    assert got["state"] == "not_started" and job["id"] in w.relay.tombstones
    w.relay.start_mode = "ok"
    kind, _detail = w.manager.relay.start({"job_id": job["id"], "agent": "mc-agent", "task": "late", "inbox": None, "deadline_s": 1})
    assert kind == "refused" and w.relay.starts == []                             # the late start cannot run


def test_someone_elses_stop_or_a_made_up_id_changes_nothing(w):
    job = w.go()
    w.manager.tick()
    assert w.manager.stop(w.cid, "someone-else", job["id"]) == (None, "not_found")
    assert w.manager.stop(w.cid, w.principal, "j-ffffffffffffffff") == (None, "not_found")
    assert w.manager.stop("../../x", w.principal, job["id"]) == (None, "not_found")
    assert w.relay.stops == [] and state(w, job) == "running"


def test_stopping_a_finished_job_says_so(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "done")
    w.manager.tick()
    got, outcome = w.manager.stop(w.cid, w.principal, job["id"])
    assert outcome == "already_finished" and got["state"] == "completed" and w.relay.stops == []


# ── bounds ───────────────────────────────────────────────────────────────────────

def test_one_running_job_per_conversation_the_second_waits(w):
    a, b = w.go("job a"), w.go("job b")
    w.manager.tick()
    assert (state(w, a), state(w, b)) == ("running", "queued") and len(w.relay.starts) == 1
    w.relay.finish(a["id"], "a done")
    w.manager.tick()
    assert state(w, a) == "completed" and state(w, b) == "running" and len(w.relay.starts) == 2


def test_two_running_overall_the_third_conversation_waits(w):
    others = [_new(w.client, w.token) for _ in range(2)]
    a = w.go("job a")
    b = w.go("job b", cid=others[0])
    c = w.go("job c", cid=others[1])
    w.manager.tick()
    assert sorted([state(w, a), state(w, b), state(w, c)]) == ["queued", "running", "running"]


def test_the_fourth_waiting_job_is_refused_with_a_plain_reason(w):
    cids = [_new(w.client, w.token) for _ in range(6)]
    running = [w.go(f"r{i}", cid=cids[i]) for i in range(2)]
    w.manager.tick()
    waiting = [w.go(f"w{i}", cid=cids[2 + i]) for i in range(3)]
    job, refusal, _n = w.start("one too many", cid=cids[5])
    assert job is None and refusal[0] == "queue_full" and "Nothing was started" in refusal[1]
    assert [state(w, j) for j in waiting] == ["queued"] * 3 and len(w.relay.starts) == 2


def test_a_job_that_passes_its_30_minute_deadline_is_asked_to_stop_and_never_claimed_to_have_stopped(w):
    job = w.go()
    w.manager.tick()
    w.relay.stop_mode = "requested"
    w.clock.advance(31 * 60)
    w.manager.tick()
    got = w.get(job)
    assert got["state"] == "running" and got["stop_requested_at"] and got["stop_reason"] == "deadline" and w.relay.stops == [job["id"]]
    w.clock.advance(61)
    w.manager.tick()
    assert w.get(job)["state"] == "unknown"                                       # the relay never confirmed: honest, not "stopped"


def test_the_old_shop_floor_thread_does_not_run_jobs(w):
    n = w.note()
    job, refusal = w.manager.start({"id": "shop-floor-thread"}, w.principal, n, n["request_id"])
    assert job is None and refusal[0] == "not_a_conversation"


def test_a_relay_that_is_not_connected_refuses_before_anything_is_created(w):
    w.manager.relay = JobRelay("", "", http_get=w.relay.get, http_post=w.relay.post)
    job, refusal, _n = w.start()
    assert job is None and refusal[0] == "not_connected" and w.manager.store.list(w.cid) == []


# ── losing track of a job ────────────────────────────────────────────────────────

def test_a_relay_that_goes_quiet_is_waited_for_then_the_outcome_is_unknown(w):
    job = w.go()
    w.manager.tick()
    w.relay.status_mode = "down"
    w.clock.advance(200)
    w.manager.tick()
    assert state(w, job) == "running"                                             # not yet: it may come back
    w.relay.status_mode = "ok"
    w.manager.tick()
    assert w.get(job)["unreachable_since"] is None                                # it came back: the clock starts again
    w.relay.status_mode = "down"
    w.manager.tick()
    w.clock.advance(310)
    w.manager.tick()
    done = w.get(job)
    assert done["state"] == "unknown" and done["error_class"] == "relay_unreachable"


def test_a_relay_that_has_forgotten_a_running_job_makes_it_unknown(w):
    job = w.go()
    w.manager.tick()
    w.relay.forget(job["id"])
    w.manager.tick()
    assert w.get(job)["state"] == "unknown" and w.get(job)["error_class"] == "relay_lost_job"


def test_a_garbled_status_is_treated_as_no_answer_not_as_a_result(w):
    job = w.go()
    w.manager.tick()
    w.relay.status_mode = "garbage"
    w.manager.tick()
    assert state(w, job) == "running" and w.get(job)["unreachable_since"]


# ── the files a job is given ─────────────────────────────────────────────────────

def test_a_job_gets_private_verified_copies_of_the_files_it_was_asked_to_read(w):
    d = _doc(w.client, w.token, w.cid, "resume text", "resume.txt")
    job = w.go("compare this", doc_ids=[d])
    w.manager.tick()
    [sent] = w.relay.starts
    [entry] = sent["inbox"]["files"]
    inbox = Path(job_inbox.inbox_path(w.folder, job["id"]))
    assert (inbox / entry["name"]).read_text() == "resume text" and sent["inbox"]["job_id"] == job["id"]
    assert [f["name"] for f in w.manager.view(w.get(job))["files"]] == ["resume.txt"]
    r = w.client.post(f"{API}/conversations/{w.cid}/documents/remove", json=keyed(document_id=d), headers=write_headers(w.token))
    assert r.status_code == 200 and (inbox / entry["name"]).exists()              # Remove does not reach a job's copy


def test_a_file_without_an_original_refuses_the_job_and_creates_nothing(w):
    d = _doc(w.client, w.token, w.cid, "text", "t.txt")
    (Path(w.convs.dir) / "docs" / w.cid / f"{d}.orig").unlink()
    job, refusal, _n = w.start(doc_ids=[d])
    assert job is None and refusal[0] == "no_original" and "Nothing was started" in refusal[1] and w.manager.store.list(w.cid) == []


def test_a_finished_jobs_inbox_is_cleared_after_a_week_but_a_running_ones_is_kept(w):
    d = _doc(w.client, w.token, w.cid, "text", "t.txt")
    done = w.go("one", doc_ids=[d])
    w.manager.tick()
    w.relay.finish(done["id"], "ok")
    w.manager.tick()
    live = w.go("two", doc_ids=[d])
    w.manager.tick()
    for j in (done, live):
        os.utime(job_inbox.inbox_path(w.folder, j["id"]), (w.clock() - 8 * 86400,) * 2)
    w.clock.advance(0)
    w.manager.tick()
    assert not os.path.exists(job_inbox.inbox_path(w.folder, done["id"])) and os.path.exists(job_inbox.inbox_path(w.folder, live["id"]))


# ── what the owner sees ──────────────────────────────────────────────────────────

def test_the_view_says_each_end_in_its_own_honest_words_and_never_claims_what_was_not_checked(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "wrote a file", tools=[{"name": "write", "target": "_working/mc-handoff/a.md", "ok": True}],
                   writes=[{"path": "_working/mc-handoff/a.md", "exists": True, "bytes": 12, "sha256": "a" * 64, "verified": True}])
    w.manager.tick()
    v = w.manager.view(w.get(job))
    assert v["state"] == "completed" and v["writes_verified"] is True and "relay checked the files it recognised" in v["message"]
    assert v["enforced"] is False and "instructions to it, not enforced" in v["audit_note"] and "not a complete record" in v["audit_note"]
    assert v["tools"][0]["name"] == "write" and v["writes"][0]["sha256"] == "a" * 64


def test_unverified_or_absent_file_claims_are_not_called_verified(w):
    one = w.go("one")
    w.manager.tick()
    w.relay.finish(one["id"], "ok", writes=[{"path": "x", "exists": True, "bytes": 1, "sha256": "", "verified": False}])
    w.manager.tick()
    v = w.manager.view(w.get(one))
    assert v["writes_verified"] is False and "did not confirm" in v["message"]
    two = w.go("two")
    w.manager.tick()
    w.relay.finish(two["id"], "ok")
    w.manager.tick()
    v = w.manager.view(w.get(two))
    assert v["writes_verified"] is None and "recognised no file writes" in v["message"] and "in ways it cannot see" in v["message"]


def test_the_view_never_carries_the_relays_internals_or_the_owners_note_text(w):
    job = w.go("Compare resumes. " + "filler " * 20 + "SECRETTAIL beyond the title")
    w.manager.tick()
    v = json.dumps(w.manager.view(w.get(job)))
    for banned in ("tok", "Bearer", "relay/v1", "SECRETTAIL", "task_sha256", '"principal"', '"note_id"'):
        assert banned not in v, banned


def test_jobs_of_other_conversations_and_owners_are_not_listed(w):
    other = _new(w.client, w.token)
    w.go("mine")
    w.go("theirs", cid=other)
    assert [v["title"] for v in w.manager.views(w.cid, w.principal)] == ["mine"]
    assert w.manager.views(w.cid, "someone-else") == []


def test_a_job_file_is_private_and_holds_no_secret_or_note_text(w):
    job = w.go("Compare resumes. " + "filler " * 20 + "SECRETTAIL beyond the title")
    path = Path(w.folder) / "jobs" / w.cid / f"{job['id']}.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct(path.parent.stat().st_mode & 0o777) == "0o700"
    text = path.read_text()
    assert "SECRETTAIL" not in text and '"tok"' not in text and "Bearer" not in text and "relay/v1" not in text
    w.manager.tick()
    w.relay.finish(job["id"], "a result")
    w.manager.tick()
    result = Path(w.folder) / "jobs" / w.cid / f"{job['id']}.result.txt"
    assert result.read_text() == "a result" and oct(result.stat().st_mode & 0o777) == "0o600"


def test_a_500_from_the_relay_on_start_says_nothing_either_way_so_the_job_is_followed_not_failed_or_resent(w):
    job = w.go()
    w.relay.start_mode = "http500"
    w.manager.tick()
    assert state(w, job) == "dispatching"                                          # not "not_started", not "failed"
    w.relay.start_mode = "ok"
    w.manager.tick()
    assert state(w, job) == "running" and len(w.relay.starts) == 1


def test_a_result_the_note_store_will_not_accept_is_never_called_completed(w, monkeypatch):
    from minimoi_portal.guild_ui.stores import WriteResult
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "an answer")
    floor = w.manager._floor(w.get(job))
    monkeypatch.setattr(type(floor), "add_note", lambda self, *a, **kw: WriteResult("idempotency_mismatch", {"request_id": "someone-elses"}, True))
    w.manager.tick()
    got = w.get(job)
    assert got["state"] == "running" and got["result_pending"] == "an answer" and got["result_note_id"] is None


@pytest.mark.parametrize("old,new", [("completed", "running"), ("failed", "running"), ("stopped", "completed"), ("unknown", "running"),
                                      ("not_started", "dispatching"), ("running", "queued"), ("running", "dispatching"), ("queued", "running"),
                                      ("queued", "completed")])
def test_the_lifecycle_refuses_every_move_it_does_not_allow(old, new):
    with pytest.raises(J.BadTransition):
        J._set_state({"state": old}, new, 1.0)


@pytest.mark.parametrize("old,new", [("queued", "dispatching"), ("dispatching", "running"), ("dispatching", "queued"), ("running", "completed"),
                                      ("running", "stopped"), ("dispatching", "not_started"), ("running", "unknown")])
def test_the_lifecycle_allows_exactly_its_moves(old, new):
    job = {"state": old}
    J._set_state(job, new, 5.0)
    assert job["state"] == new and job["t_" + new] == 5.0 and (("finished_at" in job) == (new in J.TERMINAL))


def test_a_note_store_that_is_briefly_down_puts_the_job_back_instead_of_ending_it(w, monkeypatch):
    job = w.go()
    real = w.manager._note

    def down(j):
        raise RuntimeError("floor store down")
    monkeypatch.setattr(w.manager, "_note", down)
    w.manager.tick()
    assert state(w, job) == "queued" and w.relay.starts == []                    # nothing was sent, nothing was lost
    monkeypatch.setattr(w.manager, "_note", real)
    w.manager.tick()
    assert state(w, job) == "running" and len(w.relay.starts) == 1


def test_calls_the_relay_flagged_are_kept_marked_and_counted_in_the_words_of_the_end(w):
    job = w.go()
    w.manager.tick()
    tools = [{"name": "read", "target": "a.md", "ok": True}, {"name": "read", "target": "~/.ssh/id_rsa", "ok": True, "flag": "touches_restricted_area"},
             {"name": "write", "target": "/src/x.py", "ok": True, "flag": "write_outside_allowed_area"}, {"name": "exec", "target": "ls", "ok": True, "flag": "invented"}]
    w.relay.finish(job["id"], "done", tools=tools)
    w.manager.tick()
    v = w.manager.view(w.get(job))
    assert [t.get("flag") for t in v["tools"]] == [None, "touches_restricted_area", "write_outside_allowed_area", None]       # an unknown flag is dropped
    assert v["flagged"] == 2 and "2 tool calls touched an area the job rules forbid" in v["message"]


def test_an_audit_the_relay_could_not_read_is_said_to_be_missing_not_empty(w):
    job = w.go()
    w.manager.tick()
    w.relay.finish(job["id"], "done")
    real = w.relay.jobs[job["id"]]
    w.relay.jobs[job["id"]] = real
    w.relay.audit_available = False
    w.manager.tick()
    v = w.manager.view(w.get(job))
    assert v["audit_available"] is False and "run record could not be read" in v["message"]
