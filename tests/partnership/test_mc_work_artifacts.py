from dataclasses import replace
from core.mc_tools import catalogue
from services.partnership_tasks.client import TaskClient
from services.partnership_tasks.http_api import create_app
from tests.cos.test_cos_tasks_e2e import Session
from tests.partnership.test_helper_ask import rig  # noqa: F401
from tests.partnership.test_build_item import caller


def test_mc_bound_save_retries_and_cos_canonical_read(rig, tmp_path):
    svc = rig.svc
    svc.bindings._by_hash = {k:replace(b,work_artifacts=frozenset({'read','save'}))
                            if b.seat in ('mc','cos') else b for k,b in svc.bindings._by_hash.items()}
    session = Session(create_app(svc,work_artifacts_root=tmp_path/'private-work'))
    rig.adapter.client = TaskClient('http://tasks:8772','tok-mc',session=session)
    args = catalogue.validate('minimoi_work_save', {'title':'Delivery plan', 'content':'Test reload.\nThen test resume.',
                                                  'expected_revision':0,'unresolved':['Who checks voice?']})
    saved = rig.adapter.work_save(rig.host,args,caller())
    assert saved['status']=='done',saved
    artifact = saved['data']['artifact']
    assert artifact['revision']==1 and artifact['author']=='mc'
    duplicate = rig.adapter.work_save(rig.host,args,caller())
    assert duplicate['data']['artifact']['duplicate'] is True
    cos = TaskClient('http://tasks:8772','tok-cos',session=session)
    read = cos.work_artifacts('read',{'artifact_id':artifact['artifact_id']},scope='personal')
    assert read['content']=='Test reload.\nThen test resume.' and read['source_turn'].startswith('mc-floor:')


def test_committed_save_without_readback_keeps_unknown_receipt(rig, monkeypatch):
    from types import SimpleNamespace
    rig.adapter.client = SimpleNamespace(work_artifacts=lambda *a, **k: {
        'status':'saved_readback_unavailable', 'committed':True,
        'artifact_id':'wa-'+'a'*32, 'revision':1})
    args = catalogue.validate('minimoi_work_save', {'title':'Plan', 'content':'Draft',
                                                  'expected_revision':0,'unresolved':[]})
    saved = rig.adapter.work_save(rig.host,args,caller())
    assert saved['status']=='unknown' and saved['data']['committed'] is True
    assert [r['state'] for r in rig.receipts.rows.values()]==['unknown']
    assert all(r['outcome'] is None for r in rig.receipts.rows.values())


def test_lost_save_response_uses_document_reconciliation(rig):
    from types import SimpleNamespace
    from services.partnership_tasks.client import TaskServiceUnavailable
    def unavailable(*a, **k):
        raise TaskServiceUnavailable('lost response')
    rig.adapter.client = SimpleNamespace(work_artifacts=unavailable)
    args = catalogue.validate('minimoi_work_save', {'title':'Plan', 'content':'Draft',
                                                  'expected_revision':0,'unresolved':[]})
    saved = rig.adapter.work_save(rig.host,args,caller())
    assert saved['status']=='unknown'
    assert saved['data']['title']=='Plan' and saved['data']['source_event_id'].startswith('SE-work-')
    assert 'minimoi_work_read' in saved['message'] and 'minimoi_task_list' not in saved['message']


def test_owner_source_scrubbed_before_retention():
    from services.partnership_tasks.work_artifact_source import prepare_source
    import json
    words = 'Please save Authorization: Bearer secret_credential_123456789 for later'
    sid, cmd = prepare_source(source={'store':'cos-history','id':'conversation:turn','position':1},
                              words=words, environment='synthetic', scope='personal')
    retained = json.dumps(cmd)
    assert 'secret_credential_123456789' not in retained
    assert 'Full canonical source sha256:' in retained
    assert 'conversation:turn' in retained
