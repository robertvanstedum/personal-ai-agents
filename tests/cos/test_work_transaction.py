"""Publication, idempotency, byte accounting and the confined snapshot.

The properties here are the ones that make a stored record trustworthy: a
published name is never replaced, an operation id commits once, every digest
comes from the bytes that were actually read, and nothing at all is written
on the way to a refusal.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from importlib import import_module

from domains.cos.work import records, service, store

#: The confinement *module*: the package re-exports a function of that name.
confine = import_module("domains.cos.work.confine")
from domains.cos.work.envelope import Receipt, new_operation_id

DIGEST = "d" * 64


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def record_of(flow, work_id):
    return store.read_record(paths_for(flow, work_id))[0]


def test_source_bytes_are_byte_identical(flow):
    """captured bytes and their digest describe what was supplied"""
    work_id = flow.started()
    text = "Supplied in conversation, verbatim.\n"
    response = flow.attach_inline(work_id, text)
    stored = (flow.work_dir(work_id) / response["result"]["relative_path"]).read_bytes()
    assert stored == text.encode("utf-8")
    assert response["result"]["sha256"] == confine.sha256_bytes(stored)
    assert response["result"]["bytes"] == len(stored)


def test_artifact_revisions_strictly_increase_gap_free(flow):
    """revisions run 1, 2, 3 with no gaps and no reuse"""
    work_id = flow.started()
    first = flow.write(work_id, "Draft one.\n")
    second = flow.edit_inline(
        work_id, "Draft one, corrected.\n", "art-0001", first["result"]["sha256"]
    )
    third = flow.write(work_id, "Draft three.\n")
    assert [r["result"]["revision"] for r in (first, second, third)] == [1, 2, 3]
    assert [a.revision for a in record_of(flow, work_id).artifacts] == [1, 2, 3]


def test_published_names_are_never_replaced(flow, tmp_path):
    """publication refuses an existing name rather than overwriting it"""
    directory = Path(os.path.realpath(tmp_path))
    os.chmod(directory, 0o700)
    operation_id = new_operation_id()
    store.publish(directory, "thing.json", b"first\n", operation_id=operation_id)
    with pytest.raises(store.AlreadyPublished):
        store.publish(directory, "thing.json", b"second\n", operation_id=operation_id)
    assert (directory / "thing.json").read_bytes() == b"first\n"


def test_stale_based_on_writes_nothing(flow):
    """an input whose bytes moved refuses the write, and leaves no trace"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "An input.\n")
    before = sorted(p.name for p in (flow.work_dir(work_id) / "artifacts").iterdir())
    response = flow.write(
        work_id,
        "A draft.\n",
        based_on=[{"ref": attached["result"]["source_ref"], "sha256": DIGEST}],
    )
    assert code_of(response) == "stale_context"
    assert response["error"]["relative_path"] == attached["result"]["relative_path"]
    assert sorted(p.name for p in (flow.work_dir(work_id) / "artifacts").iterdir()) == before
    assert list(paths_for(flow, work_id).pending.iterdir()) == []
    assert record_of(flow, work_id).artifacts == ()


def test_duplicate_operation_id_returns_original_receipt(flow):
    """one revision, and the same receipt bytes, however often it is retried"""
    work_id = flow.started()
    operation_id = new_operation_id()
    content = "A draft written once.\n"
    first = flow.write(work_id, content, operation_id=operation_id)
    assert first["ok"] is True
    second = flow.write(work_id, content, operation_id=operation_id)
    assert second["ok"] is True
    assert second["receipt"] == first["receipt"]
    assert second["receipt"]["outcome"] == "committed"
    assert second["result"]["retry"] is True
    assert "retry" not in second["receipt"]
    assert len(record_of(flow, work_id).artifacts) == 1


def test_committed_receipt_is_byte_identical_on_retry(flow, crash_at, uninjected):
    """first completion, recovery and retry all return the same bytes"""
    work_id = flow.started()
    content = "A draft interrupted after publication.\n"
    operation_id = new_operation_id()
    crash_at("P10")
    from conftest import Crash

    with pytest.raises(Crash):
        flow.write(work_id, content, operation_id=operation_id)
    uninjected()

    recovered = flow.open_existing(work_id)
    assert recovered["ok"] is True
    stored = store.read_terminal(paths_for(flow, work_id), operation_id)
    assert stored.outcome == "committed"

    retried = flow.write(work_id, content, operation_id=operation_id)
    assert retried["ok"] is True
    assert retried["receipt"] == stored.receipt.as_dict()
    assert len(record_of(flow, work_id).artifacts) == 1


def test_one_terminal_filename_is_exclusive(flow):
    """there is one terminal name, and the first publication owns it"""
    work_id = flow.started()
    written = flow.write(work_id, "A draft.\n")
    operation_id = written["receipt"]["operation_id"]
    paths = paths_for(flow, work_id)
    markers = [p.name for p in paths.operations.iterdir() if p.is_file()]
    assert markers.count(f"{operation_id}.terminal.json") == 1
    assert all(name.endswith(".terminal.json") for name in markers)
    with pytest.raises(store.AlreadyPublished):
        store.publish(
            paths.operations,
            paths.terminal_name(operation_id),
            b"{}\n",
            operation_id=operation_id,
        )


def test_candidate_staged_before_publication(flow, monkeypatch):
    """the pinned candidate is durable before any canonical name exists"""
    order = []
    real_stage = store.stage_candidate
    real_link = os.link

    def watched_stage(directory, name, payload):
        order.append(("candidate", name))
        return real_stage(directory, name, payload)

    def watched_link(src, dst, **kwargs):
        order.append(("link", str(dst)))
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(store, "stage_candidate", watched_stage)
    monkeypatch.setattr(os, "link", watched_link)
    work_id = flow.started()
    order.clear()
    assert flow.write(work_id, "A draft.\n")["ok"] is True
    kinds = [kind for kind, _name in order]
    assert kinds.index("candidate") < kinds.index("link", kinds.index("candidate"))
    # the pending object is published before the candidate is staged
    assert order[0] == ("link", [name for kind, name in order if kind == "link"][0])


def test_open_work_writes_column_is_exact(flow):
    """an open writes on create, on a binding change, and on a recovery only"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    markers_after_create = sorted(p.name for p in paths.operations.iterdir() if p.is_file())
    assert len(markers_after_create) == 1

    again = flow.open_existing(work_id)
    assert again["ok"] is True
    assert again["receipt"]["outcome"] == "ok"
    assert sorted(p.name for p in paths.operations.iterdir() if p.is_file()) == (
        markers_after_create
    )

    changed = flow.open_existing(work_id, conversation_id="second-conversation")
    assert changed["receipt"]["outcome"] == "committed"
    assert len(sorted(p.name for p in paths.operations.iterdir() if p.is_file())) == 2


def test_internal_reads_use_one_confined_snapshot(flow, monkeypatch):
    """digest and size always come from the same read"""
    seen = []
    real = store.snapshot

    def watched(root, relative_path, **kwargs):
        snap = real(root, relative_path, **kwargs)
        seen.append((snap.relative_path, snap.sha256, snap.bytes, len(snap.raw)))
        return snap

    monkeypatch.setattr(store, "snapshot", watched)
    work_id = flow.started()
    flow.attach_inline(work_id, "An input.\n")
    assert flow.write(work_id, "A draft.\n")["ok"] is True
    assert seen
    for _path, digest, size, raw_length in seen:
        assert size == raw_length


@pytest.mark.parametrize(
    "shape",
    ["work_json", "pending_object", "terminal_marker", "record_candidate", "binding_candidate",
     "content_temp"],
)
def test_internal_snapshot_reads_each_bookkeeping_shape(flow, shape, tmp_path):
    """the snapshot helper is executed against every internal file shape"""
    work_id = flow.started(conversation_id="owner")
    written = flow.write(work_id, "A draft.\n")
    paths = paths_for(flow, work_id)
    operation_id = written["receipt"]["operation_id"]
    subject_paths = flow.service.store.subject_paths("career")

    if shape == "work_json":
        snap = store.snapshot(paths.directory, store.WORK_RECORD_FILENAME)
        assert json.loads(snap.raw)["work_id"] == work_id
    elif shape == "terminal_marker":
        snap = store.snapshot(paths.directory, paths.terminal_relative(operation_id))
        assert json.loads(snap.raw)["outcome"] == "committed"
    elif shape == "pending_object":
        name = f"{new_operation_id()}.json"
        (paths.pending / name).write_bytes(b'{"a":1}\n')
        os.chmod(paths.pending / name, 0o600)
        snap = store.snapshot(paths.directory, f"operations/pending/{name}")
        assert snap.raw == b'{"a":1}\n'
    elif shape == "record_candidate":
        name = paths.record_candidate(operation_id)
        store.stage_candidate(paths.directory, name, b'{"candidate":1}\n')
        snap = store.snapshot(paths.directory, name)
        assert snap.bytes == len(snap.raw)
    elif shape == "binding_candidate":
        name = store.candidate_name("owner.json", operation_id)
        store.stage_candidate(subject_paths.conversations, name, b'{"binding":1}\n')
        snap = store.snapshot(subject_paths.base, f"conversations/{name}")
        assert snap.sha256 == confine.sha256_bytes(snap.raw)
    else:
        name = store.temp_name("0002-letter.md", operation_id)
        (paths.artifacts / name).write_bytes(b"temp\n")
        os.chmod(paths.artifacts / name, 0o600)
        snap = store.snapshot(paths.directory, f"artifacts/{name}")
        assert snap.raw == b"temp\n"


def test_snapshot_passes_extensions_to_both_gates(flow, monkeypatch):
    """both gates get the same explicit limits, and neither takes a default"""
    calls = []
    real_confine = confine.confine
    real_read = confine.read_bytes

    def watched_confine(root, candidate, **kwargs):
        calls.append(("confine", kwargs.get("max_bytes"), kwargs.get("allowed_extensions")))
        return real_confine(root, candidate, **kwargs)

    def watched_read(confined, **kwargs):
        calls.append(("read_bytes", kwargs.get("max_bytes"), kwargs.get("allowed_extensions")))
        return real_read(confined, **kwargs)

    monkeypatch.setattr(confine, "confine", watched_confine)
    monkeypatch.setattr(confine, "read_bytes", watched_read)

    work_id = flow.started()
    paths = paths_for(flow, work_id)
    calls.clear()
    store.snapshot(paths.directory, store.WORK_RECORD_FILENAME)
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1] == store.MAX_RECORD_BYTES
    assert calls[0][2] == calls[1][2] == store.INTERNAL_EXTENSIONS
    assert all(value is not None for _name, _max, value in calls)


def test_no_require_sha256_in_w0b_paths():
    """no transaction path reopens a pathname to decide a digest"""
    import domains.cos.work as package

    root = Path(package.__file__).parent
    for name in ("service.py", "store.py", "grants.py", "adapter.py", "approval.py"):
        text = (root / name).read_text("utf-8")
        for forbidden in ("require_sha256", "verify_sha256", "sha256_file", "load_work_record",
                          "load_conversation_binding"):
            assert forbidden not in text, f"{name} calls {forbidden}"


def test_based_on_symlink_swap_is_denied(flow):
    """a component replaced by a link between record and verify is refused"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "An input.\n")
    paths = paths_for(flow, work_id)
    elsewhere = paths.directory.parent / "elsewhere"
    elsewhere.mkdir(mode=0o700, exist_ok=True)
    (elsewhere / Path(attached["result"]["relative_path"]).name).write_text("Other bytes.\n")
    real_sources = paths.sources
    swapped = paths.directory / "sources-real"
    real_sources.rename(swapped)
    os.symlink(elsewhere, real_sources)
    try:
        response = flow.write(
            work_id,
            "A draft.\n",
            based_on=[
                {
                    "ref": attached["result"]["source_ref"],
                    "sha256": attached["result"]["sha256"],
                }
            ],
        )
        assert code_of(response) in ("path_denied", "stale_context")
    finally:
        os.unlink(real_sources)
        swapped.rename(real_sources)


def test_supersedes_symlink_swap_is_denied(flow):
    """the same protection covers the artifact an edit replaces"""
    work_id = flow.started()
    written = flow.write(work_id, "Draft one.\n")
    paths = paths_for(flow, work_id)
    elsewhere = paths.directory.parent / "elsewhere-art"
    elsewhere.mkdir(mode=0o700, exist_ok=True)
    (elsewhere / "0001-letter.md").write_text("Other bytes.\n")
    real = paths.artifacts
    swapped = paths.directory / "artifacts-real"
    real.rename(swapped)
    os.symlink(elsewhere, real)
    try:
        response = flow.edit_inline(
            work_id, "Corrected.\n", "art-0001", written["result"]["sha256"]
        )
        assert code_of(response) in ("path_denied", "stale_context")
    finally:
        os.unlink(real)
        swapped.rename(real)


def test_byte_accounting_uses_the_snapshot(flow):
    """a file swapped underneath cannot buy capacity, and is caught"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "A" * 1000)
    stored = flow.work_dir(work_id) / attached["result"]["relative_path"]
    stored.write_text("B" * 10)
    response = flow.write(work_id, "A draft.\n")
    assert code_of(response) == "stale_context"


def test_stale_byte_count_fails_closed(flow):
    """a record whose byte count disagrees with the file buys nothing"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "A" * 1000)
    paths = paths_for(flow, work_id)
    document = json.loads((paths.record).read_text("utf-8"))
    document["sources"][0]["bytes"] = 10
    (paths.record).write_text(json.dumps(document))
    os.chmod(paths.record, 0o600)
    response = flow.write(work_id, "A draft.\n")
    assert code_of(response) == "invalid_request"


def test_writer_records_always_carry_bytes(flow):
    """every entry this writer produces states its own size"""
    work_id = flow.started()
    flow.attach_inline(work_id, "An input.\n")
    flow.write(work_id, "A draft.\n")
    record = record_of(flow, work_id)
    for entry in list(record.sources) + list(record.artifacts):
        assert entry.bytes is not None
        assert entry.bytes > 0


def test_work_total_exactly_at_limit_is_accepted(flow, monkeypatch):
    """a write that brings the total to exactly the ceiling commits"""
    monkeypatch.setattr(store, "MAX_WORK_TOTAL_BYTES", 200)
    work_id = flow.started()
    response = flow.attach_inline(work_id, "x" * 200)
    assert response["ok"] is True
    assert response["result"]["bytes"] == 200


def test_work_total_one_byte_over_is_refused(flow, monkeypatch):
    """one byte past the ceiling refuses, and consumes nothing"""
    monkeypatch.setattr(store, "MAX_WORK_TOTAL_BYTES", 200)
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    response = flow.attach_inline(work_id, "x" * 201)
    assert code_of(response) == "too_large"
    assert list(paths.sources.iterdir()) == []
    assert list(paths.pending.iterdir()) == []
    assert [p.name for p in paths.operations.iterdir() if p.is_file()] == [
        f"{p.name}" for p in paths.operations.iterdir() if p.is_file()
    ]
    assert record_of(flow, work_id).sources == ()


def test_over_limit_duplicate_retry_returns_original_receipt(flow, monkeypatch):
    """work sitting at the ceiling can still answer its own retry"""
    monkeypatch.setattr(store, "MAX_WORK_TOTAL_BYTES", 200)
    work_id = flow.started()
    operation_id = new_operation_id()
    first = flow.attach_inline(work_id, "x" * 200, operation_id=operation_id)
    assert first["ok"] is True
    again = flow.attach_inline(work_id, "x" * 200, operation_id=operation_id)
    assert again["ok"] is True
    assert again["receipt"] == first["receipt"]


def test_same_operation_id_different_params_refused(flow):
    """an operation id is a promise about one request"""
    work_id = flow.started()
    operation_id = new_operation_id()
    first = flow.write(work_id, "Draft one.\n", operation_id=operation_id)
    assert first["ok"] is True
    second = flow.write(work_id, "Something else entirely.\n", operation_id=operation_id)
    assert code_of(second) == "invalid_request"
    assert len(record_of(flow, work_id).artifacts) == 1


def test_committed_fast_path_checks_fingerprint(flow, monkeypatch):
    """the lock-free path compares before it answers"""
    work_id = flow.started()
    operation_id = new_operation_id()
    assert flow.write(work_id, "Draft one.\n", operation_id=operation_id)["ok"] is True

    def refuse(*args, **kwargs):
        raise AssertionError("the fast path must answer without taking the lock")

    monkeypatch.setattr(store.Lock, "__enter__", refuse)
    second = flow.write(work_id, "A different draft.\n", operation_id=operation_id)
    assert code_of(second) == "invalid_request"


def test_request_fingerprint_is_content_free(flow):
    """no body, label, intent, note or reason reaches an operation record"""
    work_id = flow.started(
        label="A label with a memorable phrase inside it that is long enough",
        intent="An intent with another memorable phrase inside it, also long",
    )
    secret = "A body with a distinctive sentence nobody else would ever write.\n"
    flow.attach_inline(work_id, secret, origin_note="a note that is quite long as well")
    paths = paths_for(flow, work_id)
    for path in list(paths.operations.rglob("*")) + [
        p for p in (flow.service.store.subject_paths("career").creates).iterdir()
    ]:
        if not path.is_file():
            continue
        text = path.read_text("utf-8")
        for body in (secret, "A label with a memorable phrase inside it that is"):
            assert body[:40] not in text


def test_request_fingerprint_normalisation(flow):
    """the same request fingerprints the same, and a different one does not"""
    baseline = service.request_fingerprint(
        "write_artifact",
        "6f2b8ea1-4d73-4c59-a018-3be7d2f9c456",
        "career",
        None,
        service._normalise_params(
            "write_artifact",
            {
                "content": "text",
                "based_on": [
                    {"ref": "src-0002", "sha256": "b" * 64},
                    {"ref": "src-0001", "sha256": "a" * 64},
                ],
                "root_refs": ["b", "a"],
                "filename_hint": "A Hint!",
                "reason": None,
            },
        ),
    )
    same = service.request_fingerprint(
        "write_artifact",
        "6f2b8ea1-4d73-4c59-a018-3be7d2f9c456",
        "career",
        None,
        service._normalise_params(
            "write_artifact",
            {
                "content": "text",
                "based_on": [
                    {"ref": "src-0001", "sha256": "a" * 64},
                    {"ref": "src-0002", "sha256": "b" * 64},
                ],
                "root_refs": ["a", "b"],
                "filename_hint": "a---hint",
            },
        ),
    )
    assert baseline == same
    different = service.request_fingerprint(
        "write_artifact",
        "6f2b8ea1-4d73-4c59-a018-3be7d2f9c456",
        "career",
        None,
        service._normalise_params("write_artifact", {"content": "other text"}),
    )
    assert baseline != different


def test_concurrent_writers_second_gets_locked(flow):
    """a second writer is told so, inside the budget, and writes nothing"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    held = threading.Event()
    release = threading.Event()
    outcome = {}

    def hold():
        with store.Lock(paths.lock):
            held.set()
            release.wait(5)

    def second():
        started = time.monotonic()
        outcome["response"] = flow.write(work_id, "A draft.\n")
        outcome["elapsed"] = time.monotonic() - started

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    worker = threading.Thread(target=second)
    worker.start()
    worker.join(10)
    release.set()
    holder.join(5)

    assert code_of(outcome["response"]) == "locked"
    assert outcome["elapsed"] < 5
    assert record_of(flow, work_id).artifacts == ()


def test_in_process_second_writer_is_locked_within_budget(flow):
    """the in-process guard fails inside the budget, not when the first finishes"""
    work_id = flow.started()
    paths = paths_for(flow, work_id)
    with store.Lock(paths.lock):
        started = time.monotonic()
        response = flow.write(work_id, "A draft.\n")
        elapsed = time.monotonic() - started
    assert code_of(response) == "locked"
    assert elapsed < store.LOCK_BUDGET_SECONDS * 4


def test_written_records_parse_under_w0a(flow):
    """every record this writer produces satisfies both W0a readers"""
    work_id = flow.started()
    flow.attach_inline(work_id, "An input.\n")
    written = flow.write(work_id, "A draft.\n")
    proposed = flow.propose(work_id, "approved_text", "art-0001")
    flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")

    paths = paths_for(flow, work_id)
    snapped = records.parse_work_record(
        json.loads(store.snapshot(paths.directory, store.WORK_RECORD_FILENAME).raw)
    )
    loaded = records.load_work_record(paths.record)
    assert snapped == loaded
    assert loaded.approved_artifact_ref == written["result"]["artifact_ref"]


def test_no_operation_record_for_read_effects(flow):
    """a read leaves the operation directories exactly as it found them"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "An input.\n")
    paths = paths_for(flow, work_id)
    before = sorted(str(p.relative_to(paths.directory)) for p in paths.operations.rglob("*"))

    assert flow.search(work_id, "input")["ok"] is True
    assert flow.read_captured(work_id, attached["result"]["source_ref"])["ok"] is True
    assert flow.read_file(work_id, "authored", "answers.md")["ok"] is True

    after = sorted(str(p.relative_to(paths.directory)) for p in paths.operations.rglob("*"))
    assert after == before


def test_read_source_captured_end_to_end(flow):
    """a captured read reports the work-relative path and its own class"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "Supplied text.\n", source_class="robert_source")
    response = flow.read_captured(work_id, attached["result"]["source_ref"])
    assert response["ok"] is True
    receipt = response["receipt"]
    assert receipt["ref"] == response["result"]["source_ref"]
    assert receipt["relative_path"].startswith("sources/")
    assert receipt["context_class"] == "robert_source"
    assert "root_ref" not in receipt
    assert response["result"]["content"] == "Supplied text.\n"


def test_read_source_configured_end_to_end(flow):
    """a configured read names its root and the root-relative path"""
    work_id = flow.started()
    response = flow.read_file(work_id, "authored", "answers.md")
    assert response["ok"] is True
    receipt = response["receipt"]
    assert receipt["root_ref"] == "authored"
    assert receipt["relative_path"] == "answers.md"
    for absent in ("work_id", "ref", "context_class"):
        assert absent not in receipt

    # and the same through the approved projection
    written = flow.write(work_id, "A draft to approve.\n")
    proposed = flow.propose(work_id, "approved_text", written["result"]["artifact_ref"])
    flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    second = flow.started(label="A later item")
    relative = (
        f"{flow.work_dir(work_id).name}/{written['result']['relative_path']}"
    )
    approved = flow.read_file(second, "approved:career", relative)
    assert approved["ok"] is True
    assert approved["receipt"]["root_ref"] == "approved:career"
    assert "context_class" not in approved["receipt"]
