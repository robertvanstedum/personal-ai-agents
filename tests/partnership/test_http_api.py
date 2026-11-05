"""B4 step 2: the task service over HTTP is a thin transport. Identity comes from the bearer token's binding, never
the body; not-found and not-yours look the same; the service's own answers pass through unchanged."""
import json

from core import portal_assertion as PA
from services.partnership_tasks.http_api import OWNER_AUDIENCE, OWNER_HEADER, create_app
from tests.partnership.synth import Flow, cmd, service

KEY = ("owner-kid", "partnership-owner-secret-0123456789abcdef")


class Spend:
    def __init__(self):
        self.seen = set()

    def __call__(self, nonce, expires, now=None):
        if nonce in self.seen:
            return False
        self.seen.add(nonce)
        return True


def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    tid = Flow(svc).promoted_task()
    app = create_app(svc, owner_key=KEY, owner_env="test", owner_subject="owner:1", spend=Spend())
    return svc, tid, OwnerClient(app.test_client())


class OwnerClient:
    """Signs owner (tok-principal) requests as the portal would; everything else passes through."""

    def __init__(self, client):
        self.c = client

    def _h(self, method, path, headers, data):
        headers = dict(headers or {})
        if headers.get("Authorization") == "Bearer tok-principal" and not headers.pop("X-Unsigned", None):
            body = data.encode() if isinstance(data, str) else (data or b"")
            target = PA.canonical_target(path.split("?")[0], path.split("?")[1] if "?" in path else "")
            headers[OWNER_HEADER] = PA.sign(KEY, audience=OWNER_AUDIENCE, subject="owner:1", method=method,
                                            target=target, body=body, env="test")
        return headers

    def get(self, path, headers=None):
        return self.c.get(path, headers=self._h("GET", path, headers, None))

    def post(self, path, data=None, headers=None):
        return self.c.post(path, data=data, headers=self._h("POST", path, headers, data))


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


def test_the_owner_token_alone_never_acts_as_robert(tmp_path):
    svc, tid, c = rig(tmp_path)
    unsigned = {**bearer("tok-principal"), "X-Unsigned": "1"}
    assert c.get(f"/v1/records/task/{tid}", headers=unsigned).status_code == 401
    body = json.dumps(cmd("request.link_source", "request", "RQ-syn-0001", {"source_event_id": "SE-syn-0001"}))
    assert c.post("/v1/commands", data=body, headers={**unsigned, "Content-Type": "application/json"}).status_code == 401
    # A signature over another body, another route or a replay does not count.
    h = c._h("POST", "/v1/commands", {**bearer("tok-principal"), "Content-Type": "application/json"}, "{}")
    assert c.c.post("/v1/commands", data=body, headers=h).status_code == 401
    h = c._h("GET", f"/v1/records/task/{tid}", bearer("tok-principal"), None)
    assert c.c.get(f"/v1/records/task/{tid}", headers=h).status_code == 200
    assert c.c.get(f"/v1/records/task/{tid}", headers=h).status_code == 401          # nonce spent
    other = create_app(svc, owner_key=KEY, owner_env="test", owner_subject="owner:2", spend=Spend()).test_client()
    h = c._h("GET", "/v1/open", bearer("tok-principal"), None)
    assert other.get("/v1/open", headers=h).status_code == 401                        # not the configured owner
    unconfigured = create_app(svc).test_client()
    assert unconfigured.get("/v1/open", headers=c._h("GET", "/v1/open", bearer("tok-principal"), None)).status_code == 401
    assert unconfigured.get("/v1/work", headers=bearer("tok-mc")).status_code == 200  # agents are unaffected


def test_every_route_but_health_requires_authentication(tmp_path):
    """Design v0.3 R-N1: the owner rule lives in http_api, so no route may skip the token check."""
    svc, tid, c = rig(tmp_path)
    app = c.c.application
    for rule in app.url_map.iter_rules():
        if rule.endpoint in ("static", "health"):
            continue
        path = rule.rule.replace("<target_type>", "task").replace("<target_id>", tid).replace(
            "<start_type>", "task").replace("<start_id>", tid).replace("<task_id>", tid)
        method = "POST" if "POST" in rule.methods else "GET"
        r = c.c.open(path + ("?words=x" if path.endswith("history") else ""), method=method,
                     data="{}", content_type="application/json")
        assert r.status_code == 401, (rule.rule, r.status_code)
