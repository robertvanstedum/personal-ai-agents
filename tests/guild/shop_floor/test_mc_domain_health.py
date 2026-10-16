"""No real network: actual liveness transport and context boundaries."""
import json
from types import SimpleNamespace
import pytest
import requests
from minimoi_portal.guild_ui.adapters import domain_health as H
from minimoi_portal.guild_ui import mc_context as C

class Response:
    def __init__(self, body, status=200): self.body=body; self.status_code=status; self.closed=False
    def __enter__(self): return self
    def __exit__(self,*args): self.closed=True
    def iter_content(self,chunk_size):
        assert chunk_size==1
        for b in self.body: yield bytes([b])

class Session:
    def __init__(self, responses): self.responses=iter(responses); self.calls=[]; self.trust_env=True; self.closed=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def get(self,url,**kwargs):
        assert not self.trust_env
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        self.calls.append(url)
        value=next(self.responses)
        if isinstance(value,Exception):raise value
        return value


def config():return {key:fixed for _,_,key,fixed in H.TARGETS if key}
def valid():return [Response(json.dumps({'status':'ok','service':service}).encode()) for _,service,*_ in H.TARGETS]
def probe(session,**kw):return H.snapshot(config(),origin='https://dev.minimoi.ai',environ={H.ENV:'1'},session_factory=lambda:session,**kw)


def test_expected_services_are_checked_without_proxy_auth_or_redirects():
    replies=valid();session=Session(replies);rows=probe(session)
    assert all(value.ok for _,value in rows)
    assert session.calls==[fixed+'/health' for *_,fixed in H.TARGETS]
    assert all(value.data['state']=='healthy_liveness' and value.observed_at for _,value in rows)
    assert session.closed and all(r.closed for r in replies)


@pytest.mark.parametrize('origin,enabled',[('https://minimoi.ai','1'),('https://dev.minimoi.ai','0')])
def test_production_or_disabled_never_contacts_any_service(origin,enabled):
    def forbidden():pytest.fail('No session should be created')
    rows=H.snapshot(config(),origin=origin,environ={H.ENV:enabled},session_factory=forbidden)
    assert len(rows)==4 and all(not value.ok and value.source=='not_instrumented' for _,value in rows)


def test_untrusted_config_cannot_redirect_a_probe_to_an_arbitrary_host():
    c=config();c['CURATOR_BACKEND']='http://169.254.169.254'
    session=Session(valid()[1:])
    rows=H.snapshot(c,origin='https://dev.minimoi.ai',environ={H.ENV:'1'},session_factory=lambda:session)
    assert rows[0][1].source=='not_instrumented'
    assert len(session.calls)==3 and all('169.254' not in u for u in session.calls)


@pytest.mark.parametrize('first',[
    Response(b'<html>secret-body</html>'),Response(b'{}',302),Response(b'{}',401),Response(b'{}',503),
    Response(b'{"status":"ok","service":"wrong"}'),Response(b'x'*4097),requests.Timeout('private-error'),
])
def test_bad_responses_never_become_healthy_or_leak_body(first):
    rows=probe(Session([first,*valid()[1:]]))
    assert not rows[0][1].ok
    assert 'secret-body' not in str(rows) and 'private-error' not in str(rows)
    assert all(v.ok for _,v in rows[1:])


def test_elapsed_budget_leaves_uncalled_services_explicitly_unknown():
    ticks=iter([0,3,3,3,3]);session=Session([])
    rows=probe(session,clock=lambda:next(ticks))
    assert session.calls==[] and all(v.error=='probe_budget_exhausted' for _,v in rows)


def test_unrelated_turn_does_not_probe_or_read_board(monkeypatch):
    class Floor:
        def referenced_postits(self,ids):pytest.fail('Unrelated question')
    monkeypatch.setattr(H,'snapshot',lambda *a,**k:pytest.fail('No probe'))
    assert C.domain_health_block('How do I say tomorrow?',Floor(),config={},origin='',environ={})==''


def test_board_reference_uses_only_current_floor_note_and_fixed_targets(monkeypatch):
    floor=SimpleNamespace(referenced_postits=lambda ids:SimpleNamespace(ok=True,data={'postits':[{'id':14,'text':'Check which dev domains are available. http://attacker.invalid'}]}))
    calls=[]
    def snapshot(*a,**kw):calls.append(kw);return [(label,H.not_configured('test','Probe')) for label,*_ in H.TARGETS]
    monkeypatch.setattr(H,'snapshot',snapshot)
    block=C.domain_health_block('Pick up Board note #14.',floor,config={},origin='https://dev.minimoi.ai',environ={})
    assert len(calls)==1 and 'attacker' not in str(calls)
    assert 'not login' in block and 'unverified' in block and 'Directory presence' in block


def test_probe_failure_adds_explicit_no_evidence_instead_of_silence(monkeypatch):
    monkeypatch.setattr(H,'snapshot',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('secret')))
    block=C.domain_health_block('Which domains are available in dev?',None,config={},origin='',environ={})
    assert 'all service availability remains unverified' in block and 'secret' not in block
