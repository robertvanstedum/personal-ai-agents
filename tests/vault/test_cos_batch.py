"""The production CoS export batch (#509): the family on MC's batch code, the export file, and the private check at the Mac's door.
Synthetic history only."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_shelf import cos
from core.memory_sync import cos_batch as CB
from core.memory_sync import mc_batch as MB
from tests.vault.cos_history import History, name

BID = "20261111T150000Z-0a1b2c3d"
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def export_file(folder, h: History, cid: str):
    raw = h.turns_path(cid).read_bytes() if h.turns_path(cid).exists() else b""
    path = folder / name(h.environment, cid)
    path.write_bytes(CB.export_doc(h.environment, cid, h.meta_path(cid).read_bytes(), raw))
    return path


def files_of(paths):
    return [(p.name, p, B.sha256_file(p), p.stat().st_size) for p in paths]


def manifest_for(files, instance="prod"):
    return {"batch_id": BID, "instance": instance, "files": sorted([n, s, z] for n, _p, s, z in files)}


@pytest.fixture
def h(tmp_path):
    return History(tmp_path / "history")


def test_the_names_schema_and_bounds_agree_with_the_shelf_reader():
    assert CB.FILE.pattern == cos.FILE.pattern and CB.EXPORT_SCHEMA == cos.EXPORT_SCHEMA
    assert CB.MAX_FILE_BYTES == cos.MAX_BYTES and CB.META_MAX == cos.META_MAX
    assert CB.COS.format != MB.MC.format and CB.COS.export_prefix != MB.MC.export_prefix and CB.COS.ack_prefix != MB.MC.ack_prefix


def test_mc_family_is_unchanged():
    assert (MB.MC.format, MB.MC.ack_format, MB.MC.export_prefix, MB.MC.ack_prefix) == (
        "vault-mc-export/1", "vault-mc-export-ack/1", "sync/mc-export", "sync/mc-export-acks")
    assert MB.MC.file is MB.FILE and MB.name_ok("production--c-0123456789ab.json", "production")
    assert not MB.name_ok("prod--cosc-0123456789abcdef01234567.json")
    assert MB.name_ok("prod--cosc-0123456789abcdef01234567.json", "prod", CB.COS)


def test_the_export_file_is_deterministic_and_parses_back(tmp_path, h):
    cid = h.conversation(("KILN-SECRET when?", "KILN-SECRET Tuesday"))
    a = export_file(tmp_path, h, cid).read_bytes()
    assert export_file(tmp_path, h, cid).read_bytes() == a and a.endswith(b"\n") and a.count(b"\n") == 1
    parsed = cos.parse([a])
    assert parsed.source_id == f"prod:{cid}" and parsed.provider == "minimoi-cos"
    assert [t.text for t in parsed.turns] == ["KILN-SECRET when?", "KILN-SECRET Tuesday"]


def test_a_batch_round_trips_and_every_file_checks(tmp_path, h):
    src = tmp_path / "src"
    src.mkdir()
    files = files_of([export_file(src, h, h.conversation(("one", "two"))), export_file(src, h, h.conversation(("three", None)))])
    MB.write_tar(files, BID, "prod", tmp_path / "b.tar.gz", CB.COS)
    doc = MB.extract(tmp_path / "b.tar.gz", tmp_path / "x", manifest_for(files), CB.COS)
    assert doc["format"] == CB.FORMAT and doc["batch_id"] == BID
    assert all(MB.check_file(tmp_path / "x", n, s, z, CB.COS) is None for n, _p, s, z in files)


def test_an_mc_batch_is_not_a_cos_batch(tmp_path, h):
    src = tmp_path / "src"
    src.mkdir()
    files = files_of([export_file(src, h, h.conversation(("one", "two")))])
    MB.write_tar(files, BID, "prod", tmp_path / "b.tar.gz", CB.COS)
    with pytest.raises(BackupError) as exc:
        MB.extract(tmp_path / "b.tar.gz", tmp_path / "x", manifest_for(files))          # read as MC's: the format differs
    assert exc.value.code == "batch_doc_mismatch"
    with pytest.raises(BackupError) as exc:
        MB.write_tar(files, BID, "production", tmp_path / "c.tar.gz")                  # MC's names never carry a CoS file
    assert exc.value.code == "export_file_malformed"


@pytest.mark.parametrize("change", [
    {"private": True}, {"private": None}, {"private": "false"}, {"private": 0}, "omit_private", {"environment": "dev"},
    {"data_scope": "work"}, {"conversation_id": "cosc-ffffffffffffffffffffffff"}, {"schema": "something/1"}])
def test_a_placed_file_whose_packed_metadata_may_not_travel_is_not_an_export(tmp_path, h, change):
    cid = h.conversation(("PRIVATE-SECRET words", "PRIVATE-SECRET reply"))
    meta = json.loads(h.meta_path(cid).read_text())
    if change == "omit_private":
        del meta["private"]
    else:
        meta.update(change)
    raw = h.turns_path(cid).read_bytes()
    path = tmp_path / name("prod", cid)
    path.write_bytes(CB.export_doc("prod", cid, json.dumps(meta).encode(), raw))
    assert MB.check_file(tmp_path, path.name, B.sha256_file(path), path.stat().st_size, CB.COS) == "not_an_export"
    with pytest.raises(cos.BadExport):
        cos.parse([path.read_bytes()])                                                  # and the watcher's parser refuses it too


def test_a_file_named_for_another_environment_is_not_an_export(tmp_path, h):
    cid = h.conversation(("one", "two"))
    good = export_file(tmp_path, h, cid)
    moved = tmp_path / name("dev", cid)
    moved.write_bytes(good.read_bytes())
    assert MB.check_file(tmp_path, moved.name, B.sha256_file(moved), moved.stat().st_size, CB.COS) == "not_an_export"


def test_acks_parse_per_family(tmp_path):
    files = [[name("prod", "cosc-0123456789abcdef01234567"), "a" * 64, MB.PLACED]]
    doc = MB.ack_doc(BID, files, NOW, CB.COS)
    assert doc["format"] == CB.ACK_FORMAT and MB.parse_ack(doc, BID, CB.COS) == doc
    with pytest.raises(BackupError):
        MB.parse_ack(doc, BID)                                                          # not MC's ack format
    mc_doc = MB.ack_doc(BID, [["production--c-0123456789ab.json", "a" * 64, MB.PLACED]], NOW)
    with pytest.raises(BackupError):
        MB.parse_ack(mc_doc, BID, CB.COS)


def test_read_ack_uses_the_cos_prefix(tmp_path):
    dest = tmp_path / "dest"
    d = B.Dest(f"file://{dest}")
    doc = MB.ack_doc(BID, [[name("prod", "cosc-0123456789abcdef01234567"), "b" * 64, "rejected:file_hash_mismatch"]], NOW, CB.COS)
    local = tmp_path / "ack.json"
    local.write_text(json.dumps(doc))
    d.put(local, f"{CB.ACK_PREFIX}/{BID}.json")
    work = tmp_path / "work"
    work.mkdir()
    assert MB.read_ack(d, BID, work) is None                                            # MC's prefix holds nothing
    assert MB.read_ack(d, BID, work, CB.COS)["files"][0][2] == "rejected:file_hash_mismatch"
    assert list(work.iterdir()) == []
