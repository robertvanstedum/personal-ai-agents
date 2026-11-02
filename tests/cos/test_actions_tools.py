from flask import Flask
import pytest
from domains.cos.store.api import install
from domains.cos.store.writer import StoreWriter

PREFIX = '/internal/cos-actions/v1'
AUTH = {'Authorization':'Bearer actions-test-token'}

@pytest.fixture
def setup(tmp_path):
    token=tmp_path/'token';token.write_text('actions-test-token')
    app=Flask(__name__)
    writer=install(app,environ={'COS_STORE_DIR':str(tmp_path/'store'),'COS_STORE_ENVIRONMENT':'dev',
                               'COS_ACTIONS_TOKEN_FILE':str(token)})
    return app.test_client(),writer

def post(client,op,n,**body):
    return client.post(PREFIX+'/'+op,headers=AUTH,json={'op_id':f'operation-{n:08d}',
        'at':'2026-11-02T18:00:00Z',**body})

def test_full_lifecycle_receipts_and_shared_store(setup):
    c,w=setup
    opened=post(c,'open',1,title='Review resume',kind='action',priority=1)
    assert opened.status_code==200,opened.json
    row=opened.json['row'];item=row['item_id']
    assert row['opened_by']=='cos' and row['title_provenance']=='agent_draft'
    assert row['with']=='cos' and row['priority']==1
    assert post(c,'open',1,title='Review resume',kind='action',priority=1).json['result']['replayed']
    assert post(c,'open',1,title='Different').status_code==422
    updated=post(c,'update',2,item_id=item,revision=1,status='doing').json['row']
    assert updated['revision']==2
    assert post(c,'update',3,item_id=item,revision=1,priority=3).status_code==409
    comment=post(c,'comment',4,item_id=item,revision=2,text='Draft ready').json['row']
    assert comment['comments'][0]['author']=='cos'
    closed=post(c,'close',5,item_id=item,revision=3,outcome='done').json['row']
    assert closed['closed_at'] and closed['status']=='done'
    assert c.get(PREFIX+'/items',headers=AUTH).json['items'][0]['outcome']=='done'
    assert StoreWriter(w.root,'dev').get('cos.in_flight',item)==closed
    assert post(c,'update',6,item_id=item,revision=4,title='No').status_code==422

@pytest.mark.parametrize('extra',[{'actor':'robert'},{'provenance':'approved'},{'target':{}},{'environment':'prod'},{'by':'mc'},{'source':{'type':'x','id':'y'}}])
def test_rejects_authority_overrides(setup,extra):
    c,w=setup
    assert post(c,'open',1,title='Test',**extra).status_code==422
    assert w.docs('cos.in_flight')==[]

def test_grant_not_general_store_and_auth_required(setup):
    c,w=setup
    assert c.get(PREFIX+'/items').status_code==401
    assert c.post('/internal/cos-store/v1/commands',headers=AUTH,json={}).status_code==404
    assert post(c,'open',1,title='Test',status='done').status_code==422
    assert post(c,'update',2,item_id='IF-abc',title='Test').status_code==422
    assert post(c,'memory',3,title='Test').status_code==400

@pytest.mark.parametrize('kind',['action','briefing','follow-up','research','decision','risk'])
def test_types_share_priority_status_and_cancel(setup,kind):
    c,w=setup
    row=post(c,'open',1,title='Test '+kind,kind=kind,priority=3,status='waiting').json['row']
    assert row['kind']==kind and row['status']=='waiting'
    row=post(c,'close',2,item_id=row['item_id'],revision=1,outcome='dropped').json['row']
    assert row['outcome']=='dropped' and row['closed_at']

@pytest.mark.parametrize('field,size,op',[('title',201,'open'),('text',4001,'comment'),('note',2001,'close')])
def test_oversize_is_refused_not_truncated(setup,field,size,op):
    c,w=setup
    body={field:'x'*size}
    assert post(c,op,1,**body).status_code==422
    assert w.docs('cos.in_flight')==[]
