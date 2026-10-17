"""Synthetic policy bytes/metadata; no real grant, source, writer or transport."""
import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest
from core.memory_ask.identity import Grant
from core.memory_shelf import record
from core.vault_t1 import reader

from core.memory_index import processing as p

RID = '01ARZ3NDEKTSV4RRFFQ69G5FAV'
SOURCE = 'codex:synthetic-session'
EDITION = 'a' * 64
PROFILE = 'c' * 64


def document():
    return {'schema': p.SCHEMA,
            'destinations': [{'id': 'prod-main', 'revision_sha256': PROFILE}],
            'enrollments': [{'caller': 'mc-prod', 'destination_id': 'prod-main',
                             'destination_revision_sha256': PROFILE, 'revoked_at': None}],
            'sources': [{'source_id': SOURCE, 'local_only': False}],
            'records': [{'record_id': RID, 'source_id': SOURCE, 'local_only': False}],
            'editions': [{'caller': 'mc-prod', 'destination_id': 'prod-main', 'destination_revision_sha256': PROFILE,
                          'record_id': RID, 'edition_hash': EDITION, 'owner_confirmation_ref': 'synthetic-owner-action-1',
                          'revoked_at': None}]}


def setup(doc=None):
    snapshot = p.parse(json.dumps(document() if doc is None else doc).encode())
    context = p.ProcessingContext('prod-main', PROFILE, snapshot.revision_sha256)
    grant = Grant('mc-prod', 'tool', ('curated',), ('robert',), 5, 30, False, None)
    meta = {'id': RID, 'source': SOURCE, 'source_hash': 'b' * 64, 'edition_hash': EDITION,
            'created': '2026-10-16T00:00:00Z', 'kind': 'session', 'chair': 'Codex', 'scope': 'robert',
            'tier': 'curated', 'events': [], 'normalized': {'normalizer': 3}}
    return snapshot, grant, context, meta


def decision(items, **kwargs):
    snapshot, grant, context, meta = items
    return p.evaluate(snapshot, grant, context, meta,
                      current_revision_sha256=snapshot.revision_sha256, **kwargs)


def test_exact_authorized_edition_is_the_only_positive_case():
    items = setup()
    assert decision(items) == p.Decision(True, 'allowed')
    items[-1]['edition_hash'] = 'd' * 64
    assert decision(items) == p.Decision(False, 'edition_unavailable')


@pytest.mark.parametrize('where', ['source', 'record', 'metadata'])
def test_local_only_overrides_an_explicit_edition_allow(where):
    doc = document()
    if where == 'source': doc['sources'][0]['local_only'] = True
    if where == 'record': doc['records'][0]['local_only'] = True
    items = setup(doc)
    if where == 'metadata': items[-1]['local_only'] = True
    assert decision(items).code == 'local_only'


@pytest.mark.parametrize('missing', ['sources', 'records', 'enrollments', 'editions'])
def test_missing_classification_or_allow_never_inherits_permission(missing):
    # Valid empty policies remain usable as deny snapshots, not automatically permissive.
    doc = document()
    if missing in ('sources', 'records'):
        doc['sources'] = []; doc['records'] = []; doc['editions'] = []
    elif missing == 'enrollments': doc['enrollments'] = []; doc['editions'] = []
    else: doc['editions'] = []
    assert not decision(setup(doc)).allowed


def test_legacy_missing_policy_or_context_default_is_deny_and_never_reads(monkeypatch):
    items = setup()
    def no_reader(*args, **kwargs): pytest.fail('policy called an evidence reader')
    monkeypatch.setattr(reader, 'read_path', no_reader)
    monkeypatch.setattr(record, 'read', no_reader)
    snapshot, grant, context, meta = items
    assert not p.evaluate(None, grant, context, meta).allowed
    assert not p.evaluate(snapshot, grant, None, meta).allowed
    assert not p.evaluate(snapshot, grant, context, meta).allowed  # missing fresh revision
    assert decision(items).allowed  # Even a positive evaluation does not materialize evidence.


@pytest.mark.parametrize('which', ['identity', 'enrollment', 'edition'])
def test_revocation_at_every_layer_denies(which):
    doc = document()
    if which != 'identity': doc[which + 's'][0]['revoked_at'] = '2026-10-16T01:00:00Z'
    snapshot, grant, context, meta = setup(doc)
    if which == 'identity': grant = replace(grant, revoked=True)
    assert not decision((snapshot, grant, context, meta)).allowed


def test_stale_policy_context_and_current_revision_cannot_be_replayed():
    snapshot, grant, context, meta = setup()
    assert not p.evaluate(snapshot, grant, context, meta, current_revision_sha256='e' * 64).allowed
    doc = document(); doc['editions'][0]['revoked_at'] = '2026-10-16T01:00:00Z'
    newer = setup(doc)[0]
    assert not p.evaluate(newer, grant, context, meta, current_revision_sha256=newer.revision_sha256).allowed
    assert not p.evaluate(snapshot, grant, replace(context, destination_revision_sha256='d' * 64), meta,
                          current_revision_sha256=snapshot.revision_sha256).allowed


def test_host_context_and_real_grant_type_required():
    snapshot, grant, context, meta = setup()
    assert not p.evaluate(snapshot, {'name': 'mc-prod', 'approved': True}, context, meta,
                          current_revision_sha256=snapshot.revision_sha256).allowed
    assert not p.evaluate(snapshot, grant, {'destination_id': 'prod-main'}, meta,
                          current_revision_sha256=snapshot.revision_sha256).allowed
    other = replace(grant, name='cos-prod')
    assert not decision((snapshot, other, context, meta)).allowed


@pytest.mark.parametrize('field,value', [('scope', 'mandate:01ARZ3NDEKTSV4RRFFQ69G5FAV'), ('tier', 'raw'),
                                       ('source', 'codex:unclassified-source'), ('local_only', 'false')])
def test_existing_scope_tier_and_exact_source_mapping_are_still_gates(field, value):
    items = setup(); items[-1][field] = value
    assert not decision(items).allowed


def test_raw_requires_both_original_raw_grant_and_exact_edition_policy():
    snapshot, grant, context, meta = setup(); meta['tier'] = 'raw'
    assert not decision((snapshot, grant, context, meta), include_raw=True).allowed
    assert decision((snapshot, replace(grant, tiers=('curated', 'all')), context, meta), include_raw=True).allowed


@pytest.mark.parametrize('bad', [b'', b'[]', b'{', b'\xff', b'null', b'{"schema":NaN}', b'x' * (p.MAX_BYTES + 1)])
def test_malformed_and_oversize_bytes_refuse(bad):
    with pytest.raises(p.Refused): p.parse(bad)


def test_duplicate_json_unknown_fields_and_duplicate_authorization_rows_refuse():
    raw = json.dumps(document()).replace('"schema":', '"schema":"other", "schema":', 1).encode()
    with pytest.raises(p.Refused): p.parse(raw)
    for key in ('destinations', 'enrollments', 'sources', 'records', 'editions'):
        doc = document(); doc[key].append(copy.deepcopy(doc[key][0]))
        with pytest.raises(p.Refused): p.parse(json.dumps(doc).encode())
    doc = document(); doc['editions'][0]['approved'] = True
    with pytest.raises(p.Refused): p.parse(json.dumps(doc).encode())


@pytest.mark.parametrize('change', ['dangling_source', 'dangling_destination', 'dangling_record', 'ambiguous_mapping',
                                    'nonbool_classification', 'bad_date', 'reserved_caller', 'wildcard_edition'])
def test_invalid_references_and_implicit_authority_refuse(change):
    doc = document()
    if change == 'dangling_source': doc['records'][0]['source_id'] = 'codex:missing'
    elif change == 'dangling_destination': doc['enrollments'][0]['destination_revision_sha256'] = 'd' * 64
    elif change == 'dangling_record': doc['editions'][0]['record_id'] = '01ARZ3NDEKTSV4RRFFQ69G5FAW'
    elif change == 'ambiguous_mapping': doc['records'].append({**doc['records'][0], 'source_id': 'other'})
    elif change == 'nonbool_classification': doc['sources'][0]['local_only'] = 0
    elif change == 'bad_date': doc['editions'][0]['revoked_at'] = '2026-02-30T00:00:00Z'
    elif change == 'reserved_caller': doc['enrollments'][0]['caller'] = 'owner'
    else: doc['editions'][0]['edition_hash'] = '*'
    with pytest.raises(p.Refused): p.parse(json.dumps(doc).encode())


def test_row_caps_apply_before_construction():
    doc = document(); doc['sources'] = doc['sources'] * (p.LIMITS['sources'] + 1)
    with pytest.raises(p.Refused): p.parse(json.dumps(doc).encode())


def test_exact_byte_revision_and_immutable_outputs():
    raw = json.dumps(document()).encode(); first = p.parse(raw); second = p.parse(raw + b' ')
    assert first.revision_sha256 != second.revision_sha256
    with pytest.raises(FrozenInstanceError): first.revision_sha256 = 'x'
    assert type(first.editions) is tuple and all(type(row) is tuple for row in first.editions)
