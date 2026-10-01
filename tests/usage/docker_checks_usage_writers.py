"""The usage store with root writers and the non-root gateway (the U2
first-writer ownership fix): cos-bot and cos-scheduler (root) write their
helper records only into their own folders; the gateway (non-root) owns the
shared monthly file.

Opt-in (needs Docker and a local image with Python and the repo's
dependencies, the staging portal image by default):

    USAGE_UID_IMAGE=minimoi-staging/portal:<sha7> \\
    venv/bin/python3 -m pytest tests/usage/docker_checks_usage_writers.py -q -p no:cacheprovider

Each check runs throwaway containers on a named Docker volume (real Linux file
ownership, not a Mac bind mount), with no network: root for cos-bot and
cos-scheduler, uid 1000 for the gateway. The store is opened to the gateway's
uid first, as staging's shared folder is.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("USAGE_UID_IMAGE", "minimoi-staging/portal:924f8fe")


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason=f"needs docker and the local image {IMAGE}")

GATEWAY_APPEND = (
    "import datetime\nfrom services.usage import usage_record\n"
    "rec = {'occurred_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'env': 'staging',"
    " 'emitter': 'gateway', 'actor': 'cos', 'kind': 'model', 'route': 'minimoi-cos-agent', 'status': 'ok',"
    " 'input_tokens': 50, 'output_tokens': 7, 'cost_usd': 0.001, 'cost_source': 'price_table'}\n"
    "try:\n    usage_record.append(rec, '/data'); print('appended')\n"
    "except PermissionError: print('permission denied')\n")


def _run(volume: str, script: str, *, user: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "-v", f"{REPO}:/src:ro",
           "-v", f"{volume}:/data", "-e", "PYTHONPATH=/src", "-w", "/src"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    if user:
        cmd += ["--user", user]
    cmd += ["--entrypoint", "python", IMAGE, "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def _helper_writes(volume: str, writer: str) -> subprocess.CompletedProcess:
    return _run(volume, (
        "from services.usage import direct, usage_record\n"
        "assert direct.search(name='cos-novelty-watch', actor='cos')\n"
        "assert direct.model_call(name='cos-grok-backend', actor='cos', route='cos-grok-direct:chat', provider='xai',"
        " model='grok-4.3', response={'usage': {'prompt_tokens': 3, 'completion_tokens': 2}})\n"
        "usage_record.flush()\n"), env={"MINIMOI_USAGE_DIR": "/data", "MINIMOI_USAGE_WRITER": writer})


@pytest.fixture
def volume():
    name = f"usage-writers-{secrets.token_hex(4)}"
    subprocess.run(["docker", "volume", "create", name], capture_output=True, check=True)
    _run(name, "import os; os.chmod('/data', 0o777)")
    yield name
    subprocess.run(["docker", "volume", "rm", "-f", name], capture_output=True)


def test_the_hazard_a_root_writer_that_creates_the_shared_file_first_locks_the_gateway_out(volume):
    assert _run(volume, GATEWAY_APPEND).stdout.strip() == "appended"                  # root, into the shared file
    assert _run(volume, GATEWAY_APPEND, user="1000:1000").stdout.strip() == "permission denied"


def test_cos_writers_use_their_own_folders_and_the_gateway_keeps_appending(volume):
    for writer in ("cos-bot", "cos-scheduler"):                                       # root writers first, as on Oct 1
        r = _helper_writes(volume, writer)
        assert r.returncode == 0, r.stderr
    listing = _run(volume, "import os, json; print(json.dumps(sorted(os.listdir('/data'))))")
    assert json.loads(listing.stdout.strip()) == ["cos-bot", "cos-scheduler"]         # no shared monthly file yet
    for _ in range(2):
        gateway = _run(volume, GATEWAY_APPEND, user="1000:1000")
        assert gateway.stdout.strip() == "appended", gateway.stderr
    assert _helper_writes(volume, "cos-bot").returncode == 0                          # root keeps writing its own
    read = _run(volume, (
        "import json\nfrom services.usage import usage_record\n"
        "print(json.dumps(sorted(r['emitter'] for r in usage_record.read_all('/data'))))\n"))
    assert json.loads(read.stdout.strip().splitlines()[-1]) == sorted(
        ["gateway"] * 2 + ["helper:cos-grok-backend", "helper:cos-novelty-watch"] * 3)
