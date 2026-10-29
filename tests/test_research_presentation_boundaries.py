"""Frozen parent output, hostile text and real adapter journeys."""
import copy
import hashlib
import json
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import pytest
from domains.curator.research.legacy_scan_render import render_scan
from domains.curator.research.scan_catalog import collect_catalog
from domains.curator.research.scan_index_render import render_catalog
from domains.curator.research.dive_render import render_challenger, render_dive

FIXTURE=json.loads((Path(__file__).parent/'fixtures/research/batch4_parent_outputs.json').read_text())

def sha(text): return hashlib.sha256(text.encode()).hexdigest()


def test_safe_legacy_scan_and_challenger_match_parent_bytes():
    assert sha(render_scan(*FIXTURE['scan_args'],today=FIXTURE['date']))==FIXTURE['scan_sha256']
    assert sha(render_challenger(FIXTURE['challenger']))==FIXTURE['challenger_sha256']


def test_index_empty_and_populated_match_parent_bytes(tmp_path):
    assert sha(render_catalog([],[]))==FIXTURE['empty_index_sha256']
    scans=tmp_path/'scans';dives=tmp_path/'dives';scans.mkdir();dives.mkdir()
    (scans/'abc12-example.md').write_text(FIXTURE['scan_document'])
    (dives/'topic-001.md').write_text(FIXTURE['dive_document'])
    rows,threads=collect_catalog(scans,dives,now=lambda:datetime(2026,10,29,12))
    before=copy.deepcopy((rows,threads))
    assert sha(render_catalog(rows,threads))==FIXTURE['index_sha256']
    assert (rows,threads)==before


class Tags(HTMLParser):
    def __init__(self,html):
        super().__init__();self.tags=[];self.feed(html)
    def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))


def test_legacy_page_treats_untrusted_fields_as_text_and_checks_links():
    args=copy.deepcopy(FIXTURE['scan_args'])
    args[0]='<img src=x onerror=bad>'
    args[1]={'title':'<script>bad</script>','source':'<svg onload=bad>', 'url':'javascript:bad()'}
    args[2]='<img src=x>';args[3]='<script>bad</script>'
    args[4]='## Findings\n\n<script>bad()</script>\n\n**Important**\n\n## Sources\n- <img src=x onerror=bad>'
    page=render_scan(*args,today='2026-10-29')
    tags=Tags(page).tags
    assert not any(tag in ('script','img','svg') for tag,_ in tags)
    assert not any(key.startswith('on') for _,attrs in tags for key in attrs)
    assert any(tag=='a' and attrs.get('href')=='#' for tag,attrs in tags)
    assert '<strong>Important</strong>' in page and '&lt;script&gt;' in page


def test_index_encodes_artifact_paths_and_escapes_record_text():
    row={'title':'<img src=x>','source':'<script>bad</script>','hash_id':'a"/><img','date':datetime(2026,10,29)}
    thread={'topic':'<svg>','excerpt':'<img>','stats':'<script>','stem':'a"/><script','date':datetime(2026,10,29)}
    page=render_catalog([row],[thread]);tags=Tags(page).tags
    assert not any(t in ('img','svg') for t,_ in tags)
    # The template has its own fixed script, but metadata cannot add another.
    assert sum(t=='script' for t,_ in tags)==1
    links=[a['href'] for t,a in tags if t=='a' and 'href' in a]
    assert '/research/scan/a%22%2F%3E%3Cimg' in links
    assert '/research/dive-result/a%22%2F%3E%3Cscript' in links


def test_challenger_counts_and_dive_content_are_escaped():
    ch={**FIXTURE['challenger'],'accepted_count':'<img src=x>','rejected_count':'<svg>'}
    assert '<img' not in render_challenger(ch) and '<svg' not in render_challenger(ch)
    assert render_challenger({'enabled':False,'show_process':True})==''
    page=render_dive('# Header\n\n<script>bad</script>\n\n- Item','<img>','')
    assert '<script>' not in page and '<img>' not in page and '<li>Item</li>' in page


def test_fake_provider_scan_to_index_journey(tmp_path,monkeypatch):
    from domains.curator import curator_feedback as adapter
    monkeypatch.setattr(adapter,'REPO_ROOT',tmp_path)
    monkeypatch.setattr(adapter,'_RESEARCH_DATA',tmp_path/'research-data')
    monkeypatch.setattr(adapter,'get_anthropic_api_key',lambda:'synthetic-key')
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text='## Findings\n\nUseful **evidence**.')],
                               usage=SimpleNamespace(input_tokens=10,output_tokens=20))
    monkeypatch.setattr(adapter,'Anthropic',lambda **kw:SimpleNamespace(messages=SimpleNamespace(create=create)))
    article={'title':'A useful article','source':'Example','url':'https://example.com/article'}
    markdown,cost,path=adapter.generate_scan('abc12',article,'Evidence')
    assert len(calls)==1 and calls[0]['model']=='claude-sonnet-4-5' and calls[0]['max_tokens']==4000
    assert cost==pytest.approx(0.00033) and Path(path).read_text()==markdown
    assert '<strong>evidence</strong>' in Path(path).with_suffix('.html').read_text()
    adapter.regenerate_scans_index()
    index=(Path(path).parent/'index.html').read_text()
    assert '/research/scan/abc12' in index and 'A useful article' in index


def test_index_missing_directory_does_not_publish(tmp_path,monkeypatch):
    from domains.curator import curator_feedback as adapter
    monkeypatch.setattr(adapter,'REPO_ROOT',tmp_path)
    adapter.regenerate_scans_index()
    assert list(tmp_path.iterdir())==[]


def test_dive_route_preserves_responses_and_bad_sidecar(tmp_path,monkeypatch):
    from flask import Flask
    from domains.curator import research_routes as routes
    monkeypatch.setattr(routes,'DIVES_DIR',tmp_path)
    app=Flask('batch4');app.register_blueprint(routes.research_bp);client=app.test_client()
    assert client.get('/research/dive-result/invalid!').status_code==400
    assert client.get('/research/dive-result/missing').status_code==404
    (tmp_path/'topic.md').write_text('# A title\n\nUseful *detail*.')
    (tmp_path/'topic-challenger.json').write_text('{invalid')
    response=client.get('/research/dive-result/topic')
    assert response.status_code==200 and b'<em>detail</em>' in response.data


def test_full_dive_page_matches_parent_bytes():
    assert sha(render_dive(FIXTURE['dive_raw'], 'A useful Dive', '')) == FIXTURE['dive_sha256']


def test_research_route_map_matches_parent():
    from flask import Flask
    from domains.curator.research_routes import research_bp
    app=Flask('batch4-route-map');app.register_blueprint(research_bp)
    actual=[]
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith('research.'):
            methods=sorted(set(rule.methods)-{'HEAD','OPTIONS'})
            actual.append([rule.rule,rule.endpoint.removeprefix('research.'),methods])
    assert sorted(actual)==sorted(FIXTURE['routes'])
