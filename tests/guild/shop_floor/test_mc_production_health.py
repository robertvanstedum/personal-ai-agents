"""Actual fixed-target transport with synthetic responses only; no real network."""
import pytest
from minimoi_portal.guild_ui.adapters import domain_health as H
from test_mc_domain_health import Session,Response,config,valid
PROD='https://minimoi.ai'
FLAGS={H.PRODUCTION_ENV:'1','MINIMOI_GUILD_NEXT':'1','MINIMOI_GUILD_PRODUCTION':'1','MINIMOI_GUILD_CHAT':'cos'}
def test_explicit_production_profile_reads_only_fixed_internal_targets():
 session=Session(valid());rows=H.snapshot(config(),origin=PROD,environ=FLAGS,session_factory=lambda:session)
 assert all(v.ok and v.evidence.startswith('internal_production_health:') for _,v in rows)
 assert session.calls==[fixed+'/health' for *_,fixed in H.TARGETS]
 assert session.closed and not session.trust_env
@pytest.mark.parametrize('missing',list(FLAGS))
def test_each_production_gate_required_without_any_transport(missing):
 env=dict(FLAGS);env.pop(missing)
 rows=H.snapshot(config(),origin=PROD,environ=env,session_factory=lambda:pytest.fail('No transport allowed'))
 assert all(not v.ok and v.source=='not_instrumented' for _,v in rows)
@pytest.mark.parametrize('origin',['https://www.minimoi.ai','http://minimoi.ai','https://minimoi.ai.attacker.invalid','https://minimoi.ai/path','https://minimoi.ai:443','https://other.invalid','http://127.0.0.1:5001',''])
def test_non_exact_deployment_origins_refused(origin):
 assert H.profile_for(origin,FLAGS)is None
 H.snapshot(config(),origin=origin,environ=FLAGS,session_factory=lambda:pytest.fail('No transport allowed'))
def test_profile_flags_do_not_enable_the_other_environment():
 assert H.profile_for(PROD,{H.ENV:'1'})is None
 assert H.profile_for('https://dev.minimoi.ai',FLAGS)is None
 assert H.profile_for('https://dev.minimoi.ai',{H.ENV:'1'})=='dev'
@pytest.mark.parametrize('changed',['http://169.254.169.254','https://curator:8766','http://curator:8766/redirect','http://user:pass@curator:8766'])
def test_production_config_cannot_turn_into_arbitrary_url(changed):
 c=config();c['CURATOR_BACKEND']=changed;session=Session(valid()[1:]);rows=H.snapshot(c,origin=PROD,environ=FLAGS,session_factory=lambda:session)
 assert not rows[0][1].ok and rows[0][1].source=='not_instrumented';assert len(session.calls)==3
 assert all(changed not in url for url in session.calls)
def test_production_failures_never_become_public_journey_success():
 session=Session([Response(b'{}',302),*valid()[1:]])
 rows=H.snapshot(config(),origin=PROD,environ=FLAGS,session_factory=lambda:session)
 assert not rows[0][1].ok and rows[0][1].data is None;assert all(v.data=={'state':'healthy_liveness','http_status':200} for _,v in rows[1:]);assert len(session.calls)==4

def test_context_scope_does_not_mislabel_production_as_dev_or_user_journey(monkeypatch):
 from minimoi_portal.guild_ui import mc_context as C
 monkeypatch.setattr(H,'snapshot',lambda *a,**kw:[('Curator',H.live_ok({'state':'healthy_liveness'},'internal_production_health:curator',fresh_for_s=0))])
 block=C.domain_health_block('Which domains are available?',None,config=config(),origin=PROD,environ=FLAGS)
 assert 'internal_production_health:curator' in block and "this deployment's fixed internal /health" in block
 assert 'not login, public accessibility' in block and 'Only internal dev' not in block
