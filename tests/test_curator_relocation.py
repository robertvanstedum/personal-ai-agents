"""Canonical source with synthetic private state; no provider/network calls."""
import importlib,json,sys,types
from pathlib import Path
from flask import Flask
import pytest
from domains.curator import research_routes as routes
from domains.curator.research.paths import ResearchPaths


def test_namespaced_modules_do_not_import_generic_agent(monkeypatch):
 poison=types.ModuleType('agent');monkeypatch.setitem(sys.modules,'agent',poison)
 for name in ('threads','feedback','candidates','leanings','source_utils','run','research','observe','thread_expiry'):
  module=importlib.import_module('domains.curator.research.agent.'+name)
  assert '/domains/curator/research/agent/' in module.__file__
  assert module.ROOT.name=='research' and '_NewDomains' not in str(module.ROOT)


def test_existing_web_and_static_urls_serve_canonical_files():
 app=Flask('synthetic-canonical-source');app.register_blueprint(routes.research_bp);client=app.test_client()
 for path in ('/research/observe','/research/candidates','/research/save','/research/leanings','/research/static/css/nav.css','/research/static/js/annotations.js'):
  response=client.get(path);assert response.status_code==200


def test_leaning_tip_still_uses_repository_config_not_code_depth(tmp_path,monkeypatch):
 paths=ResearchPaths(tmp_path,tmp_path/'private-data',tmp_path/'private-state')
 paths.tips.parent.mkdir(parents=True);paths.tips.write_text(json.dumps({'curator.leanings':{'active':True,'text':'SYNTHETIC ROOT TIP'}}))
 monkeypatch.setattr(routes,'PATHS',paths)
 app=Flask('tips');app.register_blueprint(routes.research_bp)
 assert b'SYNTHETIC ROOT TIP' in app.test_client().get('/research/leanings').data


def test_dive_subprocess_uses_code_file_repo_cwd_and_private_log(tmp_path,monkeypatch):
 calls=[];data=tmp_path/'data';monkeypatch.setattr(routes,'DATA_ROOT',data)
 def popen(command,**kw):calls.append((command,kw));return types.SimpleNamespace(pid=42,poll=lambda:None)
 monkeypatch.setattr(routes.subprocess,'Popen',popen)
 process,log=routes._start_dive_job('synthetic',data/'dives/synthetic.md')
 assert process.pid==42 and log.parent==data/'dive_jobs'
 assert calls[0][0][1]==str(routes.RESEARCH_ROOT/'scripts/generate_dive.py')
 assert calls[0][1]['cwd']==str(routes._REPO_ROOT)
 calls[0][1]['stdout'].close()


def test_direct_cli_bootstrap_works_without_legacy_tree(tmp_path):
 import shutil,subprocess,os
 repo=tmp_path/'isolated-repository';here=Path(__file__).resolve().parents[1]
 # Copy only this code package; never unrelated domains or their private data.
 (repo/'domains/curator').mkdir(parents=True)
 shutil.copytree(here/'domains/curator/research',repo/'domains/curator/research')
 (repo/'domains/__init__.py').write_text('')
 (repo/'domains/curator/__init__.py').write_text('')
 managed=str(here)
 (repo/'sitecustomize.py').write_text("import core.get_secret\ndef refused(*a,**k):raise AssertionError('provider or secret call forbidden')\ncore.get_secret.get_secret=refused\nimport requests\nrequests.Session.request=refused\nimport socket\nsocket.create_connection=refused\nimport subprocess\nsubprocess.Popen=refused\n")
 env={**os.environ,'PYTHONPATH':str(repo)+os.pathsep+managed,'PYTHONDONTWRITEBYTECODE':'1','AWS_EC2_METADATA_DISABLED':'true'}
 for rel in ('agent/run.py','agent/research.py','agent/threads.py','agent/thread_expiry.py','scripts/generate_dive.py'):
  result=subprocess.run([sys.executable,str(repo/'domains/curator/research'/rel),'--help'],env=env,capture_output=True,text=True,timeout=15,cwd=tmp_path)
  assert result.returncode==0,(rel,result.stderr)
  assert 'usage:' in result.stdout.lower()
 assert not (repo/'_NewDomains').exists() and not (repo/'data').exists()


def test_start_session_argv_resolves_actual_script_with_repo_cwd(tmp_path,monkeypatch):
 from domains.curator.research.agent import threads
 paths=ResearchPaths(tmp_path,tmp_path/'data',tmp_path/'state');paths.config.parent.mkdir(parents=True)
 paths.config.write_text(json.dumps({'session_searches':{'synthetic':[]}}))
 monkeypatch.setattr(routes,'PATHS',paths);monkeypatch.setattr(routes,'DATA_ROOT',paths.data);monkeypatch.setattr(routes,'STATE_ROOT',paths.state)
 monkeypatch.setattr(routes,'_RUN_STATE_PATH',paths.data/'run_state.json');monkeypatch.setattr(routes,'_run_state_active',lambda:False)
 monkeypatch.setattr(threads,'load_thread',lambda _:None)
 calls=[]
 def launch(args,**kw):calls.append((args,kw));return types.SimpleNamespace(pid=42)
 monkeypatch.setattr(routes.subprocess,'Popen',launch)
 app=Flask('canonical-start');app.register_blueprint(routes.research_bp)
 response=app.test_client().post('/api/research/run-session',json={'topic':'synthetic'})
 assert response.status_code==200 and len(calls)==1
 args,kw=calls[0];assert Path(args[1]).is_absolute() and Path(args[1]).is_file()
 assert Path(args[1])==routes.RESEARCH_ROOT/'agent/research.py' and kw['cwd']==str(routes._REPO_ROOT)
 kw['stdout'].close()


def test_actual_curator_server_registers_canonical_research_blueprint():
 server=importlib.import_module('domains.curator.curator_server')
 assert 'research' in server.app.blueprints
 assert '/api/research/run-session' in {str(rule) for rule in server.app.url_map.iter_rules()}


@pytest.mark.parametrize('legacy',[True,False])
def test_completed_dive_and_error_logs_resolve_retained_paths_in_memory(tmp_path,monkeypatch,legacy):
 from domains.curator.research.agent import threads
 paths=ResearchPaths(tmp_path/'repo',tmp_path/'data',tmp_path/'state')
 dive=paths.data/'dives/synthetic.md';dive.parent.mkdir(parents=True);dive.write_text('# synthetic retained dive')
 log=paths.data/'dive_jobs/synthetic.log';log.parent.mkdir();log.write_text('synthetic bounded failure detail')
 def stored(p):return '/app/_NewDomains/research-intelligence/'+paths.saved_label(p) if legacy else str(p)
 state=paths.data/'dd_run_state.json';original={'topic':'synthetic','pid':42,'output_path':stored(dive),'log_path':stored(log)};state.write_text(json.dumps(original))
 monkeypatch.setattr(routes,'PATHS',paths);monkeypatch.setattr(routes,'_DD_STATE_PATH',state);monkeypatch.setattr(routes,'_dd_proc',types.SimpleNamespace(poll=lambda:0))
 record=types.SimpleNamespace(deeper_dive_generated=False,deeper_dive_path='');saved=[]
 monkeypatch.setattr(threads,'load_thread',lambda _:record);monkeypatch.setattr(threads,'save_thread',lambda topic,row:saved.append(row))
 assert routes._dive_error_excerpt(original)=='synthetic bounded failure detail'
 assert routes._scan_error_excerpt(original)=='synthetic bounded failure detail'
 app=Flask('legacy-output');app.register_blueprint(routes.research_bp)
 response=app.test_client().get('/api/research/generate-dive/status')
 assert response.status_code==200 and response.json['failed'] is False
 assert record.deeper_dive_generated and record.deeper_dive_path=='data/dives/synthetic.md' and saved==[record]
 assert response.json['output_path']==original['output_path']
 assert dive.read_text()=='# synthetic retained dive'
