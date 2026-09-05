"""The crash truth tables, exercised against the real transaction.

Every row here is reached by interrupting the actual sequence at one named
step and then opening the work item again. Nothing is simulated: the store
runs its ordinary code with one real failure in it, and the recovery pass
decides from digests alone.

Two invariants sit behind all of it. Nothing reports success without a
committed operation record, and no recoverable state depends on an exact
retry to become visible.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
from pathlib import Path

import pytest

from conftest import Crash
from domains.cos.work import records, store
from domains.cos.work.envelope import new_operation_id


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def crash_write(flow, work_id, step, crash_at, uninjected, *, content="A draft.\n"):
    """Interrupt one artifact write at ``step`` and return its operation id."""
    operation_id = new_operation_id()
    crash_at(step)
    with pytest.raises(Crash):
        flow.write(work_id, content, operation_id=operation_id)
    uninjected()
    return operation_id


def crash_disposition(flow, work_id, step, crash_at, uninjected):
    """Interrupt one metadata-only write at ``step``."""
    operation_id = new_operation_id()
    crash_at(step)
    with pytest.raises(Crash):
        flow.propose(work_id, "closed", operation_id=operation_id)
    uninjected()
    return operation_id


# -- content-producing writes: rows R0 to R7 -------------------------------


def test_crash_row_R0(flow, crash_at, uninjected):
    """before the pending object: nothing landed, and a retry is fresh"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    operation_id = crash_write(flow, work_id, "link:%s.json" % "", crash_at, uninjected) if False else None
    operation_id = new_operation_id()
    crash_at(f"link:{operation_id}.json")
    with pytest.raises(Crash):
        flow.write(work_id, "A draft.\n", operation_id=operation_id)
    uninjected()
    assert list(paths.pending.iterdir()) == []
    assert [p.name for p in paths.staging.iterdir()] == [
        f"{operation_id}.json.{operation_id}.tmp"
    ]
    assert flow.open_existing(work_id)["ok"] is True
    fresh = flow.write(work_id, "A draft.\n")
    assert fresh["ok"] is True


def test_crash_row_R1(flow, crash_at, uninjected):
    """after the pending object, before the candidate: abandon"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P6", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    assert (paths.pending / f"{operation_id}.json").exists()

    assert flow.open_existing(work_id)["ok"] is True
    terminal = store.read_terminal(paths, operation_id)
    assert terminal.outcome == "abandoned"
    assert store.read_record(paths)[0].artifacts == ()


@pytest.mark.parametrize("step,row", [("P7", "R2"), ("P8", "R3")])
def test_crash_rows_R2_and_R3(flow, crash_at, uninjected, step, row):
    """a staged candidate with no published content abandons"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, step, crash_at, uninjected)
    paths = paths_for(flow, work_id)
    assert (paths.directory / paths.record_candidate(operation_id)).exists()
    assert not (paths.artifacts / "0001-letter.md").exists()

    assert flow.open_existing(work_id)["ok"] is True
    assert store.read_terminal(paths, operation_id).outcome == "abandoned"
    assert not (paths.directory / paths.record_candidate(operation_id)).exists()
    assert store.read_record(paths)[0].artifacts == ()


def test_crash_row_R4(flow, crash_at, uninjected):
    """content published and a matching candidate: commit forward"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    assert (paths.artifacts / "0001-letter.md").exists()

    assert flow.open_existing(work_id)["ok"] is True
    terminal = store.read_terminal(paths, operation_id)
    assert terminal.outcome == "committed"
    record = store.read_record(paths)[0]
    assert [a.ref for a in record.artifacts] == ["art-0001"]


def test_crash_row_R4x_recovery_never_rebuilds_a_record(flow, crash_at, uninjected):
    """with the pinned candidate gone, nothing is reconstructed: quarantine"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    (paths.directory / paths.record_candidate(operation_id)).unlink()

    response = flow.open_existing(work_id)
    assert response["ok"] is True
    terminal = store.read_terminal(paths, operation_id)
    assert terminal.outcome == "quarantined"
    record = store.read_record(paths)[0]
    assert record.artifacts == ()
    assert not (paths.artifacts / "0001-letter.md").exists()
    assert any(paths.quarantine.iterdir())


def test_crash_row_R5(flow, crash_at, uninjected):
    """record installed, marker missing: publish the marker only"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P11", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    assert store.read_record(paths)[0].artifacts != ()
    assert store.read_terminal(paths, operation_id) is None

    assert flow.open_existing(work_id)["ok"] is True
    assert store.read_terminal(paths, operation_id).outcome == "committed"
    assert len(store.read_record(paths)[0].artifacts) == 1


def test_crash_row_R6(flow, crash_at, uninjected):
    """marker published, cleanup missing: publish nothing, tidy only"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P12", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    assert (paths.pending / f"{operation_id}.json").exists()
    terminal_before = store.read_terminal(paths, operation_id)

    assert flow.open_existing(work_id)["ok"] is True
    assert not (paths.pending / f"{operation_id}.json").exists()
    assert store.read_terminal(paths, operation_id).as_document() == (
        terminal_before.as_document()
    )


def test_crash_row_R7(flow):
    """after cleanup there is nothing to recover, and a retry answers from the marker"""
    work_id = flow.started()
    operation_id = new_operation_id()
    first = flow.write(work_id, "A draft.\n", operation_id=operation_id)
    paths = paths_for(flow, work_id)
    assert list(paths.pending.iterdir()) == []
    again = flow.write(work_id, "A draft.\n", operation_id=operation_id)
    assert again["receipt"] == first["receipt"]


def test_crash_before_candidate_abandons(flow, crash_at, uninjected):
    """R1 restated: nothing canonical exists, so abandoning is truthful"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P6", crash_at, uninjected)
    flow.open_existing(work_id)
    paths = paths_for(flow, work_id)
    assert store.read_terminal(paths, operation_id).outcome == "abandoned"
    assert list(paths.artifacts.iterdir()) == []


def test_crash_after_candidate_before_link_abandons(flow, crash_at, uninjected):
    """a record naming an artifact that was never published is never installed"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P8", crash_at, uninjected)
    flow.open_existing(work_id)
    paths = paths_for(flow, work_id)
    assert store.read_terminal(paths, operation_id).outcome == "abandoned"
    assert not (paths.directory / paths.record_candidate(operation_id)).exists()


def test_crash_after_link_commits_from_candidate(flow, crash_at, uninjected):
    """R4 restated: the staged candidate is what gets installed"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    staged = json.loads((paths.directory / paths.record_candidate(operation_id)).read_bytes())
    flow.open_existing(work_id)
    installed = json.loads(paths.record.read_bytes())
    assert installed == staged


def test_crash_after_record_replace_commits(flow, crash_at, uninjected):
    """R5 restated"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P11", crash_at, uninjected)
    flow.open_existing(work_id)
    assert store.read_terminal(paths_for(flow, work_id), operation_id).outcome == "committed"


def test_crash_after_terminal_marker_cleans_up(flow, crash_at, uninjected):
    """R6 restated: the pending object survives the marker and is only tidied"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P12", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    before = paths.operations.joinpath(f"{operation_id}.terminal.json").read_bytes()
    flow.open_existing(work_id)
    after = paths.operations.joinpath(f"{operation_id}.terminal.json").read_bytes()
    assert after == before


def test_crash_between_terminal_and_pending_cleanup(flow, crash_at, uninjected):
    """re-driving the same crash twice gives the same outcome and receipt"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P12", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    first = flow.open_existing(work_id)
    marker = store.read_terminal(paths, operation_id)
    second = flow.open_existing(work_id)
    assert second["ok"] is True
    assert store.read_terminal(paths, operation_id).as_document() == marker.as_document()
    assert first["ok"] is True


# -- metadata-only writes: rows M0 to M5 -----------------------------------


def test_crash_row_M1_metadata_only_crash_commits_forward(flow, crash_at, uninjected):
    """the private reason survives inside the candidate, never in a record"""
    work_id = flow.started()
    written = flow.write(work_id, "A draft.\n")
    proposed = flow.propose(work_id, "approved_text", written["result"]["artifact_ref"])
    pending_id = proposed["result"]["pending_id"]

    operation_id = new_operation_id()
    secret = "Because the second depot reconciliation is still open, and I said so."
    crash_at("P11")
    with pytest.raises(Crash):
        flow.decide(
            work_id, pending_id, "approved_text", reason=secret, operation_id=operation_id
        )
    uninjected()
    paths = paths_for(flow, work_id)

    assert flow.open_existing(work_id)["ok"] is True
    assert store.read_terminal(paths, operation_id).outcome == "committed"
    record = store.read_record(paths)[0]
    assert record.disposition is not None
    assert record.disposition.reason == secret
    for path in paths.operations.rglob("*"):
        if path.is_file():
            assert secret[:40] not in path.read_text("utf-8")


def test_crash_row_M2_metadata_only_crash_abandons(flow, crash_at, uninjected):
    """no usable candidate means nothing is mutated"""
    work_id = flow.started()
    operation_id = crash_disposition(flow, work_id, "P6", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    before = paths.record.read_bytes()

    assert flow.open_existing(work_id)["ok"] is True
    assert store.read_terminal(paths, operation_id).outcome == "abandoned"
    assert paths.record.read_bytes() == before


def test_installed_candidate_recognised_by_digest(flow, crash_at, uninjected):
    """M3: an already-installed candidate only needs its marker"""
    work_id = flow.started()
    operation_id = crash_disposition(flow, work_id, "P11", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    installed = paths.record.read_bytes()
    assert store.read_record(paths)[0].pending_approval is not None

    assert flow.open_existing(work_id)["ok"] is True
    assert store.read_terminal(paths, operation_id).outcome == "committed"
    assert paths.record.read_bytes() == installed


def test_record_changed_underneath_is_quarantined(flow, crash_at, uninjected):
    """M4: the live record hashes to neither pinned digest"""
    work_id = flow.started()
    operation_id = crash_disposition(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    document = json.loads(paths.record.read_text("utf-8"))
    document["label"] = "Changed by something else"
    paths.record.write_text(json.dumps(document))
    os.chmod(paths.record, 0o600)
    changed = paths.record.read_bytes()

    assert flow.open_existing(work_id)["ok"] is True
    terminal = store.read_terminal(paths, operation_id)
    assert terminal.outcome == "quarantined"
    assert terminal.reason_code == "record_changed_underneath"
    assert paths.record.read_bytes() == changed
    assert any(paths.quarantine.iterdir())


def test_crash_row_M5(flow, crash_at, uninjected):
    """a metadata write whose marker landed is only tidied"""
    work_id = flow.started()
    operation_id = crash_disposition(flow, work_id, "P12", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    assert (paths.pending / f"{operation_id}.json").exists()
    flow.open_existing(work_id)
    assert not (paths.pending / f"{operation_id}.json").exists()
    assert store.read_terminal(paths, operation_id).outcome == "committed"


# -- binding writes: rows B1 to B5 -----------------------------------------


def test_binding_recovery_rows(flow, crash_at, uninjected):
    """B1 to B4, one assertion each"""
    subject_paths = flow.service.store.subject_paths(flow.subject)

    # B1: a candidate exists and the pointer is still at its pre-image
    work_id = flow.started()
    operation_id = new_operation_id()
    crash_at("P10b")
    with pytest.raises(Crash):
        flow.open_existing(
            work_id, conversation_id="branch-one", operation_id=operation_id
        )
    uninjected()
    assert not (subject_paths.conversations / "branch-one.json").exists()
    assert flow.open_existing(work_id)["ok"] is True
    binding = json.loads((subject_paths.conversations / "branch-one.json").read_text("utf-8"))
    assert binding["work_id"] == work_id

    # B2: already installed, only the marker is missing
    second = flow.started(label="Second item")
    operation_id = new_operation_id()
    crash_at("P11")
    with pytest.raises(Crash):
        flow.open_existing(
            second, conversation_id="branch-two", operation_id=operation_id
        )
    uninjected()
    assert (subject_paths.conversations / "branch-two.json").exists()
    assert flow.open_existing(second)["ok"] is True
    paths = paths_for(flow, second)
    assert store.read_terminal(paths, operation_id).outcome == "committed"

    # B3: no usable candidate before anything canonical landed
    third = flow.started(label="Third item")
    operation_id = new_operation_id()
    crash_at("P10")
    with pytest.raises(Crash):
        flow.open_existing(
            third, conversation_id="branch-three", operation_id=operation_id
        )
    uninjected()
    (subject_paths.conversations / store.candidate_name(
        "branch-three.json", operation_id
    )).unlink()
    assert flow.open_existing(third)["ok"] is True
    assert store.read_terminal(paths_for(flow, third), operation_id).outcome == "abandoned"

    # B4: the pointer hashes to neither pinned digest
    fourth = flow.started(label="Fourth item")
    operation_id = new_operation_id()
    crash_at("P10b")
    with pytest.raises(Crash):
        flow.open_existing(
            fourth, conversation_id="branch-four", operation_id=operation_id
        )
    uninjected()
    interfered = subject_paths.conversations / "branch-four.json"
    interfered.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "conversation_id": "branch-four",
                "subject": flow.subject,
                "work_id": work_id,
                "updated_at": "2026-09-05T00:00:00Z",
            }
        )
    )
    os.chmod(interfered, 0o600)
    kept = interfered.read_bytes()
    assert flow.open_existing(fourth)["ok"] is True
    assert store.read_terminal(paths_for(flow, fourth), operation_id).outcome == "quarantined"
    assert interfered.read_bytes() == kept


# -- unreferenced and referenced outputs -----------------------------------


def test_mismatched_unreferenced_output_is_quarantined(flow, crash_at, uninjected):
    """X1: an unexplained file at a name we own is moved aside, not deleted"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    published = paths.artifacts / "0001-letter.md"
    published.write_text("Something else entirely.\n")
    os.chmod(published, 0o600)

    assert flow.open_existing(work_id)["ok"] is True
    terminal = store.read_terminal(paths, operation_id)
    assert terminal.outcome == "quarantined"
    assert not published.exists()
    preserved = list(paths.quarantine.iterdir())
    assert preserved
    assert any(p.read_text("utf-8") == "Something else entirely.\n" for p in preserved)


def test_mismatched_referenced_output_is_recorded_not_moved(flow, crash_at, uninjected):
    """X2: recorded bytes that changed are reported, and left where they are"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P11", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    published = paths.artifacts / "0001-letter.md"
    published.write_text("Edited in the folder by hand.\n")
    os.chmod(published, 0o600)

    assert flow.open_existing(work_id)["ok"] is True
    terminal = store.read_terminal(paths, operation_id)
    assert terminal.outcome == "quarantined"
    assert terminal.reason_code == "recorded_bytes_changed"
    assert published.read_text("utf-8") == "Edited in the folder by hand.\n"

    later = flow.write(work_id, "A later draft.\n", based_on=[
        {"ref": "art-0001", "sha256": "0" * 64}
    ])
    assert code_of(later) == "stale_context"


# -- bounds ----------------------------------------------------------------


def test_recovery_is_bounded_to_one_work_item(flow, monkeypatch):
    """another work item's operations are never opened"""
    first = flow.started(label="First")
    second = flow.started(label="Second")
    other_operations = paths_for(flow, first).operations
    real_scandir = os.scandir

    def watched(path=".", *args, **kwargs):
        assert os.path.realpath(str(path)) != os.path.realpath(str(other_operations))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", watched)
    assert flow.open_existing(second)["ok"] is True


def test_recovery_cap_reports_without_acting(flow):
    """more pending objects than the bound leave the pass bounded"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    for _ in range(store.MAX_RECOVERED_OPERATIONS + 5):
        (paths.pending / f"{uuid.uuid4()}.json").write_text("{}")
    for path in paths.pending.iterdir():
        os.chmod(path, 0o600)
    response = flow.open_existing(work_id)
    assert response["ok"] is True
    assert response["result"]["recovery_view_truncated"] is True


def test_recovery_bounded_with_large_committed_history(flow, monkeypatch):
    """a long committed history is never listed"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    for _ in range(300):
        (paths.operations / f"{uuid.uuid4()}.terminal.json").write_text("{}")
    for path in paths.operations.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)

    scanned = []
    real_scandir = os.scandir

    def watched(path=".", *args, **kwargs):
        scanned.append(os.path.realpath(str(path)))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", watched)
    assert flow.open_existing(work_id)["ok"] is True
    assert os.path.realpath(str(paths.operations)) not in scanned


def test_sixty_fifth_pending_marker_truncates_view(flow):
    """the pass stops at its bound rather than extending"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    for _ in range(store.MAX_RECOVERED_OPERATIONS + 1):
        (paths.pending / f"{uuid.uuid4()}.json").write_text("{}")
    for path in paths.pending.iterdir():
        os.chmod(path, 0o600)
    found, truncated = flow.service.store.select_pending(paths)
    assert truncated is True
    assert len(found) <= store.MAX_RECOVERED_OPERATIONS


def test_orphan_pending_temps_cannot_starve_recovery(flow, crash_at, uninjected):
    """orphan temps live elsewhere, so a real operation is reached at once"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)

    for _ in range(store.MAX_ORPHAN_TEMPS_SWEPT + 1):
        stray = uuid.uuid4()
        (paths.staging / f"{stray}.json.{stray}.tmp").write_text("orphan")
    for path in paths.staging.iterdir():
        os.chmod(path, 0o600)
    orphans = len(list(paths.staging.iterdir()))
    assert orphans == store.MAX_ORPHAN_TEMPS_SWEPT + 1

    assert flow.open_existing(work_id)["ok"] is True
    assert store.read_terminal(paths, operation_id).outcome == "committed"
    after_one = len(list(paths.staging.iterdir()))
    assert after_one < orphans

    passes = 0
    while list(paths.staging.iterdir()) and passes < 5:
        before = len(list(paths.staging.iterdir()))
        assert flow.open_existing(work_id)["ok"] is True
        assert len(list(paths.staging.iterdir())) < before
        passes += 1
    assert list(paths.staging.iterdir()) == []


def test_staging_sweep_is_bounded_and_monotonic(flow):
    """the sweep removes at most its bound, and nothing outside the grammar"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    for _ in range(store.MAX_ORPHAN_TEMPS_SWEPT + 10):
        stray = uuid.uuid4()
        (paths.staging / f"{stray}.json.{stray}.tmp").write_text("orphan")
    mismatched = f"{uuid.uuid4()}.json.{uuid.uuid4()}.tmp"
    (paths.staging / mismatched).write_text("not this operation's temp")
    (paths.staging / "something-else.txt").write_text("not a temp at all")
    for path in paths.staging.iterdir():
        os.chmod(path, 0o600)

    removed = flow.service.store.sweep_staging(paths)
    assert removed <= store.MAX_ORPHAN_TEMPS_SWEPT
    assert (paths.staging / mismatched).exists()
    assert (paths.staging / "something-else.txt").exists()


def test_pending_namespace_holds_only_final_names(flow, crash_at, uninjected):
    """nothing but a final operation name is ever created in the namespace"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    seen = set()

    for step in ("P6", "P7", "P8", "P10", "P11", "P12"):
        operation_id = crash_write(flow, work_id, step, crash_at, uninjected)
        seen.update(p.name for p in paths.pending.iterdir())
        flow.open_existing(work_id)
        seen.update(p.name for p in paths.pending.iterdir())

    for name in seen:
        assert name.endswith(".json")
        assert not name.endswith(".tmp")
        assert store._PENDING_NAME.fullmatch(name) is not None


def test_post_link_crash_leaves_no_orphan_temp(flow, crash_at, uninjected):
    """after a commit forward exactly one name points at the inode"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P9", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    published = paths.artifacts / "0001-letter.md"
    assert published.exists()
    assert published.stat().st_nlink == 2

    assert flow.open_existing(work_id)["ok"] is True
    assert published.stat().st_nlink == 1
    assert [p.name for p in paths.artifacts.iterdir()] == ["0001-letter.md"]


def test_no_dangling_candidates(flow, crash_at, uninjected):
    """after recovery from every injection point nothing is left staged"""
    for step in ("P6", "P7", "P8", "P10", "P11", "P12"):
        work_id = flow.started(label=f"Item for {step}")
        crash_write(flow, work_id, step, crash_at, uninjected)
        assert flow.open_existing(work_id)["ok"] is True
        paths = paths_for(flow, work_id)
        leftovers = [
            str(path.relative_to(paths.directory))
            for path in paths.directory.rglob("*")
            if path.is_file()
            and (path.name.endswith(".candidate") or path.name.endswith(".tmp"))
            and "quarantine" not in path.parts
        ]
        assert leftovers == [], f"{step} left {leftovers}"
        assert list(paths.staging.iterdir()) == []


def test_no_intent_without_discoverability(flow, crash_at, uninjected):
    """every injection point leaves nothing, or a scannable pending object"""
    for step in ("P6", "P7", "P8", "P10", "P11"):
        work_id = flow.started(label=f"Item for {step}")
        operation_id = crash_write(flow, work_id, step, crash_at, uninjected)
        paths = paths_for(flow, work_id)
        found, _truncated = flow.service.store.select_pending(paths)
        assert [intent.operation_id for intent in found] == [operation_id]


def test_abandoned_is_a_failure_response(flow, crash_at, uninjected):
    """an abandoned operation is never a write success"""
    work_id = flow.started()
    operation_id = crash_write(flow, work_id, "P6", crash_at, uninjected)
    response = flow.write(work_id, "A draft.\n", operation_id=operation_id)
    assert response["ok"] is False
    assert response["receipt"] is None
    assert response["error"]["code"] == "internal_error"

    fresh = flow.write(work_id, "A draft.\n")
    assert fresh["ok"] is True


def test_quarantined_is_a_failure_response(flow, crash_at, uninjected):
    """a quarantined operation names a work-relative path and carries no receipt"""
    work_id = flow.started()
    operation_id = crash_disposition(flow, work_id, "P10", crash_at, uninjected)
    paths = paths_for(flow, work_id)
    document = json.loads(paths.record.read_text("utf-8"))
    document["label"] = "Changed by something else"
    paths.record.write_text(json.dumps(document))
    os.chmod(paths.record, 0o600)

    response = flow.propose(work_id, "closed", operation_id=operation_id)
    assert response["ok"] is False
    assert response["receipt"] is None
    assert response["error"]["code"] == "stale_context"
    assert not response["error"]["relative_path"].startswith("/")

    fresh = flow.propose(work_id, "closed")
    assert fresh["ok"] is True


def test_no_success_without_commit_marker(flow, crash_at, uninjected):
    """no injected failure produces a committed receipt without its marker"""
    for step in ("P5", "P6", "P7", "P8", "P10", "P11"):
        work_id = flow.started(label=f"Item for {step}")
        paths = paths_for(flow, work_id)
        operation_id = new_operation_id()
        crash_at(step)
        with pytest.raises(Crash):
            flow.write(work_id, "A draft.\n", operation_id=operation_id)
        uninjected()
        terminal = store.read_terminal(paths, operation_id)
        assert terminal is None or terminal.outcome != "committed"

        response = flow.open_existing(work_id)
        assert response["ok"] is True
        assert response["receipt"]["outcome"] == "ok"


def test_no_terminal_marker_for_a_non_mutating_resume(flow):
    """an open that changes no pointer publishes nothing at all"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    before = sorted(str(p.relative_to(paths.directory)) for p in paths.operations.rglob("*"))
    response = flow.open_existing(work_id)
    assert response["ok"] is True
    assert response["receipt"]["outcome"] == "ok"
    after = sorted(str(p.relative_to(paths.directory)) for p in paths.operations.rglob("*"))
    assert after == before


@pytest.mark.parametrize("row", ["C0t", "C1", "C1a", "C2", "C3", "C4", "C4x", "C7"])
def test_create_truth_table_rows(flow, crash_at, uninjected, row):
    """one interruption per create row, decided by the reservation"""
    subject_paths = flow.service.store.subject_paths(flow.subject)
    operation_id = new_operation_id()
    conversation = f"row-{row.casefold()}"

    step = {
        "C0t": None,
        "C1": "create:mkdir:work",
        "C1a": "create:mkdir:artifacts",
        "C2": "P10",
        "C3": "P10b",
        "C4": "P11",
        "C4x": "P12",
        "C7": None,
    }[row]

    if row == "C0t":
        crash_at("", matcher=lambda name: name.startswith("link:") and "terminal" not in name)
        with pytest.raises(Crash):
            flow.create(operation_id=operation_id)
        uninjected()
        assert not (subject_paths.creates / f"{operation_id}.json").exists()
        completed = flow.create(operation_id=operation_id)
        assert completed["ok"] is True
        return

    if row == "C7":
        first = flow.create(conversation_id=conversation, operation_id=operation_id)
        (subject_paths.conversations / f"{conversation}.json").unlink()
        response = flow.create(conversation_id=conversation, operation_id=operation_id)
        assert code_of(response) == "stale_context"
        assert first["ok"] is True
        return

    crash_at(step)
    with pytest.raises(Crash):
        flow.create(conversation_id=conversation, operation_id=operation_id)
    uninjected()
    completed = flow.create(conversation_id=conversation, operation_id=operation_id)
    assert completed["ok"] is True, completed
    reservation = json.loads(
        (subject_paths.creates / f"{operation_id}.json").read_text("utf-8")
    )
    assert completed["receipt"]["work_id"] == reservation["work_id"]
    binding = json.loads(
        (subject_paths.conversations / f"{conversation}.json").read_text("utf-8")
    )
    assert binding["work_id"] == reservation["work_id"]
    assert len(list(subject_paths.work_base.iterdir())) == 1
