"""Real Node CoS tool -> signed Python Board API -> isolated store and UI floor."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
from dataclasses import replace
import pytest
from werkzeug.serving import make_server
from core.domain_api.auth import Authenticator, KeyRegistry
from core.domain_api.contract import KeyProfile
from minimoi_portal.guild_ui.board_headless import create_app
from minimoi_portal.guild_ui.board_actions import BoardActions, BoardContext
from test_board_actions import board_case, CAPS

ROOT = Path(__file__).resolve().parents[3]
NODE = os.environ.get('NODE_BINARY') or shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='Node is required for cross-runtime Board integration')

@pytest.fixture
def cos_tool(board_case, tmp_path):
    _, source, _, _ = board_case
    # The same store/database as the UI, but its separate CoS floor.
    store = source.for_floor('cos-board')
    mc = source.for_floor('guild')
    profile = KeyProfile('cos-test', 'test-owner', 'cos', 'dev', 'guild', CAPS | {'guild.board.capabilities.read'}, b'a'*32)
    registry = KeyRegistry([profile])
    app = create_app(store=store, owner='test-owner', environment='dev', cursor_key=b'b'*32,
                     registry=registry, authenticator=Authenticator(registry.lookup, domain='guild', environment='dev'))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True); worker.start()
    client = ROOT / 'docker/cos-agent-a/plugins/cos-board/client.mjs'
    script = tmp_path/'invoke.mjs'
    script.write_text('''
import {executeBoard} from ''' + json.dumps(client.as_uri()) + ''';
import {readFile} from 'node:fs/promises';
let stdin='';for await(const chunk of process.stdin)stdin+=chunk;const input=JSON.parse(stdin);
let sends=0;
const result=await executeBoard(input.id,input.args,{
 cacheRoot:input.cache, readProfile:async()=>input.profile,
 exchange:async(method,route,raw,headers)=>{
  sends++;
  const r=await fetch('http://127.0.0.1:'+input.port+route,{method,headers,redirect:'manual',...(method==='GET'?{}:{body:raw})});
  const body=Buffer.from(await r.arrayBuffer());
  if(input.fault==='lost')throw new Error('lost after commit');
  return {status:r.status,raw:body,signature:input.fault==='signature'?'0'.repeat(64):r.headers.get('X-MiniMoi-Receipt')};
 }});
console.log(JSON.stringify({result:result.details,sends}));
''')
    payload = dict(key_id=profile.key_id, principal=profile.principal, actor='cos', domain='guild', environment='dev',
                   board='cos-board', secret_hex=profile.secret.hex())
    def call(id, args, fault=None, changed_profile=None, lose_cache=False):
        if lose_cache: shutil.rmtree(tmp_path/'cache', ignore_errors=True)
        p = subprocess.run([NODE, str(script)], input=json.dumps(dict(id=id,args=args,cache=str(tmp_path/'cache'),
                profile=changed_profile or payload,port=server.server_port,fault=fault)), text=True,capture_output=True,timeout=15)
        assert p.returncode==0, p.stderr
        return json.loads(p.stdout)
    try: yield call, store, mc, registry, profile, payload
    finally: server.shutdown();server.server_close();worker.join(timeout=2)


def test_cos_board_save_read_update_done_undo_receipts_and_mc_isolation(cos_tool):
    call, store, mc, registry, profile, payload = cos_tool
    first=call('save1',dict(operation='save',text='Préparer le résumé — Danke 😀',label='followup'))['result']
    assert first['state']=='verified', first
    note=first['current']['id']; op=first['receipt']['operation_id']
    assert first['receipt']['executor']=='cos' and first['receipt']['board']=='cos-board'
    listed=call('list1',dict(operation='list',query='résumé'))['result']
    assert [x['id'] for x in listed['notes']]==[note]
    current=call('read1',dict(operation='read',note_ref=note))['result']
    assert current['semantic_revision']==1
    done=call('update1',dict(operation='update',note_ref=note,expected_revision=1,changes={'done':True}))['result']
    assert done['state']=='verified' and done['current']['state']=='done'
    stale=call('update2',dict(operation='update',note_ref=note,expected_revision=1,changes={'text':'Stale edit'}))['result']
    assert stale['state']=='not_applied' and stale['code']=='revision_conflict'
    undo=call('undo1',dict(operation='undo',operation_ref=done['receipt']['operation_id'],expected_revision=2))['result']
    assert undo['state']=='verified' and undo['current']['state']=='active'
    recovered=call('result1',dict(operation='result',operation_ref=op))['result']
    assert recovered['receipt']==first['receipt'] and recovered['current']['semantic_revision']==3
    svc=BoardActions(mc,owner='test-owner',environment='dev',cursor_key=b'b'*32)
    ctx=BoardContext('test-owner','dev','guild','mc','test',CAPS)
    assert svc.query(ctx)['notes']==[]
    assert svc.update(ctx,'00000000-0000-4000-8000-000000000001',note,3,{'text':'wrong floor'})['code']=='note_not_found'


def test_lost_response_recovers_same_operation_without_duplicate_or_nested_argument_reuse(cos_tool):
    call, *_=cos_tool
    args=dict(operation='save',text='One note, once')
    lost=call('one',args,fault='lost')['result']
    assert lost['state']=='unknown' and lost['operation_id']
    recovered=call('recover',dict(operation='result',operation_ref=lost['operation_id']))['result']
    assert recovered['state']=='verified'
    replay=call('one',args,lose_cache=True)['result']
    assert replay['state']=='verified' and replay['replayed']
    changed=call('one',dict(operation='save',text='different'))
    assert changed['sends']==0
    note=replay['current']['id']
    update=dict(operation='update',note_ref=note,expected_revision=1,changes={'label':'idea'})
    assert call('edit',update)['result']['state']=='verified'
    assert call('edit',{**update,'changes':{'label':'fyi'}})['sends']==0
    assert len(call('list',dict(operation='list'))['result']['notes'])==1


def test_signature_revocation_bad_scope_and_model_identity_never_report_saved(cos_tool):
    call, store, mc, registry, profile, payload=cos_tool
    bad=call('unsigned',dict(operation='save',text='Saved, but acknowledgement tampered'),fault='signature')['result']
    assert bad['state']=='unknown'
    assert call('recover',dict(operation='result',operation_ref=bad['operation_id']))['result']['state']=='verified'
    assert call('forged',dict(operation='save',text='no',principal='somebody'))['sends']==0
    assert call('floor',dict(operation='save',text='no'),changed_profile={**payload,'board':'guild'})['sends']==0
    assert call('long',dict(operation='save',text='x'*281))['sends']==0
    registry.replace([replace(profile,enabled=False)])
    denied=call('revoked',dict(operation='save',text='no'))['result']
    assert denied['state']!='verified'
    registry.replace([profile])
    assert len(call('list',dict(operation='list'))['result']['notes'])==1
