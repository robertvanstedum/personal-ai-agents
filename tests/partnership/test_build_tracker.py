"""B3D: build as first customer (build plan section "Build as first customer"). SYNTHETIC store only: the real build
keeps CURRENT_HANDOFF.json as its record until the task store's receipt/replay/scope acceptance is recorded."""
from __future__ import annotations

import json

import pytest

import hashlib

from services.partnership_tasks.build_tracker import GateClosed, gate, open_package, record_package_words
from services.partnership_tasks.reconstruct import Reconstructor
from tests.partnership.synth import Flow, cmd, ref, service

GRANT = {"store": "grants", "id": "grant-syn-build", "edition": "1", "digest": None}
DIFF = "sha256:" + "d" * 64


def test_a_package_is_tracked_from_assignment_to_codex_acceptance(tmp_path):
    svc = service(tmp_path / "store")
    f = Flow(svc)
    ck = tmp_path / "CURRENT_HANDOFF.json"
    ck.write_text("{}")
    sid = record_package_words(svc, lead_token="tok-peer", package_id="B3D",
                               words="Build B3D: track build packages in the task store.")   # the lead's own call
    tid = open_package(svc, ck, builder_token="tok-builder", package_id="B3D", source_event_id=sid, lead="codex",
                       holder="claude-code", grant_ref=GRANT)
    again = open_package(svc, ck, builder_token="tok-builder", package_id="B3D", source_event_id=sid, lead="codex",
                         holder="claude-code", grant_ref=GRANT)
    assert again == tid                                                      # replay, not a second package
    task = svc.get("task", tid, "tok-builder")
    assert (task["lead"], task["holder"], task["data_scope"]) == ("codex", "claude-code", "shared:build")
    t = svc.get("task", tid, "tok-builder")
    f.ok(cmd("task.transition", "task", tid, {"to": "working", "reason": None, "waiting": None,
                                              "evidence_refs": [ref("artifacts", "worktree-partnership-v11")],
                                              "fence_generation": t["fence_generation"]},
             expected=t["revision"], scope="shared:build"), "tok-builder")
    t = svc.get("task", tid, "tok-builder")
    f.ok(cmd("task.submit_artifacts", "task", tid,
             {"artifacts": [ref("artifacts", "B3D.diff", DIFF, "1")], "fence_generation": t["fence_generation"]},
             expected=t["revision"], scope="shared:build"), "tok-builder")
    t = svc.get("task", tid, "tok-builder")
    current = t["artifact_sets"][-1]["digest"]
    f.ok(cmd("task.record_review", "task", tid,
             {"review_id": "RV-syn-b3d-1", "reviewed_set_digest": current, "input_digest": current, "verdict": "approve",
              "review_ref": ref("artifacts", "REVIEW_B3D", "sha256:" + "e" * 64, "1"), "findings_ref": None},
             expected=t["revision"], scope="shared:build"), "tok-mc")                  # a non-author reviewer
    t = svc.get("task", tid, "tok-builder")
    own = svc.submit(cmd("task.record_review", "task", tid,
                         {"review_id": "RV-syn-self-1", "reviewed_set_digest": current, "input_digest": current,
                          "verdict": "approve", "review_ref": ref("artifacts", "SELF", "sha256:" + "f" * 64, "1"),
                          "findings_ref": None}, expected=t["revision"], scope="shared:build"), "tok-builder")
    assert own["detail"] == "author_cannot_review_own_set"                   # no self-review
    res = svc.submit(cmd("task.accept", "task", tid, {"artifact_set_digest": current, "acknowledged_disagreements": [],
                                                      "fence_generation": t["fence_generation"]},
                         expected=t["revision"], scope="shared:build"), "tok-builder")
    assert res["detail"] == "only_the_lead_accepts"                          # the builder never self-approves
    f.ok(cmd("task.accept", "task", tid, {"artifact_set_digest": current, "acknowledged_disagreements": [],
                                          "fence_generation": t["fence_generation"]},
             expected=t["revision"], scope="shared:build"), "tok-peer")
    out = Reconstructor(svc).reconstruct("task", tid, "tok-builder")
    assert out["chain"]["task"]["state"] == "completed" and out["acceptances"][-1]["by"] == "codex"


def test_the_gate_needs_a_hash_verified_codex_acceptance_naming_the_reviewed_diff(tmp_path):
    real = service(tmp_path / "dev-store", environment="dev")
    ck = tmp_path / "CURRENT_HANDOFF.json"
    diff_sha = "a" * 64
    acc = tmp_path / "REVIEW_CODEX_TASK_STORE_ACCEPTANCE.md"
    acc.write_text(f"accepted_by: codex\nreviewed task-store diff: {diff_sha}\n")
    good = hashlib.sha256(acc.read_bytes()).hexdigest()
    ck.write_text(json.dumps({"build_tracking": {"enabled": True, "accepted_by": "codex",
                                                 "acceptance_ref": "anything"}}))
    with pytest.raises(GateClosed):                                          # self-asserted fields do not open it
        gate(real, ck, reviewed_diff_sha256=diff_sha)
    ck.write_text(json.dumps({"build_tracking": {"acceptance": {"path": str(acc), "sha256": good}}}))
    assert gate(real, ck, reviewed_diff_sha256=diff_sha)["open"] is True
    with pytest.raises(GateClosed, match="reviewed task-store diff"):
        gate(real, ck, reviewed_diff_sha256="b" * 64)                         # accepted something else
    acc.write_text(f"accepted_by: codex\nreviewed task-store diff: {diff_sha}\nedited later\n")
    with pytest.raises(GateClosed, match="does not match its recorded hash"):
        gate(real, ck, reviewed_diff_sha256=diff_sha)


def test_a_synthetic_label_on_a_private_store_path_stays_closed(tmp_path):
    svc = service(tmp_path / "private" / "tasks")
    with pytest.raises(GateClosed):
        gate(svc, tmp_path / "ck.json", synthetic_root_guard=[tmp_path / "private"])


def test_package_ids_never_collide_and_changed_words_are_refused(tmp_path):
    svc = service(tmp_path / "store")
    a = record_package_words(svc, lead_token="tok-peer", package_id="B3.D", words="One.")
    b = record_package_words(svc, lead_token="tok-peer", package_id="B3-D", words="Two.")
    assert a != b
    with pytest.raises(RuntimeError):
        record_package_words(svc, lead_token="tok-peer", package_id="B3.D", words="Changed.")
