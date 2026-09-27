"""A deploy must never overwrite the live Build Queue on EC2.

The production portal writes /opt/minimoi/data/guild/build_queue.json when the
owner saves a status. Deploys seed it only when it is missing or empty; the
repository copy replaces a live queue only through the explicit
`sync_docs.sh --publish-queue`, which backs up first.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "scripts" / "operations" / "seed_build_queue.sh"
SYNC = REPO / "scripts" / "sync_docs.sh"
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"


def run_seed(src: Path, dest: Path, backups: Path):
    return subprocess.run(
        ["bash", str(SEED), src.as_uri(), str(dest), str(backups)],
        capture_output=True, text=True,
    )


@pytest.fixture
def repo_copy(tmp_path):
    src = tmp_path / "repo_queue.json"
    src.write_text(json.dumps([{"id": 1, "status": "idea"}]))
    return src


def test_first_deploy_seeds_missing_queue(tmp_path, repo_copy):
    dest = tmp_path / "data" / "guild" / "build_queue.json"
    result = run_seed(repo_copy, dest, tmp_path / "backups")
    assert result.returncode == 0, result.stderr
    assert json.loads(dest.read_text()) == [{"id": 1, "status": "idea"}]
    assert "seeded" in result.stdout
    assert not list(dest.parent.glob("*.seed.*"))


def test_existing_live_queue_is_kept_and_backed_up(tmp_path, repo_copy):
    dest = tmp_path / "build_queue.json"
    live = json.dumps([{"id": 1, "status": "in_build", "note": "owner save"}])
    dest.write_text(live)
    backups = tmp_path / "backups"
    result = run_seed(repo_copy, dest, backups)
    assert result.returncode == 0, result.stderr
    assert dest.read_text() == live, "deploy overwrote the live queue"
    saved = list(backups.glob("build_queue.*.json"))
    assert len(saved) == 1 and saved[0].read_text() == live
    assert "not overwritten" in result.stdout


def test_empty_live_file_is_seeded_in_place(tmp_path, repo_copy):
    # The live file may be a single-file bind mount: it must keep its inode.
    dest = tmp_path / "build_queue.json"
    dest.write_text("")
    inode = dest.stat().st_ino
    result = run_seed(repo_copy, dest, tmp_path / "backups")
    assert result.returncode == 0, result.stderr
    assert json.loads(dest.read_text())[0]["id"] == 1
    assert dest.stat().st_ino == inode, "seed replaced the file; a single-file mount would detach"
    assert not list(tmp_path.glob("*.seed.*"))


def test_invalid_seed_source_writes_nothing(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    dest = tmp_path / "build_queue.json"
    result = run_seed(bad, dest, tmp_path / "backups")
    assert result.returncode != 0
    assert not dest.exists()
    assert not list(tmp_path.glob("*.seed.*"))


def test_workflow_never_downloads_over_the_live_queue():
    text = WORKFLOW.read_text()
    # No command may write the live queue path directly with curl -o.
    assert not re.search(r"curl[^\n\"]*-o\s+/opt/minimoi/data/guild/build_queue\.json", text)
    assert "/opt/minimoi/scripts/seed_build_queue.sh https://raw.githubusercontent.com/" in text
    assert "${{ github.sha }}/data/guild/build_queue.json" in text, "seed source must be pinned to the deployed commit"
    assert "base64 -w 0 scripts/operations/seed_build_queue.sh" in text
    assert "/opt/minimoi/scripts/seed_build_queue.sh\\\"]" in text, "seed script must be made executable on EC2"


def _sync_dry_run(*args):
    env = dict(os.environ, SYNC_DOCS_DRY_RUN="1")
    return subprocess.run(["bash", str(SYNC), *args], capture_output=True, text=True, env=env, cwd=REPO)


def test_sync_docs_leaves_live_queue_by_default():
    result = _sync_dry_run()
    assert result.returncode == 0, result.stderr
    assert "build_queue.json" not in result.stdout.replace("Build Queue", "")
    assert "left untouched" in result.stdout


def test_sync_docs_publish_queue_backs_up_before_replacing():
    result = _sync_dry_run("--publish-queue")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    backup = next(i for i, l in enumerate(lines) if "before-publish.json" in l)
    publish = next(i for i, l in enumerate(lines) if "build_queue.json.publish" in l)
    assert backup < publish
    assert "json.tool" in lines[publish], "published copy must be validated before it replaces the live file"
    # Publish writes into the existing file (keeps a single-file bind mount attached), never mv.
    assert "cat /opt/minimoi/data/guild/build_queue.json.publish > /opt/minimoi/data/guild/build_queue.json" in lines[publish]
    assert " mv " not in lines[publish]


def test_sync_docs_rejects_unknown_arguments():
    result = _sync_dry_run("--everything")
    assert result.returncode == 2
