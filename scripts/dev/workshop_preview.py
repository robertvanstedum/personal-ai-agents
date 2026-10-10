#!/usr/bin/env python3
"""A local preview of the topic Workshop over the shared Workshop record, on invented data. Local only: it binds 127.0.0.1, signs you in
as a test owner (there is no login here), uses a throw-away data folder, and touches no real topic, queue, journal or credential.

    python scripts/dev/workshop_preview.py [--port 5057] [--data DIR] [--reset]
    open http://127.0.0.1:5057/guild-next/guild/workshop

It builds six topics so every state can be looked at: a linked topic with a waiting question and a claimed approval, a second linked
topic, an unlinked one, and linked topics whose shared record is damaged, cut off, or not synced yet."""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "guild" / "shop_floor"))

OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 1}
WORKSHOP = "workshop-neubau"
API = "/guild-next/api/v1"


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        self.now += timedelta(minutes=7)
        return self.now


def build_workshops(home: Path) -> None:
    from core.workshop_journal.journal import Journal
    work = home.parent / "source-workshops"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, mode=0o700)
    clock = Clock(datetime.now(timezone.utc) - timedelta(days=2, hours=3))
    j = Journal(str(work), WORKSHOP, clock=clock)

    def send(actor, kind, text, payload, topic, **extra):
        r = j.append({"actor": actor, "kind": kind, "item": f"topic:{topic}", "topic": topic, "text": text, "payload": payload, **extra})
        assert r.ok, r.to_json()
        return r
    g = "garden-build"
    send("claude-code", "decision", "Proposal A: pine beds, six of them.", {"record_event_kind": "proposed", "resolves": [], "reason": "cheapest"}, g)
    req = send("claude-code", "request", "Review plan v1 of the raised beds.", {"action": "review", "expected_result": "Numbered findings."}, g, recipients=["codex"])
    send("codex", "receipt", "Codex picked up the review.", {"request_id": req.event_id, "recipient": "codex"}, g)
    send("codex", "result", "Three findings: pine will rot in five years; the drip line is missing; the corner shades after 3 pm.",
         {"request_id": req.event_id, "recipient": "codex", "outcome": "completed", "limitations": ["did not visit the site"]}, g)
    send("claude-code", "needs_you", "Cedar costs about 40% more than pine. Which do you want?",
         {"reason_code": "owner_choice", "requested_action": "Pick cedar or pine.", "incident_id": "11111111-2222-4333-8444-555555555551"}, g, recipients=["robert"])
    req2 = send("claude-code", "request", "Build the bed frames from plan v2 (cedar).", {"action": "build", "expected_result": "Frames cut and a test report.", "resource": "garage-bench"},
                g, recipients=["codex"])
    send("codex", "receipt", "Codex picked up the build.", {"request_id": req2.event_id, "recipient": "codex"}, g)
    send("codex", "result", "All six frames cut and checked; one is 3 mm out of square.", {"request_id": req2.event_id, "recipient": "codex", "outcome": "completed",
         "limitations": ["drip line not installed"], "test_summary": {"reported": 12, "run": 12, "passed": 11, "failed": 1, "not_run": 0}}, g)
    send("robert", "decision", "Skip the timer.", {"record_event_kind": "approved-direct", "resolves": [], "reason": "claimed"}, g,
         authority_ref={"type": "owner-control", "ref": "not-recognised"})
    send("claude-code", "needs_you", "One frame failed the squareness check. Re-cut it, or accept 3 mm out?",
         {"reason_code": "owner_choice", "requested_action": "Re-cut or accept.", "incident_id": "11111111-2222-4333-8444-555555555552"}, g, recipients=["robert"])
    send("claude-code", "progress", "Drip line parts ordered; install scheduled for Saturday.", {"action": "ordering"}, g)
    v = "vault-backup"
    send("claude-code", "progress", "Vault T1 tools reviewed; custody work not started.", {"action": "status"}, v)
    send("codex", "needs_you", "Backup destination still undecided.", {"reason_code": "owner_choice", "requested_action": "Choose a destination.",
         "incident_id": "11111111-2222-4333-8444-555555555553"}, v, recipients=["robert"])
    # the synced copy the portal reads: events only, no lock file
    shutil.rmtree(home, ignore_errors=True)
    for name, mutate in ((WORKSHOP, None), ("workshop-damaged", b"this line is not json\n"), ("workshop-cutoff", b'{"v":2,"cut off in the mid')):
        (home / name).mkdir(parents=True, mode=0o700)
        os.chmod(home, 0o700)
        data = (work / WORKSHOP / "events.jsonl").read_bytes()
        (home / name / "events.jsonl").write_bytes(data + (mutate or b""))
        os.chmod(home / name / "events.jsonl", 0o600)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5057)
    ap.add_argument("--data", default=str(REPO.parent / "workshop-preview-data"))
    ap.add_argument("--reset", action="store_true", help="rebuild all invented data")
    a = ap.parse_args()
    data = Path(a.data)
    if a.reset or not data.exists():
        shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True, exist_ok=True)
    os.chmod(data, 0o700)
    home = data / "workshops"
    if not (home / WORKSHOP).exists():
        build_workshops(home)
    os.environ.update({"MINIMOI_GUILD_NEXT": "1", "MINIMOI_WORKSHOPS_DIR": str(home), "MINIMOI_WORKSHOP_ID": WORKSHOP})
    os.environ.pop("DATABASE_URL", None)
    import core.get_secret as secrets_module
    import minimoi_portal.config as portal_config
    from floor_helpers import write_queue
    from floor_db_helpers import SqliteFloor
    secrets_module.get_secret = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the preview never reads secrets"))
    from domains.guild import queue_store as qs
    qs._running_in_container = lambda: False
    queue = write_queue(data / "state" / "guild" / "build_queue.json")
    portal_config.GUILD_QUEUE_PATH = str(queue)
    portal_config.BASE_URL = f"http://127.0.0.1:{a.port}"
    spec = importlib.util.spec_from_file_location("workshop_preview_app", REPO / "minimoi_portal" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.app
    app.secret_key = app.secret_key or "local-preview-only"
    app.config.update(SESSION_COOKIE_SECURE=False, SESSION_COOKIE_SAMESITE="Lax")
    floor = SqliteFloor(data / "floor")
    app.extensions["guild_ui_next"]["services"].floor = floor.store()
    from flask import session

    @app.before_request
    def signed_in_as_the_test_owner():                    # local preview only: there is no login on this server
        if "user" not in session:
            session["user"] = dict(OWNER)
    seed(app, data, queue)
    print(f"\nLocal preview (invented data, no login, 127.0.0.1 only): http://127.0.0.1:{a.port}/guild-next/guild/workshop\n")
    app.run(host="127.0.0.1", port=a.port, debug=False, use_reloader=False)
    return 0


_COUNT = itertools.count()


def seed(app, data: Path, queue: Path) -> None:
    marker = data / "seeded.json"
    if marker.exists():
        return
    import json
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = dict(OWNER)
    token = client.get(f"{API}/session").get_json()["csrf_token"]
    hdr = {"X-CSRF-Token": token, "X-Record-Mode": "on_record"}

    def post(path, body):
        r = client.post(f"{API}{path}", json={"idempotency_key": f"seed-{next(_COUNT):06d}-{os.getpid()}", **body}, headers=hdr)
        assert r.status_code == 200, (path, r.status_code, r.get_json())
        return r.get_json()
    made = {}
    for key, title, summary, workshop, topic in (
            ("garden", "Garden build", "Raised beds for the south-east corner", WORKSHOP, "garden-build"),
            ("vault", "Vault backup", "Where the second copy lives", WORKSHOP, "vault-backup"),
            ("plain", "Unlinked example topic", "A topic with no shared record", None, None),
            ("damaged", "Damaged record example", "Its shared record is damaged", "workshop-damaged", "garden-build"),
            ("cutoff", "Cut-off record example", "Its shared record was cut off mid-write", "workshop-cutoff", "garden-build"),
            ("absent", "Not-synced example", "Its workshop has not been synced here", "workshop-absent", "garden-build")):
        tid = post("/topics/create", {"title": title, "summary": summary})["topic"]["id"]
        made[key] = tid
        if workshop:
            post(f"/topics/{tid}/record-link", {"workshop": workshop, "topic": topic})
    post(f"/topics/{made['garden']}/items", {"kind": "document", "title": "Raised beds, plan v2 (cedar)", "text": "# Raised beds, plan v2\n\nSix beds, cedar, south-east corner for tomatoes; drip line on a timer.\n\n- 4 x 8 ft, 11 in. high\n- cedar boards, stainless screws\n"})
    post(f"/topics/{made['garden']}/items", {"kind": "note", "title": "Corner shade check", "text": "The corner shades after 3 pm in October. Tomatoes want the sun until 4."})
    post(f"/topics/{made['garden']}/items", {"kind": "request", "title": "Re-cut frame four", "text": "Waiting on the squareness decision.", "request": {"to": "Codex", "stage": "queued", "included": "the garage-bench plan"}})
    post(f"/topics/{made['vault']}/items", {"kind": "note", "title": "Destination options", "text": "External disk, a second Mac, or a private bucket."})
    marker.write_text(json.dumps(made))


if __name__ == "__main__":
    sys.exit(main())
