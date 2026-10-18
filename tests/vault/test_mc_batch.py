"""The production MC export batch: what EC2 writes and the Mac verifies (17 Oct 2026). Synthetic export files only."""
import errno
import io
import json
import os
import tarfile
from datetime import datetime, timezone

import pytest

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_shelf import mc
from core.memory_sync import mc_batch as MB

BID = "20261017T180000Z-0a1b2c3d"
NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)


def export_file(folder, cid, text="hello"):
    doc = {"schema": mc.SCHEMA, "instance": "production", "conversation_id": cid, "source_id": f"production:{cid}", "turns": [{"text": text}]}
    path = folder / f"production--{cid}.json"
    path.write_bytes(json.dumps(doc, sort_keys=True).encode())
    return path


def files_of(paths):
    return [(p.name, p, B.sha256_file(p), p.stat().st_size) for p in paths]


def manifest_for(files, plain):
    return {"batch_id": BID, "instance": "production", "files": sorted([n, s, z] for n, _p, s, z in files)}


def test_the_names_and_bound_agree_with_the_shelf_reader():
    assert MB.FILE.pattern == mc.FILE.pattern and MB.MAX_FILE_BYTES == mc.MAX_BYTES


def test_a_batch_round_trips_and_every_file_checks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = files_of([export_file(src, "c-0123456789ab"), export_file(src, "c-ba9876543210", "other")])
    MB.write_tar(files, BID, "production", tmp_path / "b.tar.gz")
    doc = MB.extract(tmp_path / "b.tar.gz", tmp_path / "x", manifest_for(files, None))
    assert doc["batch_id"] == BID
    assert all(MB.check_file(tmp_path / "x", n, s, z) is None for n, _p, s, z in files)


def test_only_declared_files_at_their_declared_size_are_extracted(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = files_of([export_file(src, "c-0123456789ab")])
    MB.write_tar(files, BID, "production", tmp_path / "b.tar.gz")
    with tarfile.open(tmp_path / "b.tar.gz", "r:gz") as a, tarfile.open(tmp_path / "f.tar.gz", "w:gz") as b:
        for m in a:
            data = a.extractfile(m).read()
            b.addfile(m, io.BytesIO(data))
        extra = tarfile.TarInfo("production--c-ffffffffffff.json")
        extra.size = 2
        b.addfile(extra, io.BytesIO(b"{}"))                               # not in BATCH.json
    with pytest.raises(BackupError) as exc:
        MB.extract(tmp_path / "f.tar.gz", tmp_path / "y", manifest_for(files, None))
    assert exc.value.code == "archive_unsafe"
    lying = [(files[0][0], files[0][1], files[0][2], files[0][3] + 1)]
    with pytest.raises(BackupError):
        MB.write_tar(lying, BID, "production", tmp_path / "c.tar.gz")       # the bytes no longer match what was listed


def test_a_file_that_is_not_the_export_for_its_own_name_is_refused(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = export_file(src, "c-0123456789ab")
    wrong = src / "production--c-ba9876543210.json"
    path.rename(wrong)                                                     # its body names another conversation
    assert MB.check_file(src, wrong.name, B.sha256_file(wrong), wrong.stat().st_size) == "not_an_export"
    assert MB.name_ok("production--c-0123456789ab.json", "production") and not MB.name_ok("dev--c-0123456789ab.json", "production")
    assert not MB.name_ok("../production--c-0123456789ab.json")


def test_acknowledgements_parse_and_refuse_anything_else():
    doc = MB.ack_doc(BID, [["production--c-0123456789ab.json", "a" * 64, MB.PLACED]], NOW)
    assert MB.parse_ack(doc, BID)["files"][0][2] == "placed"
    for bad in ({**doc, "batch_id": "x"}, {**doc, "files": [["../x", "a" * 64, "placed"]]}, {**doc, "files": [["production--c-0123456789ab.json", "a" * 64, "kept"]]}):
        with pytest.raises(BackupError):
            MB.parse_ack(bad, BID)


def test_a_truncated_or_corrupt_stream_is_a_fixed_integrity_code_for_both_batch_kinds(tmp_path):
    """Review of the Mac pull: a broken gzip/tar must map to archive_unsafe (rejected, retried by the sender), never a raw exception."""
    from core.memory_sync import batch as BT
    src = tmp_path / "src"
    src.mkdir()
    files = files_of([export_file(src, "c-0123456789ab", "x" * 5000)])
    MB.write_tar(files, BID, "production", tmp_path / "b.tar.gz")
    data = (tmp_path / "b.tar.gz").read_bytes()
    for i, broken in enumerate((data[: len(data) // 2], data[:10] + b"\xff" * 64 + data[74:], b"not gzip at all")):
        path = tmp_path / f"broken{i}.tar.gz"
        path.write_bytes(broken)
        for call in (lambda: MB.extract(path, tmp_path / f"m{i}", manifest_for(files, None)),
                     lambda: BT.extract(path, tmp_path / f"s{i}")):
            with pytest.raises(BackupError) as exc:
                call()
            assert exc.value.code == "archive_unsafe"


def _one_file_batch(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = files_of([export_file(src, "c-0123456789ab", "x" * 5000)])
    MB.write_tar(files, BID, "production", tmp_path / "b.tar.gz")
    return files


class _FullDisk:
    """An output file whose writes fail as a full disk does."""

    def __init__(self, fd):
        os.close(fd)

    def write(self, _block):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def close(self):
        pass


def test_a_local_write_fault_while_extracting_is_transient_never_archive_unsafe(tmp_path, monkeypatch):
    """Review LOW-1 (17 Oct): ENOSPC / EACCES creating or writing the output is the Mac's fault (``extract_write_failed``, retried), while a
    member that already exists or a file where the folder must be stays the archive's (``archive_unsafe``)."""
    files = _one_file_batch(tmp_path)
    m = manifest_for(files, None)
    monkeypatch.setattr(MB.os, "fdopen", lambda fd, mode: _FullDisk(fd))
    with pytest.raises(BackupError) as exc:
        MB.extract(tmp_path / "b.tar.gz", tmp_path / "full", m)
    assert exc.value.code == "extract_write_failed"
    monkeypatch.undo()
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)                                    # no write permission in the output folder
    try:
        with pytest.raises(BackupError) as exc:
            MB.extract(tmp_path / "b.tar.gz", locked, m)
        assert exc.value.code == "extract_write_failed"
    finally:
        locked.chmod(0o700)
    dup = tmp_path / "dup"
    dup.mkdir()
    (dup / files[0][0]).write_bytes(b"already here")             # the member's name already exists: a duplicate
    with pytest.raises(BackupError) as exc:
        MB.extract(tmp_path / "b.tar.gz", dup, m)
    assert exc.value.code == "archive_unsafe"
    (tmp_path / "afile").write_bytes(b"")                        # a file where the output folder must be
    with pytest.raises(BackupError) as exc:
        MB.extract(tmp_path / "b.tar.gz", tmp_path / "afile", m)
    assert exc.value.code == "archive_unsafe"
    assert MB.extract(tmp_path / "b.tar.gz", tmp_path / "ok", m)["batch_id"] == BID
