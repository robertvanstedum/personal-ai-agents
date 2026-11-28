"""Records preflight: a prepared schema plus reviewed additive steps passes; any other drift is refused.

Production (28 Nov 2026): the 9 Nov preparation fingerprint plus exactly room_proposals and room_approval_turns, which
Records' own startup created from the 26 Nov release on. Every preflight after that refused prepared_schema_mismatch.
"""
import json
import sqlite3

import pytest

from services import records_runtime as RT
from tests.guild.shop_floor.test_records_production_profile import prepared  # noqa: F401  (fixture)

STEP = 'room_approval_20261125'
STEP_TABLES = ('room_proposals', 'room_approval_turns')


def _db(p):
    return p.data / 'records.sqlite3'


def _set_marker_to_schema_without(p, names):
    """The manifest as preparation would have written it before Records' startup added NAMES."""
    with sqlite3.connect(_db(p)) as db:
        rows = [r for r in RT.schema_rows(db) if r[2] not in names]
    manifest = p.data / RT.MANIFEST
    marker = json.loads(manifest.read_text())
    marker['schema_sha256'] = RT.rows_hash(rows)
    manifest.write_text(json.dumps(marker))
    manifest.chmod(0o600)


def test_prepared_fixture_already_has_the_step_tables(prepared):
    with sqlite3.connect(_db(prepared)) as db:
        names = {r[1] for r in RT.schema_rows(db)}
    assert set(STEP_TABLES) <= names


def test_exact_prepared_schema_passes_with_no_steps(prepared):
    assert RT.preflight(prepared.data, prepared.env)['schema_steps'] == []


def test_prepared_schema_plus_registered_step_passes(prepared):
    _set_marker_to_schema_without(prepared, STEP_TABLES)
    result = RT.preflight(prepared.data, prepared.env)
    assert result['schema_steps'] == [STEP]
    assert result['provisioned_here'] is False


def test_step_recognition_is_read_only(prepared):
    _set_marker_to_schema_without(prepared, STEP_TABLES)
    before = _db(prepared).read_bytes()
    manifest_before = (prepared.data / RT.MANIFEST).read_bytes()
    RT.preflight(prepared.data, prepared.env)
    assert _db(prepared).read_bytes() == before
    assert (prepared.data / RT.MANIFEST).read_bytes() == manifest_before


def test_an_unregistered_extra_table_is_refused(prepared):
    with sqlite3.connect(_db(prepared)) as db:
        db.execute('CREATE TABLE surprise(x TEXT)')
    with pytest.raises(ValueError, match='prepared_schema_mismatch'):
        RT.preflight(prepared.data, prepared.env)


def test_an_unregistered_table_beside_the_step_is_refused(prepared):
    _set_marker_to_schema_without(prepared, STEP_TABLES)
    with sqlite3.connect(_db(prepared)) as db:
        db.execute('CREATE TABLE surprise(x TEXT)')
    with pytest.raises(ValueError, match='prepared_schema_mismatch'):
        RT.preflight(prepared.data, prepared.env)


def test_half_a_step_is_refused(prepared):
    _set_marker_to_schema_without(prepared, STEP_TABLES)
    with sqlite3.connect(_db(prepared)) as db:
        db.execute('DROP TABLE room_approval_turns')
    with pytest.raises(ValueError, match='prepared_schema_mismatch'):
        RT.preflight(prepared.data, prepared.env)


def test_a_step_table_with_another_definition_is_refused(prepared):
    _set_marker_to_schema_without(prepared, STEP_TABLES)
    with sqlite3.connect(_db(prepared)) as db:
        db.execute('DROP TABLE room_approval_turns')
        db.execute('CREATE TABLE room_approval_turns(turn_id TEXT PRIMARY KEY,room TEXT NOT NULL,code TEXT NOT NULL,'
                   'kind TEXT NOT NULL,extra TEXT)')
    with pytest.raises(ValueError, match='prepared_schema_mismatch'):
        RT.preflight(prepared.data, prepared.env)


def test_a_missing_prepared_object_is_refused_even_with_the_step(prepared):
    _set_marker_to_schema_without(prepared, STEP_TABLES)
    with sqlite3.connect(_db(prepared)) as db:
        victim = next(r[1] for r in RT.schema_rows(db)
                      if r[0] == 'index' and r[2] not in STEP_TABLES and r[3])
        db.execute(f'DROP INDEX "{victim}"')
    with pytest.raises(ValueError, match='prepared_schema_mismatch'):
        RT.preflight(prepared.data, prepared.env)


def test_recognised_schema_reports_none_for_a_foreign_fingerprint(prepared):
    with sqlite3.connect(_db(prepared)) as db:
        assert RT.recognised_schema(db, '0' * 64) is None


def test_a_step_must_contribute_objects(prepared, monkeypatch):
    """A registered module whose DDL creates nothing can never make a mismatch pass."""
    import types
    empty = types.ModuleType('empty_step')
    empty.SCHEMA = ''
    monkeypatch.setitem(__import__('sys').modules, 'empty_step', empty)
    with sqlite3.connect(_db(prepared)) as db:
        db.execute('CREATE TABLE surprise(x TEXT)')
    with sqlite3.connect(_db(prepared)) as db:
        marker = json.loads((prepared.data / RT.MANIFEST).read_text())['schema_sha256']
        assert RT.recognised_schema(db, marker, steps=(('empty', 'empty_step'),)) is None


def test_step_rows_match_what_room_approval_creates_in_a_live_store(prepared):
    """The registered rows are the exact sqlite_master rows the real initializer leaves in a store."""
    expected = set(RT.step_rows('domains.records.room_approval'))
    with sqlite3.connect(_db(prepared)) as db:
        live = set(RT.schema_rows(db))
    assert expected and expected <= live
