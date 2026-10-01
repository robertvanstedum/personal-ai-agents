"""The usage store with two writers of different uids (#275 review, finding 4):
the portal (root) and the gateway (non-root) share one folder.

Opt-in (needs Docker and a local image with Python and the repo's
dependencies, the staging portal image by default):

    USAGE_UID_IMAGE=minimoi-staging/portal:<sha7> \\
    venv/bin/python3 -m pytest tests/usage/docker_checks_usage_owners.py -q -p no:cacheprovider

Each check runs throwaway containers on a named Docker volume (real Linux file
ownership, not a Mac bind mount), with no network: root for the portal, uid
1000 for the gateway. The folder is opened to the gateway's uid first, as
staging's shared folder is.

* The hazard, as it was: a root writer that creates this month's shared file
  first (0600) leaves a file the gateway can no longer append to.
* The fix: the portal writes only into usage/portal/, so the gateway creates
  and appends its own monthly file, both writers keep writing, and the
  readers find both kinds of record.
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
    r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason=f"needs docker and the local image {IMAGE}")

GATEWAY_RECORD = ("{'occurred_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), "
                  "'env': 'staging', 'emitter': 'gateway', 'actor': 'mc', 'kind': 'model', 'route': 'minimoi-mc-agent', "
                  "'status': 'ok', 'input_tokens': 50, 'output_tokens': 7, 'cost_usd': 0.001, 'cost_source': 'price_table'}")


def _run(volume: str, script: str, user: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "-v", f"{REPO}:/src:ro",
           "-v", f"{volume}:/data", "-e", "PYTHONPATH=/src", "-w", "/src"]
    if user:
        cmd += ["--user", user]
    cmd += ["--entrypoint", "python", IMAGE, "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


@pytest.fixture
def volume():
    name = f"usage-owners-{secrets.token_hex(4)}"
    subprocess.run(["docker", "volume", "create", name], capture_output=True, check=True)
    _run(name, "import os; os.chmod('/data', 0o777)")                 # the shared folder, open to the gateway's uid
    yield name
    subprocess.run(["docker", "volume", "rm", "-f", name], capture_output=True)


GATEWAY_APPEND = ("import sys; from services.usage import usage_record\n"
                  "try:\n    usage_record.append(" + GATEWAY_RECORD + ", '/data'); print('appended')\n"
                  "except PermissionError: print('permission denied')\n")


def test_the_hazard_a_root_writer_that_creates_the_shared_file_first_locks_the_gateway_out(volume):
    first = _run(volume, GATEWAY_APPEND)                                 # root creates the shared monthly file
    assert first.stdout.strip() == "appended", first.stderr
    gateway = _run(volume, GATEWAY_APPEND, user="1000:1000")
    assert gateway.stdout.strip() == "permission denied", gateway.stderr


def test_the_portal_writes_only_its_own_folder_and_both_writers_keep_writing(volume):
    portal = _run(volume, (
        "import os, json\n"
        "os.environ['MINIMOI_USAGE_DIR'] = '/data'\n"
        "from minimoi_portal.guild_ui.mc import stream_usage\n"
        "assert stream_usage.record_run(turn_id='a' * 32, status='ok', prompt_tokens=50, completion_tokens=7)\n"
        "stream_usage.flush()\n"
        "print(json.dumps({'shared': sorted(n for n in os.listdir('/data') if n.startswith('usage-')),"
        " 'portal': sorted(os.listdir('/data/portal'))}))\n"))
    assert portal.returncode == 0, portal.stderr
    placed = json.loads(portal.stdout.strip().splitlines()[-1])
    assert placed["shared"] == [] and len(placed["portal"]) == 1          # the shared monthly file was not created
    for _ in range(2):                                                    # the gateway creates, then appends again
        gateway = _run(volume, GATEWAY_APPEND, user="1000:1000")
        assert gateway.stdout.strip() == "appended", gateway.stderr
    again = _run(volume, (
        "import os\nos.environ['MINIMOI_USAGE_DIR'] = '/data'\n"
        "from minimoi_portal.guild_ui.mc import stream_usage\n"
        "assert stream_usage.record_run(turn_id='b' * 32, status='error', error_class='stopped')\n"
        "stream_usage.flush()\n"))
    assert again.returncode == 0, again.stderr
    read = _run(volume, (
        "import json\nfrom services.usage import usage_record\n"
        "shared = usage_record.read('/data'); own = usage_record.read('/data/portal')\n"
        "print(json.dumps({'gateway': [r['emitter'] for r in shared], 'portal': [r['emitter'] for r in own]}))\n"))
    found = json.loads(read.stdout.strip().splitlines()[-1])
    assert found == {"gateway": ["gateway", "gateway"], "portal": ["runtime-stream", "runtime-stream"]}
