"""B1E: J1 on a synthetic corpus, and the B1 parts of F13-F16 through the scoped reconstruction query (Q07).

SYNTHETIC. Every passing test here is synthetic evidence only: no real source, provider, billing or monitor."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import durable_files as fsio
from services.partnership_tasks import contracts
from services.partnership_tasks.reconstruct import Reconstructor, history, open_work
from services.partnership_tasks.service import StoreUnavailable
from tests.partnership.synth import Flow, cmd, ref, service, SEATS
from services.partnership_tasks.auth import token_sha256

REPO = Path(__file__).resolve().parents[2]
CORPUS = json.loads((Path(__file__).parent / "fixtures" / "j1_corpus.json").read_text("utf-8"))


def synthetic_lookup(words):
    """The responder's search over the synthetic target list: results plus honest coverage."""
    hits = [p for p in CORPUS["postings"] if all(w in (p["title"] + (" leadership" if p["leadership"] else "")).lower()
                                                  for w in words)]
    gaps = [f"{p['id']}: JD text truncated in the target list." for p in hits if p["jd_truncated"]]
    gaps.append("The external JD archive was not searchable.")
    return {"result_refs": [ref("artifacts", p["id"], edition=CORPUS["edition"]) for p in hits],
            "coverage": {"sources": [{"ref": ref("artifacts", "target-list", edition=CORPUS["edition"]),
                                      "status": "searched"},
                                     {"ref": ref("external", "jd-archive"), "status": "unavailable"}],
                         "gaps": gaps}}


def test_j1_lookup_coverage_refinement_next_session_recall_and_no_open_list_clutter(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    f.source("SE-syn-0001", "Which sales jobs are in my target list?")
    f.request("RQ-syn-0001", "SE-syn-0001", "Sales roles in the target list.")
    assert open_work(svc, "tok-cos") == [{"type": "request", "id": "RQ-syn-0001", "state": "recorded"}]
    f.ok(cmd("request.set_state", "request", "RQ-syn-0001", {"state": "active", "reason": None}, expected=1), "tok-cos")
    first = synthetic_lookup(["sales"])
    f.ok(cmd("request.answer", "request", "RQ-syn-0001", dict(first, fence_generation=0), expected=2), "tok-cos")
    assert len(first["result_refs"]) == 4 and "post-syn-03: JD text truncated in the target list." in first["coverage"]["gaps"]
    f.source("SE-syn-0002", "Only leadership roles.")
    f.ok(cmd("request.refine", "request", "RQ-syn-0001",
             {"source_event_id": "SE-syn-0002", "intent_text": "Sales leadership roles in the target list.",
              "interpretation_ref": None}, expected=3), "tok-cos")
    second = synthetic_lookup(["sales", "leadership"])
    f.ok(cmd("request.answer", "request", "RQ-syn-0001", dict(second, fence_generation=1), expected=4), "tok-cos")
    assert open_work(svc, "tok-cos") == []                                   # answered: history, not open list

    next_session = service(tmp_path)                                        # a new process, the next day
    recall = history(next_session, "tok-principal", "leadership sales")
    assert [r["request_id"] for r in recall] == ["RQ-syn-0001"]
    r = recall[0]
    assert [x["id"] for x in r["result_refs"]] == ["post-syn-01", "post-syn-03", "post-syn-05"]
    assert r["editions"][0]["text"] == "Sales roles in the target list." and len(r["editions"][0]["result_refs"]) == 4
    assert {"ref": {"store": "external", "id": "jd-archive", "edition": None, "digest": None},
            "status": "unavailable"} in r["coverage"]["sources"]
    assert history(next_session, "tok-peer", "sales") == []                  # other scope: nothing


def test_q07_reconstruction_of_a_task_shows_the_chain_and_marks_reports_and_gaps(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", token="tok-mc", evidence=[ref("harness", "attempt-syn-1")])
    out = Reconstructor(svc).reconstruct("task", tid, "tok-principal")
    assert contracts.errors("reconstruction", "answer", out) == []
    assert out["synthetic"] is True and out["integrity"] == "verified"
    lead_report = [t for t in out["timeline"] if t["kind"] == "task.transition"][0]
    assert lead_report["actor_seat"] == "mc" and lead_report["classification"] == "actor_report"   # lead's too
    chain = out["chain"]
    assert [s["source_event_id"] for s in chain["instructions"]] == ["SE-syn-0001"]
    assert chain["authority"][0]["authority"]["kind"] == "existing_grant"
    assert chain["authority"][0]["adoption_is_not_approval"] is True
    kinds = [t["kind"] for t in out["timeline"]]
    assert kinds == ["source_event.record", "interpretation.record", "interpretation.dispose", "request.record",
                     "request.promote", "task.transition"]
    assert out["timeline"][1]["runtime"]["model"] == "synthetic-model"       # who interpreted, with what runtime
    assert any("harness:attempt-syn-1" in g and "actor report" in g for g in out["gaps"])
    assert out["costs"]["status"] == "no_cost_evidence"
    assert {"store": "harness", "status": "not_joined"} in out["coverage"]
    assert out["hypotheses"] == []


def test_f13_b1_no_cost_is_invented_and_runtime_provider_is_on_every_line(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    out = Reconstructor(svc).reconstruct("task", tid, "tok-principal")
    assert out["costs"]["status"] == "no_cost_evidence"
    assert not any(isinstance(v, (int, float)) for v in out["costs"].values())          # no number invented
    for row in out["timeline"]:
        assert set(row["runtime"]) >= {"kind"}                               # identity captured or explicitly null
    assert all(row["runtime"]["provider"] == "synthetic" for row in out["timeline"])


def test_f14_b1_who_owed_the_duty_at_the_time_despite_a_later_reassignment(tmp_path):
    now = {"t": "2026-11-01T09:00:00+00:00"}
    svc = service(tmp_path, clock=lambda: now["t"])
    f = Flow(svc)
    tid = f.promoted_task()
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.assign", "task", tid, {"role": "duty", "seat": "cos", "reason": "Weekly disk check."},
             expected=t["revision"]), "tok-mc")
    now["t"] = "2026-11-03T09:00:00+00:00"
    t = svc.get("task", tid, "tok-principal")
    f.ok(cmd("task.assign", "task", tid, {"role": "lead", "seat": "cos", "reason": "Reorganised."},
             expected=t["revision"]), "tok-principal")
    then = Reconstructor(svc).reconstruct("task", tid, "tok-principal", at="2026-11-02T00:00:00+00:00")
    in_force = {(a["role"], a["seat"]) for a in then["assignments"] if a["in_force_at_query_time"]}
    assert in_force == {("lead", "mc"), ("holder", "mc"), ("duty", "cos")}
    later = Reconstructor(svc).reconstruct("task", tid, "tok-principal", at="2026-11-04T00:00:00+00:00")
    assert {(a["role"], a["seat"]) for a in later["assignments"] if a["in_force_at_query_time"]} == \
        {("lead", "cos"), ("holder", "mc"), ("duty", "cos")}
    as_of_task = then["as_of"][f"tasks/{tid}.json"]
    assert as_of_task["lead"] == "mc"                                        # the record as it stood then


def test_f15_b1_wrong_interpretation_shows_where_it_diverged_and_that_dispatch_was_fenced(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    f.source("SE-syn-0001", "Open an issue about the signup page.")
    f.interpret("IN-syn-0001", ["SE-syn-0001"], "Open an issue about the login page.")      # the misreading
    f.adopt("IN-syn-0001")
    f.request("RQ-syn-0001", "SE-syn-0001", "Issue: login page.", iid="IN-syn-0001")
    f.promote("RQ-syn-0001", "TK-syn-0001")
    old = svc.get("task", "TK-syn-0001", "tok-mc")
    f.source("SE-syn-0002", "No, the signup page.", correction_of="SE-syn-0001")
    f.interpret("IN-syn-0002", ["SE-syn-0001"], "Open an issue about the signup page.", corrections=["SE-syn-0002"],
                supersedes="IN-syn-0001")
    f.adopt("IN-syn-0002")
    req = svc.get("request", "RQ-syn-0001", "tok-cos")
    f.ok(cmd("correction.apply", "request", "RQ-syn-0001",
             {"correction_event_id": "SE-syn-0002", "changes": ["outcome"], "interpretation_ref": "IN-syn-0002",
              "intent_text": "Issue: signup page."}, expected=req["revision"]), "tok-cos")
    assert svc.dispatch_check("TK-syn-0001", old["fence_generation"], "IN-syn-0001", "tok-mc")["code"] == "fenced"
    out = Reconstructor(svc).reconstruct("task", "TK-syn-0001", "tok-principal")
    chain = out["chain"]
    assert chain["instructions"][0]["content"] == "Open an issue about the signup page."
    first, second = chain["interpretations"]
    assert (first["proposed"]["outcome"], first["superseded_by"], first["disposition"]) == \
        ("Open an issue about the login page.", "IN-syn-0002", "corrected")
    assert first["interpreter"]["seat"] == "cos" and first["authority"]["kind"] == "existing_grant"
    assert chain["current_interpretation"] == "IN-syn-0002"
    assert [c["content"] for c in chain["corrections"]] == ["No, the signup page."]
    corr_line = [t for t in out["timeline"] if t["kind"] == "correction.apply"][0]
    assert "tasks/TK-syn-0001.json" in corr_line["targets"]                  # the task was fenced by that line
    assert any("Dispatch checks are answered but not recorded" in g for g in out["gaps"])


def test_f16_b1_denied_access_looks_like_absence_and_a_damaged_store_claims_nothing(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    tid = f.promoted_task()
    r = Reconstructor(svc)
    assert r.reconstruct("task", tid, "tok-peer") is None                    # out of scope
    assert r.reconstruct("task", "TK-syn-9999", "tok-peer") is None          # missing: same answer
    assert r.reconstruct("task", tid, "bad-token") is None
    fsio.write_doc(tmp_path / "tasks" / f"{tid}.json", {"tampered": True})
    fresh = service(tmp_path)
    held = Reconstructor(fresh).reconstruct("task", tid, "tok-principal")
    assert held["integrity"] == "damaged" and held["chain"] is None and held["timeline"] == []
    assert contracts.errors("reconstruction", "answer", held) == []
    with pytest.raises(StoreUnavailable):
        open_work(fresh, "tok-principal")
    with pytest.raises(StoreUnavailable):
        history(fresh, "tok-principal", "design")


def test_f16_b1_a_chain_cannot_reach_into_another_scope_so_nothing_is_hidden_inside_it(tmp_path):
    """N3: an interpretation may not cite a source from another scope, so a scoped reconstruction never contains a
    link it would have to hide. (Unavailable joined stores are named as gaps: see the harness gap test above.)"""
    svc = service(tmp_path)
    f = Flow(svc)
    f.source("SE-syn-0001", "Build-room context.", token="tok-mc", scope="shared:build")
    f.source("SE-syn-0002", "Personal ask based on it.")
    res = svc.submit(cmd("interpretation.record", "interpretation", "IN-syn-0002",
                         {"source_event_ids": ["SE-syn-0002", "SE-syn-0001"], "correction_event_ids": [],
                          "proposed": {"outcome": "Combined.", "scope": "personal", "recipients": [], "continuing": False},
                          "context_refs": [], "supersedes": None}), "tok-mc")
    assert (res["code"], res["detail"]) == ("not_found", "source_event")


def test_q07_cli_reconstructs_read_only_with_the_token_from_a_file(tmp_path):
    svc = service(tmp_path / "store")
    tid = Flow(svc).promoted_task()
    bindings = []
    for token, (seat, origin, ops, scopes, read) in SEATS.items():
        if token in ("tok-principal", "tok-peer"):
            bindings.append({"binding_id": f"bind-{token[4:]}", "token_sha256": token_sha256(token), "seat": seat,
                             "principal": "principal", "environment": "synthetic", "origin": origin,
                             "scopes": scopes, "read_scopes": read, "operations": sorted(set(ops))})
    (tmp_path / "bindings.json").write_text(json.dumps(bindings))
    before = sorted(p.name for p in (tmp_path / "store" / "journal").iterdir())
    def run(token, *args, mode=0o600, root=None):
        (tmp_path / "tok").write_text(token)
        (tmp_path / "tok").chmod(mode)
        return subprocess.run([sys.executable, "-m", "services.partnership_tasks.cli", "--root", str(root or tmp_path / "store"),
                               "--environment", "synthetic", "--bindings", str(tmp_path / "bindings.json"),
                               "--token-file", str(tmp_path / "tok"), *args],
                              cwd=REPO, capture_output=True, text=True, timeout=60)
    ok = run("tok-principal", "reconstruct", "--task", tid)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["chain"]["task"]["task_id"] == tid
    assert contracts.errors("reconstruction", "answer", json.loads(ok.stdout)) == []
    denied = run("tok-peer", "reconstruct", "--task", tid)
    assert denied.returncode == 2 and json.loads(denied.stdout) == {"found": False}
    assert run("tok-principal", "reconstruct", "--task", tid, mode=0o644).returncode == 4     # readable token file
    assert run("", "reconstruct", "--task", tid).returncode == 4                             # no token
    assert run("tok-nobody", "reconstruct", "--task", tid).returncode == 4                   # unknown token
    assert run("tok-principal", "reconstruct", "--task", tid, "--at", "2026-11-02").returncode == 64
    nowhere = tmp_path / "nowhere"
    assert run("tok-principal", "open", root=nowhere).returncode == 3 and not nowhere.exists()
    assert sorted(p.name for p in (tmp_path / "store" / "journal").iterdir()) == before
    fsio.write_doc(tmp_path / "store" / "tasks" / f"{tid}.json", {"tampered": True})
    assert run("tok-principal", "open").returncode == 3
    assert run("tok-principal", "history", "design").returncode == 3


def test_q07_s1_out_of_scope_activity_does_not_change_a_scoped_answer(tmp_path):
    now = {"t": "2026-11-04T16:00:00+00:00"}
    svc = service(tmp_path, clock=lambda: now["t"])
    f = Flow(svc)
    f.source("SE-syn-0001", "Build task.", token="tok-mc", scope="shared:build")
    f.request("RQ-syn-0001", "SE-syn-0001", "Build ask.", responder="mc", token="tok-mc", scope="shared:build")
    before = Reconstructor(svc).reconstruct("request", "RQ-syn-0001", "tok-peer")
    for n in range(5):                                                          # personal work the peer cannot see
        f.source(f"SE-syn-01{n:02d}", "Private.")
    after = Reconstructor(svc).reconstruct("request", "RQ-syn-0001", "tok-peer")
    assert before == after
    assert "journal_head" not in after and all("seq" not in t for t in after["timeline"])


def test_f14_duty_handover_closes_the_previous_interval(tmp_path):
    now = {"t": "2026-11-01T09:00:00+00:00"}
    svc = service(tmp_path, clock=lambda: now["t"])
    f = Flow(svc)
    tid = f.promoted_task()
    for seat, when in (("cos", "2026-11-01T10:00:00+00:00"), ("codex", "2026-11-03T10:00:00+00:00")):
        now["t"] = when
        t = svc.get("task", tid, "tok-principal")
        f.ok(cmd("task.assign", "task", tid, {"role": "duty", "seat": seat, "reason": "Disk check."},
                 expected=t["revision"]), "tok-mc")
    duty_at = lambda at: {a["seat"] for a in Reconstructor(svc).reconstruct("task", tid, "tok-principal", at=at)[
        "assignments"] if a["role"] == "duty" and a["in_force_at_query_time"]}
    assert duty_at("2026-11-02T00:00:00+00:00") == {"cos"}
    assert duty_at("2026-11-04T00:00:00+00:00") == {"codex"}


def test_reconstruct_refuses_a_time_without_an_offset(tmp_path):
    svc = service(tmp_path)
    tid = Flow(svc).promoted_task()
    with pytest.raises(ValueError, match="at_needs_offset"):
        Reconstructor(svc).reconstruct("task", tid, "tok-principal", at="2026-11-02T00:00:00")
