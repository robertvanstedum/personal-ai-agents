"""The conversation lane may PROPOSE a job by starting its reply with "JOB: <title>"; the server decides (amendment B1).
The model's words never become the task, the marker is never shown, a refusal is said plainly, and with jobs off a reply
is kept exactly as written. A scripted chat relay plus the fake job relay; no model, no network."""
from __future__ import annotations

import json
import uuid

import pytest

from fake_job_relay import FakeJobRelay
from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_streaming import StreamRuntime, _events, _stream, _wait_idle, nd  # noqa: F401
from test_mc_turns import API, _keep, _openclaw, _turn_on
from test_mc_upload_reading import _doc, _new

from minimoi_portal.guild_ui.jobs import interpret  # noqa: F401
from minimoi_portal.guild_ui.jobs_api import ANNOUNCE, JobHook
from minimoi_portal.guild_ui.jobs_wiring import attach_jobs

NAME = "guild_ui_next"


class Runtime(StreamRuntime):
    def __init__(self):
        super().__init__()
        self.jobs = FakeJobRelay()
        self.nonstream_text = None                    # when set, a non-streaming chat turn answers with this text

    def get(self, url, **kw):
        return self.jobs.get(url, **kw) if "/jobs" in url else super().get(url, **kw)

    def post(self, url, data=None, headers=None, stream=False, **kw):
        if "/jobs" in url:
            return self.jobs.post(url, data=data, headers=headers, **kw)
        if not stream and self.nonstream_text is not None and url.endswith("/chat/completions"):
            from test_mc_turns import Resp
            return Resp(200, json.dumps({"choices": [{"message": {"content": self.nonstream_text}, "finish_reason": "stop"}]}))
        return super().post(url, data=data, headers=headers, stream=stream, **kw)


def _rig(floored, tmp_path, monkeypatch, *, jobs_on=True):
    monkeypatch.setenv("MINIMOI_USAGE_DIR", str(tmp_path / "usage"))
    (tmp_path / "usage").mkdir()
    runtime = Runtime()
    services = _turn_on(floored, _openclaw(runtime))
    services.mc_stream = True
    floored.extra.update(runtime=runtime, services=services, usage=tmp_path / "usage")
    if jobs_on:
        floored.extra["manager"] = attach_jobs(floored.app, NAME, services, environ={"MINIMOI_GUILD_JOBS": "1", "MINIMOI_GUILD_JOBS_TICKER": "0"})
    return floored


@pytest.fixture
def lane(floored, tmp_path, monkeypatch):
    return _rig(floored, tmp_path, monkeypatch)


@pytest.fixture
def lane_off(floored, tmp_path, monkeypatch):
    return _rig(floored, tmp_path, monkeypatch, jobs_on=False)


def _reply(portal, *chunks, reason="stop"):
    portal.extra["runtime"].script = [nd({"t": "delta", "text": c}) for c in chunks] + [nd({"t": "finish", "reason": reason}),
                                                                                         nd({"t": "usage", "prompt_tokens": 5, "completion_tokens": 3})]


def _ask(portal, text="Compare my new resume to my old resume and write the differences into a file.", **extra):
    client = portal.owner()
    token = portal.csrf(client)
    cid = extra.pop("cid", None) or _new(client, token)
    note = _keep(client, token, text, conversation_id=cid)
    resp = _stream(client, token, note["request_id"], request_id=uuid.uuid4().hex, conversation_id=cid, **extra)
    events = _events(resp)
    _wait_idle()
    return client, token, cid, note, events


def _shown(events):
    return "".join(e["text"] for e in events if e["t"] == "delta")


# ── the proposal ─────────────────────────────────────────────────────────────────

def test_a_job_proposal_starts_a_job_from_the_owners_note_and_the_marker_is_never_shown(lane):
    _reply(lane, "J", "OB: Compare the resumes\\nI'll".replace("\\n", "\n"), " run that in the background.")
    client, token, cid, note, events = _ask(lane)
    done = events[-1]
    assert done["t"] == "done" and done["status"] == "answered" and done["reply_note"]["text"] == "I'll run that in the background."
    assert "JOB" not in _shown(events) and "JOB" not in json.dumps([e for e in events if e["t"] == "render"])        # no marker, ever, in the browser stream
    job = done["job"]
    assert job["state"] == "queued" and job["title"] == "Compare the resumes" and job["can_stop"]
    mgr = lane.extra["manager"]
    mgr.tick()
    [sent] = lane.extra["runtime"].jobs.starts
    assert sent["task"] == note["text"] and "I'll run that" not in sent["task"] and "Compare the resumes" not in sent["task"]    # the owner's words, not the model's
    assert [r["text"] for r in lane.extra["floor"].rows("floor_messages") if r["author_kind"] == "agent"] == ["I'll run that in the background."]
    import hashlib
    assert mgr.store.get(cid, job["id"])["task_sha256"] == hashlib.sha256(note["text"].encode()).hexdigest()        # the job records the owner's words


def test_the_proposal_works_with_the_reply_arriving_in_odd_pieces_and_a_single_line(lane):
    _reply(lane, "JOB:", " ", "Do it")                                         # one line, no newline, clean end
    client, token, cid, note, events = _ask(lane)
    done = events[-1]
    assert done["job"]["title"] == "Do it" and done["reply_note"]["text"] == ANNOUNCE and _shown(events) == ""


@pytest.mark.parametrize("chunks,reason", [
    (("Just a moment, let me think. ", "Here is the answer."), "stop"),                  # starts with J, then is not a proposal
    (("Sure.\nJOB: Compare\nmore",), "stop"),                                            # not the first line
    (("JOB: Compare\nI'll do it",), "length"),                                           # the reply was cut: not a cleanly ended stream
    (("job: compare\nlower case",), "stop"), ((" JOB: Compare\nspace first",), "stop"), (("JOB:Compare\nno space",), "stop"),
    (("JOB: " + "x" * 90 + "\nlong title",), "stop"), (("JOBS are fun",), "stop"), (("J",), "stop"),
])
def test_anything_that_is_not_exactly_a_proposal_is_an_ordinary_reply_shown_and_kept_whole(lane, chunks, reason):
    _reply(lane, *chunks, reason=reason)
    client, token, cid, note, events = _ask(lane)
    done = events[-1]
    assert done["t"] == "done" and "job" not in done and lane.extra["manager"].store.list(cid) == []
    whole = "".join(chunks)
    assert done["reply_note"]["text"] == whole                                                       # what is kept is everything the model wrote
    if not whole.startswith("JOB: ") or "\n" not in whole:
        assert _shown(events) == whole                                                               # and what streamed is the same, nothing withheld
    else:
        assert _shown(events) == whole.partition("\n")[2]                                            # only the marker line was held back while streaming


def test_one_job_per_turn_and_a_second_marker_line_is_just_text(lane):
    _reply(lane, "JOB: First\nI'll start.\nJOB: Second\nAnd another.")
    client, token, cid, note, events = _ask(lane)
    assert len(lane.extra["manager"].store.list(cid)) == 1 and events[-1]["job"]["title"] == "First"
    assert "JOB: Second" in events[-1]["reply_note"]["text"]


def test_a_refused_proposal_is_said_plainly_and_the_model_is_not_left_claiming_a_job_that_does_not_exist(lane):
    mgr = lane.extra["manager"]
    others = []
    client = lane.owner()
    token = lane.csrf(client)
    for i in range(2):                                                          # fill the two running places, then the three waiting ones
        c = _new(client, token)
        n = _keep(client, token, f"filler {i}", conversation_id=c)
        mgr.start(mgr.conversations.get(c, mgr.store.get.__self__ and json.loads(open(f"{mgr.conversations.dir}/{c}.json").read())["principal"]), json.loads(open(f"{mgr.conversations.dir}/{c}.json").read())["principal"], n, n["request_id"])
    mgr.tick()
    for i in range(3):
        c = _new(client, token)
        n = _keep(client, token, f"waiting {i}", conversation_id=c)
        principal = json.loads(open(f"{mgr.conversations.dir}/{c}.json").read())["principal"]
        mgr.start(mgr.conversations.get(c, principal), principal, n, n["request_id"])
    _reply(lane, "JOB: One too many\nI'll do that as a background job.")
    client, token, cid, note, events = _ask(lane)
    done = events[-1]
    assert done["t"] == "done" and "job" not in done and mgr.store.list(cid) == []
    text = done["reply_note"]["text"]
    assert text.startswith("I'll do that as a background job.") and "**Not started. Three jobs are already waiting." in text and "Nothing was started." in text


def test_documents_in_the_turn_go_to_the_job_as_private_copies_and_a_lost_original_refuses_it(lane):
    client = lane.owner()
    token = lane.csrf(client)
    cid = _new(client, token)
    d = _doc(client, token, cid, "old resume text", "old.txt")
    _reply(lane, "JOB: Compare\nOn it.")
    _c, _t, _cid, note, events = _ask(lane, "Compare this with the new one", cid=cid, attachment_ids=[d])
    job = events[-1]["job"]
    assert job["files"] == [{"name": "old.txt", "bytes": 15}]
    lane.extra["manager"].tick()
    assert lane.extra["runtime"].jobs.starts[0]["inbox"]["files"][0]["name"].endswith("old.txt")
    from pathlib import Path
    d2 = _doc(client, token, cid, "second", "second.txt")
    (Path(lane.extra["manager"].conversations.dir) / "docs" / cid / f"{d2}.orig").unlink()
    _reply(lane, "JOB: Compare again\nOn it.")
    _c, _t, _cid, note2, events2 = _ask(lane, "Another comparison", cid=cid, attachment_ids=[d2])
    assert "job" not in events2[-1] and "**Not started." in events2[-1]["reply_note"]["text"] and len(lane.extra["manager"].store.list(cid)) == 1


def test_asking_again_for_the_same_note_does_not_start_a_second_job(lane):
    _reply(lane, "JOB: Once\nOn it.")
    client, token, cid, note, events = _ask(lane)
    again = client.post(f"{API}/mc/turns/stream", json={"note_request_id": note["request_id"], "record_mode": "on_record", "request_id": uuid.uuid4().hex, "conversation_id": cid},
                        headers=write_headers(token))
    assert again.status_code == 200 and len(lane.extra["manager"].store.list(cid)) == 1


def test_the_non_streaming_turn_proposes_jobs_the_same_way(lane):
    lane.extra["runtime"].nonstream_text = "JOB: Compare resumes\nI'll run it in the background."
    lane.extra["services"].mc_stream = False
    client = lane.owner()
    token = lane.csrf(client)
    cid = _new(client, token)
    note = _keep(client, token, "Compare my resumes and write it down.", conversation_id=cid)
    r = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "record_mode": "on_record", "conversation_id": cid, "request_id": uuid.uuid4().hex},
                    headers=write_headers(token))
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "answered" and body["reply_note"]["text"] == "I'll run it in the background."
    assert body["job"]["title"] == "Compare resumes" and len(lane.extra["manager"].store.list(cid)) == 1


# ── jobs off: nothing changes ────────────────────────────────────────────────────

def test_with_jobs_off_a_reply_that_starts_with_the_marker_is_kept_and_shown_exactly_as_written(lane_off):
    _reply(lane_off, "J", "OB: Compare\nI'll do it.")
    client, token, cid, note, events = _ask(lane_off)
    done = events[-1]
    assert done["reply_note"]["text"] == "JOB: Compare\nI'll do it." and "job" not in done
    assert [e["text"] for e in events if e["t"] == "delta"] == ["J", "OB: Compare\nI'll do it."]            # no holding, no change at all


# ── the hook's own rules ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,holding", [("", True), ("J", True), ("JO", True), ("JOB", True), ("JOB:", True), ("JOB: ", True), ("JOB: Compare", True),
                                          ("JOB: Compare\n", False), ("Jx", False), ("Hello", False), ("JOB:x", False), ("job: x", False), ("JOB: " + "x" * 100, False)])
def test_the_browser_is_held_back_only_while_the_text_could_still_be_a_proposal_line(text, holding):
    assert JobHook.holding(text) is holding
