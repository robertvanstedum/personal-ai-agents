import pytest
from services.partnership_tasks.http_api import create_app
from tests.partnership.synth import service, cmd


def test_scoped_api_keeps_source_and_cross_lead_resume(tmp_path):
    svc=service(tmp_path/'tasks')
    from dataclasses import replace
    svc.bindings._by_hash = {k: replace(b, work_artifacts=frozenset({'read','save'})) if b.seat in ('mc','cos') else b for k,b in svc.bindings._by_hash.items()}
    source=cmd('source_event.record','source_event','SE-work-0001',
               {'source':{'store':'mc-floor','id':'c-123:note-1','position':1},
                'content':'Keep this working decision for next time.','correction_of':None})
    assert svc.submit(source,'tok-platform')['outcome']=='accepted'
    app=create_app(svc,work_artifacts_root=tmp_path/'private-work').test_client()
    def call(token,op,args,scope='personal'):
        return app.post('/v1/work-artifacts',json={'operation':op,'scope':scope,'arguments':args},
                        headers={'Authorization':'Bearer '+token})
    saved=call('tok-mc','save',{'source_event_id':'SE-work-0001','expected_revision':0,
               'title':'Review plan','content':'Start with conversation tests.','unresolved':['Which tests?']})
    assert saved.status_code==200 and saved.json['status']=='saved',saved.json
    read=call('tok-cos','read',{'artifact_id':saved.json['artifact_id']})
    assert read.json['content']=='Start with conversation tests.'
    assert read.json['source_turn']=='mc-floor:c-123:note-1'
    assert call('tok-cos','read',{'artifact_id':saved.json['artifact_id']},'shared:build').status_code==403
    assert call('tok-worker','find',{'query':'Review'}).status_code==403
    assert call('tok-cos','save',{'source_event_id':'SE-work-0001','expected_revision':0,
               'title':'Forged source','content':'no','unresolved':[]}).status_code==403


def test_disabled_api_and_untrusted_source_refuse(tmp_path):
    svc=service(tmp_path/'tasks')
    app=create_app(svc).test_client()
    r=app.post('/v1/work-artifacts',json={},headers={'Authorization':'Bearer tok-mc'})
    assert r.status_code==503
    app=create_app(svc,work_artifacts_root=tmp_path/'private-work').test_client()
    assert app.post('/v1/work-artifacts',json={}).status_code==401
