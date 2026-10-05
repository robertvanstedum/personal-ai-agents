import hashlib
import json
import os

import pytest

from services.mc_operations.relay import launch


def files(tmp_path):
    config=tmp_path/'config.json';token=tmp_path/'caller';source=tmp_path/'relay.mjs'
    config.write_text(json.dumps({'agents':{'entries':{'mc-agent':{}}},'gateway':{'bind':'loopback',
        'auth':{'mode':'token','token':'upstream-'+'a'*32},'http':{'endpoints':{'chatCompletions':{'enabled':True}}}}}))
    token.write_text('caller-'+'b'*32);source.write_text('// synthetic')
    config.chmod(0o600);token.chmod(0o600)
    return config,token,source,hashlib.sha256(source.read_bytes()).hexdigest()


def test_relay_keeps_operator_secret_out_of_args(tmp_path,monkeypatch):
    c,t,s,h=files(tmp_path);captured={}
    monkeypatch.setattr(os,'execve',lambda binary,args,env:captured.update(binary=binary,args=args,env=env))
    launch(c,t,s,h,'/node')
    assert 'upstream-' not in str(captured['args'])
    assert captured['env']['MC_RELAY_TARGET']=='http://127.0.0.1:18789'
    assert captured['env']['MC_RELAY_TOKEN']!=captured['env']['MC_OPENCLAW_GATEWAY_TOKEN']


@pytest.mark.parametrize('failure',['public_file','wrong_source','lan','missing_mc','http_off','same_key'])
def test_refuses_incomplete_or_unsafe_runtime(tmp_path,monkeypatch,failure):
    c,t,s,h=files(tmp_path);doc=json.loads(c.read_text())
    if failure=='public_file':t.chmod(0o644)
    if failure=='wrong_source':s.write_text('// changed')
    if failure=='lan':doc['gateway']['bind']='lan'
    if failure=='missing_mc':doc['agents']['entries']={}
    if failure=='http_off':doc['gateway']['http']={}
    if failure=='same_key':t.write_text(doc['gateway']['auth']['token'])
    c.write_text(json.dumps(doc))
    monkeypatch.setattr(os,'execve',lambda *_:pytest.fail('must refuse before launch'))
    with pytest.raises(ValueError):launch(c,t,s,h,'/node')
