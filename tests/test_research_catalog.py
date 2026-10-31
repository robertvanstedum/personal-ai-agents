"""Catalog and dashboard boundaries use temporary trees and loaded records."""
import json
import math
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from domains.curator.research.scan_catalog import collect_catalog
from domains.curator.research.session_catalog import list_sessions, parse_session_log
from domains.curator.research.dashboard_view import topic_summary, dashboard_payload


def test_catalog_dates_roots_and_order(tmp_path):
    scans=tmp_path/'scans';dives=tmp_path/'dives';scans.mkdir();dives.mkdir()
    (scans/'abc12-first.md').write_text('# First\n**Source:** Example\n**Date:** invalid\n')
    (scans/'def34-second.md').write_text('# Second\n**Source:** Example\n**Date:** 2020-01-01\n')
    (scans/'ignored.md').write_text('# Missing metadata')
    (dives/'topic.md').write_text('# DEEPER DIVE: Topic\nGenerated: 2026-10-29\n2 sessions 3 sources Est. cost: $0.20\n## 1. REVISED FRAMING\n'+('x'*170))
    rows,threads=collect_catalog(scans,dives,now=lambda:datetime(2026,10,29))
    assert [r['hash_id'] for r in rows] == ['abc12','def34']
    assert rows[0]['date']==datetime(2026,10,29)
    assert threads[0]['excerpt']==('x'*160)+'…'
    assert threads[0]['stats']=='2 sessions · 3 sources · $0.20'
    assert collect_catalog(tmp_path/'absent',tmp_path/'absent',now=datetime.now)==([],[])


def test_sessions_merge_both_roots_with_exact_inclusion(tmp_path):
    a=tmp_path/'old';b=tmp_path/'new';a.mkdir();b.mkdir()
    for name in ('topic-002.md','topic-001.md','CONTEXT.md','sources-candidates-x.md','odd_name.md'):(a/name).touch()
    (b/'topic-001.md').touch();(b/'topic-003.md').touch();(b/'directory.md').mkdir()
    assert list_sessions([a,b],pattern=re.compile(r'^[a-z][a-z0-9-]*\.md$'),excluded_prefixes=('sources-candidates-',))==['topic-001','topic-002','topic-003']


def test_session_log_last_row_budget_and_nonfinite_compatibility():
    log=parse_session_log('agent_active: TRUE\n| Date | Session | Duration | Cost | Cumulative | Notes |\n|---|---|---|---|---|---|\n| 2026-10-29 | one | 5m | $2 | $8 | top find: A |\n| bad | bad | 1m | nope | $9 | bad |\n| 2026-10-28 | two | 2m | — | $3 | B |')
    assert log['agent_active'] is True and log['budget_cumulative']==3
    assert [r['session_id'] for r in log['rows']]==['one','two']
    assert math.isnan(parse_session_log('| x | y | z | nan | nan |')['budget_cumulative'])
    assert parse_session_log('')=={'agent_active':False,'budget_cumulative':0.0,'rows':[]}


def test_dashboard_counts_ties_lifecycle_and_actual_active_state():
    log={'one':{'date':'2026-10-29','notes':'top find: first'},'two':{'date':'2026-10-29','notes':'top find: second'}}
    thread=SimpleNamespace(status='retired',expires='2026-11-01',deeper_dive_generated=True,deeper_dive_path='dives/topic.md')
    result=topic_summary('topic',['one','two'],[{'session_id':'one'},{'session_id':'one'},{'session_id':'other'}],
        [{'topic':'topic','status':'candidate'},{'topic':'topic','status':'saved'}],log,thread,1)
    assert result['saved_count']==2 and result['candidate_count']==1 and result['top_find']=='first'
    assert result['status']=='retired' and result['sessions_at_last_dive']==1
    payload=dashboard_payload([result],3,0,18,True)
    assert payload['agent_active'] is True and payload['budget']['pct']==0.0


def test_dashboard_route_loads_both_topic_homes_and_actual_process_state(tmp_path,monkeypatch):
    from flask import Flask
    from domains.curator import research_routes as routes
    from domains.curator.research.agent import threads
    from domains.curator.research.paths import configured
    data=tmp_path/'data';state=tmp_path/'state'
    paths=configured({'CURATOR_RESEARCH_DATA_DIR':str(data),'CURATOR_RESEARCH_STATE_DIR':str(state)},repo=tmp_path/'repo')
    paths.config.parent.mkdir(parents=True)
    paths.config.write_text(json.dumps({'session_searches':{'configured':[]},'budget':{'total_limit':10,'total_warn':8}}))
    old=state/'topics/configured';old.mkdir(parents=True);(old/'configured-001.md').write_text('# Session')
    durable=data/'threads/discovered';(durable/'sessions').mkdir(parents=True)
    (durable/'sessions/discovered-001.md').write_text('# New session')
    (durable/'thread.json').write_text(json.dumps({'id':'discovered','topic':'discovered','opened':'2026-10-29',
        'motivation':'Synthetic question','prior_belief':'Unknown','status':'paused',
        'deeper_dive_generated':True,'deeper_dive_path':'data/dives/discovered.md'}))
    (data/'dives').mkdir();(data/'dives/discovered.md').write_text('# Dive\n3 sessions\n')
    (data/'feedback').mkdir()
    (data/'feedback/article_signals.json').write_text(json.dumps([{'session_id':'configured-001'}]))
    (data/'feedback/query_candidates.json').write_text(json.dumps([{'topic':'discovered','status':'candidate'}]))
    paths.library.mkdir()
    (paths.library/'session-log.md').write_text('agent_active: false\n| 2026-10-28 | configured-001 | 1m | $1 | $1 | top find: stale |\n| 2026-10-29 | configured-001 | 2m | $2 | $3 | top find: replacement |')
    monkeypatch.setattr(routes,'PATHS',paths);monkeypatch.setattr(routes,'STATE_ROOT',state)
    monkeypatch.setattr(routes,'DATA_ROOT',data);monkeypatch.setattr(threads,'THREADS',data/'threads')
    monkeypatch.setattr(routes,'_run_state_active',lambda:True)
    app=Flask('batch4-dashboard');app.register_blueprint(routes.research_bp)
    response=app.test_client().get('/api/research/dashboard')
    assert response.status_code==200
    result=response.get_json();assert result['agent_active'] is True
    assert result['budget']=={'used':3.0,'total':10,'warn':8,'pct':30.0}
    assert [t['topic'] for t in result['topics']]==['configured','discovered']
    first,second=result['topics']
    assert first['saved_count']==1 and first['top_find']=='replacement' and first['last_run']=='2026-10-29'
    assert second['session_count']==1 and second['candidate_count']==1
    assert second['status']=='paused' and second['sessions_at_last_dive']==3
