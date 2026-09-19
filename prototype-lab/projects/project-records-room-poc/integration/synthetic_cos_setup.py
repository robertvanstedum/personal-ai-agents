"""Owner-operated synthetic test only; never deploys or patches mounted CoS.

The reviewed backend is executed in memory inside the existing development
container. Credentials stay there. This tests the Records connector against
the actual dev runtime, NOT the portal route or production integration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cos_agent_responder import OpenClawMeetingModel, SyntheticSessionPolicy, respond
from cos_records_bridge import RoomBridgeError, RoomClient, private_file
from cos_room_responder import TurnJournal
from store import Store


BACKEND = Path(__file__).resolve().parents[4] / "domains/cos/backends/openclaw_backend.py"
# Trusted local source, sent on stdin; no file is changed inside the container.
REMOTE = '''import dataclasses, hashlib, json, sys, types
try:
    data = json.load(sys.stdin)
    source = data["source"]
    if hashlib.sha256(source.encode()).hexdigest() != data["sha256"]:
        raise ValueError("source mismatch")
    module = types.ModuleType("records_reviewed_backend")
    sys.modules[module.__name__] = module
    exec(compile(source, "<records-reviewed-backend>", "exec"), module.__dict__)
    backend = module.OpenClawBackend(write_memory=None, dispatch_tool=None)
    if backend._gateway_url != "http://cos-agent-a:18789/v1" or backend._agent_id != "cos-agent-a":
        raise ValueError("unexpected development route")
    reply = backend.call_backend_with_evidence(data["prompt"], data["context"], data["policy"])
    print(json.dumps(dataclasses.asdict(reply)))
except Exception:
    # Never serialize credentials, provider error text or private environment.
    print("Synthetic runtime attempt failed; reconcile before retry.", file=sys.stderr)
    sys.exit(1)
'''


class DevContainerBackend:
    def __init__(self, expected_sha256, runner=subprocess.run):
        self.source = BACKEND.read_text()
        self.digest = hashlib.sha256(self.source.encode()).hexdigest()
        if self.digest != expected_sha256:
            raise RoomBridgeError("Reviewed backend source changed; review again before inference.")
        self.runner = runner

    def call_backend_with_evidence(self, prompt, context, policy):
        payload = dict(source=self.source, sha256=self.digest, prompt=prompt,
                       context=context, policy=policy)
        try:
            completed = self.runner(
                ["docker", "--context", "colima", "exec", "-i", "minimoi-cos-dev",
                 "python", "-c", REMOTE],
                input=json.dumps(payload), text=True, capture_output=True,
                timeout=140, check=False)
            if completed.returncode:
                raise ValueError
            value = json.loads(completed.stdout)
            if not isinstance(value, dict):
                raise ValueError
            return SimpleNamespace(**value)
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            raise RoomBridgeError("Dev runtime outcome uncertain; no automatic inference retry.") from None


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(value)


def initialize():
    # Always fresh: cannot accidentally provision an old/private live database.
    root = Path(tempfile.mkdtemp(prefix="records-synthetic-cos-"))
    store = Store(root / "records")
    session_id = store.create_room("robert", "synthetic-create", dict(
        title="Synthetic CoS participation test", purpose="Non-private test; no operational action.",
        mode="meeting", recording_acknowledged=True))["result"]["id"]
    token = store.add_principal("robert", "synthetic-principal", dict(
        id="cos-dev", label="Development CoS"))["access_token"]
    store.membership("robert", "synthetic-invite", session_id,
                     dict(actor="cos-dev", role="contributor"))
    store.append("robert", "synthetic-question", session_id, dict(
        body="Synthetic test: identify your role and summarize this test's purpose. Cite the source record IDs. Take no external action.",
        context_class="robert_source"))
    write_private(root / "cos-key.txt", token)
    write_private(root / "bridge.json", json.dumps(dict(url="http://127.0.0.1:18880",
        actor_id="cos-dev", token_file=str(root / "cos-key.txt"))))
    manifest = dict(session_id=session_id, synthetic_only=True,
                    backend_sha256=hashlib.sha256(BACKEND.read_bytes()).hexdigest())
    write_private(root / "setup.json", json.dumps(manifest))
    return root


def run_turn(root, request_id, action, *, owner_watching=False):
    if owner_watching is not True:
        raise RoomBridgeError("Robert must be present and explicitly authorize this test turn.")
    manifest = json.loads(private_file(root / "setup.json").read_text())
    if manifest.get("synthetic_only") is not True:
        raise RoomBridgeError("Only synthetic setup is supported.")
    return respond(manifest["session_id"], request_id,
        client=RoomClient(root / "bridge.json"),
        model=OpenClawMeetingModel(DevContainerBackend(manifest["backend_sha256"])),
        journal=TurnJournal(root / "attempts"),
        policy=SyntheticSessionPolicy([manifest["session_id"]]),
        owner_authorized=True, action=action)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "serve", "respond"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--action", choices=["contribute", "brief"], default="contribute")
    parser.add_argument("--owner-watching", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        root = initialize()
        print(json.dumps(dict(root=str(root), owner_key_file=str(root / "records/owner-key.txt"))))
        return
    if args.root is None:
        parser.error("--root is required")
    private_file(args.root / "setup.json")
    if args.command == "serve":
        from app import create_app
        create_app(args.root / "records").run(host="127.0.0.1", port=18880, use_reloader=False)
    else:
        print(json.dumps(run_turn(args.root, args.request_id, args.action,
                                  owner_watching=args.owner_watching), indent=2))


if __name__ == "__main__":
    main()
