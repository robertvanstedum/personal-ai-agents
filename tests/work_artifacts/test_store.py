from concurrent.futures import ThreadPoolExecutor
import pytest
from core.work_artifacts import Binding, Store


def store(tmp_path, lead='mc', scope='personal', turn='turn-1'):
    return Store(tmp_path / 'work', Binding('owner', 'synthetic', scope, lead, turn), validate_source=lambda b: None)


def save(s, *, artifact_id=None, expected_revision=0, operation_id='one', content='Version one', title='Trip decision'):
    return s.save(artifact_id=artifact_id, expected_revision=expected_revision, operation_id=operation_id,
                  title=title, content=content, unresolved=['Which day?'])


def test_fresh_cos_session_resumes_mc_document_and_corrected_revision(tmp_path):
    first = save(store(tmp_path))
    assert first['status'] == 'saved', first
    second_lead = store(tmp_path, lead='cos', turn='turn-2')
    found = second_lead.find('trip')['items']
    assert len(found) == 1
    resumed = second_lead.read(found[0]['artifact_id'])
    assert resumed['content'] == 'Version one' and resumed['source_turn'] == 'turn-1'
    newer = save(second_lead, artifact_id=first['artifact_id'], expected_revision=1, operation_id='two', content='Version two')
    assert newer['status'] == 'saved' and newer['revision'] == 2
    old = store(tmp_path).read(first['artifact_id'], 1)
    assert old['superseded'] and old['current_revision'] == 2
    assert newer['indexed_revision'] is None and newer['authority'] == 'working_draft'


def test_retry_and_changed_retry_do_not_create_new_revision(tmp_path):
    s = store(tmp_path)
    first = save(s)
    assert save(s)['duplicate']
    assert save(s, content='Different')['status'] == 'id_conflict'
    assert s.read(first['artifact_id'])['revision'] == 1


def test_concurrent_revisions_cannot_overwrite_each_other(tmp_path):
    first = save(store(tmp_path))
    def update(n):
        return save(store(tmp_path, turn='turn-' + str(n)), artifact_id=first['artifact_id'], expected_revision=1,
                    operation_id='update-' + str(n), content='Candidate ' + str(n))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [2, 3]))
    assert sorted(r['status'] for r in results) == ['policy_refused', 'saved'], results
    assert store(tmp_path).read(first['artifact_id'])['revision'] == 2


def test_scope_isolation_and_ambiguous_names(tmp_path):
    s = store(tmp_path)
    one = save(s)
    save(s, operation_id='another')
    assert s.find('Trip')['ambiguous']
    other = store(tmp_path, scope='other')
    assert other.find('Trip')['items'] == []
    assert other.read(one['artifact_id'])['status'] == 'not_found'


def test_revoked_source_cannot_commit(tmp_path):
    calls = []
    def validate(binding):
        calls.append(binding)
        if len(calls) > 1:
            raise PermissionError('source revoked')
    s = Store(tmp_path / 'work', Binding('owner', 'synthetic', 'personal', 'mc', 'turn-1'), validate_source=validate)
    with pytest.raises(PermissionError):
        save(s)
    assert store(tmp_path).find('Trip')['items'] == []


def test_credential_scrubbing_preserves_json_and_host_provenance(tmp_path):
    s = store(tmp_path)
    result = s.save(artifact_id=None, expected_revision=0, title='Private draft',
                    content='Bearer abc', unresolved=['password: example-secret'], operation_id='secret-case')
    assert result['status'] == 'saved', result
    assert result['redactions'] >= 2
    assert 'abc' not in result['content']
    assert 'example-secret' not in result['unresolved'][0]
    assert result['source_turn'] == 'turn-1' and result['author'] == 'mc'
    assert s.read(result['artifact_id'])['content'] == result['content']


def test_interrupted_publish_never_claims_saved(tmp_path, monkeypatch):
    from core.workshop_journal import artifacts
    def fail(*args, **kwargs):
        raise OSError('synthetic interruption')
    monkeypatch.setattr(artifacts, 'publish', fail)
    result = save(store(tmp_path))
    assert result['committed'] is False and result['status'] != 'saved'
    assert store(tmp_path).find('Trip')['items'] == []
