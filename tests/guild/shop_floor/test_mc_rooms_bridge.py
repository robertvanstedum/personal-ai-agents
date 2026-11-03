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


def test_saved_pause_is_actionable_without_dispatch_or_replay(world):
    from minimoi_portal.guild_ui.mc.ledger_gate import Refusal
    def paused(*args, **kwargs):
        raise Refusal('paused', 'An earlier action failed.', 409, ledger_id=267)
    world.bridge.gate = paused
    first = world.bridge.run(IDS)
    assert first['outcome'] == 'refused' and first['reason'] == 'mc_paused'
    assert world.bridge.run(IDS) == first
    assert world.backend.calls == 0 and 'bind' not in world.events
