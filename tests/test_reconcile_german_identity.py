import copy
import importlib
import json
from pathlib import Path

import pytest


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _bytes_by_path(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def migration_module():
    return importlib.import_module("scripts.reconcile_german_identity")


@pytest.fixture
def german_data(tmp_path):
    root = tmp_path / "german"
    _write_json(
        root / "sessions" / "2026-04-01_001.json",
        {
            "session_id": "session_1",
            "date": "2026-04-01",
            "raw_transcript": [{"speaker": "Robert", "text": "PRIVATE TRANSCRIPT"}],
        },
    )
    _write_json(
        root / "sessions" / "2026-05-01_001.json",
        {
            "session_id": "session_2",
            "date": "2026-05-01",
            "user_id": None,
        },
    )
    _write_json(
        root / "sessions" / "2026-06-01_001.json",
        {
            "session_id": "session_3",
            "date": "2026-06-01",
            "user_id": "robert",
        },
    )
    _write_json(
        root / "sessions" / "2026-07-01_001.json",
        {
            "session_id": "session_4",
            "date": "2026-07-01",
            "user_id": 7,
        },
    )
    _write_json(
        root / "sessions" / "2026-07-02_001.json",
        {
            "session_id": "session_other",
            "date": "2026-07-02",
            "user_id": 8,
        },
    )
    _write_json(
        root / "writing_sessions" / "user_robert.json",
        {
            "entries": [
                {"id": "legacy", "timestamp": "2026-05-01T10:00:00", "user_id": "robert"}
            ]
        },
    )
    _write_json(
        root / "writing_sessions" / "user_7.json",
        {
            "entries": [
                {"id": "current", "timestamp": "2026-07-01T10:00:00", "user_id": 7}
            ]
        },
    )
    _write_json(
        root / "lesen_notes" / "user_robert.json",
        {"notes": [{"note_id": "n1", "user_id": "robert"}]},
    )
    _write_json(
        root / "lesen_drills" / "2026-06-18.json",
        [
            {"id": "d1", "date": "2026-06-18", "user": "robert"},
            {"id": "d2", "date": "2026-06-18", "user": "7"},
        ],
    )
    _write_json(
        root / "config" / "phrasebook.json",
        {"phrases": [{"id": "p1", "user": "robert"}, {"id": "p2", "user": "7"}]},
    )
    _write_json(
        root / "config" / "persona_memory.json",
        {
            "robert_anna": {"user": "robert", "persona": "anna"},
            "7_anna": {"user": "7", "persona": "anna"},
        },
    )
    return root


def _manifest(module, root):
    return module.build_manifest(
        root,
        target_user_id=7,
        session_sources=["missing", "null", "string:robert"],
        writing_source_keys=["robert"],
    )


def test_dry_run_inventory_is_no_write_and_contains_no_personal_payload(
    migration_module, german_data
):
    before = _bytes_by_path(german_data)
    manifest = _manifest(migration_module, german_data)

    assert _bytes_by_path(german_data) == before
    assert manifest["target_user_id"] == 7
    assert manifest["summary"]["session_changes"] == 3
    assert manifest["summary"]["writing_sources"] == 1
    assert "PRIVATE TRANSCRIPT" not in json.dumps(manifest)
    assert set(manifest["inventory"]) >= {
        "sessions",
        "writing_sessions",
        "lesen_notes",
        "lesen_drills",
        "phrasebook",
        "persona_memory",
    }
    assert manifest["inventory"]["lesen_drills"]["files"][0]["identity_counts"] == {
        "string:7": 1,
        "string:robert": 1,
    }
    assert manifest["digest"] == migration_module.manifest_digest(manifest)


def test_apply_changes_only_approved_sessions_and_merges_writing(
    migration_module, german_data, tmp_path
):
    before = _bytes_by_path(german_data)
    manifest = _manifest(migration_module, german_data)
    backup = tmp_path / "backup"

    receipt = migration_module.apply_manifest(
        german_data,
        manifest,
        approved_digest=manifest["digest"],
        backup_dir=backup,
    )

    assert receipt["manifest_digest"] == manifest["digest"]
    sessions = [
        json.loads(path.read_text())
        for path in sorted((german_data / "sessions").glob("*.json"))
    ]
    by_id = {session["session_id"]: session for session in sessions}
    assert len(sessions) == 5
    assert by_id["session_1"]["user_id"] == 7
    assert by_id["session_2"]["user_id"] == 7
    assert by_id["session_3"]["user_id"] == 7
    assert by_id["session_4"]["user_id"] == 7
    assert by_id["session_other"]["user_id"] == 8

    merged = json.loads(
        (german_data / "writing_sessions" / "user_7.json").read_text()
    )
    assert {entry["id"] for entry in merged["entries"]} == {"legacy", "current"}
    assert all(entry["user_id"] == 7 for entry in merged["entries"])
    assert not (german_data / "writing_sessions" / "user_robert.json").exists()
    assert (
        german_data / "writing_sessions" / ".reconciled" / "user_robert.json"
    ).exists()

    # Inventory-only stores remain byte-identical.
    for relative in (
        "lesen_notes/user_robert.json",
        "lesen_drills/2026-06-18.json",
        "config/phrasebook.json",
        "config/persona_memory.json",
    ):
        assert (german_data / relative).read_bytes() == before[relative]


def test_state_drift_aborts_before_backup_or_write(
    migration_module, german_data, tmp_path
):
    manifest = _manifest(migration_module, german_data)
    changed = json.loads(
        (german_data / "sessions" / "2026-04-01_001.json").read_text()
    )
    changed["date"] = "2026-04-02"
    _write_json(german_data / "sessions" / "2026-04-01_001.json", changed)
    before_apply = _bytes_by_path(german_data)
    backup = tmp_path / "backup"

    with pytest.raises(migration_module.MigrationRefused, match="state changed"):
        migration_module.apply_manifest(
            german_data,
            manifest,
            approved_digest=manifest["digest"],
            backup_dir=backup,
        )

    assert _bytes_by_path(german_data) == before_apply
    assert not backup.exists()


def test_wrong_approval_digest_is_rejected(
    migration_module, german_data, tmp_path
):
    manifest = _manifest(migration_module, german_data)

    with pytest.raises(migration_module.MigrationRefused, match="digest"):
        migration_module.apply_manifest(
            german_data,
            manifest,
            approved_digest="not-approved",
            backup_dir=tmp_path / "backup",
        )


def test_inventory_only_store_drift_aborts_apply(
    migration_module, german_data, tmp_path
):
    manifest = _manifest(migration_module, german_data)
    phrasebook_path = german_data / "config" / "phrasebook.json"
    phrasebook = json.loads(phrasebook_path.read_text())
    phrasebook["phrases"].append({"id": "later", "user": "7"})
    _write_json(phrasebook_path, phrasebook)

    with pytest.raises(migration_module.MigrationRefused, match="state changed"):
        migration_module.apply_manifest(
            german_data,
            manifest,
            approved_digest=manifest["digest"],
            backup_dir=tmp_path / "backup",
        )


def test_lesen_drill_drift_aborts_apply(
    migration_module, german_data, tmp_path
):
    manifest = _manifest(migration_module, german_data)
    drill_path = german_data / "lesen_drills" / "2026-06-18.json"
    drills = json.loads(drill_path.read_text())
    drills.append({"id": "later", "date": "2026-06-18", "user": "7"})
    _write_json(drill_path, drills)

    with pytest.raises(migration_module.MigrationRefused, match="state changed"):
        migration_module.apply_manifest(
            german_data,
            manifest,
            approved_digest=manifest["digest"],
            backup_dir=tmp_path / "backup",
        )


def test_writing_source_with_other_numeric_owner_is_refused(
    migration_module, german_data
):
    source = german_data / "writing_sessions" / "user_robert.json"
    data = json.loads(source.read_text())
    data["entries"].append({"id": "not-robert", "user_id": 8})
    _write_json(source, data)

    with pytest.raises(migration_module.MigrationRefused, match="unapproved identities"):
        _manifest(migration_module, german_data)


def test_backup_must_be_outside_live_data_root(migration_module, german_data):
    manifest = _manifest(migration_module, german_data)

    with pytest.raises(migration_module.MigrationRefused, match="outside"):
        migration_module.apply_manifest(
            german_data,
            manifest,
            approved_digest=manifest["digest"],
            backup_dir=german_data / "backups" / "issue95",
        )


def test_invalid_inventory_only_json_blocks_manifest(migration_module, german_data):
    (german_data / "config" / "phrasebook.json").write_text("{not-json")

    with pytest.raises(migration_module.MigrationRefused, match="phrasebook"):
        _manifest(migration_module, german_data)


def test_invalid_lesen_drill_json_blocks_manifest(migration_module, german_data):
    (german_data / "lesen_drills" / "2026-06-18.json").write_text("{not-json")

    with pytest.raises(migration_module.MigrationRefused, match="identity-keyed"):
        _manifest(migration_module, german_data)


def test_conflicting_writing_ids_stop_manifest_creation(
    migration_module, german_data
):
    _write_json(
        german_data / "writing_sessions" / "user_robert.json",
        {"entries": [{"id": "current", "timestamp": "different", "user_id": "robert"}]},
    )

    with pytest.raises(migration_module.MigrationRefused, match="collision"):
        _manifest(migration_module, german_data)


def test_manifest_is_invalidated_when_edited(migration_module, german_data, tmp_path):
    manifest = _manifest(migration_module, german_data)
    tampered = copy.deepcopy(manifest)
    tampered["session_changes"] = tampered["session_changes"][:-1]

    with pytest.raises(migration_module.MigrationRefused, match="digest"):
        migration_module.apply_manifest(
            german_data,
            tampered,
            approved_digest=manifest["digest"],
            backup_dir=tmp_path / "backup",
        )


def test_restore_returns_byte_equivalent_originals(
    migration_module, german_data, tmp_path
):
    original = _bytes_by_path(german_data)
    manifest = _manifest(migration_module, german_data)
    backup = tmp_path / "backup"
    migration_module.apply_manifest(
        german_data,
        manifest,
        approved_digest=manifest["digest"],
        backup_dir=backup,
    )

    migration_module.restore_backup(
        german_data,
        backup,
        approved_digest=manifest["digest"],
    )

    assert _bytes_by_path(german_data) == original


def test_restore_refuses_tampered_backup_metadata(
    migration_module, german_data, tmp_path
):
    manifest = _manifest(migration_module, german_data)
    backup = tmp_path / "backup"
    migration_module.apply_manifest(
        german_data,
        manifest,
        approved_digest=manifest["digest"],
        backup_dir=backup,
    )
    metadata_path = backup / "backup_manifest.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["generated_paths"] = []
    _write_json(metadata_path, metadata)

    with pytest.raises(migration_module.MigrationRefused, match="metadata digest"):
        migration_module.restore_backup(
            german_data,
            backup,
            approved_digest=manifest["digest"],
        )


def test_second_apply_is_safe_noop(migration_module, german_data, tmp_path):
    manifest = _manifest(migration_module, german_data)
    migration_module.apply_manifest(
        german_data,
        manifest,
        approved_digest=manifest["digest"],
        backup_dir=tmp_path / "backup-1",
    )
    after_first = _bytes_by_path(german_data)

    second_manifest = _manifest(migration_module, german_data)
    assert second_manifest["summary"]["session_changes"] == 0
    assert second_manifest["summary"]["writing_sources"] == 0
    assert second_manifest["writing_merges"] == []
    assert _bytes_by_path(german_data) == after_first
