"""Companion uses actual owner guard, shared conversation and scoped embed policy."""
import json
import re
import pytest
from floor_helpers import load_portal
from floor_db_helpers import attach, floor_db

@pytest.fixture
def host(load_portal, floor_db, monkeypatch):
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE', '1')
    portal = load_portal(reserve_pages=False)
    attach(portal, floor_db.store())
    return portal

def page_data(response):
    return json.loads(re.search(r'<script type="application/json" id="guild-page">(.*?)</script>', response.text, re.S).group(1))

def test_embed_reuses_conversation_and_retains_route(host):
    client = host.owner()
    normal = client.get('/guild-next/guild/build')
    embedded = client.get('/guild-next/guild/companion')
    assert embedded.status_code == 200
    assert page_data(normal)['conversation']['id'] == page_data(embedded)['conversation']['id']
    assert page_data(embedded)['urls']['floor'] == '/guild-next/guild/companion'
    assert 'id="portal-nav-bar"' not in embedded.text
    assert 'data-mc-embed="true"' in embedded.text
    assert "frame-ancestors 'self'" in embedded.headers['Content-Security-Policy']
    assert embedded.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert "frame-ancestors 'none'" in normal.headers['Content-Security-Policy']
    assert 'no-store' in embedded.headers['Cache-Control']

@pytest.mark.parametrize('kind', ['client','guest'])
def test_embed_preserves_owner_boundary(host, kind):
    response = getattr(host, kind)().get('/guild-next/guild/companion')
    assert response.status_code in (302,401,403)
    assert 'data-mc-embed="true"' not in response.text

def test_shell_present_only_on_owner_domain_html(host, monkeypatch):
    # Existing dashboard is a real portal route; no backend access.
    response = host.owner().get('/dashboard')
    assert response.status_code == 200
    assert 'id="minimoi-companion"' in response.text
    assert '/guild/ui-assets/' in response.text
    assert 'id="minimoi-companion"' not in host.guest().get('/dashboard').text
    assert 'id="minimoi-companion"' not in host.owner().get('/guild-next/guild/build').text
    assert 'id="minimoi-companion"' not in host.owner().get('/guild-next/api/v1/session').text
    monkeypatch.delenv('MINIMOI_MC_EVERYWHERE')
    assert 'id="minimoi-companion"' not in host.owner().get('/dashboard').text


def test_domain_context_is_allowlisted_and_switch_disables_embed(host, monkeypatch):
    client=host.owner()
    response=client.get('/guild-next/guild/companion/german')
    assert response.status_code==200
    data=page_data(response)
    assert data['area']=='Mein Deutsch'
    assert data['urls']['floor']=='/guild-next/guild/companion/german'
    assert client.get('/guild-next/guild/companion/arbitrary').status_code==404
    monkeypatch.delenv('MINIMOI_MC_EVERYWHERE')
    assert client.get('/guild-next/guild/companion').status_code==404

@pytest.mark.parametrize('path,label', [('/app/german','Mein Deutsch'),('/app/portuguese','Meu Português'),('/app/curator','Curator'),('/app/cos','Chief of Staff'),('/app/iotconnect','IoT Connect')])
def test_each_domain_uses_shared_owner_shell(host,monkeypatch,path,label):
    monkeypatch.setattr(host.module._proxy,'proxy_to',lambda *a,**k:'<!doctype html><html><body><h1>Synthetic domain</h1></body></html>')
    response=host.owner().get(path)
    assert response.status_code==200
    assert response.text.count('id="minimoi-companion"')==1
    assert label+' · your ongoing conversation' in response.text


def test_native_cos_hook_waits_for_both_stops_and_refuses_uncertainty():
    from pathlib import Path
    import subprocess
    source=(Path(__file__).resolve().parents[3]/'domains/cos/templates/cos_ui.html').read_text()
    hook=source[source.index("document.addEventListener('minimoi:companion-opening'"):source.index("window.addEventListener('pagehide'")]
    harness='''const assert=require('node:assert/strict');let handler;const document={addEventListener:(_,h)=>handler=h};
let calls=[],voiceRestartBlocked=false,finish;const dictation={stop:async reason=>calls.push('dictation:'+reason)};
const stopVoice=reason=>{calls.push('voice:'+reason);return new Promise(r=>finish=r)};
'''+hook+'''
(async()=>{let wait;handler({detail:{waitUntil:p=>wait=p}});await Promise.resolve();assert.equal(calls.length,2);
let done=false;wait.then(()=>done=true);await Promise.resolve();assert.equal(done,false);finish();await wait;assert.equal(done,true);
voiceRestartBlocked=true;handler({detail:{waitUntil:p=>wait=p}});await Promise.resolve();finish();await assert.rejects(wait,/verification/);
assert.deepEqual(calls.slice(0,2),['dictation:companion_opened','voice:companion_opened']);})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',harness],check=True,capture_output=True,text=True)
