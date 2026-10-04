"""CoS text-turn log (Spec 160 §2; tests T5, T6, T9 and the log half of T7).

No model, no network, no Docker: ConferTurnService with a fake backend and
``record_turn=turn_log.recorder()`` against a temporary COS_TURNS_DIR.
"""
import errno
import json
import stat
import threading
from datetime import datetime, timezone

import pytest

from domains.cos import private_mode, turn_log
from domains.cos.confer_service import (
    ALLOWED_CHANNELS, ConferTurnRequest, ConferTurnService,
)

FIXED = datetime(2026, 10, 3, 17, 30, 0, tzinfo=timezone.utc)      # 12:30 in Chicago, same local day
FAKE_KEY = "sk-ant-FAKEFAKEFAKE12345"
MARKER = "CANARY-MARKER-7QX"


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = tmp_path / "cos-turns"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv("COS_TURNS_DIR", str(folder))
    monkeypatch.setenv("COS_TURN_LOG_ENABLED", "1")
    monkeypatch.setenv("COS_TURNS_MIN_FREE_BYTES", "1")
    monkeypatch.setenv("COS_AGENT_TIMEZONE", "America/Chicago")
    monkeypatch.setenv("COS_CONTAINER_NAME", "cos-test")
    return folder


def service(reply="a reply", hook=None, backend=None, **kw):
    calls = []
    recorder = turn_log.recorder(clock=lambda: FIXED)
    if hook is None:
        kw.setdefault("begin_turn", recorder.begin)
    return ConferTurnService(
        call_backend=backend or (lambda prompt, ctx, policy: reply),
        build_context=lambda: {"system_prompt": "s"},
        increment_chat=lambda: calls.append(1),
        backend_metadata=lambda: ("Test backend", "test-model"),
        save_note=lambda text, op: {"saved": True, "deduplicated": False},
        reset_conversation=lambda cid: True,
        record_turn=hook if hook is not None else recorder,
        **kw,
    )


def ask(svc, text="hello", channel="html_text", **kw):
    return svc.handle(ConferTurnRequest(text=text, channel=channel, **kw))


def lines(root):
    out = []
    for path in sorted(root.rglob("*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(raw))
            except ValueError:
                pass          # a crash fragment: readers skip it
    return out


def turns(root):
    return [r for r in lines(root) if r.get("record_type") == "cos_turn"]


# ── T5: one valid line per turn, on every channel ─────────────────────────────

@pytest.mark.parametrize("channel", sorted(ALLOWED_CHANNELS))
@pytest.mark.parametrize("backend", ["grok", "openclaw"])
def test_one_valid_line_per_turn_on_every_channel(root, monkeypatch, channel, backend):
    monkeypatch.setenv("COS_BACKEND_TYPE", backend)
    result = ask(service(), "What is next?", channel=channel)
    [rec] = turns(root)
    assert result.history_saved is True
    assert rec["schema_version"] == 1 and rec["record_type"] == "cos_turn"
    assert rec["channel"] == channel and rec["backend_type"] == backend
    assert rec["turn_id"] == result.turn_id and rec["receipt_id"] == result.turn_id
    assert rec["conversation_id"] == "owner"
    assert rec["user_text"] == "What is next?" and rec["reply"] == "a reply"
    assert rec["time"] == "2026-10-03T17:30:00Z"            # UTC inside, file named by local day
    assert (root / "2026" / "2026-10-03.jsonl").exists()
    assert rec["sanitized"] is False


def test_operations_are_logged_with_their_operation(root):
    svc = service()
    ask(svc, "/new")
    ask(svc, "Save a note: buy milk")
    ops = [r["operation"]["type"] for r in turns(root)]
    assert ops == ["session_reset", "note_save"]


def test_modes_and_folders_are_private_to_the_owner(root):
    ask(service())
    day = root / "2026" / "2026-10-03.jsonl"
    assert stat.S_IMODE(day.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "2026").stat().st_mode) == 0o700
    status = root / "_status" / "cos-test.json"
    assert stat.S_IMODE(status.stat().st_mode) == 0o600


def test_two_writers_at_once_leave_only_whole_lines(root):
    svc = service()
    errors = []

    def worker(tag):
        try:
            for i in range(25):
                ask(svc, f"{tag}-{i}")
        except Exception as exc:                              # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    raw = (root / "2026" / "2026-10-03.jsonl").read_text(encoding="utf-8").splitlines()
    assert not errors and len(raw) == 50
    assert all(json.loads(line)["record_type"] == "cos_turn" for line in raw)


def test_a_crash_truncated_tail_is_closed_with_a_log_gap(root):
    target = root / "2026" / "2026-10-03.jsonl"
    target.parent.mkdir(mode=0o700)
    target.write_text('{"record_type":"cos_turn","schema_version":1,"user_te', encoding="utf-8")   # no newline
    ask(service(), "after the crash")
    all_lines = target.read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(l).get("record_type") if l.startswith("{") and l.endswith("}") else "fragment" for l in all_lines]
    assert "log_gap" in kinds and kinds[-1] == "cos_turn"
    assert [r["user_text"] for r in turns(root)] == ["after the crash"]      # the fragment is skipped


# ── T5: failure is visible and the turn still answers ─────────────────────────

def test_a_full_disk_answers_the_turn_and_says_not_saved(root, monkeypatch):
    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(turn_log, "append_record", boom)
    result = ask(service(reply="still answered"))
    assert result.reply == "still answered" and result.history_saved is False
    assert result.public_dict()["history_saved"] is False
    status = json.loads((root / "_status" / "cos-test.json").read_text())
    assert status["last_failure_code"] == "disk_write_failed" and "last_failure_at" in status


def test_below_the_free_space_floor_nothing_is_written_and_nothing_deleted(root, monkeypatch):
    keep = root / "keep.txt"
    keep.write_text("do not delete")
    monkeypatch.setenv("COS_TURNS_MIN_FREE_BYTES", str(10 ** 18))
    result = ask(service())
    assert result.history_saved is False and turns(root) == []
    assert keep.read_text() == "do not delete"
    assert json.loads((root / "_status" / "cos-test.json").read_text())["last_failure_code"] == "disk_low"


def test_a_hook_that_raises_never_stops_the_answer(root):
    def broken(request, result):
        raise RuntimeError("hook exploded")
    result = ask(service(reply="fine", hook=broken))
    assert result.reply == "fine" and result.history_saved is False


def test_no_turn_log_configured_keeps_nothing_and_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("COS_TURNS_DIR", raising=False)
    result = ask(service())
    assert result.history_saved is None and "history_saved" not in result.public_dict()


def test_switched_off_keeps_nothing_and_is_not_a_failure(root, monkeypatch):
    monkeypatch.setenv("COS_TURN_LOG_ENABLED", "0")
    result = ask(service())
    assert result.history_saved is None and turns(root) == []


# ── T6: Private ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("channel", sorted(ALLOWED_CHANNELS))
def test_private_writes_nothing_on_any_channel(root, channel):
    private_mode.set_private(root, True)
    result = ask(service(), channel=channel)
    assert turns(root) == [] and result.history_saved is None


def test_new_does_not_end_private(root):
    private_mode.set_private(root, True)
    svc = service()
    ask(svc, "/new")
    ask(svc, "still private")
    assert turns(root) == [] and private_mode.is_private(root)


def test_only_an_explicit_public_ends_private(root):
    private_mode.set_private(root, True)
    ask(service(), "private one")
    private_mode.set_private(root, False)
    ask(service(), "public one")
    assert [r["user_text"] for r in turns(root)] == ["public one"]


def test_the_request_saying_private_is_enough(root):
    result = ask(service(), "this one only", private=True)
    assert turns(root) == [] and result.history_saved is None


def test_an_unreadable_mode_is_treated_as_private(root):
    (root / "_mode.json").write_text("{not json", encoding="utf-8")
    result = ask(service())
    assert turns(root) == [] and result.history_saved is None


def test_telegram_and_web_share_the_mode(root):
    private_mode.set_private(root, True)          # what /private on Telegram does
    ask(service(), channel="html_text")
    ask(service(), channel="telegram_text")
    ask(service(), channel="html_voice")
    assert turns(root) == []


# ── scrub and the error canary (T4-style, T9) ─────────────────────────────────

def test_payment_details_and_credentials_are_scrubbed_before_they_are_kept(root):
    text = f"my card 4242 4242 4242 4242 and mail robert@example.com and key {FAKE_KEY}"
    ask(service(reply=f"echo {FAKE_KEY} and 4242 4242 4242 4242"), text)
    [rec] = turns(root)
    blob = json.dumps(rec)
    assert "4242 4242 4242 4242" not in blob and "robert@example.com" not in blob and FAKE_KEY not in blob
    assert rec["sanitized"] is True
    assert FAKE_KEY not in (root / "_status" / "cos-test.json").read_text()


def test_text_fields_are_capped_before_they_are_kept(root):
    ask(service(reply="r" * 20_000), "u" * 12_000)
    [rec] = turns(root)
    assert len(rec["user_text"]) == turn_log.MAX_USER_TEXT and len(rec["reply"]) == turn_log.MAX_REPLY


def test_error_text_never_reaches_logs_status_or_results(root, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError(errno.EIO, f"disk error mentioning {MARKER} in /secret/{MARKER}.md")
    monkeypatch.setattr(turn_log, "append_record", boom)
    result = ask(service(), f"user text {MARKER}")
    out = capsys.readouterr()
    status_text = (root / "_status" / "cos-test.json").read_text()
    for blob in (out.out, out.err, status_text, json.dumps(result.public_dict())):
        assert MARKER not in blob and "/secret/" not in blob


def test_status_file_holds_codes_and_times_only(root):
    ask(service())
    status = json.loads((root / "_status" / "cos-test.json").read_text())
    assert set(status) <= {"schema_version", "container", "last_success_at", "last_failure_at", "last_failure_code"}
    assert status["last_success_at"] == "2026-10-03T17:30:00Z"


# ── the mode is latched when the turn starts (Codex review of M1, 2026-10-04) ──────────────────────────────

def flip_during_turn(root, *modes, conversation_id="owner"):
    """A backend that changes the stored mode while the turn is running."""
    def backend(prompt, ctx, policy):
        for private in modes:
            private_mode.set_private(root, private, conversation_id)
        return "a reply that must not be kept"
    return backend


def test_a_turn_that_starts_private_and_becomes_public_while_it_runs_is_never_saved(root):
    private_mode.set_private(root, True, "owner")
    result = ask(service(backend=flip_during_turn(root, False)))
    assert lines(root) == [] and result.history_saved is None


def test_a_turn_that_starts_public_and_becomes_private_while_it_runs_is_never_saved(root):
    private_mode.set_private(root, False, "owner")
    result = ask(service(backend=flip_during_turn(root, True)))
    assert lines(root) == [] and result.history_saved is None


def test_public_to_private_to_public_inside_one_turn_is_still_not_saved(root):
    private_mode.set_private(root, False, "owner")
    result = ask(service(backend=flip_during_turn(root, True, False)))
    assert lines(root) == [] and result.history_saved is None


@pytest.mark.parametrize("channel", sorted(ALLOWED_CHANNELS))
def test_every_channel_follows_the_latch(root, channel):
    private_mode.set_private(root, True, "owner")
    ask(service(backend=flip_during_turn(root, False)), channel=channel)
    assert lines(root) == []


def test_a_turn_whose_mode_never_moves_is_saved_as_before(root):
    private_mode.set_private(root, False, "owner")
    result = ask(service())
    assert len(lines(root)) == 1 and result.history_saved is True


def test_a_turn_whose_mode_could_not_be_read_at_the_start_is_not_saved(root, monkeypatch):
    real = private_mode.read_mode
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("unreadable")
        return real(*a, **k)
    monkeypatch.setattr(private_mode, "read_mode", flaky)
    ask(service())
    assert lines(root) == []


def test_without_the_latch_hook_the_service_still_works_with_a_two_argument_recorder(root):
    seen = []
    ask(service(hook=lambda request, result: seen.append(result.reply) or True))
    assert seen == ["a reply"]


# ── the final check and the append are atomic with the mode switch (Codex review of M1, fix verdict 2026-10-04) ──

import fcntl
import os
import time


def lock_is_held_shared_or_more(root) -> bool:
    """True if an exclusive lock on the mode lock cannot be taken right now (someone holds it)."""
    fd = os.open(root / "_mode.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def test_the_mode_lock_is_held_while_the_record_is_built_and_while_it_is_appended(root, monkeypatch):
    private_mode.set_private(root, False, "owner")
    seen = {}
    real_build, real_append = turn_log._build_record, turn_log.append_record

    def build(*a, **k):
        seen["build"] = lock_is_held_shared_or_more(root)
        return real_build(*a, **k)

    def append(r, record, now):
        seen["append"] = lock_is_held_shared_or_more(root)
        return real_append(r, record, now)
    monkeypatch.setattr(turn_log, "_build_record", build)
    monkeypatch.setattr(turn_log, "append_record", append)
    assert ask(service()).history_saved is True
    assert seen == {"build": True, "append": True}
    assert lock_is_held_shared_or_more(root) is False                     # and released afterwards


def test_a_switch_to_private_started_after_the_final_check_waits_for_the_turn_instead_of_racing_the_append(root, monkeypatch):
    """Codex's boundary: the switch lands between the last mode read and the append. It must now queue behind the turn."""
    private_mode.set_private(root, False, "owner")
    real_build = turn_log._build_record
    order, switcher = [], {}

    def build(*a, **k):
        def flip():
            private_mode.set_private(root, True, "owner")
            order.append("switched")
        switcher["t"] = threading.Thread(target=flip)
        switcher["t"].start()
        time.sleep(0.3)                                                    # the switch is now trying; it must be waiting
        order.append("record built")
        return real_build(*a, **k)
    monkeypatch.setattr(turn_log, "_build_record", build)
    result = ask(service())
    switcher["t"].join(10)
    assert result.history_saved is True and order == ["record built", "switched"]        # the turn finished first
    assert len(lines(root)) == 1
    assert private_mode.read_mode(root, "owner")[0] is True
    assert ask(service()).history_saved is None and len(lines(root)) == 1                 # and nothing after the switch


def test_no_turn_is_ever_appended_while_the_mode_is_private_under_concurrent_switching(root, monkeypatch):
    private_mode.set_private(root, False, "owner")
    violations, real_append = [], turn_log.append_record

    def append(r, record, now):
        if private_mode.read_mode(r, "owner")[0]:
            violations.append(1)                                           # a line is being written while Private
        time.sleep(0.002)
        return real_append(r, record, now)
    monkeypatch.setattr(turn_log, "append_record", append)
    stop = threading.Event()

    def toggler():
        flag = True
        while not stop.is_set():
            private_mode.set_private(root, flag, "owner")
            flag = not flag
            time.sleep(0.001)
    threads = [threading.Thread(target=toggler)]
    threads += [threading.Thread(target=lambda: [ask(service(), text=f"turn {n}") for n in range(25)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads[1:]:
        t.join(120)
    stop.set()
    threads[0].join(10)
    assert violations == []


def test_a_turn_that_cannot_get_the_mode_lock_in_time_saves_nothing(root, monkeypatch):
    private_mode.set_private(root, False, "owner")
    monkeypatch.setattr(private_mode, "HOLD_WAIT_S", 0.2)
    fd = os.open(root / "_mode.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)                                          # a switch that is taking its time
    try:
        result = ask(service())
    finally:
        os.close(fd)
    assert result.history_saved is False and lines(root) == []


def test_another_process_holding_the_switch_makes_the_turn_wait_then_see_the_new_mode(root):
    import subprocess, sys, textwrap
    private_mode.set_private(root, False, "owner")
    child = textwrap.dedent("""
        import sys, time
        sys.path.insert(0, %r)
        from domains.cos import private_mode
        from pathlib import Path
        root = Path(sys.argv[1])
        private_mode.set_private(root, False, "owner")      # refresh, then flip while the parent is mid-turn
        print("go", flush=True)
        time.sleep(0.4)
        private_mode.set_private(root, True, "owner")
    """) % str(__import__("pathlib").Path(__file__).resolve().parents[2])
    proc = subprocess.Popen([sys.executable, "-c", child, str(root)], stdout=subprocess.PIPE)
    assert proc.stdout.readline().strip() == b"go"
    result = ask(service(backend=lambda p, c, k: (time.sleep(0.8) or "slow reply")))      # starts public, ends after the flip
    proc.wait(10)
    assert lines(root) == [] and result.history_saved is None


# ── the lock wait fits the capture latency budget (Codex checkpoint 2026-10-04: best effort within 50 ms) ──

def test_the_wait_for_a_mode_switch_is_short_enough_for_the_capture_budget():
    assert private_mode.HOLD_WAIT_S <= 0.05


def test_a_turn_blocked_by_a_slow_switch_gives_up_within_the_budget_and_saves_nothing(root):
    private_mode.set_private(root, False, "owner")
    fd = os.open(root / "_mode.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)                                          # a switch that never finishes
    try:
        svc = service()
        started = time.monotonic()
        result = ask(svc)
        elapsed = time.monotonic() - started
    finally:
        os.close(fd)
    assert result.history_saved is False and lines(root) == []
    assert elapsed < 0.25, elapsed                                          # was 5 s; the turn's own work is a fake backend here


def test_an_uncontended_turn_pays_no_measurable_lock_cost(root):
    private_mode.set_private(root, False, "owner")
    ask(service())                                                          # warm up
    started = time.monotonic()
    for _ in range(20):
        ask(service())
    assert (time.monotonic() - started) / 20 < 0.05                          # well inside 50 ms per turn, lock included


def test_a_normal_switch_that_takes_a_few_milliseconds_does_not_cost_the_turn_its_record(root, monkeypatch):
    """A switch holds the lock for milliseconds, not the whole budget: a turn arriving then still waits and is saved."""
    private_mode.set_private(root, False, "owner")
    fd = os.open(root / "_mode.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    threading.Timer(0.008, lambda: os.close(fd)).start()                     # released after 8 ms, mode unchanged
    assert ask(service()).history_saved is True and len(lines(root)) == 1
