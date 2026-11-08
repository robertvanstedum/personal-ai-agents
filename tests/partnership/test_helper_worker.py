"""The helper worker against the real task service (spec v0.4 H01–H06, HA3, HA4, HA11, HA13). Types use real files and a stand-in model."""
import json
from pathlib import Path

import pytest

from core.canonical_json import digest
from services.helper_seats import spend, types as T
from services.helper_seats.worker import Worker
from services.partnership_tasks.charters import Charter, Charters
from tests.partnership.synth import Flow, cmd, ref, service
from tests.partnership.test_helper_charters import GRANT, KIND, source

KINDS = {"repo-lookup": KIND, "hygiene-check": dict(KIND, model="deterministic", per_task_budget_usd=0.0)}
CHARTER = Charter("worker-a", 1, "sha256:" + "a" * 64, frozenset({"mc"}), frozenset({"personal"}), KINDS, 1.0)
INPUTS = json.dumps({"question": "where is the github issues link rendered?"})
TERMS = {"lead": "mc", "holder": "worker-a", "outcome": "Look it up.", "done_when": INPUTS, "return_window": None}


class Client:
    def __init__(self, svc, token):
        self.svc, self.token = svc, token

    def submit(self, c):
        return self.svc.submit(c, self.token)

    def get(self, kind, rid):
        return self.svc.get(kind, rid, self.token)

    def held(self):
        from services.partnership_tasks.views import work_for
        return work_for(self.svc, self.token)["holds"]


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    svc.charters = Charters([CHARTER])
    repo = tmp_path / "repo"
    (repo / "templates").mkdir(parents=True)
    (repo / "templates" / "build_log.html").write_text("<a href='/issues'>GitHub issues link</a> rendered on the build log page")
    calls = []
    ledger = spend.Ledger(tmp_path / "spend", daily_cap=1.0)

    def ctx_for(task, kind, round_no):
        def ask(prompt, purpose):
            marker = ledger.reserve(f"{task['task_id']}-{round_no}-1", bound_usd=kind["per_task_budget_usd"], task_spent=0.0, task_cap=kind["per_task_budget_usd"], purpose=purpose, task_id=task["task_id"])
            calls.append(prompt)
            if getattr(ctx_for, "crash", False):
                raise KeyboardInterrupt
            ledger.settle(marker, kind["per_task_budget_usd"])
            return "It is in templates/build_log.html."
        return T.Ctx(repo_root=repo, read_items=lambda: [{"id": 1, "spec_title": "A", "status": "in_build"}, {"id": 2, "status": "idea"}], ask_model=ask)
    f = Flow(svc)
    w = Worker(Client(svc, "tok-worker"), "worker-a", CHARTER, environment="synthetic", ledger=ledger, ctx_for=ctx_for)
    return svc, f, w, calls, ctx_for, ledger


def task(rig, type_id="repo-lookup", n="0001", done_when=INPUTS):
    svc, f, *_ = rig
    sid = source(f, f"SE-h-{n}")
    terms = dict(TERMS, done_when=done_when)
    proposed = {"outcome": "Look it up.", "scope": "personal", "recipients": ["principal"], "continuing": True, "task": terms}
    assert svc.submit(cmd("interpretation.record", "interpretation", f"IN-h-{n}", {"source_event_ids": [sid], "correction_event_ids": [], "proposed": proposed, "context_refs": [], "supersedes": None}), "tok-mc")["outcome"] == "accepted"
    assert svc.submit(cmd("interpretation.dispose", "interpretation", f"IN-h-{n}", {"disposition": "adopted", "authority": GRANT}, expected=f.rev("interpretation", f"IN-h-{n}")), "tok-mc")["outcome"] == "accepted"
    assert svc.submit(cmd("request.record", "request", f"RQ-h-{n}", {"source_event_id": sid, "intent_text": "x", "interpretation_ref": f"IN-h-{n}", "responder": "mc"}), "tok-mc")["outcome"] == "accepted"
    charter_ref = {"seat": "worker-a", "version": 1, "digest": CHARTER.digest, "type_id": type_id}
    out = svc.submit(cmd("request.promote", "request", f"RQ-h-{n}", {"task_id": f"TK-h-{n}", "parent_ref": None, **terms, "charter_ref": charter_ref}, expected=f.rev("request", f"RQ-h-{n}")), "tok-mc")
    assert out["outcome"] == "accepted", out
    return f"TK-h-{n}"


def result(svc, tid):
    t = svc.get("task", tid, "tok-mc")
    art = t["artifact_sets"][-1]["artifacts"][0]
    doc = svc.get("source_event", art["id"], "tok-mc")
    assert digest(doc["content"]) == art["digest"]
    return json.loads(doc["content"])


def test_it_works_its_task_and_submits_with_sources(rig):
    svc, _, w, calls, *_ = rig
    tid = task(rig)
    assert w.pass_once() == 1
    r = result(svc, tid)
    assert r["outcome"]["answer"] == "It is in templates/build_log.html." and "templates/build_log.html" in r["outcome"]["sources"]
    assert r["drafted_by"] == "worker-a"
    t = svc.get("task", tid, "tok-mc")
    assert t["state"] == "working" and t["artifact_sets"][-1]["authored_by"] == ["worker-a"]
    assert len(calls) == 1


def test_a_deterministic_type_never_calls_a_model(rig):
    svc, _, w, calls, *_ = rig
    tid = task(rig, "hygiene-check", done_when="{}")
    w.pass_once()
    r = result(svc, tid)
    assert calls == [] and r["outcome"]["items_checked"] == 2 and r["outcome"]["findings"] == [{"item_id": 1, "problems": ["no spec or source"]}, {"item_id": 2, "problems": ["spec_title"]}]


def test_it_does_not_redo_finished_work(rig):
    _, _, w, calls, *_ = rig
    task(rig)
    w.pass_once()
    assert w.pass_once() == 0 and len(calls) == 1


def test_a_crash_after_the_marker_never_calls_the_model_again(rig):
    """Grok: the marker is reserved before the provider call, and a resume that sees it doesn't call."""
    svc, _, w, calls, ctx_for, _ = rig
    tid = task(rig)
    ctx_for.crash = True
    with pytest.raises(KeyboardInterrupt):
        w.pass_once()
    ctx_for.crash = False
    w.pass_once()
    assert len(calls) == 1
    assert result(svc, tid)["outcome"]["stopped"] == "already_called"


def test_one_task_and_one_result_after_a_retry(rig):
    svc, _, w, *_ = rig
    tid = task(rig)
    w.pass_once()
    assert len(svc.get("task", tid, "tok-mc")["artifact_sets"]) == 1


def test_the_cap_is_a_clean_stop(rig):
    svc, _, w, _, _, ledger = rig
    ledger.daily_cap = 0.01
    tid = task(rig)
    w.pass_once()
    assert result(svc, tid)["outcome"]["stopped"] == "cap_reached"


def test_changes_requested_gets_a_new_set(rig):
    svc, f, w, calls, *_ = rig
    tid = task(rig)
    w.pass_once()
    t = svc.get("task", tid, "tok-mc")
    d = t["artifact_sets"][-1]["digest"]
    assert svc.submit(cmd("task.record_review", "task", tid, {"review_id": "RV-syn-h-9", "reviewed_set_digest": d, "input_digest": d, "verdict": "changes_requested", "review_ref": ref("artifacts", "RV-syn-h-9", digest("x"), edition="1"), "findings_ref": None}, expected=t["revision"]), "tok-mc")["outcome"] == "accepted"
    assert w.pass_once() == 1
    assert len(svc.get("task", tid, "tok-mc")["artifact_sets"]) == 2


def test_a_read_of_a_path_outside_the_repo_is_refused(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "secret").write_text("x")
    out = T.summary(T.Ctx(repo_root=tmp_path / "repo", ask_model=lambda *a: "no"), {"path": "../secret"})
    assert out["summary"] is None


def test_a_missing_answer_is_said_not_invented(tmp_path):
    (tmp_path / "repo").mkdir()
    out = T.repo_lookup(T.Ctx(repo_root=tmp_path / "repo", ask_model=lambda *a: 1 / 0), {"question": "what about kubernetes"})
    assert out["answer"] is None and "not proof" in out["note"]
