"""Creating a work item exactly once, and finding it again by derivation.

The caller has an operation id and no work id. A retry after a lost response
has to reach the *same* created item, and it has to do so from data it
already holds — there is no list-all-work operation and no scan is permitted.
The reservation is what makes that a derived lookup: it is published before
the work, and it names the directory the work will live in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import Crash, tree_snapshot
from domains.cos.work import records, store
from domains.cos.work.envelope import new_operation_id


def code_of(response):
    assert response["ok"] is False, response
    return response["error"]["code"]


def subject_tree(flow):
    return tree_snapshot(flow.service.store.subject_paths(flow.subject).base)


def reservation_of(flow, operation_id):
    base = flow.service.store.subject_paths(flow.subject).creates
    return json.loads((base / f"{operation_id}.json").read_text("utf-8"))


def test_create_retry_returns_the_same_work(flow):
    """the same operation id reaches the same work item, once"""
    operation_id = new_operation_id()
    first = flow.create(operation_id=operation_id)
    second = flow.create(operation_id=operation_id)
    assert first["ok"] and second["ok"]
    assert first["result"]["work_id"] == second["result"]["work_id"]
    assert second["result"]["retry"] is True
    assert second["receipt"] == first["receipt"]
    base = flow.service.store.subject_paths(flow.subject).work_base
    assert len(list(base.iterdir())) == 1


def test_create_retry_does_not_scan(flow, monkeypatch):
    """the retry path opens one derived name and enumerates nothing"""
    operation_id = new_operation_id()
    first = flow.create(operation_id=operation_id)
    creates = flow.service.store.subject_paths(flow.subject).creates
    real_scandir = os.scandir

    def watched(path=".", *args, **kwargs):
        assert os.path.realpath(str(path)) != os.path.realpath(str(creates))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", watched)
    second = flow.create(operation_id=operation_id)
    assert second["result"]["work_id"] == first["result"]["work_id"]


def test_reservation_published_before_work(flow, crash_at, uninjected):
    """a crash after the reservation completes forward, never duplicates"""
    operation_id = new_operation_id()
    crash_at("create:mkdir:work")
    with pytest.raises(Crash):
        flow.create(operation_id=operation_id)
    uninjected()
    base = flow.service.store.subject_paths(flow.subject).work_base
    assert list(base.iterdir()) == []
    reservation = reservation_of(flow, operation_id)

    completed = flow.create(operation_id=operation_id)
    assert completed["ok"] is True
    assert completed["result"]["work_id"] == reservation["work_id"]
    assert len(list(base.iterdir())) == 1


def test_create_retry_different_label_refused(flow):
    """a retry with a different request is neither answered nor committed"""
    operation_id = new_operation_id()
    first = flow.create(label="The first label", operation_id=operation_id)
    before = subject_tree(flow)
    second = flow.create(label="A different label", operation_id=operation_id)
    assert code_of(second) == "invalid_request"
    assert subject_tree(flow) == before
    assert first["ok"] is True


def test_resume_by_work_id_writes_nothing(flow, snapshot_tree):
    """opening work whose binding is already correct changes no byte"""
    work_id = flow.started()
    before = subject_tree(flow)
    response = flow.open_existing(work_id)
    assert response["ok"] is True
    assert response["receipt"]["outcome"] == "ok"
    assert subject_tree(flow) == before


def test_conversation_binding_is_idempotent(flow):
    """a binding that already names this work is not rewritten"""
    work_id = flow.started()
    before = subject_tree(flow)
    assert flow.open_existing(work_id)["ok"] is True
    assert subject_tree(flow) == before

    changed = flow.open_existing(work_id, conversation_id="another-conversation")
    assert changed["ok"] is True
    assert changed["receipt"]["outcome"] == "committed"
    assert subject_tree(flow) != before


def test_binding_write_is_idempotent(flow):
    """repeating a binding write under the same id writes nothing further"""
    work_id = flow.started()
    operation_id = new_operation_id()
    first = flow.open_existing(
        work_id, conversation_id="another-conversation", operation_id=operation_id
    )
    assert first["receipt"]["outcome"] == "committed"
    after_first = subject_tree(flow)
    second = flow.open_existing(
        work_id, conversation_id="another-conversation", operation_id=operation_id
    )
    assert second["receipt"] == first["receipt"]
    assert subject_tree(flow) == after_first


def test_resume_that_changes_the_binding_is_a_write(flow):
    """a pointer change takes the ordinary write path, marker and all"""
    work_id = flow.started()
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    before = {p.name for p in paths.operations.iterdir() if p.is_file()}
    response = flow.open_existing(work_id, conversation_id="a-third-conversation")
    after = {p.name for p in paths.operations.iterdir() if p.is_file()}
    assert response["receipt"]["outcome"] == "committed"
    assert len(after - before) == 1
    assert list(paths.pending.iterdir()) == []


def test_create_binding_lands_before_the_terminal_marker(flow, monkeypatch):
    """the requested pointer is in place before the create is called done"""
    order = []
    real_replace = os.replace
    real_link = os.link

    def watched_replace(src, dst, **kwargs):
        order.append(("replace", Path(str(dst)).name))
        return real_replace(src, dst, **kwargs)

    def watched_link(src, dst, **kwargs):
        order.append(("link", str(dst)))
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", watched_replace)
    monkeypatch.setattr(os, "link", watched_link)
    response = flow.create(conversation_id="fresh-conversation")
    assert response["ok"] is True
    binding_at = [
        index for index, (kind, name) in enumerate(order)
        if kind == "replace" and name == "fresh-conversation.json"
    ][0]
    terminal_at = [
        index for index, (kind, name) in enumerate(order)
        if kind == "link" and name.endswith(".terminal.json")
    ][0]
    assert binding_at < terminal_at


def test_create_crash_before_binding_completes_forward(flow, crash_at, uninjected):
    """a crash between the record and the pointer installs the pointer first"""
    operation_id = new_operation_id()
    crash_at("P10b")
    with pytest.raises(Crash):
        flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    uninjected()
    subject_paths = flow.service.store.subject_paths(flow.subject)
    assert not (subject_paths.conversations / "fresh-conversation.json").exists()

    completed = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert completed["ok"] is True
    binding = json.loads(
        (subject_paths.conversations / "fresh-conversation.json").read_text("utf-8")
    )
    assert binding["work_id"] == completed["receipt"]["work_id"]


@pytest.mark.parametrize("target", ["reservation", "pending", "terminal"])
def test_canonical_json_is_never_partial(flow, crash_at, uninjected, target):
    """a crash between the temp and the link leaves the final name absent"""
    operation_id = new_operation_id()
    if target == "reservation":
        crash_at(f"link:{operation_id}.json", matcher=lambda name: name.endswith(".json")
                 and "terminal" not in name)
        with pytest.raises(Crash):
            flow.create(operation_id=operation_id)
        creates = flow.service.store.subject_paths(flow.subject).creates
        assert not (creates / f"{operation_id}.json").exists()
        temps = [p.name for p in creates.iterdir()]
        assert temps == [f"{operation_id}.json.{operation_id}.tmp"]
        uninjected()
        completed = flow.create(operation_id=operation_id)
        assert completed["ok"] is True
        assert json.loads((creates / f"{operation_id}.json").read_text("utf-8"))
        return

    work_id = flow.started()
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    if target == "pending":
        crash_at(f"link:{operation_id}.json")
        with pytest.raises(Crash):
            flow.write(work_id, "A draft.\n", operation_id=operation_id)
        assert not (paths.pending / f"{operation_id}.json").exists()
        assert [p.name for p in paths.staging.iterdir()] == [
            f"{operation_id}.json.{operation_id}.tmp"
        ]
    else:
        crash_at(f"link:{operation_id}.terminal.json")
        with pytest.raises(Crash):
            flow.write(work_id, "A draft.\n", operation_id=operation_id)
        assert not (paths.operations / f"{operation_id}.terminal.json").exists()
    uninjected()
    recovered = flow.open_existing(work_id)
    assert recovered["ok"] is True


def test_reservation_uses_the_link_primitive(flow, monkeypatch):
    """the reservation is staged, fsynced and linked — never opened in place"""
    order = []
    real_open = os.open
    real_link = os.link

    def watched_open(path, flags, *args, **kwargs):
        # only *writes* matter here: reading the final name to probe for an
        # existing reservation is not publishing into it
        if isinstance(path, str) and flags & os.O_CREAT:
            if path.endswith(".tmp"):
                order.append(("open-temp", path))
            elif path.endswith(".json"):
                order.append(("open-final", path))
        return real_open(path, flags, *args, **kwargs)

    def watched_link(src, dst, **kwargs):
        order.append(("link", str(dst)))
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(os, "open", watched_open)
    monkeypatch.setattr(os, "link", watched_link)
    operation_id = new_operation_id()
    assert flow.create(operation_id=operation_id)["ok"] is True
    reservation_steps = [
        entry for entry in order if operation_id in entry[1] and "terminal" not in entry[1]
    ]
    assert reservation_steps[0][0] == "open-temp"
    assert any(kind == "link" for kind, _ in reservation_steps)
    assert ("open-final", f"{operation_id}.json") not in reservation_steps


def test_partial_reservation_temp_is_removed_on_retry(flow, crash_at, uninjected, monkeypatch):
    """a crashed reservation leaves a temp, and the retry unlinks exactly it"""
    operation_id = new_operation_id()
    creates = flow.service.store.subject_paths(flow.subject).creates
    crash_at("", matcher=lambda name: name.startswith("link:") and "terminal" not in name)
    with pytest.raises(Crash):
        flow.create(operation_id=operation_id)
    uninjected()
    temp = creates / f"{operation_id}.json.{operation_id}.tmp"
    assert temp.exists()

    real_scandir = os.scandir

    def watched(path=".", *args, **kwargs):
        assert os.path.realpath(str(path)) != os.path.realpath(str(creates))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", watched)
    completed = flow.create(operation_id=operation_id)
    assert completed["ok"] is True
    assert not temp.exists()


def test_crash_row_C1a_after_work_dir(flow, crash_at, uninjected):
    """a crash right after the work directory resumes and commits once"""
    operation_id = new_operation_id()
    crash_at("create:mkdir:sources")
    with pytest.raises(Crash):
        flow.create(operation_id=operation_id)
    uninjected()
    base = flow.service.store.subject_paths(flow.subject).work_base
    assert len(list(base.iterdir())) == 1

    completed = flow.create(operation_id=operation_id)
    assert completed["ok"] is True
    assert len(list(base.iterdir())) == 1
    assert completed["result"]["work_id"] == reservation_of(flow, operation_id)["work_id"]


@pytest.mark.parametrize(
    "after",
    ["sources", "artifacts", "operations", "operations/pending", "operations/staging"],
)
def test_crash_after_each_create_subdir_resumes(flow, crash_at, uninjected, after):
    """a crash right after each required subdirectory resumes and commits"""
    order = list(store.CREATE_SUBDIRECTORIES)
    index = order.index(after)
    following = order[index + 1] if index + 1 < len(order) else None
    operation_id = new_operation_id()
    if following is None:
        crash_at("P5")
    else:
        crash_at(f"create:mkdir:{following}")
    with pytest.raises(Crash):
        flow.create(operation_id=operation_id)
    uninjected()

    completed = flow.create(operation_id=operation_id)
    assert completed["ok"] is True
    base = flow.service.store.subject_paths(flow.subject).work_base
    assert len(list(base.iterdir())) == 1
    directory = base / reservation_of(flow, operation_id)["work_dirname"]
    for name in store.CREATE_SUBDIRECTORIES:
        assert (directory / name).is_dir()


def test_create_dir_occupied_by_a_file_is_refused(flow, crash_at, uninjected):
    """a non-directory at the reserved name is refused, and left untouched"""
    operation_id = new_operation_id()
    crash_at("create:mkdir:work")
    with pytest.raises(Crash):
        flow.create(operation_id=operation_id)
    uninjected()
    reservation = reservation_of(flow, operation_id)
    base = flow.service.store.subject_paths(flow.subject).work_base
    occupier = base / reservation["work_dirname"]
    occupier.write_text("not a directory\n")
    os.chmod(occupier, 0o600)

    response = flow.create(operation_id=operation_id)
    assert code_of(response) == "stale_context"
    assert occupier.read_text("utf-8") == "not a directory\n"


def test_create_dir_with_a_foreign_record_is_refused(flow, crash_at, uninjected):
    """a reserved directory holding someone else's work is not completed"""
    operation_id = new_operation_id()
    crash_at("create:mkdir:sources")
    with pytest.raises(Crash):
        flow.create(operation_id=operation_id)
    uninjected()
    reservation = reservation_of(flow, operation_id)
    directory = (
        flow.service.store.subject_paths(flow.subject).work_base / reservation["work_dirname"]
    )
    (directory / "work.json").write_text('{"schema_version": 1}')
    os.chmod(directory / "work.json", 0o600)

    response = flow.create(operation_id=operation_id)
    assert code_of(response) == "stale_context"
    assert (directory / "work.json").read_text("utf-8") == '{"schema_version": 1}'


def test_binding_candidate_lost_before_record_abandons(flow, crash_at, uninjected):
    """record and pointer are decided together before anything canonical lands"""
    operation_id = new_operation_id()
    crash_at("P10")
    with pytest.raises(Crash):
        flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    uninjected()
    subject_paths = flow.service.store.subject_paths(flow.subject)
    reservation = reservation_of(flow, operation_id)
    directory = subject_paths.work_base / reservation["work_dirname"]
    assert not (directory / "work.json").exists()
    candidate = subject_paths.conversations / store.candidate_name(
        "fresh-conversation.json", operation_id
    )
    candidate.unlink()

    response = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert code_of(response) == "internal_error"
    assert not (directory / "work.json").exists()


def test_binding_candidate_lost_after_record_quarantines(flow, crash_at, uninjected):
    """once the record exists, the answer never claims nothing was written"""
    operation_id = new_operation_id()
    crash_at("P10b")
    with pytest.raises(Crash):
        flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    uninjected()
    subject_paths = flow.service.store.subject_paths(flow.subject)
    reservation = reservation_of(flow, operation_id)
    directory = subject_paths.work_base / reservation["work_dirname"]
    assert (directory / "work.json").exists()
    (subject_paths.conversations / store.candidate_name(
        "fresh-conversation.json", operation_id
    )).unlink()

    response = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert code_of(response) == "stale_context"
    assert (directory / "work.json").exists()
    record = records.load_work_record(directory / "work.json")
    assert record.work_id == reservation["work_id"]


def test_duplicate_create_waits_for_the_binding(flow):
    """a duplicate whose pointer is in place answers from the marker"""
    operation_id = new_operation_id()
    first = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    second = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert second["ok"] is True
    assert second["result"]["retry"] is True
    assert second["receipt"] == first["receipt"]


def test_committed_duplicate_never_repairs_the_binding(flow):
    """no canonical write happens after an operation is already terminal"""
    import domains.cos.work.service as service_module

    source = Path(service_module.__file__).read_text("utf-8")
    branch = source.split("def _answer_committed_create")[1].split("\n    def ")[0]
    for forbidden in ("stage_candidate", "install_candidate", "publish(", "unlink"):
        assert forbidden not in branch

    operation_id = new_operation_id()
    flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    subject_paths = flow.service.store.subject_paths(flow.subject)
    binding = subject_paths.conversations / "fresh-conversation.json"
    binding.unlink()
    before = subject_tree(flow)
    response = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert code_of(response) == "stale_context"
    assert subject_tree(flow) == before
    assert not binding.exists()


@pytest.mark.parametrize("mode", ["missing", "changed"])
def test_duplicate_create_tree_is_byte_identical(flow, mode):
    """the duplicate path leaves every name, mode, size and digest as found"""
    operation_id = new_operation_id()
    flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    subject_paths = flow.service.store.subject_paths(flow.subject)
    binding = subject_paths.conversations / "fresh-conversation.json"
    if mode == "missing":
        binding.unlink()
    else:
        other = flow.started(label="Another item")
        document = json.loads(binding.read_text("utf-8"))
        document["work_id"] = other
        binding.write_text(json.dumps(document))
        os.chmod(binding, 0o600)

    before = subject_tree(flow)
    response = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert code_of(response) == "stale_context"
    assert response["error"]["relative_path"] == "conversations/fresh-conversation.json"
    assert subject_tree(flow) == before


def test_duplicate_create_missing_binding_is_stale_context(flow):
    """the failure carries no receipt and names the pointer"""
    operation_id = new_operation_id()
    flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    subject_paths = flow.service.store.subject_paths(flow.subject)
    (subject_paths.conversations / "fresh-conversation.json").unlink()
    response = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    assert response["ok"] is False
    assert response["receipt"] is None
    assert response["error"]["code"] == "stale_context"


def test_duplicate_create_changed_binding_is_stale_context(flow):
    """and a fresh explicit open under a new id re-establishes it normally"""
    operation_id = new_operation_id()
    created = flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    work_id = created["result"]["work_id"]
    subject_paths = flow.service.store.subject_paths(flow.subject)
    binding = subject_paths.conversations / "fresh-conversation.json"
    other = flow.started(label="Another item")
    document = json.loads(binding.read_text("utf-8"))
    document["work_id"] = other
    binding.write_text(json.dumps(document))
    os.chmod(binding, 0o600)

    assert code_of(
        flow.create(conversation_id="fresh-conversation", operation_id=operation_id)
    ) == "stale_context"

    repaired = flow.open_existing(work_id, conversation_id="fresh-conversation")
    assert repaired["ok"] is True
    assert json.loads(binding.read_text("utf-8"))["work_id"] == work_id
