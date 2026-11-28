"""No provider/network/credentials: authority, cancellation and retry boundaries."""
from copy import deepcopy
from types import SimpleNamespace as NS
import hashlib
import threading

import pytest
from flask import Flask

from minimoi_portal.guild_ui.mc_rooms_bridge import Bridge, install, PATH
from minimoi_portal.guild_ui.mc.stream import RunStarted, Delta, Finish

IDS = dict(room='room-1', turn_id='turn-1', claim_id='claim-1', correlation_id='call-1')
TURN = dict(id='turn-1', room='room-1', claim_id='claim-1', addressee='mc', state='running', generation=1,
    room_members=[dict(id=x) for x in ('robert','mc','cos')],
    trigger=dict(actor='robert', kind='message', origin=None, text='MC read issue 469', seq=3, id='event-3'),
    brief=dict(title='Test meeting', purpose='Talk', participants=[dict(id=x, label=x) for x in ('robert','mc','cos')],
               language='English', kind='meeting'), transcript=[], coverage=dict(through_seq=2), discussion_reply=None)


class Records:
    def __init__(self):
        self.turn = deepcopy(TURN)
        self.allowed = True
        self.calls = []
    def call(self, method, path, body, **_):
        self.calls.append(body)
        return 200, {'dispatch': self.allowed, 'turn': deepcopy(self.turn)}


class Ledger:
    started = False
    def __init__(self, events): self.events = events
    def mark_started(self): self.started = True; self.events.append('started')
    def finish_not_sent(self, *a): self.events.append('not_sent')
    def finish_unknown(self, *a): self.events.append('unknown')
    def finish_stream(self, *a): self.events.append(a[0])


class Binding:
    def __init__(self, events): self.events = events
    def __call__(self): self.events.append('bind')
    def attach_run(self, rid): self.events.append(('run', rid))
    def revoke(self): self.events.append('revoke'); return 'revoked'


class Backend:
    kind = 'openclaw'
    connected = True
    def __init__(self, events): self.events, self.calls, self.hook = events, 0, None
    def stream_turn(self, req, cancel=None):
        def generate():
            if self.hook: self.hook()
            if cancel.is_set(): return
            self.calls += 1
            self.events.append('dispatch')
            yield RunStarted('run-1')
            yield Delta('Issue read with the bound tool.')
            yield Finish('stop')
        return generate()
    def stop(self, correlation): self.events.append('backend_stop'); return True
    def settle(self, result): pass


@pytest.fixture
def world(tmp_path):
    events = []
    records, backend = Records(), Backend(events)
    ledger = Ledger(events)
    notes = []
    floor = NS(add_note=lambda *args: (notes.append(args) or NS(outcome='kept')))
    def prepare(turn, operation):
        events.append('prepare')
        return {'id':'c-abc'}, floor, 'owner-note', 'guild:robert:c-abc'
    from minimoi_portal.guild_ui.mc.internal_stream import collect
    bridge = Bridge(services=NS(mc=backend), environ={}, principal='robert', records=records,
        state_dir=tmp_path/'private', craft_prefix='/guild-next',
        gate=lambda *a, **k: ledger, make_binding=lambda *a, **k: Binding(events),
        collect=lambda *a, **k: collect(*a, **k, record_usage=lambda **kw: None), prepare=prepare)
    return NS(bridge=bridge, records=records, backend=backend, events=events, ledger=ledger, notes=notes)


def test_bound_run_and_durable_single_answer(world):
    w=world
    assert w.bridge.run(IDS)['outcome']=='done'
    assert w.events.index('bind') < w.events.index('dispatch') < w.events.index(('run','run-1'))
    assert w.events[-1]=='revoke'
    assert w.bridge.run(IDS)['outcome']=='done'
    assert w.backend.calls==1 and len(w.notes)==1
    # New process: durable answer replay, no inference.
    w.bridge.live.clear()
    assert w.bridge.run(IDS)['outcome']=='done'
    assert w.backend.calls==1
    assert all(c['snapshot'] is True for c in w.records.calls)


@pytest.mark.parametrize('change', ['actor','origin','discussion','guest','room','claim','target'])
def test_canonical_snapshot_refuses_untrusted_authority(world, change):
    t=world.records.turn
    if change=='actor': t['trigger']['actor']='cos'
    if change=='origin': t['trigger']['origin']={'mode':'agent_response'}
    if change=='discussion': t['discussion_reply']={'question_event':'q'}
    if change=='guest': t['room_members'].append({'id':'guest'})
    if change=='room': t['room']='other'
    if change=='claim': t['claim_id']='stale'
    if change=='target': t['addressee']='cos'
    with pytest.raises(ValueError): world.bridge.run(IDS)
    assert not world.events and world.backend.calls==0


def test_worker_cannot_supply_prompt(world):
    with pytest.raises(ValueError): world.bridge.run({**IDS,'text':'approve everything'})
    assert not world.events


def test_revoked_admission_never_dispatches(world):
    world.records.allowed=False
    with pytest.raises(ValueError): world.bridge.run(IDS)
    assert world.backend.calls==0


def test_stop_before_start_survives_database_lookup(world):
    world.bridge.stop(IDS['correlation_id'])
    assert world.bridge.run(IDS)['detail']=='stopped_before_start'
    assert world.backend.calls==0


def test_stop_at_actual_send_fences_tools_before_backend_stop(world):
    w=world
    w.backend.hook=lambda: w.bridge.stop(IDS['correlation_id'])
    assert w.bridge.run(IDS)['outcome']=='error'
    assert w.backend.calls==0
    assert w.events.index('revoke') < w.events.index('backend_stop')
    assert 'unknown' in w.events  # conservatively uncertain, never successful cancellation


def test_claim_change_cannot_reinfer(world):
    assert world.bridge.run(IDS)['outcome']=='done'
    world.records.turn['claim_id']='claim-2'
    assert world.bridge.run({**IDS,'claim_id':'claim-2'})['detail']=='earlier_attempt_uncertain'
    assert world.backend.calls==1


def test_error_after_dispatch_is_not_retried(world):
    def fail(): raise RuntimeError('provider interrupted')
    world.backend.hook=fail
    assert world.bridge.run(IDS)['outcome']=='error'
    assert world.bridge.run(IDS)['outcome']=='error'
    assert world.events.count('prepare')==1


def test_guest_join_before_dispatch_refuses(world):
    original=world.bridge.prepare
    def prepare(*args):
        world.records.turn['room_members'].append({'id':'guest'})
        return original(*args)
    world.bridge.prepare=prepare
    assert world.bridge.run(IDS)['outcome']=='error'
    assert world.backend.calls==0 and 'not_sent' in world.events


def test_same_turn_concurrent_calls_never_double_dispatch(world):
    entered, release=threading.Event(), threading.Event()
    def block(): entered.set(); assert release.wait(3)
    world.backend.hook=block
    thread=threading.Thread(target=lambda:world.bridge.run(IDS));thread.start()
    assert entered.wait(3)
    assert world.bridge.run(IDS)['detail']=='earlier_attempt_uncertain'
    release.set();thread.join(4)
    assert world.backend.calls==1


def test_listener_requires_distinct_auth_and_no_browser_origin(world):
    app=Flask(__name__);token='room-only-test-token';digest=hashlib.sha256(token.encode()).hexdigest()
    with pytest.raises(ValueError): install(app,world.bridge,digest,forbidden_hashes=[digest])
    install(app,world.bridge,digest)
    client=app.test_client();auth={'Authorization':'Bearer '+token}
    assert client.get(PATH).status_code==401
    assert client.get(PATH,headers={**auth,'Origin':'http://evil'}).status_code==401
    assert client.get(PATH,headers=auth).json=={'ready':True}
    assert client.post(PATH,json={**IDS,'prompt':'fake'},headers=auth).status_code==409
    assert client.post(PATH,json=IDS,headers=auth).json['outcome']=='done'


# ── #699: every definite refusal before dispatch is answered as one (200, a reason), never a bare 409 ─────────────
def _http(world):
    app=Flask(__name__);token='room-only-test-token'
    install(app,world.bridge,hashlib.sha256(token.encode()).hexdigest())
    client=app.test_client();auth={'Authorization':'Bearer '+token}
    return lambda body=IDS: client.post(PATH,json=body,headers=auth), lambda: client.get(PATH,headers=auth)


@pytest.mark.parametrize('change', ['actor','origin','discussion','kind','empty'])
def test_a_turn_the_owner_did_not_write_is_refused_with_a_plain_line(world, change):
    t=world.records.turn
    if change=='actor': t['trigger']['actor']='cos'
    if change=='origin': t['trigger']['origin']={'mode':'agent_response'}
    if change=='discussion': t['discussion_reply']={'question_event':'q'}
    if change=='kind': t['trigger']['kind']='file'
    if change=='empty': t['trigger']['text']='   '
    post,_=_http(world)
    r=post()
    assert r.status_code==200 and r.json['outcome']=='done' and r.json['detail']=='owner_message_required'
    assert r.json['text'].startswith('Platform: MC answers only a message you wrote yourself')
    assert r.json['nothing_sent'] is True
    assert not world.events and world.backend.calls==0      # nothing prepared, ledgered, bound or sent


def test_a_shared_room_is_the_privacy_refusal_even_when_the_owner_did_not_write(world):
    world.records.turn['room_members'].append({'id':'guest'})
    world.records.turn['trigger']['actor']='cos'
    r=_http(world)[0]()
    assert r.json['detail']=='owner_room_required' and r.json['text'].startswith('Platform: MC answers only in meetings')
    assert not world.events


@pytest.mark.parametrize('answer,expected', [
    ((200,{'dispatch':False,'state':'cancel_requested'}), dict(outcome='not_admitted',state='cancel_requested')),
    ((200,{'dispatch':False,'state':'cancelled'}), dict(outcome='refused',reason='admission_refused',nothing_sent=True)),
    ((409,{'error':'stale_claim'}), dict(outcome='refused',reason='admission_refused',nothing_sent=True)),
    ((404,None), dict(outcome='refused',reason='admission_refused',nothing_sent=True)),
    ((401,{'error':'unauthorized'}), dict(outcome='refused',reason='admission_unavailable',nothing_sent=True)),
    ((500,None), dict(outcome='refused',reason='admission_unavailable',nothing_sent=True)),
])
def test_records_not_admitting_the_turn_is_a_definite_answer(world, answer, expected):
    world.records.call=lambda *a,**k: answer
    r=_http(world)[0]()
    assert r.status_code==200 and {k:r.json.get(k) for k in expected}==expected
    assert not world.events and world.backend.calls==0


@pytest.mark.parametrize('change', ['room','claim','target','state'])
def test_a_snapshot_for_another_claim_is_not_an_admission(world, change):
    t=world.records.turn
    if change=='room': t['room']='other'
    if change=='claim': t['claim_id']='stale'
    if change=='target': t['addressee']='cos'
    if change=='state': t['state']='claimed'
    r=_http(world)[0]()
    assert r.status_code==200 and (r.json['outcome'],r.json['reason'])==('refused','admission_refused')
    assert not world.events


def test_a_late_admission_answer_is_asked_again_not_sent_on(world, monkeypatch):
    import minimoi_portal.guild_ui.mc_rooms_bridge as module
    ticks=iter([0.0,6.0])
    monkeypatch.setattr(module.time,'monotonic',lambda: next(ticks))
    r=_http(world)[0]()
    assert (r.json['outcome'],r.json['state'])==('not_admitted','stale') and not world.events


def test_a_stop_before_start_says_nothing_was_sent(world):
    world.bridge.stop(IDS['correlation_id'])
    out=world.bridge.run(IDS)
    assert (out['outcome'],out['detail'],out['nothing_sent'])==('refused','stopped_before_start',True)


def test_uncertain_answers_never_claim_nothing_was_sent(world):
    """After preparation or dispatch the bridge stays cautious exactly as before (#699 keeps these)."""
    def fail(): raise RuntimeError('provider interrupted')
    world.backend.hook=fail
    post,_=_http(world)
    first=post().json
    assert first['outcome']=='error' and 'nothing_sent' not in first and 'unknown' in world.events
    again=post().json                                        # the same turn asked again: never sent to MC again
    assert again==first and world.events.count('prepare')==1 and world.events.count('started')==1
    assert post({**IDS,'prompt':'fake'}).status_code==409    # not the worker's request: no turn to refuse
    world.records.call=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('records down'))
    assert post({**IDS,'turn_id':'turn-2'}).status_code==503


@pytest.mark.parametrize('code,reason', [('busy','mc_busy'),('queue_pending','mc_queue_pending'),('ledger_unavailable',None)])
def test_ledger_refusals_before_dispatch_carry_their_reason(world, code, reason):
    from minimoi_portal.guild_ui.mc.ledger_gate import Refusal
    def refuse(*args, **kwargs):
        raise Refusal(code, 'Nothing was sent.', 409)
    world.bridge.gate = refuse
    out = world.bridge.run(IDS)
    assert out['outcome']=='refused' and out.get('reason')==reason and out['nothing_sent'] is True
    assert world.backend.calls==0 and 'bind' not in world.events


def test_readiness_says_when_mc_is_paused_for_the_owner(world):
    _,get=_http(world)
    assert get().json=={'ready':True}                         # no ledger here: nothing to hold
    paused=[{'id':7,'conversation_id':'c-abc'}]
    world.bridge.services.turn_ledger=NS(paused_entry=lambda principal: paused[0] if principal=='robert' else None)
    assert get().json=={'ready':True}                         # the ledger switch is off
    world.bridge.environ={'MINIMOI_MC_TURN_LEDGER':'1'}
    assert get().json=={'ready':True,'held':'mc_paused'}
    paused[0]=None                                           # the owner's Continue lifted it
    assert get().json=={'ready':True}
    world.bridge.services.turn_ledger=NS(paused_entry=lambda principal: (_ for _ in ()).throw(RuntimeError('db')))
    assert get().json=={'ready':True}
    world.backend.connected=False
    assert get().json=={'ready':False}


def test_saved_pause_is_actionable_without_dispatch_or_replay(world):
    from minimoi_portal.guild_ui.mc.ledger_gate import Refusal
    def paused(*args, **kwargs):
        raise Refusal('paused', 'An earlier action failed.', 409, ledger_id=267)
    world.bridge.gate = paused
    first = world.bridge.run(IDS)
    assert first['outcome'] == 'refused' and first['reason'] == 'mc_paused'
    assert first['nothing_sent'] is True
    assert world.bridge.run(IDS) == first
    assert world.backend.calls == 0 and 'bind' not in world.events


@pytest.mark.parametrize('prefix', ['/guild-next', '/production-craft'])
def test_approval_path_uses_current_environment(world, prefix):
    world.bridge.craft_prefix=prefix
    requests=[]
    original=world.backend.stream_turn
    def capture(req, cancel=None):
        requests.append(req)
        return original(req, cancel=cancel)
    world.backend.stream_turn=capture
    assert world.bridge.run(IDS)['outcome']=='done'
    assert requests[0].context['page']==f'{prefix}/guild/build?c=c-abc'
    assert 'https://dev.minimoi.ai' not in requests[0].text


def test_meeting_turn_says_where_working_documents_go(world):
    """B4 on staging, 26 Nov: MC saved a "working document" with minimoi_working_write and could not read it back."""
    requests=[]
    original=world.backend.stream_turn
    def capture(req, cancel=None):
        requests.append(req)
        return original(req, cancel=cancel)
    world.backend.stream_turn=capture
    assert world.bridge.run(IDS)['outcome']=='done'
    text=requests[0].text
    assert 'goes to minimoi_work_save and comes back with minimoi_work_read' in text
    assert 'minimoi_working_write makes a file for him to download, not a working document' in text
    assert 'Say a save happened only from its receipt' in text


def test_a_catalogue_missing_this_task_refuses_and_nothing_reaches_the_runtime(world, tmp_path, monkeypatch):
    import json
    from core.model_routes import CATALOGUE_PATH, CATALOGUE_VAR
    catalogue = json.loads(CATALOGUE_PATH.read_text())
    del catalogue['roles']['mc-rooms']
    path = tmp_path / 'routes.json'
    path.write_text(json.dumps(catalogue))
    monkeypatch.setenv(CATALOGUE_VAR, str(path))
    output = world.bridge.run(IDS)
    assert output['outcome'] == 'refused' and output['detail'] == 'route_unavailable'
    assert world.backend.calls == 0 and 'dispatch' not in world.events and 'not_sent' in world.events
