"""Master Craftsman's text-turn capture (memory build, v0.5.1 §8): one ``mc_turn``
line per answered, live turn, from the plain and the streaming endpoint, through
the writer CoS shares. Real portal, real OpenClaw adapter, scripted relay, synthetic
text only; the writer's own rules are in tests/agent_turns/test_writer.py."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401  (load_portal is a pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_streaming import (StreamRuntime, _events, _fresh_dispatch_state, _stream, _wait_idle,  # noqa: F401
                               nd, streaming)
from test_mc_turns import API, FakeRuntime, Resp, _ask, _keep, _openclaw, _trace, _turn_on, turned  # noqa: F401

from minimoi_portal.guild_ui.mc import turn_capture
from minimoi_portal.guild_ui.mc.stub import StubBackend
from minimoi_portal.guild_ui.mc.turn_log import TurnLog, done_text

FAKE_KEY = "sk-ant-FAKEFAKEFAKE12345"
STAMP_FILES = "*.jsonl"


@pytest.fixture
def mc_dir(tmp_path, monkeypatch):
    folder = tmp_path / "mc-turns"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv("MC_TURNS_DIR", str(folder))
    monkeypatch.setenv("MC_TURNS_MIN_FREE_BYTES", "1")
    monkeypatch.setenv("MC_CONTAINER_NAME", "portal-test")
    monkeypatch.setenv("COS_AGENT_TIMEZONE", "America/Chicago")
    return folder


def mc_lines(folder):
    folder = Path(folder)
    out = []
    for path in sorted(folder.rglob(STAMP_FILES)) if folder.is_dir() else []:
        out += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [l for l in out if l.get("record_type") == "mc_turn"]


def status_of(folder):
    path = Path(folder) / "_status" / "portal-test.json"
    return json.loads(path.read_text()) if path.exists() else None


def reload_reply(client, request_id):
    listed = client.get(f"{API}/notes").get_json()["notes"]
    return [n for n in listed if n["request_id"] == request_id][0]


# ── the happy path, on both endpoints ─────────────────────────────────────────

def test_a_plain_answered_turn_appends_one_mc_turn_line_and_says_saved(turned, mc_dir):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "What is stuck on #12?", context={"area": "Build Queue", "item_ref": 12, "page": "item"})
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["status"] == "answered" and body["history_saved"] is True
    [rec] = mc_lines(mc_dir)
    assert rec["record_type"] == "mc_turn" and rec["schema_version"] == 1 and rec["channel"] == "shop_floor"
    assert rec["turn_id"] == body["turn_id"] and rec["conversation_id"]
    assert rec["note_request_id"] == note["request_id"] and rec["note_id"] == note["id"]
    assert rec["reply_request_id"] == body["reply_note"]["request_id"] and rec["reply_note_id"] == body["reply_note"]["id"]
    assert rec["backend_type"] == "openclaw" and rec["backend_label"] == "Master Craftsman"
    assert rec["user_text"] == "What is stuck on #12?" and rec["reply"] == "Item 12 is in build."
    assert rec["sanitized"] is False and rec["time"].endswith("Z")
    assert "turn" not in rec and "usage" not in rec and "html" not in rec              # the record is not the note
    files = list(mc_dir.rglob("*.jsonl"))
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700
    assert json.loads(files[0].read_text().splitlines()[0]) == rec
    assert status_of(mc_dir)["container"] == "portal-test" and "last_success_at" in status_of(mc_dir)
    line = _trace(turned)[0]
    assert line["history_saved"] is True and "What is stuck" not in json.dumps(line)
    assert body["reply_note"]["turn"]["done_text"].startswith("Done in ") and "not saved" not in body["reply_note"]["turn"]["done_text"]


def test_a_streamed_answered_turn_appends_one_line_and_the_done_event_says_saved(streaming, mc_dir):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "What is in build?")
    events = _events(_stream(client, token, note["request_id"]))
    done = events[-1]
    assert done["t"] == "done" and done["status"] == "answered" and done["history_saved"] is True
    _wait_idle()
    [rec] = mc_lines(mc_dir)
    assert rec["record_type"] == "mc_turn" and rec["turn_id"] == done["turn_id"] == events[0]["turn_id"]
    assert rec["user_text"] == "What is in build?" and rec["reply"] == "Item **12** is in build."   # as kept
    assert rec["reply_request_id"] == done["reply_note"]["request_id"] and rec["note_request_id"] == note["request_id"]
    assert rec["backend_type"] == "openclaw"
    line = [l for l in _trace(streaming) if l["turn_id"] == done["turn_id"]][0]
    assert line["mode"] == "stream" and line["history_saved"] is True


# ── nothing meant to be written ───────────────────────────────────────────────

def test_without_mc_turns_dir_nothing_is_written_and_history_saved_is_absent(turned, tmp_path, monkeypatch):
    monkeypatch.delenv("MC_TURNS_DIR", raising=False)
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "unset dir")
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["status"] == "answered" and "history_saved" not in body
    assert "history_saved" not in _trace(turned)[0] and "not saved" not in body["reply_note"]["turn"]["done_text"]
    assert not list(tmp_path.rglob("*mc-turns*"))


def test_without_mc_turns_dir_the_streamed_turn_writes_nothing_either(streaming, monkeypatch):
    monkeypatch.delenv("MC_TURNS_DIR", raising=False)
    client = streaming.owner()
    token = streaming.csrf(client)
    done = _events(_stream(client, token, _keep(client, token, "q")["request_id"]))[-1]
    assert done["status"] == "answered" and "history_saved" not in done
    _wait_idle()
    assert "history_saved" not in [l for l in _trace(streaming) if l["turn_id"] == done["turn_id"]][0]


def test_the_stub_writes_nothing_on_either_endpoint(floored, mc_dir):
    client = floored.owner()
    token = floored.csrf(client)
    services = _turn_on(floored, StubBackend())
    services.mc_stream = True
    services.mc.stream_delay_s = 0
    plain = _ask(client, token, _keep(client, token, "q1")["request_id"]).get_json()
    assert plain["status"] == "answered" and plain["backend_kind"] == "stub" and "history_saved" not in plain
    done = _events(_stream(client, token, _keep(client, token, "q2")["request_id"]))[-1]
    assert done["status"] == "answered" and "history_saved" not in done
    _wait_idle()
    assert mc_lines(mc_dir) == [] and not list(mc_dir.rglob("*.jsonl")) and status_of(mc_dir) is None


def test_a_turn_that_did_not_answer_writes_nothing(floored, mc_dir):
    runtime = FakeRuntime(lambda body, headers: Resp(500, '{"error":{"message":"LLM request failed: 401 invalid api key"}}'))
    _turn_on(floored, _openclaw(runtime))
    client = floored.owner()
    token = floored.csrf(client)
    body = _ask(client, token, _keep(client, token, "hello")["request_id"]).get_json()
    assert body["status"] == "unavailable" and "history_saved" not in body
    assert mc_lines(mc_dir) == [] and "history_saved" not in _trace(floored)[0]


def test_an_interrupted_stream_writes_nothing(streaming, mc_dir):
    streaming.extra["runtime"].script = [nd({"t": "delta", "text": "no finish"})]
    client = streaming.owner()
    token = streaming.csrf(client)
    end = _events(_stream(client, token, _keep(client, token, "q")["request_id"]))[-1]
    assert end["t"] == "error" and "history_saved" not in end
    _wait_idle()
    assert mc_lines(mc_dir) == [] and status_of(mc_dir) is None
    assert "history_saved" not in [l for l in _trace(streaming) if l["turn_id"] == end["turn_id"]][0]


def test_a_reply_that_was_not_kept_writes_nothing(turned, mc_dir, monkeypatch):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "q")
    store = turned.app.extensions["guild_ui_next"]["services"].floor
    real = type(store).add_note

    class NotKept:
        outcome, value = "store_failed", None
    monkeypatch.setattr(type(store), "add_note", lambda self, request_id, *a, **k: NotKept
                        if request_id.startswith("mc-") else real(self, request_id, *a, **k))
    body = _ask(client, token, note["request_id"]).get_json()
    assert body["failure_class"] == "not_kept" and "history_saved" not in body
    assert mc_lines(mc_dir) == []


def test_off_the_record_writes_no_mc_turn_line_on_either_endpoint(streaming, mc_dir):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "on the record")
    plain = _ask(client, token, note["request_id"], mode="off_record")
    assert plain.status_code == 409 and plain.get_json()["error"] == "not_listening"
    assert "history_saved" not in plain.get_json()
    streamed = _stream(client, token, note["request_id"], mode="off_record")
    assert streamed.status_code == 409 and streamed.get_json()["error"] == "not_listening"
    assert streaming.extra["runtime"].posts == []
    assert mc_lines(mc_dir) == [] and not list(mc_dir.rglob("*.jsonl")) and status_of(mc_dir) is None


# ── once per turn ─────────────────────────────────────────────────────────────

def test_a_repeated_plain_request_writes_nothing_new(turned, mc_dir):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "once")
    first = _ask(client, token, note["request_id"]).get_json()
    again = _ask(client, token, note["request_id"]).get_json()
    assert first["history_saved"] is True and again["repeated"] is True and "history_saved" not in again
    assert len(mc_lines(mc_dir)) == 1 and len(turned.extra["runtime"].sent) == 1


def test_a_repeated_idempotency_key_writes_nothing_new(turned, mc_dir):
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "same key")
    r1 = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "request_id": "rq-" + "b" * 20,
                                              "record_mode": "on_record"},
                     headers=write_headers(token))
    r2 = client.post(f"{API}/mc/turns", json={"note_request_id": note["request_id"], "request_id": "rq-" + "b" * 20,
                                              "record_mode": "on_record"},
                     headers=write_headers(token))
    assert r1.get_json()["history_saved"] is True and len(mc_lines(mc_dir)) == 1
    assert r2.get_json()["repeated"] is True and "history_saved" not in r2.get_json()
    assert len(mc_lines(mc_dir)) == 1 and len(turned.extra["runtime"].sent) == 1


def test_a_repeated_or_replayed_stream_writes_nothing_new(streaming, mc_dir):
    client = streaming.owner()
    token = streaming.csrf(client)
    note = _keep(client, token, "stream once")
    done = _events(_stream(client, token, note["request_id"]))[-1]
    _wait_idle()
    assert done["history_saved"] is True and len(mc_lines(mc_dir)) == 1
    same_key = _stream(client, token, note["request_id"])               # the same request id
    other_key = _stream(client, token, note["request_id"], request_id="req-" + "z" * 20)   # a reply already exists
    plain = _ask(client, token, note["request_id"]).get_json()
    assert same_key.get_json()["repeated"] is True and "history_saved" not in same_key.get_json()
    assert other_key.get_json()["repeated"] is True and "history_saved" not in other_key.get_json()
    assert plain["repeated"] is True and "history_saved" not in plain
    assert len(mc_lines(mc_dir)) == 1 and len(streaming.extra["runtime"].posts) == 1


def test_one_streamed_and_one_plain_turn_make_two_lines(streaming, mc_dir):
    client = streaming.owner()
    token = streaming.csrf(client)
    _events(_stream(client, token, _keep(client, token, "first")["request_id"]))
    _wait_idle()
    plain = _ask(client, token, _keep(client, token, "second")["request_id"]).get_json()
    assert plain["history_saved"] is True
    assert [r["user_text"] for r in mc_lines(mc_dir)] == ["first", "second"]


# ── never fatal (R6, T7-style): the answer is the answer ──────────────────────

def _break_unwritable(monkeypatch, tmp_path):
    (tmp_path / "a-file").write_text("a file where a folder should be")
    monkeypatch.setenv("MC_TURNS_DIR", str(tmp_path / "a-file" / "mc-turns"))


def _break_disk_low(monkeypatch, tmp_path):
    monkeypatch.setenv("MC_TURNS_MIN_FREE_BYTES", str(10 ** 18))


def _break_internal(monkeypatch, tmp_path):
    def boom(**kw):
        raise ValueError("SECRET-DETAIL-xyz")
    monkeypatch.setattr(turn_capture, "_build_record", boom)


def _break_the_hook(monkeypatch, tmp_path):
    def boom(**kw):
        raise RuntimeError("SECRET-DETAIL-xyz")
    monkeypatch.setattr(turn_capture, "capture_turn", boom)


BREAKS = [_break_unwritable, _break_disk_low, _break_internal, _break_the_hook]


@pytest.mark.parametrize("breaker", BREAKS, ids=lambda f: f.__name__)
def test_a_capture_failure_never_changes_a_plain_answer_and_says_not_saved(turned, mc_dir, tmp_path, monkeypatch, breaker,
                                                                         capfd):
    client = turned.owner()
    token = turned.csrf(client)
    baseline = _ask(client, token, _keep(client, token, "baseline")["request_id"])
    assert baseline.get_json()["history_saved"] is True
    breaker(monkeypatch, tmp_path)
    note = _keep(client, token, "now it fails")
    r = _ask(client, token, note["request_id"])
    body = r.get_json()
    assert r.status_code == baseline.status_code == 200
    assert body["status"] == "answered" and body["reply_note"]["text"] == "Item 12 is in build."
    assert body["message"] == baseline.get_json()["message"] and body["mc_state"] == "live"
    assert body["history_saved"] is False
    assert body["reply_note"]["turn"]["done_text"].endswith("· not saved")
    assert any(n["request_id"] == body["reply_note"]["request_id"] for n in client.get(f"{API}/notes").get_json()["notes"])
    assert _trace(turned)[-1]["history_saved"] is False
    assert reload_reply(client, body["reply_note"]["request_id"])["turn"]["done_text"].endswith("· not saved")
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "· not saved</span>" in page
    assert len(mc_lines(mc_dir)) <= 1 and "SECRET-DETAIL" not in json.dumps(body) + capfd.readouterr().out


@pytest.mark.parametrize("breaker", BREAKS, ids=lambda f: f.__name__)
def test_a_capture_failure_never_changes_a_streamed_answer_and_says_not_saved(streaming, mc_dir, tmp_path, monkeypatch,
                                                                            breaker, capfd):
    client = streaming.owner()
    token = streaming.csrf(client)
    breaker(monkeypatch, tmp_path)
    note = _keep(client, token, "stream, then fail")
    events = _events(_stream(client, token, note["request_id"]))
    done = events[-1]
    assert done["t"] == "done" and done["status"] == "answered" and done["history_saved"] is False
    assert done["reply_note"]["text"] == "Item **12** is in build." and done["mc_state"] == "live"
    assert done["reply_note"]["turn"]["done_text"].endswith("· not saved")
    _wait_idle()
    assert [l for l in _trace(streaming) if l["turn_id"] == done["turn_id"]][0]["history_saved"] is False
    assert reload_reply(client, done["reply_note"]["request_id"])["turn"]["done_text"].endswith("· not saved")
    assert mc_lines(mc_dir) == []
    assert "SECRET-DETAIL" not in json.dumps(events) + capfd.readouterr().out


def test_a_failed_capture_leaves_a_status_code_and_no_text(turned, mc_dir, monkeypatch):
    _break_disk_low(monkeypatch, None)
    client = turned.owner()
    token = turned.csrf(client)
    assert _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()["history_saved"] is False
    status = status_of(mc_dir)
    assert status["last_failure_code"] == "disk_low" and "last_success_at" not in status
    assert "q" not in json.dumps(status).replace("last_failure_code", "")
    assert mc_lines(mc_dir) == []


def test_a_capture_failure_in_the_plain_hook_is_not_a_failed_turn_even_when_it_raises_midway(turned, mc_dir, monkeypatch):
    def boom(root, record, now):
        raise OSError(28, "No space left on device: SECRET-DETAIL")
    monkeypatch.setattr("core.agent_turns.writer.append_record", boom)
    client = turned.owner()
    token = turned.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert body["status"] == "answered" and body["history_saved"] is False
    assert status_of(mc_dir)["last_failure_code"] == "disk_write_failed"


# ── scrub, caps, probes ───────────────────────────────────────────────────────

def test_a_secret_in_the_reply_is_scrubbed_before_it_is_kept_and_the_line_says_so(floored, mc_dir):
    runtime = FakeRuntime(lambda body, headers: Resp(200, json.dumps({
        "id": "x", "choices": [{"index": 0, "message": {"role": "assistant",
                                                         "content": f"Use {FAKE_KEY} and card 4111 1111 1111 1111"},
                                "finish_reason": "stop"}]}), {"X-MC-Correlation-Id": headers.get("X-MC-Correlation-Id")}))
    _turn_on(floored, _openclaw(runtime))
    client = floored.owner()
    token = floored.csrf(client)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert body["history_saved"] is True
    [rec] = mc_lines(mc_dir)
    assert FAKE_KEY not in json.dumps(rec) and "4111" not in json.dumps(rec) and rec["sanitized"] is True


def test_the_text_fields_are_capped(tmp_path, mc_dir):
    note, reply = {"id": 1}, {"id": 2, "request_id": "mc-x", "text": "r" * 20000, "author_label": "Master Craftsman"}
    assert turn_capture.capture_turn(turn_id="t", conversation_id="c", note=note, note_id="n-1", user_text="u" * 9000,
                                     reply_note=reply, backend_kind="openclaw")[0] is True
    [rec] = mc_lines(mc_dir)
    assert len(rec["user_text"]) == 8000 and len(rec["reply"]) == 16000


def test_capture_turn_unit_rules(mc_dir, monkeypatch):
    reply = {"id": 2, "request_id": "mc-x", "text": "a", "author_label": "Master Craftsman"}
    args = dict(turn_id="t", conversation_id="c", note={"id": 1}, note_id="n-1", user_text="q", reply_note=reply)
    assert turn_capture.capture_turn(**args, backend_kind="stub") == (None, "not_live")
    assert turn_capture.capture_turn(**args, backend_kind=None) == (None, "not_live")
    assert turn_capture.capture_turn(**args, backend_kind="grok") == (True, "saved")
    assert mc_lines(mc_dir)[0]["backend_type"] == "grok"
    monkeypatch.delenv("MC_TURNS_DIR")
    assert turn_capture.capture_turn(**args, backend_kind="openclaw") == (None, "no_turn_log")
    monkeypatch.setenv("MC_TURNS_DIR", "   ")
    assert turn_capture.capture_turn(**args, backend_kind="openclaw") == (None, "no_turn_log")


def test_capture_turn_never_raises_whatever_it_is_given(mc_dir):
    def bad_clock():
        raise RuntimeError("SECRET-DETAIL")
    args = dict(turn_id="t", conversation_id="c", note={"id": 1}, note_id="n-1", user_text="q",
                reply_note={"id": 2, "text": "a"}, backend_kind="openclaw")
    assert turn_capture.capture_turn(**args, clock=bad_clock) == (False, "internal")
    assert turn_capture.capture_turn(**{**args, "reply_note": None}) == (False, "internal")
    assert turn_capture.capture_turn(**{**args, "note": None, "reply_note": {"text": None}}) == (True, "saved")
    assert [r["user_text"] for r in mc_lines(mc_dir)] == ["q"]


def test_the_operator_probes_do_not_capture(turned, mc_dir):
    from minimoi_portal.guild_ui.api import run_mc_turn
    client = turned.owner()
    token = turned.csrf(client)
    note = _keep(client, token, "probe-like")
    services = turned.app.extensions["guild_ui_next"]["services"]
    floor = services.floor
    stored = floor.get_note(note["request_id"])
    conv = {"id": "shop-floor-thread", "legacy": True, "notes_floor": floor.floor, "unfiled": True}
    answer, status = run_mc_turn(services, conversations=None, floor=floor, conv=conv, principal=stored["who"],
                                 note=stored, note_id=note["request_id"], capture=False)
    assert status == 200 and answer["status"] == "answered" and "history_saved" not in answer
    assert mc_lines(mc_dir) == []


# ── the footer and the timing line ────────────────────────────────────────────

def test_done_text_says_not_saved_only_when_the_capture_failed():
    assert done_text(1234) == "Done in 1.2s" and done_text(1234, None) == "Done in 1.2s"
    assert done_text(1234, True) == "Done in 1.2s"
    assert done_text(1234, False) == "Done in 1.2s · not saved" and done_text(15400, False) == "Done in 15s · not saved"


def test_the_timing_line_keeps_history_saved_only_when_it_is_a_boolean(tmp_path):
    log = TurnLog(str(tmp_path))
    log.record(turn_id="t1", status="answered", backend_kind="openclaw", duration_ms=1234, reply_request_id="mc-a",
               history_saved=False)
    log.record(turn_id="t2", status="answered", backend_kind="openclaw", duration_ms=1234, reply_request_id="mc-b",
               history_saved=True)
    log.record(turn_id="t3", status="answered", backend_kind="openclaw", duration_ms=1234, reply_request_id="mc-c")
    log.record(turn_id="t4", status="answered", backend_kind="openclaw", duration_ms=1234, reply_request_id="mc-d",
               history_saved="yes")
    raw = [json.loads(l) for l in (tmp_path / "mc_turns.jsonl").read_text().splitlines()]
    assert [l.get("history_saved", "absent") for l in raw] == [False, True, "absent", "absent"]
    got = log.turns_for(["mc-a", "mc-b", "mc-c", "mc-d"])
    assert got["mc-a"]["done_text"] == "Done in 1.2s · not saved" and got["mc-a"]["history_saved"] is False
    assert got["mc-b"]["done_text"] == "Done in 1.2s" and got["mc-b"]["history_saved"] is True
    assert "history_saved" not in got["mc-c"] and "history_saved" not in got["mc-d"]


# ── off the record, under concurrency (Codex 4 Oct: MC's hook is not covered by CoS's Private latch) ─────────────────
#
# MC has no server-owned Private mode and no _mode.lock: "off the record" is the record mode each write declares, and
# the write guard (security.check_write) refuses off_record with 409 before any read, dispatch or capture (spec v0.5.1 §8:
# no CoS-style Private for MC). The only decision capture takes is therefore the one the guard already took for that
# request, so there is no check-then-write window on the server to close. These tests pin what that rests on.

def test_off_the_record_requests_never_reach_capture_while_on_the_record_turns_run(streaming, mc_dir, monkeypatch):
    import threading
    import time
    reached = []
    real = turn_capture.capture_turn

    def watch(**kw):
        reached.append(kw["user_text"])
        return real(**kw)
    monkeypatch.setattr(turn_capture, "capture_turn", watch)

    on = streaming.owner()
    on_token = streaming.csrf(on)
    kept = [_keep(on, on_token, f"on {i}") for i in range(4)]
    off_notes = [_keep(on, on_token, f"off {i}") for i in range(4)]
    statuses, stop = [], threading.Event()

    def hammer_off_the_record():                    # a second client of the same owner, every request declared off_record
        client = streaming.owner()
        token = streaming.csrf(client)
        for n in range(40):                          # bounded and paced: a hot loop starves the turns under the GIL
            if stop.is_set():
                break
            note = off_notes[n % len(off_notes)]
            for call in (_ask, _stream):
                statuses.append(call(client, token, note["request_id"], mode="off_record").status_code)
            time.sleep(0.005)

    worker = threading.Thread(target=hammer_off_the_record)
    worker.start()
    try:
        for note in kept:                            # on-the-record turns, one at a time (one turn in flight per owner)
            body = _ask(on, on_token, note["request_id"]).get_json()
            assert body["status"] == "answered" and body["history_saved"] is True
    finally:
        stop.set()
        worker.join(timeout=30)
    assert statuses and set(statuses) == {409}      # every off_record request was refused at the guard
    assert sorted(reached) == [f"on {i}" for i in range(4)]
    assert sorted(l["user_text"] for l in mc_lines(mc_dir)) == [f"on {i}" for i in range(4)]
    assert not any("off " in l["user_text"] for l in mc_lines(mc_dir))


def test_the_only_callers_of_capture_sit_behind_the_write_guard():
    """Both endpoints take their capture hook from api.py after _mc_prelude (the write guard); nothing else builds one."""
    root = Path(__file__).resolve().parents[3] / "minimoi_portal"
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if "capturer(" in p.read_text(encoding="utf-8") and p.name != "turn_capture.py")
    assert users == ["guild_ui/api.py"]
    api = (root / "guild_ui" / "api.py").read_text(encoding="utf-8")
    for endpoint in ("def mc_turn():", "def mc_turn_stream():"):
        body = api.split(endpoint, 1)[1].split("\n@owner_api", 1)[0].split("\ndef ", 1)[0]
        assert "_mc_prelude()" in body
    assert "check_write(" in api.split("def _mc_prelude():", 1)[1].split("\ndef ", 1)[0]
