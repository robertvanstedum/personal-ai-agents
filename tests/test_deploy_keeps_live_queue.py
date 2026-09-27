"""A deploy must never overwrite the live Build Queue on EC2.

The production portal writes /opt/minimoi/data/guild/build_queue.json through
domains/guild/queue_store.py. Deploys seed it only when it is missing; an
existing EMPTY file fails loudly. The repository copy replaces a live queue
only through the explicit, two-step `sync_docs.sh --publish-queue`. Every
host-side write takes the portal's own sidecar lock, replaces atomically,
keeps the file's mode, and journals a `replaced` line.
"""
import fcntl
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from domains.guild import queue_store as qs

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "scripts" / "operations" / "seed_build_queue.sh"
CHECK = REPO / "scripts" / "operations" / "check_queue_mount.sh"
SYNC = REPO / "scripts" / "sync_docs.sh"
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"


def run_seed(*args, env=None):
    return subprocess.run(["bash", str(SEED), *map(str, args)], capture_output=True,
                          text=True, env=dict(os.environ, **(env or {})))


def journal(folder: Path):
    path = folder / "queue_journal.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.fixture
def repo_copy(tmp_path):
    src = tmp_path / "repo_queue.json"
    src.write_text(json.dumps([{"id": 1, "status": "idea"}]))
    return src


class held_lock:
    """Hold the queue's sidecar flock, exactly as the portal's store does."""

    def __init__(self, queue: Path):
        self.path = queue.parent / f".{queue.name}.lock"

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# ── Deploy seed ───────────────────────────────────────────────────────────────

def test_first_deploy_seeds_missing_queue(tmp_path, repo_copy):
    dest = tmp_path / "data" / "guild" / "build_queue.json"
    result = run_seed(repo_copy.as_uri(), dest, tmp_path / "backups")
    assert result.returncode == 0, result.stderr
    assert json.loads(dest.read_text()) == [{"id": 1, "status": "idea"}]
    assert "seeded" in result.stdout
    assert not list(dest.parent.glob("*.seed*"))
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644, "a seeded queue must not be left 0600"
    [line] = journal(dest.parent)
    assert line["kind"] == "replaced" and line["writer"] == "seed"
    assert line["before_digest"] is None
    assert line["after_digest"] == qs.sha256_bytes(dest.read_bytes())


def test_existing_live_queue_is_kept_and_backed_up(tmp_path, repo_copy):
    dest = tmp_path / "build_queue.json"
    live = json.dumps([{"id": 1, "status": "in_build", "note": "owner save"}])
    dest.write_text(live)
    backups = tmp_path / "backups"
    result = run_seed(repo_copy.as_uri(), dest, backups)
    assert result.returncode == 0, result.stderr
    assert dest.read_text() == live, "deploy overwrote the live queue"
    saved = list(backups.glob("build_queue.*.json"))
    assert len(saved) == 1 and saved[0].read_text() == live
    assert "not overwritten" in result.stdout


def test_existing_empty_live_file_fails_loudly(tmp_path, repo_copy):
    dest = tmp_path / "build_queue.json"
    dest.write_text("")
    result = run_seed(repo_copy.as_uri(), dest, tmp_path / "backups")
    assert result.returncode != 0
    assert "EMPTY" in result.stderr
    assert dest.read_text() == "", "the seed must not paper over an empty live queue"
    assert not (tmp_path / "backups").exists()
    assert journal(tmp_path) == []


def test_invalid_seed_source_writes_nothing(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    dest = tmp_path / "build_queue.json"
    result = run_seed(bad.as_uri(), dest, tmp_path / "backups")
    assert result.returncode != 0
    assert not dest.exists()
    assert not list(tmp_path.glob(".*seed*"))


def test_seed_waits_for_the_portal_lock(tmp_path, repo_copy):
    dest = tmp_path / "build_queue.json"
    with held_lock(dest):
        result = run_seed(repo_copy.as_uri(), dest, tmp_path / "backups",
                          env={"QUEUE_LOCK_TIMEOUT_S": "0.3"})
    assert result.returncode == 75, result.stderr
    assert "held by another writer" in result.stderr
    assert not dest.exists()
    assert not list(tmp_path.glob(".*seed*"))
    result = run_seed(repo_copy.as_uri(), dest, tmp_path / "backups")
    assert result.returncode == 0, result.stderr


def test_seed_script_requires_python3_explicitly():
    text = SEED.read_text()
    assert "command -v python3" in text and "python3 is required" in text


# ── Publish (two-step, checked, locked) ───────────────────────────────────────

@pytest.fixture
def live_queue(tmp_path):
    folder = tmp_path / "guild"
    folder.mkdir()
    dest = folder / "build_queue.json"
    dest.write_bytes(qs.serialize([{"id": 1, "status": "in_build", "summary": "live"}]))
    os.chmod(dest, 0o664)
    return dest


def fake_docker(tmp_path: Path, mounts: list) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "docker"
    script.write_text("#!/bin/bash\n[ \"$1\" = inspect ] && { cat <<'JSON'\n"
                      + json.dumps(mounts) + "\nJSON\nexit 0; }\nexit 1\n")
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def publish(live_queue, repo_copy, expect, env):
    return run_seed("--publish", "--expect-live-sha256", expect, repo_copy.as_uri(),
                    live_queue, live_queue.parent / "backups", env=env)


def test_publish_replaces_under_the_lock_and_journals(tmp_path, live_queue, repo_copy):
    env = fake_docker(tmp_path, [{"Source": str(live_queue.parent),
                                  "Destination": "/app/runtime/guild"}])
    before = qs.sha256_bytes(live_queue.read_bytes())
    result = publish(live_queue, repo_copy, before, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(live_queue.read_text()) == [{"id": 1, "status": "idea"}]
    assert stat.S_IMODE(live_queue.stat().st_mode) == 0o664, "publish changed the file mode"
    [backup] = (live_queue.parent / "backups").glob("*.before-publish.json")
    assert qs.sha256_bytes(backup.read_bytes()) == before
    [line] = journal(live_queue.parent)
    assert (line["kind"], line["writer"], line["before_digest"]) == ("replaced", "publish", before)
    assert line["after_digest"] == qs.sha256_bytes(live_queue.read_bytes())


def test_publish_takes_the_lock(tmp_path, live_queue, repo_copy):
    env = fake_docker(tmp_path, [{"Source": str(live_queue.parent),
                                  "Destination": "/app/runtime/guild"}])
    before_bytes = live_queue.read_bytes()
    with held_lock(live_queue):
        result = publish(live_queue, repo_copy, qs.sha256_bytes(before_bytes),
                         dict(env, QUEUE_LOCK_TIMEOUT_S="0.3"))
    assert result.returncode == 75, result.stderr
    assert live_queue.read_bytes() == before_bytes


def test_publish_refuses_when_live_changed_since_status(tmp_path, live_queue, repo_copy):
    env = fake_docker(tmp_path, [{"Source": str(live_queue.parent),
                                  "Destination": "/app/runtime/guild"}])
    before_bytes = live_queue.read_bytes()
    result = publish(live_queue, repo_copy, "0" * 64, env)
    assert result.returncode == 1
    assert "changed since you looked" in result.stderr
    assert live_queue.read_bytes() == before_bytes
    assert journal(live_queue.parent) == []


def test_publish_refuses_without_the_portal_folder_mount(tmp_path, live_queue, repo_copy):
    env = fake_docker(tmp_path, [{"Source": str(live_queue),
                                  "Destination": "/app/data/guild/build_queue.json"}])
    before_bytes = live_queue.read_bytes()
    result = publish(live_queue, repo_copy, qs.sha256_bytes(before_bytes), env)
    assert result.returncode == 1
    assert "would detach" in result.stderr
    assert live_queue.read_bytes() == before_bytes


def test_publish_line_does_not_hide_an_unfinished_save(tmp_path, live_queue, repo_copy):
    # A Save crashed before its replace (intent only), then a publish ran.
    s = qs.QueueStore(live_queue, in_container=False)
    live = qs.sha256_bytes(live_queue.read_bytes())
    (live_queue.parent / "queue_journal.jsonl").write_text(json.dumps(
        {"kind": "intent", "op_id": "e" * 32, "op": "status", "item_id": 1,
         "before_digest": live, "after_digest": "9" * 64}) + "\n")
    env = fake_docker(tmp_path, [{"Source": str(live_queue.parent),
                                  "Destination": "/app/runtime/guild"}])
    assert publish(live_queue, repo_copy, live, env).returncode == 0
    outcomes = s.reconcile()
    assert [(o["op_id"], o["outcome"]) for o in outcomes] == [("e" * 32, "not_applied")]


def test_status_prints_the_digest_to_expect(live_queue):
    result = run_seed("--status", live_queue)
    assert result.returncode == 0, result.stderr
    digest = qs.sha256_bytes(live_queue.read_bytes())
    assert f"--expect-live-sha256={digest}" in result.stdout


def test_publish_requires_an_expected_digest(live_queue, repo_copy):
    result = run_seed("--publish", repo_copy.as_uri(), live_queue)
    assert result.returncode == 2


# ── Workflow and sync_docs wiring ─────────────────────────────────────────────

def test_workflow_never_downloads_over_the_live_queue():
    text = WORKFLOW.read_text()
    # No command may write the live queue path directly with curl -o.
    assert not re.search(r"curl[^\n\"]*-o\s+/opt/minimoi/data/guild/build_queue\.json", text)
    assert "/opt/minimoi/scripts/seed_build_queue.sh https://raw.githubusercontent.com/" in text
    assert "${{ github.sha }}/data/guild/build_queue.json" in text, "seed source must be pinned to the deployed commit"
    assert "base64 -w 0 scripts/operations/seed_build_queue.sh" in text
    assert "/opt/minimoi/scripts/seed_build_queue.sh\\\"]" in text, "seed script must be made executable on EC2"


def test_workflow_checks_the_portal_queue_after_deploy():
    import yaml
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]["steps"]
    names = [step.get("name") for step in steps]
    deploy = names.index("Deploy on EC2")
    check = names.index("Verify the portal sees the live Build Queue")
    assert check == deploy + 1
    run = steps[check]["run"]
    assert "base64 -w 0 scripts/operations/check_queue_mount.sh" in run
    assert ("/opt/minimoi/scripts/check_queue_mount.sh /opt/minimoi/data/guild/build_queue.json "
            "minimoi-portal /app/runtime/guild/build_queue.json") in run
    assert 'if [ "$STATUS" != "Success" ]' in run and "exit 1" in run


def _fake_portal(tmp_path, host_queue: Path, container_file: Path, mounts: str, env_path: str):
    bin_dir = tmp_path / "cbin"
    bin_dir.mkdir()
    script = bin_dir / "docker"
    script.write_text(f"""#!/bin/bash
if [ "$1" = inspect ]; then echo '{mounts}'; exit 0; fi
if [ "$1" = exec ] && [ "$3" = printenv ]; then echo '{env_path}'; exit 0; fi
if [ "$1" = exec ] && [ "$3" = sha256sum ]; then sha256sum '{container_file}'; exit 0; fi
exit 1
""")
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def run_check(host_queue, env):
    return subprocess.run(["bash", str(CHECK), str(host_queue), "minimoi-portal",
                           "/app/runtime/guild/build_queue.json"],
                          capture_output=True, text=True, env=dict(os.environ, **env))


def test_post_deploy_check_passes_when_the_portal_sees_the_live_file(tmp_path, live_queue):
    env = _fake_portal(tmp_path, live_queue, live_queue,
                       f"{live_queue.parent}=/app/runtime/guild;",
                       "/app/runtime/guild/build_queue.json")
    result = run_check(live_queue, env)
    assert result.returncode == 0, result.stderr
    assert "queue check OK" in result.stdout


def test_post_deploy_check_fails_on_a_single_file_mount(tmp_path, live_queue):
    env = _fake_portal(tmp_path, live_queue, live_queue,
                       f"{live_queue}=/app/data/guild/build_queue.json;",
                       "/app/runtime/guild/build_queue.json")
    result = run_check(live_queue, env)
    assert result.returncode == 1 and "does not mount" in result.stderr


def test_post_deploy_check_fails_when_checksums_differ(tmp_path, live_queue):
    baked = tmp_path / "baked.json"
    baked.write_text("[]")
    env = _fake_portal(tmp_path, live_queue, baked,
                       f"{live_queue.parent}=/app/runtime/guild;",
                       "/app/runtime/guild/build_queue.json")
    result = subprocess.run(
        ["bash", "-c", f"sleep() {{ :; }}; export -f sleep; bash {CHECK} {live_queue} "
                       "minimoi-portal /app/runtime/guild/build_queue.json"],
        capture_output=True, text=True, env=dict(os.environ, **env))
    assert result.returncode == 1
    assert "not the host's live file" in result.stderr


def _sync_dry_run(*args):
    env = dict(os.environ, SYNC_DOCS_DRY_RUN="1")
    return subprocess.run(["bash", str(SYNC), *args], capture_output=True, text=True, env=env, cwd=REPO)


def test_sync_docs_leaves_live_queue_by_default():
    result = _sync_dry_run()
    assert result.returncode == 0, result.stderr
    assert "build_queue.json" not in result.stdout.replace("Build Queue", "")
    assert "left untouched" in result.stdout


def test_sync_docs_publish_queue_step_one_publishes_nothing():
    result = _sync_dry_run("--publish-queue")
    assert result.returncode == 0, result.stderr
    assert "NOT published" in result.stdout
    assert "/opt/minimoi/scripts/seed_build_queue.sh --status /opt/minimoi/data/guild/build_queue.json" in result.stdout
    assert "--publish " not in result.stdout


def test_sync_docs_publish_queue_uses_the_locked_checked_writer():
    digest = "a" * 64
    result = _sync_dry_run("--publish-queue", f"--expect-live-sha256={digest}")
    assert result.returncode == 0, result.stderr
    [line] = [l for l in result.stdout.splitlines() if "build_queue.json" in l and "seed_build_queue" in l]
    assert line.startswith("/opt/minimoi/scripts/seed_build_queue.sh --publish --expect-live-sha256 " + digest)
    assert "/data/guild/build_queue.json' /opt/minimoi/data/guild/build_queue.json /opt/minimoi/data/guild/backups" in line
    # No unlocked writer is left: no in-place cat, no bare mv, no curl -o over the queue.
    assert "> /opt/minimoi/data/guild/build_queue.json" not in result.stdout
    assert " mv " not in result.stdout


def test_sync_docs_rejects_bad_digest_and_unknown_arguments():
    assert _sync_dry_run("--everything").returncode == 2
    assert _sync_dry_run("--publish-queue", "--expect-live-sha256=xyz").returncode == 2
    assert _sync_dry_run("--expect-live-sha256=" + "a" * 64).returncode == 2
