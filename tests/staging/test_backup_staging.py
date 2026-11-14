"""The daily staging backup (job staging-backup; Robert, 13 Nov 2026: staging had no backups). No AWS, no Docker: the
producer is a local command and the uploader writes to a file, so every byte kept and every checksum can be checked."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("backup_staging", REPO / "scripts/staging/backup_staging.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def file_uploader(store: Path):
    """An uploader that keeps what it is sent at store/<the key after the bucket prefix>."""
    def make(dest: str) -> list[str]:
        target = store / dest.replace("s3://", "")
        target.parent.mkdir(parents=True, exist_ok=True)
        return ["sh", "-c", f"cat > '{target}'"]
    return make


def test_stream_forwards_every_byte_and_returns_the_checksum_of_what_was_uploaded(tmp_path):
    digest = backup.stream(["printf", "hello staging"], "s3://b/x/plain.txt", uploader=file_uploader(tmp_path))
    kept = (tmp_path / "b/x/plain.txt").read_bytes()
    assert kept == b"hello staging" and digest == hashlib.sha256(kept).hexdigest()


def test_stream_can_gzip_on_the_way(tmp_path):
    digest = backup.stream(["printf", "dump"], "s3://b/x/db.sql.gz", compress=True, uploader=file_uploader(tmp_path))
    kept = (tmp_path / "b/x/db.sql.gz").read_bytes()
    assert gzip.decompress(kept) == b"dump" and digest == hashlib.sha256(kept).hexdigest()


def test_a_failing_producer_or_upload_is_an_error_never_a_kept_piece(tmp_path):
    for producer, uploader in ((["sh", "-c", "exit 3"], file_uploader(tmp_path)),
                               (["printf", "x"], lambda dest: ["sh", "-c", "cat >/dev/null; exit 1"])):
        try:
            backup.stream(producer, "s3://b/x/y", uploader=uploader)
        except RuntimeError:
            continue
        raise AssertionError("a failure was reported as kept")


def test_a_night_writes_every_piece_a_manifest_and_an_ok_status(tmp_path):
    plan = lambda with_images: [("files/a.txt", ["printf", "a"], False), ("databases/p.sql.gz", ["printf", "p"], True)]
    code = backup.run(label="daily", with_images=False, jobs_root=str(tmp_path / "jobs"), prefix="s3://b/staging",
                      uploader=file_uploader(tmp_path / "s3"), plan=plan)
    assert code == 0
    [night] = list((tmp_path / "s3/b/staging").iterdir())
    manifest = (night / "MANIFEST.sha256").read_text().splitlines()
    assert [l.split("  ")[1] for l in manifest] == ["files/a.txt", "databases/p.sql.gz"]
    assert manifest[0].split("  ")[0] == hashlib.sha256(b"a").hexdigest()
    doc = json.loads((tmp_path / "jobs/staging-backup.json").read_text())
    assert doc["job_id"] == "staging-backup" and doc["host"] == "dev" and doc["state"] == "ok"
    assert doc["results"]["kept"] == "2" and doc["results"]["failed"] == "0" and doc["last_success_at"]


def test_one_failed_piece_fails_the_night_and_names_it(tmp_path):
    plan = lambda with_images: [("files/a.txt", ["printf", "a"], False), ("volumes/v.tar.gz", ["sh", "-c", "exit 2"], False)]
    code = backup.run(label="daily", with_images=False, jobs_root=str(tmp_path / "jobs"), prefix="s3://b/staging",
                      uploader=file_uploader(tmp_path / "s3"), plan=plan)
    doc = json.loads((tmp_path / "jobs/staging-backup.json").read_text())
    assert code == 1 and doc["state"] == "failed" and "volumes:v.tar.gz" in doc["summary"]
    assert doc.get("last_success_at") is None                      # a failed night is never counted as a success


def test_images_are_kept_only_when_asked(monkeypatch):
    out = {"docker ps": "minimoi-portal minimoi-staging/portal:x\npostgres-ai-agents postgres:latest",
           "docker volume": "minimoi-mc-state"}
    monkeypatch.setattr(backup, "lines", lambda cmd: out[" ".join(cmd[:2])].splitlines())
    daily = [name for name, _, _ in backup.pieces(False)]
    assert daily == ["files/opt-minimoi-staging.tar.gz", "volumes/minimoi-mc-state.tar.gz",
                     "databases/postgres-ai-agents.pg_dumpall.sql.gz", "meta/running-containers.txt"]
    full = [name for name, _, _ in backup.pieces(True)]
    assert "images/minimoi-staging_portal_x.tar.gz" in full and "images/postgres_latest.tar.gz" in full


def test_the_dev_installer_adds_the_nightly_backup_timer_and_copies_the_script(tmp_path):
    calls = tmp_path / "calls"
    fake = tmp_path / "systemctl"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\n")
    fake.chmod(0o755)
    env = dict(os.environ, MINIMOI_ETC=str(tmp_path / "etc"), SYSTEMCTL=str(fake))
    script = (REPO / "scripts/jobs/install_host_watchdog.sh").read_text().replace(
        "tools=/opt/minimoi-staging/jobs-tools", f"tools={tmp_path / 'tools'}")
    copy = tmp_path / "install.sh"
    copy.write_text(script)
    done = subprocess.run(["/bin/sh", str(copy), "dev", "--from", str(REPO)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    units = tmp_path / "etc/systemd/system"
    assert "OnCalendar=*-*-* 04:40:00 America/Chicago" in (units / "minimoi-staging-backup.timer").read_text()
    assert "scripts/staging/backup_staging.py" in (units / "minimoi-staging-backup.service").read_text()
    assert (tmp_path / "tools/scripts/staging/backup_staging.py").is_file()
    assert "minimoi-staging-backup.timer" in calls.read_text()


def test_the_job_is_on_the_master_list_for_dev():
    jobs = {j["id"]: j for j in json.loads((REPO / "config/scheduled_jobs.json").read_text())["jobs"]}
    job = jobs["staging-backup"]
    assert job["host"] == "dev" and job["status_file"] == "staging-backup.json" and job["schedule"]["at"] == "04:40"
