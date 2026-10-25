"""Execute the real queue controller with minimal DOM/HTTP seams; no provider."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")

def run(tmp_path, scenario):
    src = (ROOT / "minimoi_portal/guild_ui/static/js/mc_queue.js").read_text()
    (tmp_path / "queue.mjs").write_text(src.replace("'./dom.js'", "'./seams.mjs'").replace("'./api.js'", "'./seams.mjs'").replace("'./state.js'", "'./seams.mjs'").replace("'./mc_queue_words.js'", "'./words.mjs'"))
    (tmp_path / "words.mjs").write_text((ROOT / "minimoi_portal/guild_ui/static/js/mc_queue_words.js").read_text())
    (tmp_path / "seams.mjs").write_text("""
export const $ = () => null, el = () => {throw Error('unexpected DOM mutation')}, announce = () => {};
export const live = {known:true,off:false};
export const onChange = () => {};
export const calls=[];
export let views=[];
export const setViews = v => {views=v};
export const apiGet = async path => {calls.push(path);return {ok:true,body:views.shift()}};
export const apiPost = () => {throw Error('unexpected write')};
""")
    script = """
import {initQueue} from './queue.mjs';
import {live,calls,setViews} from './seams.mjs';
let timers=[], dispatches=0, readbacks=0;
globalThis.window={clearTimeout(){},setTimeout(fn,ms){timers.push({fn,ms});}};
globalThis.document={hidden:false,addEventListener(){}};
globalThis.CSS={escape:x=>x};
const thread={querySelector:()=>null,querySelectorAll:()=>[]};
const view=(running=null)=>({queued:[],recent:[],running,pause:null});
const flush=()=>new Promise(r=>setImmediate(r));
const boot=(readBack=async()=>{readbacks++;return true})=>initQueue({page:{conversation:{id:'c1'}},thread,readBack,dispatch:()=>{dispatches++;throw Error('must not replay')}});
SCENARIO
""".replace('SCENARIO', scenario)
    (tmp_path / "run.mjs").write_text(script)
    out = subprocess.run(['node',str(tmp_path / "run.mjs")],capture_output=True,text=True,timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)

def test_reload_running_with_empty_queue_reads_final_answer_and_stops(tmp_path):
    assert run(tmp_path, """
setViews([view({conversation_id:'c1'}),view()]);boot();await flush();
const delays=timers.map(t=>t.ms);await timers.shift().fn();await flush();
console.log(JSON.stringify({calls,readbacks,dispatches,delays,left:timers.length}));
""") == {'calls':['/mc/turns/ledger']*2,'readbacks':2,'dispatches':0,'delays':[2000],'left':0}

@pytest.mark.parametrize('state', ['live.off=true', 'live.known=false'])
def test_private_and_unknown_do_not_start_recovery_reads(tmp_path,state):
    assert run(tmp_path, state+";setViews([view()]);boot();await flush();console.log(JSON.stringify([calls,readbacks,dispatches,timers]));") == [[],0,0,[]]

def test_failed_final_readback_retries_without_dispatching(tmp_path):
    assert run(tmp_path, """
setViews([view(),view()]);boot(async()=>++readbacks>1);await flush();
const delay=timers[0].ms;await timers.shift().fn();await flush();
console.log(JSON.stringify([readbacks,dispatches,delay,timers.length]));
""") == [2,0,5000,0]


def test_hidden_idle_tab_stops_recovery_polling_after_initial_ledger_read(tmp_path):
    assert run(tmp_path, """
document.hidden=true;setViews([view({conversation_id:'c1'})]);boot();await flush();
await timers.shift().fn();await flush();
console.log(JSON.stringify([calls.length,dispatches,timers.length]));
""") == [1,0,0]


def test_backgrounding_does_not_strand_already_queued_owner_messages(tmp_path):
    assert run(tmp_path, """
document.hidden=true;setViews([{...view(),queued:[{id:1,note_request_id:'n1',conversation_id:'c1'}]},view()]);
initQueue({page:{conversation:{id:'c1'}},thread,dispatch:async()=>{dispatches++;return {error:null}}});
await flush();console.log(JSON.stringify([dispatches,calls.length,timers.length]));
""") == [1,2,0]
