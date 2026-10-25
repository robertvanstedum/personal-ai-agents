"""Read-back races and isolation, with synthetic note pages; no network or model."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest

MODULE = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/platform_notes.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(script):
    source = f"import {{platformNotes, noteWatch, confirmedStop}} from {json.dumps(MODULE.as_uri())};\n" + """
const note = (id, kind='platform') => ({id, author_kind:kind, text:'synthetic card '+id});
const page = (notes, more=false) => ({ok:true,body:{notes,more}});
const result = await (async () => { SCRIPT })();
console.log(JSON.stringify(result));
""".replace('SCRIPT', script)
    p = subprocess.run(['node', '--input-type=module', '-e', source], capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_cards_arrive_in_order_once_and_only_platform_authored():
    assert run("""let rows=[note(14),note(13,'assistant'),note(12),note(10)], inserted=[];
const s=platformNotes({after:10,current:()=>true,fetchPage:async()=>page(rows),insert:n=>inserted.push(n.id)});
await s.refresh(); await s.refresh(); return inserted;""") == [12,14]


def test_late_lower_id_commit_is_not_lost_by_a_high_water_cursor():
    assert run("""let rows=[note(14)], inserted=[];
const s=platformNotes({after:10,current:()=>true,fetchPage:async()=>page(rows),insert:n=>inserted.push(n.id)});
await s.refresh(); rows=[note(12),note(14)]; await s.refresh(); return inserted;""") == [14,12]


def test_readback_paginates_to_the_turn_boundary():
    assert run("""let paths=[], inserted=[];
const s=platformNotes({after:10,current:()=>true,fetchPage:async before=>{
paths.push(before??null); return before ? page([note(10),note(11)],true) : page([note(12),note(13)],true);
},insert:n=>inserted.push(n.id)}); return [await s.refresh(),paths,inserted];""") == [True,[None,12],[11,12,13]]


def test_inflight_reads_are_shared_and_stale_conversation_or_private_response_is_dropped():
    assert run("""let active=true,calls=0,release,inserted=[];
const s=platformNotes({after:10,current:()=>active,fetchPage:()=>{calls++; return new Promise(r=>release=r);},insert:n=>inserted.push(n.id)});
const a=s.refresh(),b=s.refresh(); active=false; release(page([note(11)]));
return [a===b, await a, await b,calls,inserted];""") == [True,False,False,1,[]]


def test_no_read_starts_when_private_hidden_or_in_another_conversation():
    assert run("""let calls=0; const s=platformNotes({after:0,current:()=>false,fetchPage:async()=>{calls++;return page([note(1)]);},insert:()=>{throw Error('leak');}});
return [await s.refresh(),calls];""") == [False,0]


def test_failed_read_can_recover_without_replaying_an_action():
    assert run("""let calls=0,inserted=[];
const s=platformNotes({after:10,current:()=>true,fetchPage:async()=> ++calls===1 ? {ok:false} : page([note(11)]),insert:n=>inserted.push(n.id)});
return [await s.refresh(),await s.refresh(),inserted];""") == [False,True,[11]]


def test_repeating_or_oversized_pagination_fails_bounded_without_partial_publication():
    assert run("""let calls=0,inserted=[];
const s=platformNotes({after:10,current:()=>true,fetchPage:async()=>{calls++;return page([note(12)],true);},insert:n=>inserted.push(n.id)});
return [await s.refresh(),calls,inserted];""") == [False,2,[]]


def test_only_an_explicit_confirmed_stop_is_treated_as_successful_cancellation():
    assert run("""const ev={status:'stopped',failure_class:'stopped'};
return [confirmedStop(ev),confirmedStop({...ev,stop_acknowledged:false}),confirmedStop({...ev,stop_acknowledged:true}),
confirmedStop({...ev,stop_acknowledged:true,tool_admission:'unconfirmed'}),confirmedStop({status:'error',stop_acknowledged:true})];""") == [False,False,True,False,False]


def test_completion_waits_for_inflight_then_takes_a_fresh_snapshot():
    assert run("""let calls=0,release,disposed=0,timers=[];
const w=noteWatch({refresh:()=>++calls===1 ? new Promise(r=>release=r) : Promise.resolve(true),
schedule:(fn,ms)=>{timers.push(ms);},cancel:()=>{},onDispose:()=>disposed++});
const first=w.wake(); const end=w.finish(); release(true); await first; await end;
await w.wake(); return [calls,disposed,timers];""") == [2,1,[]]


def test_failed_final_reads_sleep_after_three_attempts_and_reconnect_resumes():
    assert run("""let calls=0,ready=false,timers=[],disposed=0;
const w=noteWatch({refresh:async()=>{calls++; return ready;},schedule:(fn,ms)=>{timers.push(fn);},cancel:()=>{},onDispose:()=>disposed++});
await w.finish(); await timers.shift()(); await timers.shift()();
const sleeping=[calls,timers.length,disposed]; ready=true; await w.wake();
return [sleeping,calls,disposed];""") == [[3,0,0],4,1]


def test_page_leave_disposes_once_without_scheduling_another_read():
    assert run("""let release,calls=0,disposed=0,timers=[];
const w=noteWatch({refresh:()=>{calls++;return new Promise(r=>release=r);},schedule:(fn,ms)=>timers.push(ms),cancel:()=>{},onDispose:()=>disposed++});
const p=w.wake(); w.dispose(); w.dispose(); release(true); await p; await w.finish(); await w.wake();
return [calls,disposed,timers];""") == [1,1,[]]


def test_reload_recovery_reads_saved_agent_and_platform_notes_once_not_owner_text():
    assert run("""let inserted=[], active=true;
const s=platformNotes({after:10,kinds:['agent','platform'],current:()=>active,
fetchPage:async()=>page([note(14,'agent'),note(13,'owner'),note(12)]),insert:n=>inserted.push(n.id)});
await s.refresh(); await s.refresh(); active=false; await s.refresh(); return inserted;""") == [12,14]
