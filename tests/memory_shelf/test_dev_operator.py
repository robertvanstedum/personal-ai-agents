from datetime import datetime, timezone, timedelta
import io
import json
import tarfile
from pathlib import Path

import pytest

from core.jobs import status
from scripts.memory.dev_ask import app_for
from scripts.vault.dev_cycle import unpack, prune, run_worker


def archive(path, entries):
    with tarfile.open(path, "w") as tar:
        for name, raw in entries:
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))


def test_export_inventory_and_incomplete_refusal(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    tar = tmp_path / "export.tar"
    archive(tar, [("dev--c-0123456789ab.json", b"{}"),
                  ("EXPORT.json", b'{"status":"ok","counts":{"exported":1}}')])
    unpack(tar, out)
    assert len(json.loads((out / "feed-export.json").read_text())["files"]) == 1
    archive(tar, [("../outside", b"oops")])
    with pytest.raises(ValueError):
        unpack(tar, out)
    archive(tar, [("dev--c-0123456789ab.json", b"{}")])
    with pytest.raises(ValueError, match="incomplete_export"):
        unpack(tar, out)


def test_pruner_only_owns_old_scratch(tmp_path):
    import os
    old = tmp_path / "cycle-abcdefgh"
    old.mkdir()
    os.utime(old, (1, 1))
    source = tmp_path / "source-history"
    source.mkdir()
    linked = tmp_path / "cycle-abcdefgi"
    linked.symlink_to(source, target_is_directory=True)
    fresh = tmp_path / "cycle-abcdefgj"
    fresh.mkdir()
    assert prune(tmp_path) == 1
    assert source.exists() and linked.is_symlink() and fresh.exists()


def test_ask_fails_closed_without_fresh_verified_feed(tmp_path):
    app = app_for(tmp_path)
    client = app.test_client()
    assert client.get("/v1/health").status_code == 503
    now = datetime.now(timezone.utc)
    def write(state, at):
        doc = status.build(job_id="dev-memory-feed", host="dev", run_id="test", state=state,
                           started_at=at, finished_at=at if state != "running" else None)
        status.write_status(tmp_path / "jobs", "dev-memory-feed.json", doc)
    write("ok", now - timedelta(minutes=11))
    assert client.get("/v1/health").status_code == 503
    write("running", now)
    assert client.get("/v1/health").status_code == 503
    write("ok", now)
    assert client.get("/v1/health").status_code == 200
    assert client.post("/v1/ask", json={"query": "hello"}).status_code in (401, 503)


@pytest.mark.parametrize("remaining", ["", "container-id", "daemon-error"])
def test_timed_out_worker_cleanup(monkeypatch, remaining):
    import subprocess
    calls = []
    worker = "dev-memory-feed-test"
    command = ["docker", "run", "--name", worker, "image"]
    def fake(cmd, **kwargs):
        calls.append(cmd)
        if cmd == command:
            raise subprocess.TimeoutExpired(cmd, 300)
        if cmd[1] == "ps":
            assert kwargs["check"] is True
            if remaining == "daemon-error":
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=remaining)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, "run", fake)
    expected = subprocess.TimeoutExpired if not remaining else (ValueError if remaining == "container-id" else subprocess.CalledProcessError)
    with pytest.raises(expected):
        run_worker(command, worker)
    assert calls[1] == ["docker", "rm", "-f", worker]
    assert calls[2][4] == f"name=^/{worker}$"


def test_installer_refuses_unlisted_or_linked_code(tmp_path, monkeypatch):
    import hashlib
    from scripts.vault import dev_install
    monkeypatch.setattr(dev_install, "OP", tmp_path)
    (tmp_path / "code").mkdir()
    target = tmp_path / "code/safe.py"
    target.write_text("pass\n")
    manifest = {"files": {"safe.py": hashlib.sha256(target.read_bytes()).hexdigest()}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert dev_install.fence()
    unexpected = tmp_path / "code/unreviewed.py"
    unexpected.write_text("pass\n")
    with pytest.raises(ValueError, match="inventory"):
        dev_install.fence()
    unexpected.unlink()
    unexpected.symlink_to(target)
    with pytest.raises(ValueError, match="package_path"):
        dev_install.fence()


def test_private_receipts_stay_out_of_shared_jobs(tmp_path, monkeypatch):
    from scripts.vault import dev_cycle
    monkeypatch.setattr(dev_cycle, "ROOT", tmp_path)
    private = tmp_path / "data/vault-jobs"
    private.mkdir(parents=True)
    (private / "dev-memory-feed.json").write_text('{"state":"ok"}')
    (private / "dev-memory-cursors.json").write_text('{"private":"cursor"}')
    (private / "dev-memory-feed-report.json").write_text('{"private":"receipts"}')
    dev_cycle.publish_status()
    shared = tmp_path / "data/jobs"
    assert [p.name for p in shared.iterdir()] == ["dev-memory-feed.json"]
    assert json.loads((shared / "dev-memory-feed.json").read_text()) == {"state": "ok"}
    assert (shared / "dev-memory-feed.json").stat().st_mode & 0o777 == 0o600
    dev_cycle.failed_status()
    assert json.loads((private / "dev-memory-feed.json").read_text())["state"] == "failed"
    assert json.loads((shared / "dev-memory-feed.json").read_text())["state"] == "failed"


def test_schedule_cleanup_preserves_modified_units(tmp_path, monkeypatch):
    from scripts.vault import dev_install
    monkeypatch.setattr(dev_install, "UNITS", tmp_path)
    calls = []
    monkeypatch.setattr(dev_install, "command", lambda *args: calls.append(args))
    files = dev_install.schedule_files({"image": "sha256:" + "a" * 64, "principal": "robert"})
    for path, content in files.items():
        path.write_text(content)
    timer = tmp_path / dev_install.TIMER
    timer.write_text("someone else's timer")
    with pytest.raises(RuntimeError, match="uncertain"):
        dev_install.remove_schedule(files)
    assert not calls and timer.exists()
    timer.write_text(files[timer])
    dev_install.remove_schedule(files)
    assert not any(p.exists() for p in files)
    assert calls == [("systemctl", "disable", "--now", dev_install.TIMER), ("systemctl", "daemon-reload")]


def test_partial_schedule_cleanup_never_disables_missing_timer(tmp_path, monkeypatch):
    from scripts.vault import dev_install
    monkeypatch.setattr(dev_install, "UNITS", tmp_path)
    calls = []
    monkeypatch.setattr(dev_install, "command", lambda *args: calls.append(args))
    unit = tmp_path / dev_install.UNIT
    unit.write_text("owned service")
    dev_install.remove_schedule({unit: "owned service"})
    assert calls == [("systemctl", "daemon-reload")]
