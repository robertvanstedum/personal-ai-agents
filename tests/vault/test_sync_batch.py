"""The sync batch format: the tar the Mac writes and the strict checks production runs on it (17 Oct 2026). Synthetic records only."""
import io
import tarfile

import pytest

from core.memory_backup.backup import BackupError
from core.memory_sync import batch as BT
from core.memory_sync import versions as V
from tests.memory_index.helpers import add_record, make_shelf

BID = "20261017T150000Z-0a1b2c3d"


@pytest.fixture
def shelf(tmp_path):
    s = make_shelf(tmp_path)
    add_record(s, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha answer")])
    add_record(s, "codex", "c-b", [("human", "beta question"), ("assistant", "beta answer")])
    return s


def test_a_batch_round_trips_and_every_version_checks(shelf, tmp_path):
    got, _ = V.record_versions(shelf.root)
    out = tmp_path / "b.tar.gz"
    res = BT.write_tar(shelf.root, list(got.values()), V.source_index(shelf.root), BID, out)
    assert len(res["versions"]) == 2 and res["dropped"] == []
    doc = BT.extract(out, tmp_path / "x")
    assert doc["batch_id"] == BID and all(e["source_index_entry"]["id"] == e["record_id"] for e in doc["versions"])
    assert all(BT.check_version(tmp_path / "x", e) is None for e in doc["versions"])


def test_a_record_that_changes_while_it_is_read_is_dropped_from_the_batch(shelf, tmp_path):
    got, _ = V.record_versions(shelf.root)
    a = next(v for v in got.values() if v.source_key == "grok:g-a")
    add_record(shelf, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha answer, revised")])    # after the inventory
    res = BT.write_tar(shelf.root, list(got.values()), V.source_index(shelf.root), BID, tmp_path / "b.tar.gz")
    assert res["dropped"] == [a.record_id] and [v.record_id for v in res["versions"]] != [a.record_id]
    doc = BT.extract(tmp_path / "b.tar.gz", tmp_path / "x")
    assert [e["record_id"] for e in doc["versions"]] == [v.record_id for v in res["versions"]]


def test_tampered_or_incomplete_versions_are_rejected_with_fixed_codes(shelf, tmp_path):
    got, _ = V.record_versions(shelf.root)
    BT.write_tar(shelf.root, list(got.values()), {}, BID, tmp_path / "b.tar.gz")
    doc = BT.extract(tmp_path / "b.tar.gz", tmp_path / "x")
    e = doc["versions"][0]
    main = tmp_path / "x" / "records" / e["kind"] / e["name"] / f"{e['name']}.md"
    main.write_bytes(main.read_bytes() + b"x")
    assert BT.check_version(tmp_path / "x", e) == "file_size_mismatch"
    extra = main.parent / "editions" / "9--000000000000.jsonl"
    extra.write_text("{}")
    assert BT.check_version(tmp_path / "x", e) == "unexpected_file"
    assert BT.check_version(tmp_path / "x", {**doc["versions"][1], "digest": "0" * 64}) == "digest_mismatch"
    assert BT.check_version(tmp_path / "x", {**doc["versions"][1], "name": "../escape"}) == "unsafe_name"


def test_extraction_refuses_links_climbing_and_unexpected_members(tmp_path):
    def tar_with(*members):
        path = tmp_path / f"t{len(list(tmp_path.iterdir()))}.tar.gz"
        with tarfile.open(path, "w:gz") as t:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    t.addfile(info)
                else:
                    data = b'{"format": "vault-sync-batch/1", "batch_id": "x", "versions": []}' if name == V.BATCH_DOC else b"x"
                    info.size = len(data)
                    t.addfile(info, io.BytesIO(data))
        return path
    for bad in ([("records/sessions-raw/a/a.md", "file")],                                   # BATCH.json must come first
                [(V.BATCH_DOC, "file"), ("records/sessions-raw/../../etc/x", "file")],
                [(V.BATCH_DOC, "file"), ("records/sessions-raw/a/link", "link")],
                [(V.BATCH_DOC, "file"), ("records/elsewhere/a/a.md", "file")],
                [(V.BATCH_DOC, "file"), ("records/sessions-raw/a/a.md", "file"), ("records/sessions-raw/a/a.md", "file")]):
        with pytest.raises(BackupError) as exc:
            BT.extract(tar_with(*bad), tmp_path / f"out{len(list(tmp_path.iterdir()))}")
        assert exc.value.code == "archive_unsafe"


def test_extraction_writes_only_declared_files_at_their_declared_size_and_honours_a_bound(shelf, tmp_path):
    """A forged batch cannot smuggle extra members or inflate one (a decompression bomb): every member must be declared, at its size."""
    got, _ = V.record_versions(shelf.root)
    real = tmp_path / "b.tar.gz"
    BT.write_tar(shelf.root, list(got.values()), {}, BID, real)
    with pytest.raises(BackupError) as exc:
        BT.extract(real, tmp_path / "small", max_bytes=10)
    assert exc.value.code == "batch_too_large"
    forged = tmp_path / "forged.tar.gz"
    with tarfile.open(real, "r:gz") as src, tarfile.open(forged, "w:gz") as dst:
        for m in src:
            data = src.extractfile(m).read()
            if m.name != V.BATCH_DOC and m.name.endswith(".md"):
                data, m.size = data + b"\0" * 4096, len(data) + 4096        # inflated beyond what BATCH.json declares
            dst.addfile(m, io.BytesIO(data))
    with pytest.raises(BackupError) as exc:
        BT.extract(forged, tmp_path / "forged-out")
    assert exc.value.code == "archive_unsafe"
