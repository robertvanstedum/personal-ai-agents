"""Helper seats spec v0.4 §3.1–§3.3, against the real task service: a helper's chartered task starts from Robert's kept note under
the charter's grant, stays inside it, and is accepted only by its lead's own review of that exact set."""
import json
from pathlib import Path

import pytest

from core.canonical_json import digest
from services.partnership_tasks.charters import Charter, CharterRefused, Charters
from tests.partnership.synth import Flow, cmd, ref, service

KIND = {"purpose": "p", "inputs": [], "outputs": [], "evidence": [], "effects": "none", "model": "small", "per_task_budget_usd": 0.05,
        "max_revisions": 1, "escalation": "e", "return": "in_turn", "tools": ["repo.read"]}
CHARTER = Charter("worker-a", 1, "sha256:" + "a" * 64, frozenset({"mc"}), frozenset({"personal"}), {"repo-lookup": KIND, "draft": dict(KIND, effects="internal")}, 1.0)
REF = {"seat": "worker-a", "version": 1, "digest": CHARTER.digest, "type_id": "repo-lookup"}
GRANT = {"kind": "existing_grant", "ref": ref("grants", "charter:worker-a:1")}
TERMS = {"lead": "mc", "holder": "worker-a", "outcome": "Look it up.", "done_when": "Answered with sources.", "return_window": None}


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    svc.charters = Charters([CHARTER])
    return svc, Flow(svc)


def source(f, sid="SE-h-0001", store="mc-floor", token="tok-platform"):
    out = f.svc.submit(cmd("source_event.record", "source_event", sid, {"source": {"store": store, "id": "c:n", "position": 1}, "content": "Where is the link?", "correction_of": None}), token)
    assert out["outcome"] == "accepted", out
    return sid


def adopted(f, sid, iid="IN-h-0001", token="tok-mc", authority=GRANT):
    proposed = {"outcome": "Look it up.", "scope": "personal", "recipients": ["principal"], "continuing": True, "task": TERMS}
    assert f.svc.submit(cmd("interpretation.record", "interpretation", iid, {"source_event_ids": [sid], "correction_event_ids": [], "proposed": proposed, "context_refs": [], "supersedes": None}), token)["outcome"] == "accepted"
    return f.svc.submit(cmd("interpretation.dispose", "interpretation", iid, {"disposition": "adopted", "authority": authority}, expected=f.rev("interpretation", iid)), token)


def promote(f, sid, iid="IN-h-0001", rid="RQ-h-0001", tid="TK-h-0001", charter=REF, token="tok-mc", terms=TERMS):
    out = f.svc.submit(cmd("request.record", "request", rid, {"source_event_id": sid, "intent_text": "Look it up.", "interpretation_ref": iid, "responder": "mc"}), token)
    assert out["outcome"] == "accepted", out
    payload = {"task_id": tid, "parent_ref": None, **terms, **({"charter_ref": charter} if charter else {})}
    return f.svc.submit(cmd("request.promote", "request", rid, payload, expected=f.rev("request", rid)), token)


def started(f):
    sid = source(f)
    assert adopted(f, sid)["outcome"] == "accepted"
    out = promote(f, sid)
    assert out["outcome"] == "accepted", out
    assert f.transition("TK-h-0001", "working", token="tok-worker", evidence=[ref("task_service", sid)])["outcome"] == "accepted"
    return sid


def submit(f, n=1):
    task = f.svc.get("task", "TK-h-0001", "tok-mc")
    art = [ref("artifacts", f"result-{n}", digest({"n": n}), edition="1")]
    out = f.svc.submit(cmd("task.submit_artifacts", "task", "TK-h-0001", {"artifacts": art, "fence_generation": task["fence_generation"]}, expected=task["revision"]), "tok-worker")
    now = f.svc.get("task", "TK-h-0001", "tok-mc")
    return out, (now["artifact_sets"][-1]["digest"] if now["artifact_sets"] else None)


def review(f, set_digest, verdict="approve", rid="RV-syn-h-1", token="tok-mc"):
    task = f.svc.get("task", "TK-h-0001", "tok-mc")
    return f.svc.submit(cmd("task.record_review", "task", "TK-h-0001", {"review_id": rid, "reviewed_set_digest": set_digest, "input_digest": set_digest, "verdict": verdict,
                                                                         "review_ref": ref("artifacts", rid, digest(rid), edition="1"), "findings_ref": None}, expected=task["revision"]), token)


def accept(f, set_digest, token="tok-mc"):
    task = f.svc.get("task", "TK-h-0001", "tok-mc")
    return f.svc.submit(cmd("task.accept", "task", "TK-h-0001", {"artifact_set_digest": set_digest, "acknowledged_disagreements": [], "fence_generation": task["fence_generation"]}, expected=task["revision"]), token)


class TestStart:
    def test_from_his_note_under_the_charter_with_no_click(self, rig):
        _, f = rig
        started(f)
        assert f.svc.get("task", "TK-h-0001", "tok-mc")["charter_ref"] == REF

    def test_an_unchartered_type_is_refused(self, rig):
        _, f = rig
        sid = source(f)
        adopted(f, sid)
        assert promote(f, sid, charter=dict(REF, type_id="deploy-things"))["detail"] == "not_chartered"

    def test_a_changed_charter_digest_is_refused(self, rig):
        _, f = rig
        sid = source(f)
        adopted(f, sid)
        assert promote(f, sid, charter=dict(REF, digest="sha256:" + "b" * 64))["detail"] == "not_chartered"

    def test_another_holder_is_refused(self, rig):
        _, f = rig
        sid = source(f)
        adopted(f, sid)
        other = dict(TERMS, holder="claude-code")
        s = rig[0].get("interpretation", "IN-h-0001", "tok-mc")
        assert s["proposed"]["task"]["holder"] == "worker-a"
        assert promote(f, sid, terms=other)["outcome"] == "refused"

    def test_a_lead_cannot_promote_its_own_words(self, rig):
        """Grok: the source must be his kept note, not something the lead wrote."""
        _, f = rig
        sid = source(f, store="agent-note", token="tok-mc")
        adopted(f, sid)
        assert promote(f, sid)["detail"] == "not_the_owners_kept_note"

    def test_a_platform_event_from_another_store_is_refused(self, rig):
        _, f = rig
        sid = source(f, store="portal-owner-action")
        adopted(f, sid)
        assert promote(f, sid)["detail"] == "not_the_owners_kept_note"

    def test_a_lead_not_in_the_charter_cannot_use_its_grant(self, rig):
        _, f = rig
        sid = source(f)
        assert adopted(f, sid, token="tok-cos")["detail"] == "charter_does_not_cover"

    def test_a_charter_ref_without_the_grant_is_refused(self, rig):
        _, f = rig
        sid = source(f)
        out = rig[0].submit(cmd("interpretation.record", "interpretation", "IN-h-0001", {"source_event_ids": [sid], "correction_event_ids": [], "proposed": {"outcome": "x", "scope": "personal", "recipients": ["principal"], "continuing": True, "task": TERMS}, "context_refs": [], "supersedes": None}), "tok-mc")
        assert out["outcome"] == "accepted"
        rig[0].submit(cmd("interpretation.dispose", "interpretation", "IN-h-0001", {"disposition": "adopted", "authority": {"kind": "existing_grant", "ref": ref("grants", "grant-syn-lookup")}}, expected=f.rev("interpretation", "IN-h-0001")), "tok-mc")
        assert promote(f, sid)["detail"] == "charter_ref_needs_the_charter_grant"


class TestAcceptance:
    def test_its_lead_accepts_on_its_own_review(self, rig):
        _, f = rig
        started(f)
        out, d = submit(f)
        assert out["outcome"] == "accepted", out
        r = review(f, d); assert r["outcome"] == "accepted", r
        done = accept(f, d)
        assert done["outcome"] == "accepted", done
        assert f.svc.get("task", "TK-h-0001", "tok-mc")["state"] == "completed"

    def test_no_review_means_no_acceptance(self, rig):
        _, f = rig
        started(f)
        _, d = submit(f)
        assert accept(f, d)["detail"] == "no_non_author_approval_of_this_set"

    def test_changes_requested_cannot_be_accepted(self, rig):
        _, f = rig
        started(f)
        _, d = submit(f)
        review(f, d, verdict="changes_requested")
        assert accept(f, d)["outcome"] == "refused"

    def test_a_set_the_lead_edited_is_not_the_helpers_alone(self, rig):
        _, f = rig
        started(f)
        task = f.svc.get("task", "TK-h-0001", "tok-mc")
        art = [ref("artifacts", "lead-edit", digest({"lead": 1}), edition="1")]
        assert f.svc.submit(cmd("task.submit_artifacts", "task", "TK-h-0001", {"artifacts": art, "fence_generation": task["fence_generation"]}, expected=task["revision"]), "tok-mc")["outcome"] == "accepted"
        d = f.svc.get("task", "TK-h-0001", "tok-mc")["artifact_sets"][-1]["digest"]
        assert review(f, d)["outcome"] == "refused"                       # the lead authored it, so it can't review it
        assert accept(f, d)["outcome"] == "refused"

    def test_a_task_with_no_charter_keeps_the_old_rule(self, rig):
        svc, f = rig
        tid = f.promoted_task("0099")
        assert "charter_ref" not in svc.get("task", tid if isinstance(tid, str) else "TK-0099", "tok-mc")

    def test_the_revision_limit(self, rig):
        _, f = rig
        started(f)
        assert submit(f, 1)[0]["outcome"] == "accepted"
        assert submit(f, 2)[0]["outcome"] == "accepted"                   # one revision allowed
        out = submit(f, 3)[0]
        assert (out["outcome"], out["detail"]) == ("refused", "revision_limit")

    def test_no_owner_notice_for_a_helper_result(self, rig):
        svc, f = rig
        started(f)
        _, d = submit(f)
        review(f, d)
        accept(f, d)
        notices = list(Path(svc.journal.root, "outbox").glob("*")) if Path(svc.journal.root, "outbox").exists() else []
        assert not [p for p in notices if "return" in p.read_text()]


class TestCharterFile:
    def write(self, tmp_path, **over):
        doc = {"seat": "apprentice", "version": 1, "leads": ["mc"], "scopes": ["personal"], "readers": ["repo"], "daily_budget_usd": 1.0, "types": {"a": dict(KIND)}, **over}
        (tmp_path / "apprentice").mkdir(exist_ok=True)
        (tmp_path / "apprentice" / "v1.json").write_text(json.dumps(doc))
        return doc

    def approve(self, tmp_path, doc):
        (tmp_path / "approvals.json").write_text(json.dumps({"apprentice@1": {"digest": digest(doc), "approved_by": "robert", "ref": "words"}}))

    def test_a_matching_approval_loads(self, tmp_path):
        self.approve(tmp_path, self.write(tmp_path))
        assert Charters.load(tmp_path).get("apprentice", 1) is not None

    def test_a_changed_charter_refuses_the_start(self, tmp_path):
        self.approve(tmp_path, self.write(tmp_path))
        self.write(tmp_path, daily_budget_usd=9.0)
        with pytest.raises(CharterRefused, match="no approval"):
            Charters.load(tmp_path)

    @pytest.mark.parametrize("field,value", [("effects", "gated"), ("tools", ["repo.write"]), ("tools", ["shell.run"]), ("max_revisions", 9)])
    def test_the_label_cannot_lose_to_the_tool_list(self, tmp_path, field, value):
        doc = self.write(tmp_path, types={"a": dict(KIND, **{field: value})})
        self.approve(tmp_path, doc)
        with pytest.raises(CharterRefused):
            Charters.load(tmp_path)

    def test_the_two_real_charters_are_well_formed(self):
        from services.partnership_tasks.charters import _check
        root = Path(__file__).resolve().parents[2] / "config/partnership/charters"
        docs = [json.loads(p.read_text()) for p in root.glob("*/v*.json")]
        assert {d["seat"] for d in docs} == {"apprentice", "ea"}
        for d in docs:
            _check(d, d["seat"])
        assert "test-run" not in next(d for d in docs if d["seat"] == "apprentice")["types"]
        assert next(d for d in docs if d["seat"] == "ea")["leads"] == ["cos"]
