"""Read-only serving boundaries preserve dates, identities and failure contracts."""
import json
import os
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path
import pytest
from domains.curator import briefing_queries as queries,briefing_views as views,landing_queries,archive_queries

NOW=datetime(2026,10,29,12,tzinfo=timezone.utc)


def enrich(raw,now):return views.enrich_articles(raw,now,time_ago=views.time_ago,strip_summary=views.strip_summary)


def test_briefing_display_copies_records_preserves_hooks_and_rank_policy():
    raw=[{'rank':9,'final_score':30,'summary':'<b>Evidence</b> &amp; context','published':'2026-10-29T11:00:00Z'},
         {'final_score':-3,'summary':'','published':None}]
    display=enrich(raw,NOW)
    assert display[0]['score_pct']==100 and display[1]['score_pct']==0
    assert display[0]['summary']=='Evidence & context' and display[0]['time_ago']=='1h ago'
    assert display[0]['rank']==9 and 'rank' not in display[1]
    api=views.enrich_articles(raw,NOW,time_ago=lambda *a:'clock hook',strip_summary=lambda text:'summary hook',ranked=True)
    assert [x['rank'] for x in api]==[1,2] and api[0]['time_ago']=='clock hook'
    assert raw[0]['summary']=='<b>Evidence</b> &amp; context' and raw[0]['rank']==9
    assert display[0] is not raw[0]


def test_briefing_date_contracts_missing_invalid_and_stale(tmp_path):
    path=tmp_path/'latest.json';clock=lambda:NOW
    assert queries.load_briefing(path,clock=clock,datetime_type=datetime,enrich=enrich) is None
    path.write_text('{invalid')
    with pytest.raises(views.InvalidBriefing):
        queries.load_briefing(path,clock=clock,datetime_type=datetime,enrich=enrich)
    path.write_text(json.dumps([{'title':'Old','briefing_date':'2020-01-02','briefing_model':'mechanical'}]))
    result=queries.load_briefing(path,clock=clock,datetime_type=datetime,enrich=enrich)
    assert result[-1]=='2020-01-02' and result[3]=='mechanical'
    path.write_text(json.dumps([{'briefing_date':'invalid'}]))
    stamp=datetime(2021,2,3,12,tzinfo=timezone.utc).timestamp();os.utime(path,(stamp,stamp))
    assert queries.load_briefing(path,clock=clock,datetime_type=datetime,enrich=enrich)[-1]=='2021-02-03'
    assert queries.latest_briefing_date(path,datetime_type=datetime)=='—'
    path.write_text('[{}]');os.utime(path,(stamp,stamp))
    assert queries.latest_briefing_date(path,datetime_type=datetime)=='Wednesday, February 3, 2021'
    path.write_text('[]');assert queries.latest_article_count(path)==0


@pytest.mark.parametrize('text,expected',[('[]',[]),('{}',[]),('{invalid',[]),('[{"title":"Radar"}]',[{'title':'Radar'}])])
def test_radar_requires_list(tmp_path,text,expected):
    p=tmp_path/'radar.json';p.write_text(text);assert queries.load_radar(p)==expected


def test_landing_status_counts_and_inherited_total_key_behavior(tmp_path):
    root=tmp_path/'threads';root.mkdir()
    for n,status in enumerate(['active','active-pull','paused','dormant','closed']):
        p=root/str(n);p.mkdir();(p/'thread.json').write_text(json.dumps({'status':status}))
    assert landing_queries.topics_summary(root)=={'total':5,'active':2,'dormant':2}
    path=tmp_path/'leanings.json';path.write_text(json.dumps({'leanings':[{}, {'state':'leaning'},{'state':'hold'}]}))
    assert landing_queries.leanings_summary(path)=={'total':3,'question':1,'leaning':1,'hold':1}
    path.write_text(json.dumps([{'state':'total'}]))
    assert landing_queries.leanings_summary(path)['total']==2  # existing behavior, not policy repair


def test_archive_queries_dates_ids_bookmarks_and_metadata(tmp_path):
    archive=tmp_path/'archive';archive.mkdir()
    for name in ('curator_2026-10-29-a.html','curator_2026-10-29-z.html','curator_2026-10-28.html'):(archive/name).touch()
    (archive/'curator_2026-10-29.json').write_text('[{},{}]')
    (archive/'curator_2026-10-28.json').write_text('{invalid')
    assert archive_queries.daily_editions(archive)==[
        {'date':'2026-10-29','count':2,'file':'curator_2026-10-29-z.html'},
        {'date':'2026-10-28','count':0,'file':'curator_2026-10-28.html'}]
    hist=tmp_path/'history.json';hist.write_text(json.dumps({'a':{'bookmarked':True,'first_seen':'2020','title':'A'},'b':{'bookmarked':False}}))
    assert archive_queries.bookmarked_sources(hist)==[{'date':'2020','title':'A','source':'','url':''}]
    scans=tmp_path/'scans';scans.mkdir();md=scans/'longidentifier-title.md'
    md.write_text('# A title\n**Source:** Example\n**Date:** 2026-10-29\n')
    assert archive_queries.scan_metadata(md)['hash_id']=='longidentifier'
    assert archive_queries.scans(scans,scan_metadata=archive_queries.scan_metadata)[0]['url']=='research/scan/longidentifier'
    dives=tmp_path/'dives';dives.mkdir();(dives/'a-topic-deeper-dive-001.md').write_text('# DEEPER DIVE: A topic\nGenerated: 2026-10-29')
    assert archive_queries.dives(dives)[0]['topic']=='a-topic'
    result=archive_queries.scans_dives_data(scans,dives,scan_metadata=archive_queries.scan_metadata)
    assert result[0][0]['href']=='/research/dive-result/a-topic-deeper-dive-001'
    assert result[1][0]['href']=='/research/scan/longidentifier'
    (tmp_path/'intelligence_20261029.json').write_text('{invalid')
    (tmp_path/'intelligence_weekly20261029.json').write_text('{}')
    assert archive_queries.observations(tmp_path)==[{'date':'2026-10-29','count':0}]


def test_query_modules_cold_import_without_application_or_site_packages(tmp_path):
    root=Path(__file__).resolve().parents[1]
    code='''import sys
from domains.curator import briefing_queries,briefing_views,landing_queries,archive_queries,selection_rules
assert 'flask' not in sys.modules and 'anthropic' not in sys.modules
assert 'domains.curator.curator_server' not in sys.modules
'''
    result=subprocess.run([sys.executable,'-S','-c',code],cwd=tmp_path,env=dict(os.environ,PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert list(tmp_path.iterdir())==[]


def test_query_failure_boundaries_keep_partial_vs_empty_results(tmp_path):
    good=tmp_path/'good.json';good.write_text('{"status":"active"}')
    bad=tmp_path/'bad.json';bad.write_text('{invalid')
    class Root:
        def glob(self,pattern):return iter([good,bad])
    assert landing_queries.topics_summary(Root())=={'total':1,'active':1,'dormant':0}
    def metadata(path):
        if path==bad:raise ValueError('synthetic read failure')
        return {'title':'Good','date':'2026-01-01','source':'Example','hash_id':'good'}
    assert archive_queries.scans(Root(),scan_metadata=metadata)==[]
    history=tmp_path/'history.json';history.write_text(json.dumps({'good':{'bookmarked':True,'title':'Good'},'bad':None}))
    assert archive_queries.bookmarked_sources(history)==[{'date':'','title':'Good','source':'','url':''}]


def test_real_briefing_api_and_archive_routes_use_temporary_inputs(tmp_path,monkeypatch):
    from domains.curator import curator_server as server
    data=tmp_path/'data';data.mkdir();research=tmp_path/'research';research.mkdir()
    monkeypatch.setattr(server,'REPO_ROOT',tmp_path);monkeypatch.setattr(server,'_DATA_DIR',data)
    monkeypatch.setattr(server,'_RESEARCH_DATA',research);monkeypatch.setattr(server,'HISTORY_PATH',tmp_path/'history.json')
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(server,'datetime',Clock)
    article={'title':'Synthetic latest','published':'2026-10-29T11:00:00Z','final_score':10,'summary':'<b>Brief</b>', 'briefing_date':'2026-10-28'}
    (data/'curator_latest.json').write_text(json.dumps([article]))
    archive=tmp_path/'curator_archive';archive.mkdir()
    (archive/'curator_2020-01-02.json').write_text(json.dumps([{**article,'title':'Archived'}]))
    (archive/'curator_2020-01-02.html').write_text('Synthetic edition')
    client=server.app.test_client()
    response=client.get('/api/briefing');assert response.status_code==200
    actual=response.get_json()
    assert actual['date']=='2026-10-29'  # existing API request-date contract
    assert actual['articles'][0]['rank']==1 and actual['articles'][0]['score_pct']==50
    assert actual['articles'][0]['summary']=='Brief' and actual['articles'][0]['time_ago']=='1h ago'
    assert server._load_briefing_articles()[-1]=='2026-10-28'  # page preserves persisted date
    assert client.get('/api/briefing?date=2020-01-02').get_json()['articles'][0]['title']=='Archived'
    assert client.get('/api/briefing?date=1999-01-01').status_code==404
    assert client.get('/archive').status_code==200
    assert b'2020-01-02' in client.get('/archive').data
    assert client.get('/scans-dives').status_code==200
    (data/'curator_latest.json').write_text('{invalid')
    assert client.get('/api/briefing').status_code==503
    for url in ('/briefing', '/curator_briefing.html', '/curator_latest.html'):
        response = client.get(url)
        assert response.status_code == 503, url
        assert b'Briefing temporarily unavailable' in response.data

    for raw_score in ('bad', True, float('inf'), 10 ** 400):
        (data/'curator_latest.json').write_text(json.dumps([{**article, 'raw_score': raw_score}]))
        for url in ('/api/briefing', '/briefing', '/curator_briefing.html', '/curator_latest.html'):
            assert client.get(url).status_code == 503, (url, raw_score)
    for extra in ({}, {'raw_score': None}, {'raw_score': 7.25}):
        (data/'curator_latest.json').write_text(json.dumps([{**article, **extra}]))
        for url in ('/briefing', '/curator_briefing.html', '/curator_latest.html'):
            response = client.get(url)
            assert response.status_code == 200, url
            assert (b'N/A' if extra.get('raw_score') is None else b'7.2') in response.data


def test_server_route_map_matches_parent():
    from domains.curator.curator_server import app
    expected=json.loads((Path(__file__).parent/'fixtures/curator_selection_parent.json').read_text())['routes']
    names={row[1] for row in expected}
    actual=[[r.rule,r.endpoint,sorted(set(r.methods)-{'HEAD','OPTIONS'})] for r in app.url_map.iter_rules() if r.endpoint in names]
    assert sorted(actual)==sorted(expected)
