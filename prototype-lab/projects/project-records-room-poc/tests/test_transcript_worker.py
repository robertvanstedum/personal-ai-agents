import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from test_transcript_snapshot import source
from store import Problem
from transcript_publish import publish, recover, cleanup_scratch
from transcript_worker import cycle, run


def test_cycle_stable_then_changed_and_closed(source):
    store, room = source
    first = cycle(store, "robert")
    assert first["state"] == "healthy"
    assert cycle(store, "robert")["published"] == first["published"]
    store.append("robert", "new", room, {"body": "New multiline\nmessage"})
    second = cycle(store, "robert")
    assert second["published"][0]["source_revision"] > first["published"][0]["source_revision"]
    assert "New multiline" in (Path(second["published"][0]["bundle"])/"transcript.md").read_text()
    store.state("robert", "close", room, dict(state="closed", version=1, checkpoint="Done"))
    assert cycle(store, "robert")["published"][0]["publication_status"] == "final"


@pytest.mark.parametrize("stage", ["after_scratch", "before_rename"])
def test_recovery_quarantines_only_after_final_verified(source, stage):
    store, room = source
    def fail(point):
        if point == stage: raise RuntimeError("interrupted")
    with pytest.raises(RuntimeError): publish(store, "robert", room, fault=fail)
    pending = list((store.root/"transcripts").glob(".pending-*"))
    assert len(pending) == 1
    assert cleanup_scratch(store, "robert")["quarantined"] == []
    result = cycle(store, "robert")
    assert result["state"] == "healthy"
    assert len(result["recovered"]) == 1
    moved = result["cleanup"]["quarantined"]
    assert len(moved) == 1 and Path(moved[0]).is_dir()
    assert not pending[0].exists()
    assert result["cleanup"]["deleted"] == 0


def test_unknown_scratch_and_symlink_untouched(source, tmp_path):
    store, room = source
    publish(store, "robert", room)
    root = store.root/"transcripts"
    unknown = root/".pending-unknown"
    unknown.mkdir()
    (unknown/"precious.txt").write_text("keep")
    (root/".pending-link").symlink_to(tmp_path, target_is_directory=True)
    result = cleanup_scratch(store, "robert")
    assert set(result["left_untouched"]) == {".pending-unknown", ".pending-link"}
    assert (unknown/"precious.txt").read_text() == "keep"
    assert (root/".pending-link").is_symlink()


def test_failure_does_not_block_other_session_or_leak_error(source, monkeypatch):
    import transcript_worker
    store, room = source
    other = store.create_room("robert", "other", dict(title="Other", purpose="Synthetic", mode="meeting", recording_acknowledged=True))["result"]["id"]
    real = transcript_worker.publish
    def fail_one(store, actor, session):
        if session == room: raise ValueError("sensitive secret")
        return real(store, actor, session)
    monkeypatch.setattr(transcript_worker, "publish", fail_one)
    result = cycle(store, "robert")
    assert result["state"] == "degraded"
    assert result["published"][0]["session_id"] == other
    assert "sensitive" not in json.dumps(result)


def test_bounded_stop_preserves_last_cycle(source):
    store, room = source
    event = threading.Event()
    def tick(store, actor):
        event.set()
        return {"state": "degraded", "failures": [{"code": "synthetic"}]}
    assert run(store, "robert", stop_event=event, tick=tick) == 1
    status = json.loads((store.root/"transcripts"/"worker-status.json").read_text())
    assert status["reason"] == "stop_requested"
    assert status["last_cycle"]["state"] == "degraded"


@pytest.mark.parametrize("interval,duration", [(0, 1), (1, 0), (float("nan"), 1), (1, float("inf"))])
def test_invalid_bounds_rejected(source, interval, duration):
    with pytest.raises(ValueError): run(source[0], "robert", interval=interval, duration=duration)


def test_nonowner_denied_before_publication(source):
    store, _ = source
    for call in (cycle, run, cleanup_scratch):
        with pytest.raises(Problem): call(store, "cos-dev")
    assert not (store.root/"transcripts").exists()


@pytest.mark.parametrize("problem", ["tampered_final", "collision", "symlink_content"])
def test_cleanup_refuses_unsafe_or_conflicting_scratch(source, problem):
    store, room = source
    def fail(point):
        if point == "after_scratch": raise RuntimeError("interrupt")
    with pytest.raises(RuntimeError): publish(store, "robert", room, fault=fail)
    root = store.root/"transcripts"
    scratch = next(root.glob(".pending-*"))
    final = recover(store, "robert")[0]
    if problem == "tampered_final":
        (final/"transcript.md").write_text("bad")
    elif problem == "collision":
        quarantine = root/"scratch-quarantine"
        quarantine.mkdir(mode=0o700)
        (quarantine/scratch.name).mkdir()
    else:
        (scratch/"transcript.md").symlink_to(final/"transcript.md")
    result = cleanup_scratch(store, "robert")
    assert not result["quarantined"]
    assert result["left_untouched"] == [scratch.name]
    assert scratch.exists()


def test_real_bounded_cli(source):
    store, _ = source
    cli = Path(__file__).resolve().parents[1]/"manage.py"
    result = subprocess.run([sys.executable, str(cli), "watch-transcripts", "--data-dir", str(store.root),
                             "--interval", "0.1", "--duration", "0.3"], capture_output=True, text=True, check=True, timeout=15)
    reports = [json.loads(line) for line in result.stdout.splitlines()]
    assert reports[0]["state"] == "healthy"
    assert reports[-1]["state"] == "stopped"
    assert reports[-1]["cycles"] >= 1
    status = json.loads((store.root/"transcripts"/"worker-status.json").read_text())
    assert status["reason"] == "duration_elapsed"
