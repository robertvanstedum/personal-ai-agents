"""Focused synthetic route authorization; no provider/native calls."""
import pytest
from floor_helpers import load_portal, write_headers  # noqa:F401
from floor_db_helpers import floor_db, floored, keyed  # noqa:F401
from minimoi_portal.guild_ui import mc_voice_export as export
from minimoi_portal.guild_ui.conversations import conversations_of

API='/guild-next/api/v1'

def setup(portal):
    client=portal.owner();token=portal.csrf(client)
    response=client.post(API+'/conversations',json=keyed(),headers=write_headers(token))
    assert response.status_code==200
    cid=response.json['conversation']['id']
    return client,token,cid,API+'/conversations/'+cid+'/voice-transcript'

def test_owner_export_and_anonymous_refusal(floored,monkeypatch):
    client,_,cid,url=setup(floored)
    monkeypatch.setattr(export,'render',lambda *a,**kw:dict(text='Retained synthetic text',truncated=False,next_before=None))
    response=client.get(url)
    assert response.status_code==200 and response.text=='Retained synthetic text'
    assert response.mimetype=='text/plain'
    assert 'no-store' in response.headers['Cache-Control']
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert floored.app.test_client().get(url).status_code in (401,403)

@pytest.mark.parametrize('cursor',['0','-1','1.2','x','1'*20])
def test_bad_cursor_is_refused_before_read(floored,monkeypatch,cursor):
    client,_,_,url=setup(floored)
    def forbidden(*a,**kw):raise AssertionError('must not read')
    monkeypatch.setattr(export,'render',forbidden)
    assert client.get(url+'?before='+cursor).status_code==422

@pytest.mark.parametrize('change',['principal','archived','record_mode','notes_floor','scope'])
def test_scope_change_during_read_never_returns_content(floored,monkeypatch,change):
    client,_,cid,url=setup(floored)
    store=conversations_of(floored.app.extensions['guild_ui_next']['services'])
    original=type(store).get; reads=[]
    def changed(self,*args,**kw):
        result=original(self,*args,**kw).copy();reads.append(1)
        if len(reads)>1:result[change]={'principal':'foreign','archived':True,'record_mode':'off_record','notes_floor':'foreign','scope':'changed'}[change]
        return result
    monkeypatch.setattr(type(store),'get',changed)
    monkeypatch.setattr(export,'render',lambda *a,**kw:dict(text='NEVER_RETURN_THIS',truncated=False,next_before=None))
    response=client.get(url)
    assert response.status_code==404 and 'NEVER_RETURN_THIS' not in response.text

def test_archive_and_missing_conversation_refused(floored,monkeypatch):
    client,token,cid,url=setup(floored)
    response=client.post(API+'/conversations/'+cid+'/archive',json=keyed(),headers=write_headers(token))
    assert response.status_code==200
    monkeypatch.setattr(export,'render',lambda *a,**kw:pytest.fail('archived must not read'))
    assert client.get(url).status_code==404
    assert client.get(API+'/conversations/c-000000000000/voice-transcript').status_code==404
