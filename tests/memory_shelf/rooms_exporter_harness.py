"""Opt-in: build real bundles with Records' own exporter, in a throwaway store (no real data).

Mirrors the ``env`` fixture of Records' ``tests/test_rooms_r1.py``: a test app, MC provisioned, one meeting in which the
owner asks and a committed MC turn carries execution evidence (schema 1.1), and one older plain meeting (schema 1.0).
Needs Flask and jsonschema in the interpreter and the path of a Records checkout (``RECORDS_EXPORTER_DIR``)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4


def make_real_bundles(exporter_dir: Path, workdir: Path, transcripts_out: Path) -> tuple[tuple[str, ...], str]:
    sys.path.insert(0, str(exporter_dir))
    from datetime import datetime, timedelta, timezone
    from app import create_app
    from manage import provision_rooms
    from transcript_publish import publish

    workdir.mkdir(parents=True, exist_ok=True)
    app = create_app(workdir / "private", testing=True)
    store = app.extensions["records_store"]
    outbox = workdir / "outbox"
    provision_rooms(store, "mc", "Master Craftsman", outbox)
    tokens = {n: (outbox / f"{n}.token").read_text().strip() for n in ("mc", "rooms-worker")}
    client = app.test_client()
    header = lambda token: {"Authorization": "Bearer " + token}
    owner, mc, worker = header(store.owner_key), header(tokens["mc"]), header(tokens["rooms-worker"])

    def call(method, headers, path, body=None):
        h = dict(headers)
        if method != "GET":
            h["Idempotency-Key"] = str(uuid4())
        return client.open("/api/v1" + path, method=method, headers=h, json=body if method != "GET" else None)

    old = call("POST", owner, "/rooms", dict(title="Old", purpose="p", recording_acknowledged=True)).json["result"]["id"]
    call("POST", owner, f"/rooms/{old}/events", {"body": "a historical message"})
    room = call("POST", owner, "/rooms", dict(title="Design chat", purpose="Synthetic", recording_acknowledged=True)).json["result"]["id"]
    with store.connect() as db:
        db.execute("UPDATE teammates SET proven_at=? WHERE principal='mc'", ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),))
    assert call("POST", owner, f"/rooms/{room}/invite", {"actor": "mc"}).status_code == 201
    assert call("POST", mc, f"/rooms/{room}/rsvp", {"state": "accepted"}).status_code == 200
    assert call("POST", owner, f"/rooms/{room}/events", {"body": "Q"}).status_code == 201
    turn = call("POST", worker, "/turns/claim", {"addressees": ["mc"]}).json["turn"]
    call("POST", worker, f"/turns/{turn['id']}/start", {"claim_id": turn["claim_id"]})
    reply = call("POST", mc, f"/rooms/{room}/events", {
        "kind": "message", "body": "One short point.", "context_class": "agent_draft", "turn_id": turn["id"],
        "claim_id": turn["claim_id"], "expected_context": {"generation": turn["generation"], "trigger_seq": turn["trigger_seq"]},
        "origin": {"source_application": "rooms_worker", "mode": "agent_response", "agent_id": "mc-agent",
                   "runtime": "OpenClaw", "execution_id": "a" * 32}, "usage_evidence": {"status": "none"}})
    assert reply.status_code in (200, 201), reply.json
    transcripts_out.mkdir(parents=True, exist_ok=True)
    instance = ""
    for session in (old, room):
        bundle = publish(store, "robert", session)
        manifest = json.loads((bundle / "manifest.json").read_text())
        instance = manifest["source_instance_id"]
        (transcripts_out / bundle.name).symlink_to(bundle) if False else __import__("shutil").copytree(bundle, transcripts_out / bundle.name)
    return ("robert",), instance
