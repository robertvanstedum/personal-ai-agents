"""Parent-policy parity and the real curate adapter with disposable inputs."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from domains.curator.selection_rules import select_articles

FROZEN=json.loads((Path(__file__).parent/'fixtures/curator_selection_parent.json').read_text())


@pytest.mark.parametrize('case',FROZEN['cases'],ids=lambda case:str(FROZEN['cases'].index(case)))
def test_selection_matches_parent_order_mutations_callbacks_and_diagnostics(case):
    pool=copy.deepcopy(case['input']);identities={e['id']:e for e in pool};trace=[];logs=[]
    def domain(url):trace.append(['domain',url]);return url.split('/')[2]
    def interest(e,active):trace.append(['interest',e['id']]);return e['ib']
    def priority(e,active):trace.append(['priority',e['id']]);e['priority_checked']=True;return e['pb']
    args=dict(case['args']);args['source_trust']=args.pop('_source_trust');args['x_post_cap']=args.pop('X_POST_CAP')
    result=select_articles(pool,**args,domain_from_url=domain,interest_boost_fn=interest,
        priorities_boost_fn=priority,report=lambda *a,**kw:logs.append(' '.join(map(str,a))))
    assert [e['id'] for e in result]==case['selected']
    assert pool==case['pool'] and logs==case['logs']
    assert all(e is identities[e['id']] for e in result)
    assert hashlib.sha256(json.dumps(trace).encode()).hexdigest()==case['trace_sha256']


def test_curate_keeps_pipeline_effects_and_exposes_original_mutated_pool(tmp_path,monkeypatch):
    from domains.curator import curator_rss_v2 as runtime
    from scripts.x import x_to_article
    entries=[{'hash_id':str(n),'source':f'Source{n}','link':f'https://example.com/{n}',
              'title':f'Article {n}','content_type':'rss' if n==0 else 'x_bookmark'} for n in range(3)]
    monkeypatch.setattr(runtime,'FEEDS',{'fixture':'https://example.invalid'})
    monkeypatch.setattr(runtime,'fetch_feed',lambda *args:entries)
    monkeypatch.setattr(runtime.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(x_to_article,'load_x_bookmark_articles',lambda:[])
    monkeypatch.setattr(runtime,'_fetch_web_search_candidates',lambda **kw:[])
    monkeypatch.setattr(runtime,'_load_source_trust',lambda:{})
    monkeypatch.setattr(runtime,'load_active_interests',lambda:{})
    monkeypatch.setattr(runtime,'load_priorities',lambda:[])
    monkeypatch.setattr(runtime,'load_user_profile',lambda:'')
    monkeypatch.setattr(runtime,'_compute_age_multiplier',lambda entry:1.0)
    monkeypatch.setattr(runtime,'score_entry_mechanical',lambda entry:{'score':5,'raw_score':5,'category':'other','method':'mechanical'})
    monkeypatch.setattr(runtime,'X_POST_CAP',1);monkeypatch.setattr(runtime,'X_POST_MIN_SCORE',0)
    monkeypatch.setattr(runtime,'_DATA_DIR',tmp_path)
    (tmp_path/'curator_preferences.json').write_text(json.dumps({'curation_settings':{'serendipity_reserve':.5}}))
    selected,pool=runtime.curate(top_n=3,return_pool=True)
    assert len(selected)==2 and sum(e['content_type']=='x_bookmark' for e in selected)==1
    assert all(any(e is original for original in entries) for e in pool)
    assert all('final_score' in e for e in pool)
    assert entries[2]['serendipity_pick'] is True  # unselected candidates retain phase annotations
