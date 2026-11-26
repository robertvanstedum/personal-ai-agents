"""#680: the private professional-initiative store under the task service (Lane G spec v0.2 §2.6, changes 5-7)."""
import hashlib
import json
import multiprocessing
from dataclasses import replace

import pytest

from services.partnership_tasks import initiatives as PI
from services.partnership_tasks.http_api import create_app
from tests.partnership.synth import cmd, service


def _svc(tmp_path, cos=("read", "file"), mc=("read",)):
    svc = service(tmp_path / "tasks")
    grant = {"cos": frozenset(cos), "mc": frozenset(mc)}
    svc.bindings._by_hash = {k: replace(b, professional_initiatives=grant.get(b.seat, frozenset()))
                             for k, b in svc.bindings._by_hash.items()}
    return svc


def _source(svc, sid="SE-pi-0001", store="cos-history", scope="personal", token="tok-platform"):
    out = svc.submit(cmd("source_event.record", "source_event", sid,
                         {"source": {"store": store, "id": "c-1:" + sid, "position": 1},
                          "content": "Park this professional initiative with its packet.", "correction_of": None},
                         scope=scope) if scope != "personal" else
                     cmd("source_event.record", "source_event", sid,
                         {"source": {"store": store, "id": "c-1:" + sid, "position": 1},
                          "content": "Park this professional initiative with its packet.", "correction_of": None}),
                     token)
    return out


@pytest.fixture
def api(tmp_path):
    svc = _svc(tmp_path)
    assert _source(svc)["outcome"] == "accepted"
    app = create_app(svc, initiatives_root=tmp_path / "pi").test_client()

    def call(token, op, args, scope="personal"):
        return app.post("/v1/professional-initiatives", json={"operation": op, "scope": scope, "arguments": args},
                        headers={"Authorization": "Bearer " + token})

    def upload(token, data, scope="personal", sha=None):
        sha = sha or hashlib.sha256(data).hexdigest()
        return app.put(f"/v1/professional-initiatives/files/{sha}?scope={scope}", data=data,
                       headers={"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream"})
    return svc, call, upload, tmp_path


def test_cos_files_a_parked_initiative_with_packet_and_mc_reads_it(api):
    svc, call, upload, tmp = api
    staged = upload("tok-cos", b"Notes from the meeting.\nSecond line.")
    assert staged.status_code == 200, staged.json
    ref = staged.json["file_ref"]
    filed = call("tok-cos", "file", {"title": "Speaking series", "summary": "Parked until spring.",
                                     "files": [{"file_ref": ref, "name": "../notes.md"}],
                                     "source_event_id": "SE-pi-0001"})
    assert filed.status_code == 200, filed.json
    body = filed.json
    assert body["initiative_id"].startswith("PI-") and body["state"] == "parked"
    assert body["packet"][0]["name"] == "notes.md"                         # no path in a stored name
    listed = call("tok-mc", "list", {})
    assert [i["id"] for i in listed.json["initiatives"]] == [body["initiative_id"]]
    read = call("tok-mc", "read", {"initiative_id": body["initiative_id"]}).json["initiative"]
    assert read["filed_by"] == "cos" and read["source"]["store"] == "cos-history"
    text = call("tok-mc", "read_file", {"initiative_id": body["initiative_id"], "sha256": ref}).json
    assert text["text"].startswith("Notes from the meeting.")
    # Filing made no task, request or proposal.
    assert svc.get("interpretation", "IN-anything", "tok-principal") is None
    assert not list((tmp / "tasks").rglob("TK-*")) and not list((tmp / "tasks").rglob("RQ-*"))


def test_replay_returns_the_first_result_and_attach_dedupes(api):
    svc, call, upload, _ = api
    args = {"title": "Board seat", "summary": "Later.", "source_event_id": "SE-pi-0001"}
    first, again = call("tok-cos", "file", args).json, call("tok-cos", "file", args).json
    assert again["initiative_id"] == first["initiative_id"] and again["replayed"] is True
    ref = upload("tok-cos", b"%PDF-1.4 bytes").json["file_ref"]
    att = {"initiative_id": first["initiative_id"], "files": [{"file_ref": ref, "name": "deck.pdf"}],
           "source_event_id": "SE-pi-0001"}
    one = call("tok-cos", "attach", att).json
    assert one["status"] == "attached" and one["revision"] == 2
    assert _source(svc, "SE-pi-0002")["outcome"] == "accepted"
    two = call("tok-cos", "attach", {**att, "source_event_id": "SE-pi-0002"}).json
    assert two["status"] == "already_in_packet" and two["revision"] == 2
    read = call("tok-cos", "read_file", {"initiative_id": first["initiative_id"], "sha256": ref}).json
    assert read["text"] is None                                           # binary: bytes kept, no text claimed


def test_mc_cannot_file_or_attach_and_unbound_seats_get_403(api):
    _, call, upload, _ = api
    args = {"title": "x", "summary": "y", "source_event_id": "SE-pi-0001"}
    assert call("tok-mc", "file", args).status_code == 403
    assert upload("tok-mc", b"data").status_code == 403
    assert call("tok-worker", "list", {}).status_code == 403
    assert call("tok-cos", "list", {}, scope="shared:build").status_code == 403


def test_binding_without_the_field_is_refused(tmp_path):
    svc = _svc(tmp_path, cos=(), mc=())
    app = create_app(svc, initiatives_root=tmp_path / "pi").test_client()
    r = app.post("/v1/professional-initiatives", json={"operation": "list", "scope": "personal", "arguments": {}},
                 headers={"Authorization": "Bearer tok-cos"})
    assert r.status_code == 403 and r.json["error"] == "initiative_scope_unavailable"


def test_gate_off_answers_503_with_a_code(tmp_path):
    app = create_app(_svc(tmp_path)).test_client()
    r = app.post("/v1/professional-initiatives", json={}, headers={"Authorization": "Bearer tok-cos"})
    assert r.status_code == 503 and r.json["error"] == "professional_initiatives_disabled"


def test_untrusted_or_meeting_history_source_cannot_file(api):
    svc, call, _, _ = api
    # Recorded by the CoS seat, not the platform.
    _source(svc, "SE-pi-cos1", token="tok-cos")
    assert call("tok-cos", "file", {"title": "x", "summary": "y", "source_event_id": "SE-pi-cos1"}).status_code == 403
    # A platform event from an unverified store (e.g. a shared meeting's history) cannot file.
    assert _source(svc, "SE-pi-mtg1", store="cos-meeting")["outcome"] == "accepted"
    assert call("tok-cos", "file", {"title": "x", "summary": "y", "source_event_id": "SE-pi-mtg1"}).status_code == 403
    # A verified owner-private meeting turn can.
    assert _source(svc, "SE-pi-own1", store="cos-meeting-owner")["outcome"] == "accepted"
    assert call("tok-cos", "file", {"title": "x", "summary": "y", "source_event_id": "SE-pi-own1"}).status_code == 200


def test_planning_studio_ids_and_bad_packets_are_refused(api):
    _, call, upload, _ = api
    r = call("tok-mc", "read", {"initiative_id": "INIT-2026-0001"})
    assert r.status_code == 422 and r.json["error"] == "planning_studio_id_not_accepted"
    assert upload("tok-cos", b"abc", sha="0" * 64).json["error"] == "file_sha256_mismatch"
    assert upload("tok-cos", b"x" * (PI.MAX_FILE + 1)).json["error"] == "file_too_large"
    r = call("tok-cos", "file", {"title": "t", "summary": "s", "source_event_id": "SE-pi-0001",
                                 "files": [{"file_ref": "a" * 64, "name": "n.txt"}]})
    assert r.status_code == 422 and r.json["error"] == "file_ref_not_staged"


def test_file_upload_over_the_route_limit_is_413(api):
    _, _, upload, _ = api
    from services.partnership_tasks.http_api import FILE_BODY
    assert upload("tok-cos", b"x" * (FILE_BODY + 1)).status_code == 413


def test_packet_limits(tmp_path):
    store = PI.Store(tmp_path, "robert", "dev")
    refs = [store.stage(hashlib.sha256(bytes([i]) * 10).hexdigest(), bytes([i]) * 10)["file_ref"]
            for i in range(PI.MAX_FILES + 1)]
    files = [{"file_ref": r, "name": f"f{i}.txt"} for i, r in enumerate(refs)]
    with pytest.raises(PI.Refused) as exc:
        store.file(title="t", summary="s", files=files, source={}, filed_by="cos", operation_id="o1",
                   validate=lambda: None)
    assert exc.value.code == "packet_too_many_files"
    assert not list(tmp_path.rglob("PI-*"))                               # nothing half-written


def test_failed_validation_writes_nothing(tmp_path):
    store = PI.Store(tmp_path, "robert", "dev")

    def lost():
        raise PermissionError("binding changed")
    with pytest.raises(PermissionError):
        store.file(title="t", summary="s", files=[], source={}, filed_by="cos", operation_id="o1", validate=lost)
    assert not list(tmp_path.rglob("PI-*")) and store.list() == {"initiatives": []}


def test_principals_are_partitioned(tmp_path):
    a, b = PI.Store(tmp_path, "robert", "dev"), PI.Store(tmp_path, "someone", "dev")
    filed = a.file(title="t", summary="s", files=[], source={}, filed_by="cos", operation_id="o1",
                   validate=lambda: None)
    with pytest.raises(PI.Refused):
        b.read(filed["initiative_id"])
    assert b.list() == {"initiatives": []}


def _file_many(root, n, out):
    store = PI.Store(root, "robert", "dev")
    ids = [store.file(title="t", summary="s", files=[], source={}, filed_by="cos",
                      operation_id=f"op-{multiprocessing.current_process().pid}-{i}", validate=lambda: None)
           ["initiative_id"] for i in range(n)]
    out.put(ids)


def test_ids_increase_under_concurrent_filing(tmp_path):
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=_file_many, args=(tmp_path, 10, q)) for _ in range(2)]
    for p in procs:
        p.start()
    ids = q.get(timeout=30) + q.get(timeout=30)
    for p in procs:
        p.join(timeout=30)
    assert len(ids) == len(set(ids)) == 20
    numbers = sorted(int(i.rsplit("-", 1)[1]) for i in ids)
    assert numbers == list(range(1, 21))


def test_contract_schema_accepts_the_field_and_rejects_mc_filing():
    from services.partnership_tasks.auth import Bindings
    base = {"binding_id": "bind-cos-pi", "token_sha256": "a" * 64, "seat": "cos", "principal": "robert",
            "environment": "dev", "origin": "agent", "scopes": ["personal"], "read_scopes": ["personal"],
            "operations": ["interpretation.record"], "professional_initiatives": ["read", "file"]}
    assert Bindings([base])
    with pytest.raises(ValueError):
        Bindings([{**base, "seat": "mc", "binding_id": "bind-mc-pi"}])
    with pytest.raises(ValueError):
        Bindings([{**base, "professional_initiatives": ["write"]}])
