"""Master Craftsman jobs, the routes (overnight build, step 3): owner-only, off unless switched on, Private rules, the
spoken commands, downloads. A fake relay speaks the v2 job protocol; no model, no network."""
from __future__ import annotations

import io
import json
import uuid

import pytest

from fake_job_relay import FakeJobRelay
from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_streaming import StreamRuntime, _events, _stream, _wait_idle, streaming  # noqa: F401
from test_mc_upload_reading import _doc, _new

from minimoi_portal.guild_ui import jobs as J
from minimoi_portal.guild_ui.jobs_api import ANNOUNCE
from minimoi_portal.guild_ui.jobs_wiring import attach_jobs

NAME = "guild_ui_next"


class JobRuntime(FakeRuntime):
    """The chat fake, plus the job protocol on /jobs URLs."""

    def __init__(self):
        super().__init__()
        self.jobs = FakeJobRelay()

    def get(self, url, **kw):
        return self.jobs.get(url, **kw) if "/jobs" in url else super().get(url, **kw)

    def post(self, url, data=None, headers=None, **kw):
        return self.jobs.post(url, data=data, headers=headers, **kw) if "/jobs" in url else super().post(url, data=data, headers=headers, **kw)


@pytest.fixture
def jobbed(floored, tmp_path):
    runtime = JobRuntime()
    _turn_on(floored, _openclaw(runtime))
    floored.extra["runtime"] = runtime
    services = floored.app.extensions[NAME]["services"]
    manager = attach_jobs(floored.app, NAME, services, environ={"MINIMOI_GUILD_JOBS": "1", "MINIMOI_GUILD_JOBS_TICKER": "0"})
    floored.extra["manager"] = manager
    return floored


def _ask(client, token, note, mode="on_record", path="/mc/turns", **extra):
    body = {"note_request_id": note["request_id"], "record_mode": mode, "request_id": uuid.uuid4().hex, **extra}
    return client.post(f"{API}{path}", json=body, headers=write_headers(token, mode=mode))


def _start(client, token, note, cid, **extra):
    return _ask(client, token, note, path="/mc/jobs", conversation_id=cid, **extra)


def _rig(portal):
    client = portal.owner()
    token = portal.csrf(client)
    return client, token, _new(client, token), portal.extra["manager"], portal.extra["runtime"]


# ── off by default ───────────────────────────────────────────────────────────────

def test_with_the_switch_off_every_job_route_says_jobs_are_off_and_nothing_is_created(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    note = _keep(client, token, "Please run this as a job", conversation_id=cid)
    r = _start(client, token, note, cid)
    assert r.status_code == 409 and r.get_json()["error"] == "jobs_off"
    listed = client.get(f"{API}/conversations/{cid}/jobs").get_json()
    assert listed == {"enabled": False, "jobs": []}
    for method, path in (("post", f"{API}/conversations/{cid}/jobs/j-0123456789abcdef/stop"), ("get", f"{API}/conversations/{cid}/jobs/j-0123456789abcdef/result"),
                         ("get", f"{API}/conversations/{cid}/jobs/j-0123456789abcdef/note")):
        resp = getattr(client, method)(path, **({"json": {}, "headers": write_headers(token)} if method == "post" else {}))
        assert resp.status_code == 409 and resp.get_json()["error"] == "jobs_off", path
    assert "jobs" not in turned.app.extensions[NAME] and "jobs_ticker" not in turned.app.extensions[NAME]
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert '"jobs": {"enabled": false}' in page.replace('":{', '": {').replace('","', '", "') or '"jobs":{"enabled":false}' in page


def test_with_the_switch_off_a_spoken_job_command_is_just_a_message_for_the_model(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    note = _keep(client, token, "run this as a job", conversation_id=cid)
    r = _ask(client, token, note, conversation_id=cid)
    assert r.status_code == 200 and r.get_json()["status"] == "answered" and "job_command" not in r.get_json()
    assert len(turned.extra["runtime"].sent) == 1


def test_the_switch_reads_its_value_strictly():
    assert J.jobs_enabled({"MINIMOI_GUILD_JOBS": "1"}) and J.jobs_enabled({"MINIMOI_GUILD_JOBS": " On "})
    for v in ("", "0", "off", "false", "yes please", None):
        assert not J.jobs_enabled({"MINIMOI_GUILD_JOBS": v} if v is not None else {})


# ── starting, listing, stopping ─────────────────────────────────────────────────

def test_a_kept_note_starts_a_job_that_runs_and_posts_its_result_in_the_conversation(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "Compare my new resume to my old resume.", conversation_id=cid)
    r = _start(client, token, note, cid)
    body = r.get_json()
    assert r.status_code == 200 and body["result"] == "started" and body["message"] == ANNOUNCE
    job = body["job"]
    assert job["state"] == "queued" and job["can_stop"] and job["title"] == "Compare my new resume to my old resume"
    assert _start(client, token, note, cid).get_json()["result"] == "already_started"            # one note, one job
    mgr.tick()
    assert client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"][0]["state"] == "running"
    runtime.jobs.finish(job["id"], "Both resumes list the same role; the new one adds results.")
    mgr.tick()
    [done] = client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"]
    assert done["state"] == "completed" and not done["can_stop"] and done["result_note_id"]
    shown = client.get(f"{API}/conversations/{cid}/jobs/{job['id']}/note").get_json()["note"]
    assert shown["text"].startswith("Both resumes") and shown["who"] == "master_craftsman" and "html" in shown
    assert len(runtime.jobs.starts) == 1 and not [s for s in runtime.sent if "/jobs" in s["url"]]   # the chat path was never used


def test_a_job_does_not_block_a_normal_conversation_turn(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "A long task", conversation_id=cid)
    job = _start(client, token, note, cid).get_json()["job"]
    mgr.tick()
    chat = _keep(client, token, "What is stuck on #12?", conversation_id=cid)
    r = _ask(client, token, chat, conversation_id=cid)
    assert r.status_code == 200 and r.get_json()["status"] == "answered"
    assert mgr.store.get(cid, job["id"])["state"] == "running"


def test_private_cannot_start_a_job_and_nothing_is_created(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "Compare resumes", conversation_id=cid)
    r = _start(client, token, note, cid, mode="off_record") if False else client.post(
        f"{API}/mc/jobs", json={"note_request_id": note["request_id"], "record_mode": "off_record", "conversation_id": cid, "request_id": uuid.uuid4().hex},
        headers=write_headers(token, mode="off_record"))
    assert r.status_code == 409 and r.get_json()["error"] == "not_listening"
    assert mgr.store.list(cid) == [] and runtime.jobs.starts == []


def test_going_private_mid_job_does_not_stop_it_and_stop_still_works_off_the_record(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "A long task", conversation_id=cid)
    job = _start(client, token, note, cid).get_json()["job"]
    mgr.tick()
    runtime.jobs.finish(job["id"], "finished while the owner was off the record")
    mgr.tick()
    assert client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"][0]["state"] == "completed"   # the result still arrived
    second = _keep(client, token, "Another task", conversation_id=cid)
    j2 = _start(client, token, second, cid).get_json()["job"]
    mgr.tick()
    r = client.post(f"{API}/conversations/{cid}/jobs/{j2['id']}/stop", json={}, headers=write_headers(token, mode="off_record"))
    assert r.status_code == 200 and r.get_json()["result"] == "confirmed" and r.get_json()["job"]["state"] == "stopped"


def test_stop_needs_the_csrf_token_and_belongs_to_its_owner_and_conversation(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "A long task", conversation_id=cid)
    job = _start(client, token, note, cid).get_json()["job"]
    mgr.tick()
    url = f"{API}/conversations/{cid}/jobs/{job['id']}/stop"
    assert client.post(url, json={}, headers={"X-Record-Mode": "on_record"}).status_code == 403          # no CSRF token
    other = _new(client, token)
    assert client.post(f"{API}/conversations/{other}/jobs/{job['id']}/stop", json={}, headers=write_headers(token)).status_code == 404
    assert client.post(f"{API}/conversations/{cid}/jobs/j-ffffffffffffffff/stop", json={}, headers=write_headers(token)).status_code == 404
    assert client.post(f"{API}/conversations/{cid}/jobs/..%2f..%2fx/stop", json={}, headers=write_headers(token)).status_code == 404
    assert runtime.jobs.stops == [] and mgr.store.get(cid, job["id"])["state"] == "running"
    ok = client.post(url, json={}, headers=write_headers(token))
    assert ok.status_code == 200 and ok.get_json()["result"] == "confirmed" and ok.get_json()["message"].startswith("Stopped: the relay confirmed")


def test_nobody_but_the_owner_can_use_any_job_route(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "A long task", conversation_id=cid)
    job = _start(client, token, note, cid).get_json()["job"]
    mgr.tick()
    paths = [("get", f"{API}/conversations/{cid}/jobs"), ("get", f"{API}/conversations/{cid}/jobs/{job['id']}/result"),
             ("get", f"{API}/conversations/{cid}/jobs/{job['id']}/note"), ("post", f"{API}/conversations/{cid}/jobs/{job['id']}/stop"),
             ("post", f"{API}/mc/jobs")]
    for who in (jobbed.client(), jobbed.guest()):
        for method, path in paths:
            r = getattr(who, method)(path, **({"json": {}} if method == "post" else {}))
            assert r.status_code in (302, 401, 403), (path, r.status_code)
    assert mgr.store.get(cid, job["id"])["state"] == "running"


def test_the_old_shop_floor_thread_and_an_unconnected_relay_refuse_plainly(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "A task")
    r = client.post(f"{API}/mc/jobs", json={"note_request_id": note["request_id"], "record_mode": "on_record", "request_id": uuid.uuid4().hex},
                    headers=write_headers(token))
    assert r.status_code == 422 and r.get_json()["error"] == "not_a_conversation"
    mgr.relay.url = ""
    note2 = _keep(client, token, "Another task", conversation_id=cid)
    r = _start(client, token, note2, cid)
    assert r.status_code == 503 and r.get_json()["error"] == "not_connected" and mgr.store.list(cid) == []


def test_a_fourth_waiting_job_is_refused_with_its_reason_in_the_answer(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    cids = [_new(client, token) for _ in range(5)]
    for i in range(2):
        assert _start(client, token, _keep(client, token, f"r{i}", conversation_id=cids[i]), cids[i]).status_code == 200
    mgr.tick()
    for i in range(3):
        assert _start(client, token, _keep(client, token, f"w{i}", conversation_id=cids[2 + i]), cids[2 + i]).status_code == 200
    r = _start(client, token, _keep(client, token, "too many", conversation_id=cid), cid)
    assert r.status_code == 409 and r.get_json()["error"] == "queue_full" and "Nothing was started" in r.get_json()["message"]


# ── files ────────────────────────────────────────────────────────────────────────

def test_attached_documents_reach_the_job_as_private_copies_and_a_missing_original_refuses(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    d = _doc(client, token, cid, "old resume text", "old.txt")
    note = _keep(client, token, "Compare this to the new one", conversation_id=cid)
    r = _start(client, token, note, cid, attachment_ids=[d])
    assert r.status_code == 200 and r.get_json()["job"]["files"] == [{"name": "old.txt", "bytes": 15}]
    mgr.tick()
    [sent] = runtime.jobs.starts
    assert sent["inbox"]["files"][0]["name"].endswith("old.txt") and sent["inbox"]["job_id"] == r.get_json()["job"]["id"]
    d2 = _doc(client, token, cid, "second", "second.txt")
    from pathlib import Path
    from minimoi_portal.guild_ui.conversations import conversations_of
    convs = conversations_of(jobbed.app.extensions[NAME]["services"])
    (Path(convs.dir) / "docs" / cid / f"{d2}.orig").unlink()
    bad = _start(client, token, _keep(client, token, "with a lost file", conversation_id=cid), cid, attachment_ids=[d2])
    assert bad.status_code == 422 and bad.get_json()["error"] == "no_original" and len(mgr.store.list(cid)) == 1
    other = _new(client, token)
    wrong = _start(client, token, _keep(client, token, "wrong conversation", conversation_id=other), other, attachment_ids=[d])
    assert wrong.status_code in (404, 409, 422) and mgr.store.list(other) == []


# ── the spoken commands ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/mc/turns", "/mc/turns/stream"])
def test_run_this_as_a_job_starts_one_without_asking_a_model_and_stop_stops_it(jobbed, path):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "Compare my resumes. Run this as a job.", conversation_id=cid)
    r = _ask(client, token, note, path=path, conversation_id=cid)
    body = r.get_json()
    assert r.status_code == 200 and body["job_command"] == "run_job" and body["message"] == ANNOUNCE and body["job"]["state"] == "queued"
    assert runtime.sent == []                                                       # no model was asked, no chat turn was sent
    mgr.tick()
    stop = _keep(client, token, "stop", conversation_id=cid)
    r = _ask(client, token, stop, path=path, conversation_id=cid)
    body = r.get_json()
    assert r.status_code == 200 and body["job_command"] == "stop" and body["job"]["state"] == "stopped" and runtime.sent == []
    assert runtime.jobs.stops == [body["job"]["id"]]


@pytest.mark.parametrize("text", ["stop", "Stop.", "stop the job", "STOP IT!"])
def test_stop_with_no_job_running_is_an_ordinary_message(jobbed, text):
    client, token, cid, mgr, runtime = _rig(jobbed)
    r = _ask(client, token, _keep(client, token, text, conversation_id=cid), conversation_id=cid)
    assert r.status_code == 200 and r.get_json()["status"] == "answered" and "job_command" not in r.get_json()
    assert runtime.jobs.stops == []


def test_a_message_that_only_mentions_stopping_or_jobs_is_not_a_command(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "A long task", conversation_id=cid)
    _start(client, token, note, cid)
    mgr.tick()
    for text in ("please stop using jargon", "should I run this as a job or not?? no", "what is a job?"):
        r = _ask(client, token, _keep(client, token, text, conversation_id=cid), conversation_id=cid)
        body = r.get_json()
        assert body.get("job_command") != "stop", text
    assert runtime.jobs.stops == []


# ── the whole result, as a download ──────────────────────────────────────────────

def test_the_whole_result_downloads_as_a_safe_attachment_and_only_when_it_exists(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "Write the comparison", conversation_id=cid)
    job = _start(client, token, note, cid).get_json()["job"]
    mgr.tick()
    url = f"{API}/conversations/{cid}/jobs/{job['id']}/result"
    assert client.get(url).status_code == 404                                        # nothing yet
    body = "<script>alert(1)</script>\n" + "line\n" * 2000
    runtime.jobs.finish(job["id"], body)
    mgr.tick()
    r = client.get(url)
    assert r.status_code == 200 and r.get_data(as_text=True) == body
    assert r.headers["Content-Type"].startswith("text/plain") and r.headers["Content-Disposition"].startswith("attachment;")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "sandbox" in r.headers["Content-Security-Policy"]
    other = _new(client, token)
    assert client.get(f"{API}/conversations/{other}/jobs/{job['id']}/result").status_code == 404
    v = client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"][0]
    assert v["result_full"] is True and v["result_chars"] == len(body)


def test_the_job_list_carries_no_secret_and_no_relay_internals(jobbed):
    client, token, cid, mgr, runtime = _rig(jobbed)
    note = _keep(client, token, "Task with details " + "x" * 200, conversation_id=cid)
    job = _start(client, token, note, cid).get_json()["job"]
    mgr.tick()
    text = client.get(f"{API}/conversations/{cid}/jobs").get_data(as_text=True)
    from test_mc_turns import RELAY_TOKEN
    for banned in (RELAY_TOKEN, "Bearer", "mc-relay", '"principal"', "task_sha256"):
        assert banned not in text, banned


def test_a_job_whose_file_says_another_owner_is_invisible_to_every_route(jobbed):
    from pathlib import Path
    client, token, cid, mgr, runtime = _rig(jobbed)
    job = _start(client, token, _keep(client, token, "A task", conversation_id=cid), cid).get_json()["job"]
    mgr.tick()
    runtime.jobs.finish(job["id"], "an answer")
    mgr.tick()
    path = Path(mgr.store.root) / cid / f"{job['id']}.json"
    data = json.loads(path.read_text())
    data["principal"] = "someone-else"
    path.write_text(json.dumps(data))
    base = f"{API}/conversations/{cid}/jobs/{job['id']}"
    assert client.get(f"{base}/result").status_code == 404 and client.get(f"{base}/note").status_code == 404
    assert client.post(f"{base}/stop", json={}, headers=write_headers(token)).status_code == 404
    assert client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"] == []
