import json
from pathlib import Path
import urllib.request
import urllib.error

root='http://127.0.0.1:18882'
token=Path('/tmp/intelligence-data/auth/owner.token').read_text().strip()

def request(path,body=None,auth=True):
    headers={'Content-Type':'application/json'}
    if auth:
        headers['Authorization']='Bearer '+token
    raw=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(root+path,data=raw,headers=headers)
    with urllib.request.urlopen(req,timeout=15) as result:
        return json.load(result)

health=request('/health')
assert health['mode']=='synthetic-dev'
status=request('/api/status',{})
assert status['topics']==['demo']
seed=request('/api/demo',{})
assert request('/api/demo',{})['new_records']==0
answer=request('/api/search',{'query':'citations','topic':'demo'})
assert len(answer['results'])>=3
for r in answer['results']:
    for c in r['citations']:
        assert request('/api/resolve',{'citation':c})['source_bytes_verified']
practice=next(r for r in answer['results'] if r['type']=='practice')
outcome=next(r for r in answer['results'] if r['type']=='outcome')
relations=request('/api/relations',{'start_id':outcome['id'],'topic':'demo'})
assert relations['relations'][0]['kind']=='outcome_of'
old=request('/api/search',{'query':'citations','topic':'demo','as_of':'2026-10-01T00:00:00Z'})
assert not old['results']
request('/api/rebuild',{})
assert request('/api/search',{'query':'citations','topic':'demo'})['results']==answer['results']
try:
    request('/api/search',{'query':'x','topic':'forbidden'})
    raise AssertionError('topic authorization failed')
except urllib.error.HTTPError as e:
    assert e.code==400
# Owner-receipted temporary assertion and actual working-store deletion.
c=answer['results'][0]['citations'][0]
a={k:c[k] for k in ('record_id','record_hash','start','end')}
proposal=request('/api/candidate',{'text':'DEV_ACCEPTANCE_DELETE_CANARY','sources':[a],'topic':'demo','type':'decision'})['record']
assert proposal['authority']=='agent_proposal'
plan=request('/api/delete-plan',{'id':proposal['id']})
erasure=request('/api/delete',{'id':proposal['id'],'plan_hash':plan['hash']})
assert erasure['backup_erasure']=='pending'
assert not request('/api/search',{'query':'DEV_ACCEPTANCE_DELETE_CANARY','topic':'demo'})['results']
assert all(b'DEV_ACCEPTANCE_DELETE_CANARY' not in p.read_bytes() for p in Path('/tmp/intelligence-data/vault').rglob('*') if p.is_file())
assert token not in json.dumps(status)
print(json.dumps({'revision':health['revision'],'mode':health['mode'],'checks':['authenticated API','demo idempotency','exact source citations','typed learning cycle','owner-receipted outcome relation','historical knowledge exclusion','rebuild equivalence','topic denial','confirmed working-store deletion','no backup-erasure claim'],'store':request('/api/status',{})['store']},sort_keys=True,indent=2))
