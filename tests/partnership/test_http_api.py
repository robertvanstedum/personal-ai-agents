"""B4 step 2: the task service over HTTP is a thin transport. Identity comes from the bearer token's binding, never
the body; not-found and not-yours look the same; the service's own answers pass through unchanged."""
import json

from services.partnership_tasks.http_api import create_app
from tests.partnership.synth import Flow, cmd, service


def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    tid = Flow(svc).promoted_task()
    return svc, tid, create_app(svc).test_client()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_reads_are_scoped_and_need_a_known_token(tmp_path):
    svc, tid, c = rig(tmp_path)
    assert c.get(f"/v1/records/task/{tid}").status_code == 401
    assert c.get(f"/v1/records/task/{tid}", headers=bearer("tok-unknown-0000000")).status_code == 401
    r = c.get(f"/v1/records/task/{tid}", headers=bearer("tok-principal"))
    assert r.status_code == 200 and r.json["task_id"] == tid
    assert c.get("/v1/records/task/TK-nope", headers=bearer("tok-principal")).status_code == 404
    assert c.get(f"/v1/records/task/{tid}", headers=bearer("tok-peer")).status_code == 404     # not its scope
    assert c.get(f"/v1/reconstruct/task/{tid}", headers=bearer("tok-principal")).json["chain"]["task"]["task_id"] == tid
    assert c.get("/v1/open", headers=bearer("tok-principal")).status_code == 200
    assert c.get("/v1/work", headers=bearer("tok-mc")).status_code == 200
    assert c.get("/v1/work?seat=mc", headers=bearer("tok-cos")).status_code == 404            # another seat's work


def test_commands_pass_through_with_identity_from_the_token(tmp_path):
    svc, tid, c = rig(tmp_path)
    body = cmd("source_event.record", "source_event", "SE-syn-http",
               {"text": "From HTTP", "words": "From HTTP", "actor": "principal", "kind": "message",
                "correction_of": None, "attachments": []})
    r = c.post("/v1/commands", data=json.dumps(body), headers={**bearer("tok-cos"), "Content-Type": "application/json"})
    direct = svc.submit(dict(body, command_id=body["command_id"] + "-x"), "tok-cos")
    assert r.status_code == 200 and r.json["outcome"] in ("accepted", "refused")
    assert r.json["outcome"] == direct["outcome"]
    receipt = c.post("/v1/receipts", data=json.dumps(body), headers={**bearer("tok-cos"),
                                                                     "Content-Type": "application/json"})
    assert receipt.json["answer"] in ("recorded", "absent", "different")


def test_bad_bodies_are_refused_before_the_service(tmp_path):
    svc, tid, c = rig(tmp_path)
    h = {**bearer("tok-cos"), "Content-Type": "application/json"}
    assert c.post("/v1/commands", data='{"a":1,"a":2}', headers=h).status_code == 400
    assert c.post("/v1/commands", data="x", headers=bearer("tok-cos")).status_code == 400
    assert c.post("/v1/commands", data="x" * 300_000, headers=h).status_code == 413
    assert c.post(f"/v1/tasks/{tid}/dispatch-check", data='{"fence_generation":"1"}', headers=h).status_code == 400
    assert c.get("/v1/history", headers=bearer("tok-cos")).status_code == 400
    assert c.get("/healthz").json["status"] == "up"
