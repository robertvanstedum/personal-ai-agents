import json
from minimoi_portal.guild_ui import scheduled_jobs

def registry(tmp_path,name):
 path=tmp_path/(name+'.json');path.write_text(json.dumps({'schema_version':1,'jobs':[{'id':name,'name':name,'host':'mac','schedule':{'text':'daily05:30 Chicago','kind':'daily','at':'05:30','tz':'America/Chicago'},'expected_every_s':86400,'missed_after_s':180000,'stuck_after_s':600,'status_file':name+'.status.json','owner':'robert','runbook':'test'}]}));return path

def test_operate_reads_same_explicit_private_registry_as_scheduler(tmp_path,monkeypatch):
 path=registry(tmp_path,'runtime-job');monkeypatch.setenv('MINIMOI_JOBS_REGISTRY',str(path))
 assert [j['name'] for j in scheduled_jobs.overview(root=tmp_path,host='mac')['jobs']]==['runtime-job']

def test_caller_registry_override_keeps_precedence(tmp_path,monkeypatch):
 env=registry(tmp_path,'runtime-job');explicit=registry(tmp_path,'explicit-job');monkeypatch.setenv('MINIMOI_JOBS_REGISTRY',str(env))
 assert [j['name'] for j in scheduled_jobs.overview(root=tmp_path,registry_path=explicit,host='mac')['jobs']]==['explicit-job']
