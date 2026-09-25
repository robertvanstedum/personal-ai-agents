"""No Docker/model calls: transport doubles are not participation evidence."""
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_cos_agent_responder import agent_setup, turn
from test_cos_records_bridge import bridge
import synthetic_cos_setup as setup
from cos_agent_responder import OpenClawMeetingModel
from cos_records_bridge import RoomBridgeError


def digest():
    return hashlib.sha256(setup.BACKEND.read_bytes()).hexdigest()


def test_source_mismatch_prevents_transport():
    with pytest.raises(RoomBridgeError, match="source changed"):
        setup.DevContainerBackend("0" * 64)


def test_fresh_setup_private_files_and_real_store(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.tempfile, "tempdir", str(tmp_path))
    root = setup.initialize()
    second = setup.initialize()
    assert root != second
    for name in ("setup.json", "bridge.json", "cos-key.txt", "records/owner-key.txt"):
        assert (root / name).stat().st_mode & 0o077 == 0
    manifest = json.loads((root / "setup.json").read_text())
    store = setup.Store(root / "records")
    room = store.room("robert", manifest["session_id"])
    assert room["state"] == "active"
    assert any(m["id"] == "cos-dev" and m["role"] == "contributor" for m in room["members"])
    assert manifest["backend_sha256"] == digest()
    assert "Synthetic test" in room["events"][-1]["body"]


def test_watching_required_before_configuration_read(tmp_path):
    with pytest.raises(RoomBridgeError, match="Robert must"):
        setup.run_turn(tmp_path, str(uuid4()), "contribute")


def test_fixed_transport_stdin_not_arguments(agent_setup):
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        data = json.loads(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(
            text="Synthetic transport double", coordination_request_id=data["context"]["confer"]["receipt_id"],
            openclaw_run_id="chatcmpl_" + str(uuid4()), agent_id="cos-agent-a", mode="actual_agent_response")))
    model = OpenClawMeetingModel(setup.DevContainerBackend(digest(), runner))
    result = turn(agent_setup, model=model)
    assert result["operation"]["receipt_id"]
    args, options = calls[0]
    assert args[:7] == ["docker", "--context", "colima", "exec", "-i", "minimoi-cos-dev", "python"]
    assert options["timeout"] == 140 and options["check"] is False
    assert "Synthetic question" not in " ".join(args)
    assert "Synthetic question" in options["input"]
    assert "shell" not in options


@pytest.mark.parametrize("failure", ["timeout", "exit", "json", "missing"])
def test_transport_failure_is_uncertain_and_not_retried(agent_setup, failure):
    calls = []
    def runner(*args, **kwargs):
        calls.append(1)
        if failure == "timeout": raise subprocess.TimeoutExpired("docker", 140)
        if failure == "missing": raise FileNotFoundError("secret should not appear")
        return SimpleNamespace(returncode=1 if failure == "exit" else 0, stdout="secret invalid json")
    model = OpenClawMeetingModel(setup.DevContainerBackend(digest(), runner))
    request_id = str(uuid4())
    with pytest.raises(RoomBridgeError, match="uncertain") as error:
        turn(agent_setup, request_id, model=model)
    assert "secret" not in str(error.value)
    with pytest.raises(RoomBridgeError, match="uncertain"):
        turn(agent_setup, request_id, model=model)
    assert len(calls) == 1


@pytest.mark.parametrize("route", ["http://cos-agent-a:18789/v1", "https://production.example/v1"])
def test_remote_runner_route_gate_and_safe_errors(route):
    # Execute the exact shim with a fake backend, no network or Docker.
    source = '''from dataclasses import dataclass
@dataclass
class Reply:
    text: str = "synthetic double"
class OpenClawBackend:
    def __init__(self, **kwargs):
        self._gateway_url = ROUTE
        self._agent_id = "cos-agent-a"
    def call_backend_with_evidence(self, *args):
        return Reply()
'''.replace("ROUTE", repr(route))
    payload = dict(source=source, sha256=hashlib.sha256(source.encode()).hexdigest(),
                   prompt="synthetic", context={}, policy={})
    result = subprocess.run([sys.executable, "-c", setup.REMOTE], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=5)
    if route.startswith("http:"):
        assert result.returncode == 0
        assert json.loads(result.stdout)["text"] == "synthetic double"
    else:
        assert result.returncode == 1 and not result.stdout
        assert "production.example" not in result.stderr
