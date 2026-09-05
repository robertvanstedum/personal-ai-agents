"""The five boundary properties revision 7 makes mechanical.

Each test here reproduces one concrete way the committed implementation could
be made to lie: a short write reported as a durable commit, a validated grant
or receipt changed after validation, one single-use grant spent twice, a
malformed request escaping the response envelope, and a stored control record
trusted because of where it was found rather than what it says.

Nothing is simulated. The short-write tests drive the real writer through the
same seam the crash tests use, the concurrency test races two real threads
through the real issuer, and the control-record tests corrupt real files in a
real work tree and then ask the service an ordinary question about them.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading

import pytest

from conftest import Crash
from domains.cos.work import grants, store
from domains.cos.work.envelope import (
    WorkError,
    make_receipt,
    new_operation_id,
    success_response,
)


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


@pytest.fixture
def short_writes(monkeypatch):
    """Force the partial writes ``os.write`` is allowed to make.

    ``install()`` truncates every raw write to one byte for the whole call;
    ``install(arm_at=..., disarm_at=...)`` truncates only between two named
    transaction steps, so one site can be singled out. ``progress=0`` makes
    every write report no progress at all.
    """
    real = store._raw_write

    def install(*, arm_at=None, disarm_at=None, progress=1):
        state = {"armed": arm_at is None, "short": 0}

        def raw(handle, view):
            if state["armed"] and len(view) > progress:
                state["short"] += 1
                if progress == 0:
                    return 0
                return real(handle, view[:progress])
            return real(handle, view)

        monkeypatch.setattr(store, "_raw_write", raw)

        if arm_at is not None:
            def hook(step):
                if step == arm_at:
                    state["armed"] = True
                elif disarm_at is not None and step == disarm_at:
                    state["armed"] = False

            monkeypatch.setattr(store, "_checkpoint", hook)
        return state

    return install


# -- B1: a short write may never be reported as a durable commit -----------


def test_short_write_publishes_the_whole_canonical_payload(workspace, short_writes):
    """one-byte writes still produce the exact bytes the name promises"""
    state = short_writes()
    payload = store.encode_json({"schema_version": 1, "note": "x" * 400})
    operation_id = new_operation_id()

    store.publish(workspace, "control.json", payload, operation_id=operation_id)

    assert state["short"] > 1
    assert (workspace / "control.json").read_bytes() == payload


def test_short_write_installs_a_complete_record_candidate(flow, short_writes):
    """a truncated work.json can never be installed and then confirmed"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    state = short_writes(arm_at="P6", disarm_at="P7")

    response = flow.write(work_id, "A complete draft, written one byte at a time.\n")

    assert response["ok"] is True, response["error"]
    assert state["short"] > 1
    record, document, snap = store.read_record(paths)
    assert [artifact.ref for artifact in record.artifacts] == ["art-0001"]
    assert snap.raw == store.encode_json(document)


def test_short_write_publishes_the_whole_content_output(flow, short_writes):
    """the digest a record pins is the digest of what actually landed"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    content = "A draft long enough that one byte is not all of it.\n" * 20
    state = short_writes(arm_at="P7", disarm_at="P8")

    response = flow.write(work_id, content)

    assert response["ok"] is True, response["error"]
    assert state["short"] > 1
    record = store.read_record(paths)[0]
    artifact = record.artifacts[0]
    published = paths.directory / artifact.path
    assert published.read_text(encoding="utf-8") == content
    assert hashlib.sha256(published.read_bytes()).hexdigest() == artifact.sha256


def test_a_write_that_never_progresses_reaches_no_committed_marker(flow, short_writes):
    """a stalled write fails; it does not commit what it did not write"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    before = store.read_record(paths)[2].sha256
    operation_id = new_operation_id()
    short_writes(arm_at="P7", disarm_at="P8", progress=0)

    with pytest.raises(OSError):
        flow.write(work_id, "A draft that cannot be written.\n", operation_id=operation_id)

    assert store.read_terminal(paths, operation_id) is None
    assert store.read_record(paths)[2].sha256 == before
    assert store.read_record(paths)[0].artifacts == ()


# -- B2: validated authority and receipts cannot change afterwards ---------


def test_a_minted_grant_cannot_have_its_provenance_changed(issuer, flow):
    """mint-time policy is the last word on what these bytes are"""
    content = "Text an external turn captured.\n"
    grant = issuer.mint(
        effect="attach_source",
        subject="career",
        conversation_id="owner",
        data_class="external_public",
        source_class="external_source",
        work_id=flow.started(),
        content_sha256=flow._sha(content),
        content_bytes=len(content.encode("utf-8")),
    )

    with pytest.raises(TypeError):
        grant.bindings["source_class"] = "robert_source"

    assert grant.source_class == "external_source"
    assert issuer.peek(grant.grant_ref).source_class == "external_source"


def test_a_grant_binding_set_cannot_gain_a_resource(issuer, flow):
    """the authority cannot be widened to a second resource after mint"""
    grant = issuer.mint(
        effect="open_work", subject="career", work_id=flow.started()
    )

    with pytest.raises(TypeError):
        grant.bindings["allow_create"] = True

    assert grant.allow_create is False


def test_a_receipt_allowlist_cannot_change_after_construction():
    """a validated receipt cannot gain a body-carrying key afterwards"""
    operation_id = new_operation_id()
    receipt = make_receipt(
        operation_id, "search_sources", "ok", subject="career", result_count=0
    )

    with pytest.raises(TypeError):
        receipt.fields["relative_path"] = "sources/0001-leak.md"

    envelope = success_response("search_sources", operation_id, {}, receipt)
    assert set(envelope["receipt"]) == {
        "operation_id",
        "effect",
        "outcome",
        "subject",
        "result_count",
    }


# -- B3: one grant is one use, whatever the interleaving ------------------


def test_a_grant_cannot_be_spent_twice(issuer, flow):
    """spending an entry that is no longer live is refused, not ignored"""
    grant = issuer.mint(effect="open_work", subject="career", work_id=flow.started())
    issuer.consume(grant)

    with pytest.raises(WorkError) as excinfo:
        issuer.consume(grant)
    assert excinfo.value.code == "grant_invalid"


def test_two_threads_cannot_both_use_one_grant(issuer, flow):
    """verification and consumption are one step, so exactly one use wins"""
    work_id = flow.started()
    rounds = 25
    accepted: list[str] = []
    refused: list[str] = []
    guard = threading.Lock()

    for _ in range(rounds):
        grant = issuer.mint(effect="open_work", subject="career", work_id=work_id)
        barrier = threading.Barrier(2)

        def use():
            barrier.wait()
            try:
                issuer.verify_and_consume(
                    grant.grant_ref,
                    effect="open_work",
                    subject="career",
                    resolved={"work_id": work_id},
                )
            except WorkError as exc:
                with guard:
                    refused.append(exc.code)
            else:
                with guard:
                    accepted.append(grant.grant_ref)

        threads = [threading.Thread(target=use) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert len(accepted) == rounds
    assert len(refused) == rounds
    assert set(refused) == {"grant_invalid"}


# -- B4: a malformed request answers inside the envelope ------------------


@pytest.mark.parametrize(
    "root_refs",
    [
        123,
        {"root_ref": "resumes"},
        [["resumes"]],
        [{"root_ref": "resumes"}],
        ["../outside"],
        ["Resumes"],
        [None],
        [],
    ],
    ids=[
        "scalar",
        "object",
        "unhashable-member",
        "object-member",
        "traversal",
        "not-an-identifier",
        "null-member",
        "empty",
    ],
)
def test_malformed_root_refs_answer_inside_the_envelope(
    flow, work_root, snapshot_tree, root_refs
):
    """no request shape escapes as a raw exception, and none writes anything"""
    work_id = flow.started()
    available = list(flow.service.accumulation.available_root_refs("career"))
    grant = flow.mint("search_sources", work_id=work_id, root_refs=available)
    before = snapshot_tree(work_root)

    response = flow.call(
        "search_sources",
        {"work_id": work_id, "query": "operations", "root_refs": root_refs},
        grant=grant,
    )

    assert response["work_contract_version"] == 1
    assert response["effect"] == "search_sources"
    assert code_of(response) == "invalid_request"
    assert snapshot_tree(work_root) == before


# -- B5: stored control records are exact bound schemas -------------------


def crashed_pending(flow, work_id, crash_at, uninjected, *, content="A draft.\n"):
    operation_id = new_operation_id()
    crash_at("P6")
    with pytest.raises(Crash):
        flow.write(work_id, content, operation_id=operation_id)
    uninjected()
    paths = paths_for(flow, work_id)
    path = paths.pending / f"{operation_id}.json"
    return operation_id, paths, path, json.loads(path.read_bytes())


def rewrite(path, document):
    """Replace an immutable control record with a corrupted one."""
    path.unlink()
    if isinstance(document, bytes):
        path.write_bytes(document)
    else:
        path.write_bytes(store.encode_json(document))
    os.chmod(path, 0o600)


def test_a_real_pending_object_round_trips(flow, crash_at, uninjected):
    """the exact parser accepts exactly what the service writes"""
    work_id = flow.started()
    _, _, _, document = crashed_pending(flow, work_id, crash_at, uninjected)
    assert store.parse_intent(document).as_document() == document


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda d: d.update(note="a body"), "unknown-field"),
        (lambda d: d.update(operation_id="not-a-uuid"), "identifier"),
        (lambda d: d.update(record_candidate_sha256="00"), "digest"),
        (lambda d: d.update(target_relative_path="../../escape.md"), "traversal"),
        (lambda d: d.update(target_relative_path="sources/0001-letter.md"), "wrong-subtree"),
        (lambda d: d.update(effect="search_sources"), "effect-mismatch"),
        (lambda d: d.update(context_class="robert_source"), "provenance"),
        (lambda d: d.update(created_at="whenever"), "timestamp"),
        (lambda d: d.update(subject="Career/../other"), "subject"),
        (lambda d: d.update(ref="src-0001"), "ref-kind"),
        (lambda d: d.pop("record_sha256_before"), "missing-field"),
        (lambda d: d.pop("output_sha256"), "half-a-group"),
        (lambda d: d["receipt"].update(operation_id=new_operation_id()), "receipt-identity"),
        (lambda d: d["receipt"].update(work_id=new_operation_id()), "receipt-cross-field"),
    ],
)
def test_parse_intent_refuses_a_malformed_pending_object(
    flow, crash_at, uninjected, mutate, reason
):
    """every field of the pending object is validated, not stringified"""
    work_id = flow.started()
    _, _, _, document = crashed_pending(flow, work_id, crash_at, uninjected)
    mutate(document)

    with pytest.raises(WorkError) as excinfo:
        store.parse_intent(document)
    assert excinfo.value.code == "invalid_request"


def test_parse_intent_refuses_invalid_json():
    """a corrupted control record is not readable, not merely unusual"""
    with pytest.raises(WorkError) as excinfo:
        store.parse_intent(store.decode_control(b"{not json", "an operation record"))
    assert excinfo.value.code == "invalid_request"


@pytest.mark.parametrize(
    "document,reason",
    [
        ({"schema_version": 1, "operation_id": new_operation_id(), "outcome": "committed",
          "request_sha256": "a" * 64}, "committed-without-receipt"),
        ({"schema_version": 1, "operation_id": new_operation_id(), "outcome": "finished",
          "request_sha256": "a" * 64}, "unknown-outcome"),
        ({"schema_version": 1, "operation_id": new_operation_id(), "outcome": "abandoned",
          "request_sha256": "a" * 64, "note": "a body"}, "unknown-field"),
        ({"schema_version": 1, "operation_id": "not-a-uuid", "outcome": "abandoned",
          "request_sha256": "a" * 64}, "identifier"),
        ({"schema_version": 1, "operation_id": new_operation_id(), "outcome": "abandoned",
          "request_sha256": "short"}, "digest"),
        ({"schema_version": 1, "operation_id": new_operation_id(), "outcome": "quarantined",
          "request_sha256": "a" * 64, "reason_code": "because",
          "relative_path": "work.json"}, "unknown-reason"),
        ({"schema_version": 1, "operation_id": new_operation_id(), "outcome": "quarantined",
          "request_sha256": "a" * 64, "reason_code": "unreferenced_output",
          "relative_path": "../../etc/passwd"}, "traversal"),
        ({"schema_version": 2, "operation_id": new_operation_id(), "outcome": "abandoned",
          "request_sha256": "a" * 64}, "schema-version"),
    ],
)
def test_parse_terminal_refuses_a_malformed_marker(document, reason):
    """the terminal marker is an exact schema with conditional groups"""
    with pytest.raises(WorkError) as excinfo:
        store.parse_terminal(document)
    assert excinfo.value.code == "invalid_request"


def test_parse_terminal_refuses_a_receipt_for_another_operation():
    """a marker may not answer with a receipt that is not about it"""
    document = {
        "schema_version": 1,
        "operation_id": new_operation_id(),
        "outcome": "committed",
        "request_sha256": "a" * 64,
        "receipt": make_receipt(
            new_operation_id(), "search_sources", "ok", subject="career", result_count=1
        ).as_dict(),
    }
    with pytest.raises(WorkError) as excinfo:
        store.parse_terminal(document)
    assert excinfo.value.code == "invalid_request"


def reservation_document(flow, operation_id):
    subject_paths = flow.service.store.subject_paths(flow.subject)
    path = subject_paths.creates / f"{operation_id}.json"
    return subject_paths, path, json.loads(path.read_bytes())


def test_a_real_reservation_round_trips(flow):
    """the exact parser accepts exactly what a create writes"""
    operation_id = flow.new_operation_id()
    response = flow.create(operation_id=operation_id)
    assert response["ok"] is True
    _, _, document = reservation_document(flow, operation_id)
    assert store.parse_reservation(document).as_document() == document


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda d: d.update(note="a body"), "unknown-field"),
        (lambda d: d.update(work_dirname="../escape"), "directory"),
        (lambda d: d.update(work_dirname="operations-lead--" + new_operation_id()), "work-id"),
        (lambda d: d.update(work_id="not-a-uuid"), "identifier"),
        (lambda d: d.update(record_candidate_sha256="nope"), "digest"),
        (lambda d: d.update(reserved_at="whenever"), "timestamp"),
        (lambda d: d.update(record_sha256_before="b" * 64), "impossible-precondition"),
        (lambda d: d["receipt"].update(subject="decision-memo"), "receipt-cross-field"),
        (lambda d: d.pop("receipt"), "missing-receipt"),
    ],
)
def test_parse_reservation_refuses_a_malformed_record(flow, mutate, reason):
    """a create's recovery point is validated before it becomes a path"""
    operation_id = flow.new_operation_id()
    assert flow.create(operation_id=operation_id)["ok"] is True
    _, _, document = reservation_document(flow, operation_id)
    mutate(document)

    with pytest.raises(WorkError) as excinfo:
        store.parse_reservation(document)
    assert excinfo.value.code == "invalid_request"


# -- B5, end to end: recovery refuses to act on an unvalidated record ------


def test_a_corrupted_pending_object_answers_closed_and_changes_nothing(
    flow, crash_at, uninjected, work_root, snapshot_tree
):
    """a retry over a malformed intent installs nothing and reports nothing"""
    work_id = flow.started()
    operation_id, paths, path, _ = crashed_pending(flow, work_id, crash_at, uninjected)
    rewrite(path, b"{ this is not the record we wrote")
    before = snapshot_tree(work_root)

    response = flow.write(work_id, "A draft.\n", operation_id=operation_id)

    assert code_of(response) == "invalid_request"
    assert response["work_contract_version"] == 1
    assert store.read_terminal(paths, operation_id) is None
    assert snapshot_tree(work_root) == before


def test_a_pending_object_with_a_foreign_receipt_answers_closed(
    flow, crash_at, uninjected, work_root, snapshot_tree
):
    """a receipt about other work cannot become this operation's answer"""
    work_id = flow.started()
    operation_id, paths, path, document = crashed_pending(
        flow, work_id, crash_at, uninjected
    )
    document["receipt"]["operation_id"] = new_operation_id()
    rewrite(path, document)
    before = snapshot_tree(work_root)

    response = flow.write(work_id, "A draft.\n", operation_id=operation_id)

    assert code_of(response) == "invalid_request"
    assert store.read_terminal(paths, operation_id) is None
    assert snapshot_tree(work_root) == before


def test_a_pending_object_naming_a_path_outside_the_work_item_answers_closed(
    flow, crash_at, uninjected, work_root, snapshot_tree
):
    """no path is cleaned or published from an unvalidated relative path"""
    work_id = flow.started()
    operation_id, paths, path, document = crashed_pending(
        flow, work_id, crash_at, uninjected
    )
    document["target_relative_path"] = "../../../../tmp/escape.md"
    rewrite(path, document)
    before = snapshot_tree(work_root)

    response = flow.write(work_id, "A draft.\n", operation_id=operation_id)

    assert code_of(response) == "invalid_request"
    assert store.read_terminal(paths, operation_id) is None
    assert snapshot_tree(work_root) == before


def test_a_corrupted_terminal_marker_answers_closed(
    flow, work_root, snapshot_tree
):
    """a duplicate answer comes from a validated marker or from none"""
    work_id = flow.started()
    operation_id = new_operation_id()
    content = "A committed draft.\n"
    assert flow.write(work_id, content, operation_id=operation_id)["ok"] is True
    paths = paths_for(flow, work_id)
    marker = paths.directory / paths.terminal_relative(operation_id)
    document = json.loads(marker.read_bytes())
    document["receipt"]["relative_path"] = "../../elsewhere.md"
    rewrite(marker, document)
    before = snapshot_tree(work_root)

    response = flow.write(work_id, content, operation_id=operation_id)

    assert code_of(response) == "invalid_request"
    assert snapshot_tree(work_root) == before


def test_a_corrupted_create_reservation_answers_closed(
    flow, work_root, snapshot_tree
):
    """a create retry validates the record that owns its operation id"""
    operation_id = flow.new_operation_id()
    assert flow.create(operation_id=operation_id)["ok"] is True
    subject_paths, path, _ = reservation_document(flow, operation_id)
    rewrite(path, b"{ not a reservation")
    before = snapshot_tree(work_root)

    response = flow.create(operation_id=operation_id)

    assert code_of(response) == "invalid_request"
    assert snapshot_tree(work_root) == before


def test_a_reservation_naming_another_directory_answers_closed(
    flow, work_root, snapshot_tree
):
    """the directory a retry completes comes from a validated name"""
    operation_id = flow.new_operation_id()
    assert flow.create(operation_id=operation_id)["ok"] is True
    subject_paths, path, document = reservation_document(flow, operation_id)
    document["work_dirname"] = "../../escape"
    rewrite(path, document)
    before = snapshot_tree(work_root)

    response = flow.create(operation_id=operation_id)

    assert code_of(response) == "invalid_request"
    assert snapshot_tree(work_root) == before


def test_no_leaked_exception_types_from_control_records():
    """the boundary answers with Work errors, not decoder internals"""
    for call in (
        lambda: store.parse_intent(b"not a mapping"),
        lambda: store.parse_terminal([1, 2, 3]),
        lambda: store.parse_reservation(None),
        lambda: store.decode_control(b"\xff\xfe", "a terminal marker"),
    ):
        with pytest.raises(WorkError):
            call()


def test_control_record_failure_is_a_closed_work_error():
    """the failure class is a Work error carrying the closed code"""
    assert issubclass(store.ControlRecordInvalid, WorkError)
    assert store.ControlRecordInvalid("x").code == "invalid_request"
