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


def test_a_builder_writable_acceptance_file_never_opens_a_real_store(tmp_path):
    """Codex finding 1: the old gate opened on a file the builder could write. Now nothing opens a real store."""
    real = service(tmp_path / "dev-store", environment="dev")
    ck = tmp_path / "CURRENT_HANDOFF.json"
    diff_sha = "a" * 64
    acc = tmp_path / "REVIEW_CODEX_TASK_STORE_ACCEPTANCE.md"
    acc.write_text(f"accepted_by: codex\nreviewed task-store diff: {diff_sha}\n")
    good = hashlib.sha256(acc.read_bytes()).hexdigest()
    ck.write_text(json.dumps({"build_tracking": {"acceptance": {"path": str(acc), "sha256": good}}}))
    with pytest.raises(GateClosed, match="independently attributable"):
        gate(real, ck, reviewed_diff_sha256=diff_sha)
    with pytest.raises(GateClosed, match="sha256"):
        gate(real, ck, reviewed_diff_sha256="not-a-digest")


def test_root_guard_resolves_symlinks_and_compares_components(tmp_path):
    private = tmp_path / "private"; private.mkdir()
    (tmp_path / "link").symlink_to(private, target_is_directory=True)
    with pytest.raises(GateClosed):
        gate(service(tmp_path / "link" / "tasks"), synthetic_root_guard=[private])     # through a symlink
    near = service(tmp_path / "private-ish" / "tasks")                                 # a string prefix only
    assert gate(near, synthetic_root_guard=[private])["open"] is True


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
