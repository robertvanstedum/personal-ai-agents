"""Retained documents (v0.6 section 10; acceptance rows A02 and the document side of A01)."""
from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path

import pytest

from workshop_journal.conftest import WORKSHOP, envelope, new_id, progress, run_child
from core.workshop_journal import artifacts as art
from core.workshop_journal import fsutil
from core.workshop_journal.errors import ArtifactCorrupt, ArtifactMissing, SourceRefused
from core.workshop_journal.journal import Journal

DOC = "# Design\n\nDecision C replaces A. Rejected: B.\n".encode()


def base(root) -> Path:
    return Path(root) / WORKSHOP


def objects(root) -> Path:
    return base(root) / "artifacts" / "sha256"


def with_doc(data: bytes, **over) -> tuple[art.Prepared, dict]:
    item = art.prepare(data)
    return item, envelope(refs=[item.ref("spec:design", "design.md")], **over)


# ── what would be kept ─────────────────────────────────────────────────────────────────────────────────────────────
def test_prepare_hashes_what_is_read_and_what_is_kept():
    item = art.prepare(DOC)
    assert item.original_sha256 == item.retained_sha256 == hashlib.sha256(DOC).hexdigest()
    assert (item.redactions, item.data, item.original_size) == (0, DOC, len(DOC))


def test_prepare_scrubs_credentials_and_counts_them_without_keeping_the_original():
    raw = "Use token sk-abcdefghijklmnopqrstuvwx and password: hunter2 here.\n".encode()
    item = art.prepare(raw)
    assert item.redactions >= 2 and b"sk-abcdefghijklmnop" not in item.data and b"hunter2" not in item.data
    assert item.original_sha256 == hashlib.sha256(raw).hexdigest() != item.retained_sha256


@pytest.mark.parametrize("data,cls,reason", [
    (b"\xff\xfe binary", "handoff", "not_text"), (b"nul\x00byte", "handoff", "not_text"), (b"", "handoff", "empty"),
    (b"x" * (art.MAX_TEXT_BYTES + 1), "handoff", "too_large"), (b"fine", "private", "excluded_source"),
    (b"fine", "excluded", "excluded_source"), (b"fine", "mystery", "unknown_source_class"),
])
def test_prepare_refuses_with_a_fixed_code(data, cls, reason):
    with pytest.raises(SourceRefused) as caught:
        art.prepare(data, source_class=cls)
    assert caught.value.reason == reason and "fine" not in str(caught.value)


def test_a_document_at_the_size_limit_is_kept_not_truncated():
    item = art.prepare(b"a" * art.MAX_TEXT_BYTES)
    assert len(item.data) == art.MAX_TEXT_BYTES


# ── publish before the event ───────────────────────────────────────────────────────────────────────────────────────
def test_A02_the_document_is_durable_before_the_event_that_cites_it(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC)
    order = []
    real_pub, real_commit = art.publish, Journal._commit
    art.publish = lambda *a, **k: (order.append("artifact"), real_pub(*a, **k))[1]
    Journal._commit = lambda self, *a, **k: (order.append("event"), real_commit(self, *a, **k))[1]
    try:
        assert j.append(env, artifacts=[item]).committed
    finally:
        art.publish, Journal._commit = real_pub, real_commit
    assert order == ["artifact", "event"]
    assert j.open_artifact(item.retained_sha256) == DOC
    assert (objects(root) / item.retained_sha256).read_bytes() == DOC
    assert oct((objects(root) / item.retained_sha256).stat().st_mode & 0o777) == "0o600"
    assert j.artifact_report()["orphans"] == [] and j.artifact_report()["status"] == "ok"


def test_A02_a_document_changed_after_review_leaves_the_reviewed_bytes_openable(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    first, e1 = with_doc(DOC)
    assert j.append(e1, artifacts=[first]).committed
    revised = DOC + b"\nA later edit nobody reviewed.\n"
    second, e2 = with_doc(revised, text="The revised spec.")
    assert j.append(e2, artifacts=[second]).committed
    assert first.retained_sha256 != second.retained_sha256
    assert j.open_artifact(first.retained_sha256) == DOC                                     # the pinned revision
    assert j.open_artifact(second.retained_sha256) == revised
    assert j.artifact_report()["objects"] == 2


def test_A02_the_same_document_posted_twice_is_one_object_and_one_event(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC, event_id=new_id())
    assert j.append(env, artifacts=[item]).status == "committed"
    assert j.append(env, artifacts=[item]).status == "duplicate"
    other, env2 = with_doc(DOC, text="a second event citing the same bytes")
    assert j.append(env2, artifacts=[other]).committed
    assert len(list(objects(root).iterdir())) == 1


def test_A02_a_crash_between_the_document_and_the_event_leaves_a_reported_orphan_and_the_retry_completes(root, monkeypatch):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC, event_id=new_id())
    real = Journal._commit
    monkeypatch.setattr(Journal, "_commit", lambda self, *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "crash after the document")))
    with pytest.raises(OSError):
        j.append(env, artifacts=[item])
    monkeypatch.setattr(Journal, "_commit", real)
    report = j.artifact_report()
    assert report["orphans"] == [item.retained_sha256] and report["status"] == "ok"            # reported, never deleted
    done = j.append(env, artifacts=[item])                                                     # same ID, same document
    assert done.status == "committed"
    assert j.artifact_report()["orphans"] == [] and len(list(objects(root).iterdir())) == 1


def test_A02_a_directory_sync_failure_publishes_no_event(root, monkeypatch):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    assert j.append(progress("seed")).committed
    before = (base(root) / "events.jsonl").read_bytes()
    item, env = with_doc(DOC, event_id=new_id())

    def fail(dir_fd, *, durable=True):
        if durable:
            raise OSError(errno.EIO, "folder sync failed")
    monkeypatch.setattr(fsutil, "fsync_dir", fail)
    failed = j.append(env, artifacts=[item])
    assert (failed.status, failed.committed, failed.retryable) == ("write_failed", False, True)
    assert failed.reason == "artifact_not_published"
    assert (base(root) / "events.jsonl").read_bytes() == before
    monkeypatch.undo()
    assert j.append(env, artifacts=[item]).committed                                           # the stable ID survives the failure


def test_A02_a_writer_killed_while_writing_the_document_leaves_no_partial_object(root):
    crash = """
import os, sys
from core.workshop_journal import fsutil, artifacts as art
from core.workshop_journal.journal import Journal
real = fsutil._write
def half(fd, view):
    real(fd, view[: max(1, len(view) // 2)])
    os._exit(9)
fsutil._write = half
item = art.prepare(b'# a document\\n' * 100)
env = {'actor': 'codex', 'kind': 'progress', 'item': 'queue:146', 'text': 'x', 'payload': {'action': 'x'}, 'refs': [item.ref('spec:x')]}
Journal(sys.argv[1], 'workshop-neubau').append(env, artifacts=[item])
"""
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    assert j.append(progress("seed")).committed
    child = run_child(crash, root)
    assert child.returncode == 9
    names = [n.name for n in objects(root).iterdir()]
    assert names and all(n.startswith(".tmp-") for n in names)                                 # only a temp file, never a named object
    item, env = with_doc(DOC)
    assert j.append(env, artifacts=[item]).committed
    report = j.artifact_report()
    assert report["stale_temp"] == 1 and report["objects"] == 1 and report["orphans"] == []


def test_A02_a_damaged_object_is_reported_and_refused_never_served(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC)
    assert j.append(env, artifacts=[item]).committed
    target = objects(root) / item.retained_sha256
    target.write_bytes(DOC + b"tampered")
    with pytest.raises(ArtifactCorrupt):
        j.open_artifact(item.retained_sha256)
    report = j.artifact_report()
    assert report["corrupt"] == [item.retained_sha256] and report["status"] == "attention"


def test_A02_a_cited_document_that_is_gone_is_reported_missing(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC)
    assert j.append(env, artifacts=[item]).committed
    (objects(root) / item.retained_sha256).unlink()
    with pytest.raises(ArtifactMissing):
        j.open_artifact(item.retained_sha256)
    assert j.artifact_report()["missing_referenced"] == [item.retained_sha256]


def test_A02_an_event_must_cite_the_document_it_publishes(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item = art.prepare(DOC)
    refused = j.append(progress("no reference"), artifacts=[item])
    assert (refused.status, refused.committed) == ("invalid_input", False) and refused.reason == "artifact_not_referenced"
    assert not objects(root).exists()


def test_A02_a_forbidden_original_is_never_written_anywhere_in_the_workshop(root):
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123"
    raw = f"Notes. Use {secret} for the test.\n".encode()
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(raw)
    assert j.append(env, artifacts=[item]).committed
    for path in base(root).rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path
    prov = (base(root) / "artifacts" / "provenance" / f"{item.original_sha256}.json").read_text()
    assert str(item.redactions) in prov and item.original_sha256 in prov and item.retained_sha256 in prov


def test_open_exact_refuses_a_bad_hash_and_creates_nothing(root):
    j = Journal(root, WORKSHOP)
    with pytest.raises(SourceRefused):
        j.open_artifact("../../etc/passwd")
    with pytest.raises(Exception):
        j.open_artifact("0" * 64)
    assert list(Path(root).iterdir()) == []


# ── reading a source file safely (A01, documents) ─────────────────────────────────────────────────────────────────
@pytest.fixture
def src(tmp_path):
    folder = tmp_path / "src"
    folder.mkdir(mode=0o700)
    (folder / "sub").mkdir(mode=0o700)
    (folder / "a.md").write_bytes(DOC)
    (folder / "sub" / "b.md").write_bytes(b"nested\n")
    return folder


def test_capture_returns_stable_bytes(src):
    assert art.capture_file(str(src), "a.md") == DOC and art.capture_file(str(src), "sub/b.md") == b"nested\n"


@pytest.mark.parametrize("rel", ["../a.md", "/etc/passwd", "sub/../a.md", "", "./a.md", "sub//b.md", "missing.md"])
def test_capture_refuses_paths_that_leave_the_folder_or_do_not_exist(src, rel):
    with pytest.raises(SourceRefused):
        art.capture_file(str(src), rel)


def test_capture_refuses_links_of_every_kind(src, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    (src / "link.md").symlink_to(outside)
    (src / "dirlink").symlink_to(tmp_path)
    os.link(src / "a.md", tmp_path / "second-name.md")
    for rel, reason in (("link.md", "file_is_a_link_or_not_regular"), ("dirlink/outside.md", "folder_is_a_link"), ("a.md", "hard_linked")):
        with pytest.raises(SourceRefused) as caught:
            art.capture_file(str(src), rel)
        assert caught.value.reason == reason, rel
    linked_root = tmp_path / "rootlink"
    linked_root.symlink_to(src)
    with pytest.raises(SourceRefused):
        art.capture_file(str(linked_root), "sub/b.md")


def test_capture_refuses_a_folder_a_pipe_or_a_file_that_is_too_large(src):
    os.mkfifo(src / "pipe")
    for rel in ("sub", "pipe"):
        with pytest.raises(SourceRefused):
            art.capture_file(str(src), rel)
    with pytest.raises(SourceRefused) as caught:
        art.capture_file(str(src), "a.md", limit=5)
    assert caught.value.reason == "too_large"


def test_capture_reports_a_file_that_changes_while_it_is_read(src, monkeypatch):
    real, calls = fsutil.read_all, []

    def changing(fd):
        data = real(fd)
        calls.append(1)
        if len(calls) == 1:
            with open(src / "a.md", "ab") as handle:
                handle.write(b"edited during the read\n")
        return data
    monkeypatch.setattr(fsutil, "read_all", changing)
    with pytest.raises(SourceRefused) as caught:
        art.capture_file(str(src), "a.md")
    assert caught.value.reason == "source_changed"


def test_capture_reports_a_file_replaced_during_the_read(src, monkeypatch):
    real, calls = fsutil.read_all, []

    def swap(fd):
        data = real(fd)
        calls.append(1)
        if len(calls) == 1:
            os.rename(src / "a.md", src / "old.md")
            (src / "a.md").write_bytes(DOC)
        return data
    monkeypatch.setattr(fsutil, "read_all", swap)
    with pytest.raises(SourceRefused) as caught:
        art.capture_file(str(src), "a.md")
    assert caught.value.reason == "source_changed"


# ── Codex R4: "retained" must mean kept ─────────────────────────────────────────────────────────────────────────────
def retained_ref(sha: str) -> dict:
    return {"type": "artifact", "id": "claimed-document", "sha256": sha, "availability": "retained"}


def test_R4_an_event_cannot_claim_a_retained_document_that_was_never_kept(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    refused = j.append(progress("cites nothing real", refs=[retained_ref("a" * 64)]))
    assert (refused.status, refused.reason, refused.committed) == ("policy_refused", "retained_artifact_not_kept", False)
    assert len(j.read().events) == 0
    pre = j.preflight(progress("cites nothing real", refs=[retained_ref("a" * 64)]))
    assert (pre.status, pre.reason) == ("policy_refused", "retained_artifact_not_kept")


def test_R4_a_kept_intact_document_may_be_cited_by_a_later_event_and_pointers_stay_usable(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC)
    assert j.append(env, artifacts=[item]).committed
    assert j.append(progress("cites the kept one", refs=[retained_ref(item.retained_sha256)])).committed
    pointer = {"type": "artifact", "id": "elsewhere", "availability": "pointer_only"}
    missing = {"type": "artifact", "id": "gone", "sha256": "b" * 64, "availability": "missing"}
    assert j.append(progress("absent evidence is still allowed", refs=[pointer, missing])).committed


def test_R4_a_damaged_kept_document_cannot_be_cited_as_retained(root):
    j = Journal(root, WORKSHOP, lock_timeout=0.3)
    item, env = with_doc(DOC)
    assert j.append(env, artifacts=[item]).committed
    (objects(root) / item.retained_sha256).write_bytes(DOC + b"damaged")
    bad = j.append(progress("cites damaged bytes", refs=[retained_ref(item.retained_sha256)]))
    assert (bad.status, bad.committed) == ("artifact_corrupt", False)
