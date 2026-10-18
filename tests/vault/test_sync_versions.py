"""Record versions, the incremental sync's unit, and the small shared pieces both hosts use (17 Oct 2026). Synthetic records only."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.jobs import registry, status
from core.memory_backup import backup as B
from core.memory_sync import versions as V
from tests.memory_index.helpers import add_record, designate, make_shelf


def shelf_with_two(tmp_path):
    shelf = make_shelf(tmp_path)
    a = add_record(shelf, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha answer")])
    b = add_record(shelf, "codex", "c-b", [("human", "beta question"), ("assistant", "beta answer")])
    return shelf, a, b


def test_versions_cover_every_record_file_and_change_with_a_new_edition_or_a_metadata_only_change(tmp_path):
    shelf, a, b = shelf_with_two(tmp_path)
    before, problems = V.record_versions(shelf.root)
    assert set(before) == {a, b} and not problems
    va = before[a]
    assert va.edition == 1 and va.dir.startswith("sessions-raw/") and {rel for rel, _, _ in va.files} >= {f"{va.name}.md"}
    assert any(rel.startswith("editions/") for rel, _, _ in va.files)
    add_record(shelf, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha answer, revised")])
    designate(shelf, b)
    after, _ = V.record_versions(shelf.root)
    assert after[a].edition == 2 and after[a].digest != va.digest
    assert after[b].edition == 1 and after[b].digest != before[b].digest        # same edition, new main file: a new version
    assert V.version_set_digest(V.entries_of(after)) != V.version_set_digest(V.entries_of(before))


def test_the_hash_cache_skips_hashing_unchanged_files_and_rehashes_a_changed_one(tmp_path):
    shelf, a, _ = shelf_with_two(tmp_path)
    cache = V.HashCache(tmp_path / "cache.json")
    first, _ = V.record_versions(shelf.root, cache)
    cache.save()
    assert oct(os.stat(tmp_path / "cache.json").st_mode & 0o777) == "0o600"
    again = V.HashCache(tmp_path / "cache.json")
    second, _ = V.record_versions(shelf.root, again)
    assert again.misses == 0 and again.hits > 0 and {k: v.digest for k, v in second.items()} == {k: v.digest for k, v in first.items()}
    designate(shelf, a)
    third = V.HashCache(tmp_path / "cache.json")
    out, _ = V.record_versions(shelf.root, third)
    assert third.misses == 1 and out[a].digest != first[a].digest


def test_unsyncable_folders_are_counted_never_listed(tmp_path):
    shelf, a, _ = shelf_with_two(tmp_path)
    (shelf.root / "sessions-raw" / "no-record-file--x").mkdir()
    folder = shelf.root / "sessions-raw" / V.record_versions(shelf.root)[0][a].name
    os.symlink("/etc/hosts", folder / "editions" / "link")
    got, problems = V.record_versions(shelf.root)
    assert a not in got and problems == {"folder_without_record_file": 1, "record_holds_a_link_or_special_file": 1}


def test_an_inventory_holds_ids_and_hashes_only_and_parses_back(tmp_path):
    shelf, a, b = shelf_with_two(tmp_path)
    got, problems = V.record_versions(shelf.root)
    doc = V.inventory_doc(shelf.root, got, generated_utc="2026-10-17T10:00:00Z", through_batch=None, release=None, problems=problems)
    text = json.dumps(doc)
    assert got[a].name not in text and "alpha" not in text
    assert doc["source_keys"]["total"] == 2 and V.parse_inventory(doc) == V.entries_of(got)


def test_a_file_destination_lists_keys_and_tells_a_missing_object_from_a_present_one(tmp_path):
    d = B.Dest(f"file://{tmp_path / 'dest'}")
    src = tmp_path / "x.json"
    src.write_text("{}")
    d.put(src, "sync/acks/b1.json")
    d.put(src, "sync/batches/b1/COMPLETE.json")
    assert d.list_keys("sync/batches") == ["sync/batches/b1/COMPLETE.json"]
    assert d.list_keys("sync/nothing") == []
    assert d.get_optional("sync/acks/b1.json", tmp_path / "got.json") is True
    assert d.get_optional("sync/acks/b2.json", tmp_path / "got2.json") is False and not (tmp_path / "got2.json").exists()


def test_s3_get_optional_reads_a_missing_key_as_absent_and_any_other_failure_as_an_error(tmp_path):
    import subprocess

    def fake(stderr, code=255):
        return lambda argv, **kw: subprocess.CompletedProcess(argv, code, "", stderr)
    assert B.Dest("s3://b/vault", run=fake("An error occurred (NoSuchKey) when calling the GetObject operation")).get_optional("k", tmp_path / "x") is False
    try:
        B.Dest("s3://b/vault", run=fake("Could not connect to the endpoint URL")).get_optional("k", tmp_path / "x")
        raise AssertionError("a network fault must not read as a missing acknowledgement")
    except B.BackupError as exc:
        assert exc.code == "download_failed"
    listed = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps({"Contents": [{"Key": "vault/sync/batches/b1/COMPLETE.json"}]}), "")
    assert B.Dest("s3://b/vault", run=listed).list_keys("sync/batches") == ["sync/batches/b1/COMPLETE.json"]


def test_a_completed_run_can_keep_the_previous_last_success(tmp_path):
    job = registry.parse({"schema_version": 1, "jobs": [{
        "id": "vault-sync", "name": "x", "host": "mac", "schedule": {"text": "every 2 h", "kind": "interval", "every_s": 7200},
        "expected_every_s": 7200, "missed_after_s": 21600, "stuck_after_s": 3600, "status_file": "vault-sync.json",
        "owner": "robert", "runbook": "x"}]}).jobs[0]
    t1, t2 = datetime(2026, 10, 17, 10, tzinfo=timezone.utc), datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    run = status.start_run(tmp_path, job, "r1", t1)
    status.finish_run(tmp_path, job, run, state="ok", now=t1, exit_code=0, summary="ok", results={}, next_due_by=None)
    run = status.start_run(tmp_path, job, "r2", t2)
    doc = status.finish_run(tmp_path, job, run, state="ok", now=t2, exit_code=0, summary="backlog 3", results={}, next_due_by=None,
                            success=False)
    assert doc["last_success_at"] == status.iso(t1) and doc["finished_at"] == status.iso(t2)
