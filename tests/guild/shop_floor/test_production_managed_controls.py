"""Production opt-in remains separate from legacy speech and schema readiness."""
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from flask import Flask
from minimoi_portal import guild_mounts as M
ROOT=Path(__file__).resolve().parents[3]
FULL={'MINIMOI_GUILD_CHAT':'cos','MINIMOI_GUILD_VOICE_PRODUCTION':'1','MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION':'1','MINIMOI_MC_VOICE':'1','MINIMOI_DICTATION':'1','MINIMOI_MC_MANAGED_REALTIME':'1'}
@pytest.mark.parametrize('missing',['MINIMOI_GUILD_CHAT','MINIMOI_GUILD_VOICE_PRODUCTION','MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION'])
def test_each_existing_and_new_admission_gate_required(missing):
 raw=dict(FULL);raw.pop(missing);out=M.production_environ(raw);assert 'MINIMOI_MC_MANAGED_REALTIME' not in out
@pytest.mark.parametrize('chat',['stub','openclaw','grok','',None])
def test_no_relay_or_stub_selection_admits_managed_voice(chat):
 assert 'MINIMOI_MC_MANAGED_REALTIME' not in M.production_environ(dict(FULL,MINIMOI_GUILD_CHAT=chat))
def test_explicit_optin_preserves_original_flags_and_old_speech():
 out=M.production_environ(FULL);assert all(out[k]=='1' for k in ('MINIMOI_MC_MANAGED_REALTIME','MINIMOI_MC_VOICE','MINIMOI_DICTATION'));assert out['MINIMOI_GUILD_MC']=='cos';assert out['MINIMOI_GUILD_MC_TURNS']=='1'
 raw=dict(FULL);raw.pop('MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION');old=M.production_environ(raw);assert old['MINIMOI_MC_VOICE']=='1' and old['MINIMOI_DICTATION']=='1';assert 'MINIMOI_MC_MANAGED_REALTIME' not in old
 assert 'MINIMOI_MC_FILES_HOST_ROOT' not in M.production_environ(dict(FULL,MINIMOI_MC_FILES_HOST_ROOT='/untrusted'))
@pytest.mark.parametrize('origin',['https://www.minimoi.ai','http://unknown.invalid','https://dev.minimoi.ai',''])
def test_managed_optin_does_not_bypass_existing_production_host_gate(origin):
 assert not M.is_production_activation(origin,dict(FULL,MINIMOI_GUILD_NEXT='1',MINIMOI_GUILD_PRODUCTION='1'))
def test_compose_defaults_do_not_activate_managed_features_or_guess_allowance():
 services=yaml.safe_load((ROOT/'docker-compose.prod.yml').read_text())['services'];portal=dict(v.split('=',1) for v in services['portal']['environment']);backend=dict(v.split('=',1) for v in services['cos-scheduler']['environment'])
 for flag in ('MINIMOI_MC_BOARD_OUTCOMES','MINIMOI_GUILD_MANAGED_VOICE_PRODUCTION','MINIMOI_MC_MANAGED_REALTIME','MINIMOI_COS_MANAGED_REALTIME'):assert portal[flag]=='${'+flag+':-0}'
 assert portal['MINIMOI_MC_DOMAIN_HEALTH']=='0' and portal['MINIMOI_ROLE']=='production'
 assert portal['MINIMOI_COS_WRITE_GUARD']=='${MINIMOI_COS_WRITE_GUARD:-}'  # blank preserves automatic dev CSRF guard
 for env in (portal,backend):assert env['MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO']=='${MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO:-}'
 assert backend['MINIMOI_COS_MANAGED_REALTIME']=='${MINIMOI_COS_MANAGED_REALTIME:-0}'
 assert portal['MINIMOI_MC_VOICE']=='1' and portal['MINIMOI_DICTATION']=='1'
@pytest.mark.parametrize('allowance',['','5000000'])
def test_explicit_flag_does_not_register_routes_without_required_schema(tmp_path,allowance):
 from core.realtime_voice.budget import VoiceBudget
 from minimoi_portal.guild_ui.mc_managed_realtime import install
 b=VoiceBudget('sqlite:///'+str(tmp_path/'isolated.db'));b.ensure_schema();app=Flask('no-schema');app.extensions['mc']={'services':SimpleNamespace(mc=SimpleNamespace(kind='cos'))};calls=[]
 env=dict(FULL,MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO=allowance)
 assert not install(app,'mc',environ=env,budget=b,provider_factory=lambda:calls.append('provider'))
 assert not calls and not [r.rule for r in app.url_map.iter_rules() if 'managed' in r.rule]
