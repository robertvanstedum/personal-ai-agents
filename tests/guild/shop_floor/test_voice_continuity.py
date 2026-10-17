"""Synthetic same-conversation continuity and literal chat copy; no models/IO."""
from types import SimpleNamespace
import uuid
import pytest
from flask import Flask
from minimoi_portal.guild_ui import mc_voice_transcripts as voice
from minimoi_portal.guild_ui.stores import Author
from core.realtime_voice.transcripts import note_parts
from core.realtime_voice.controlled_session import SessionRefused

SID='8134d69e-218f-4f07-b14b-cb2da8088015'
OTHER='1029cba1-a6c8-48fe-a03c-7d2eb137c6cf'
ASK='Can you write that in the chat for me?'

class Floor:
    def __init__(self): self.rows={}
    def add_note(self,key,text,author,**context):
        old=self.rows.get(key)
        if old and old['text']!=text:return SimpleNamespace(outcome='conflict',value=None)
        row=old or dict(request_id=key,text=text,who=author.id,author_kind=author.kind,
                        context=context,created_at='2026-10-17T12:00:00Z')
        self.rows[key]=row
        return SimpleNamespace(outcome='kept',value=row,repeated=old is not None)
    def get_note(self,key):return self.rows.get(key)
    def list_notes(self,*,limit):
        assert limit==200
        return SimpleNamespace(ok=True,data={'notes':list(self.rows.values())[-200:],'more':False})

def retained(floor,text='Für heute ist alles erledigt.',*,sid=SID,partial=False,role='assistant',item='one'):
    for key,body in note_parts(sid,role,item,0,text,partial,limit=2000):
        floor.add_note(key,body,Author(voice.AUTHOR_PREFIX+sid,'platform','Voice transcript'),page=voice.PAGE)

def test_ordinary_german_followup_gets_recent_evidence_without_magic_words():
    floor=Floor();retained(floor)
    assert not voice.WANTED.search('did we do german practice')
    block=voice.block('did we do german practice',floor,include_recent=True)
    assert 'Für heute ist alles erledigt.' in block and 'DATA' in block
    assert 'not complete native' in block and SID not in block
    assert voice.block('hello',Floor(),include_recent=True)==''
    assert 'No retained' in voice.block('transcript',Floor(),include_recent=True)

@pytest.mark.parametrize('text',[ASK,'Please copy what you just said to the chat.'])
def test_exact_retained_copy_has_real_saved_id_and_stable_replay(text):
    floor=Floor();retained(floor);checks=[]
    out=voice.copy_to_chat(text,floor,'operation',session_id=SID,scope_check=lambda:checks.append(1))
    assert out['operation']==dict(type='voice_chat_copy',saved=True,repeated=False)
    assert out['reply'].endswith('Für heute ist alles erledigt.')
    assert floor.get_note(out['reply_note_id'])['text']==out['reply']
    retained(floor,'Different later words',item='two')
    replay=voice.copy_to_chat(text,floor,'operation',session_id=SID,scope_check=lambda:None)
    assert replay['reply']==out['reply'] and replay['operation']['repeated']
    assert len(checks)==2

@pytest.mark.parametrize('text',['Do not write that in the chat for me.',
    'Example: can you write that in the chat for me?', '"Can you write that in the chat for me?"',
    'Can you write that in the chat for me and translate it?', 'Can you write a new German phrase in chat?'])
def test_no_quoted_negated_or_expanded_copy_authority(text):
    floor=Floor()
    assert not voice.chat_copy_requested(text)
    assert voice.copy_to_chat(text,floor,'op',session_id=SID,scope_check=lambda:pytest.fail('scope')) is None
    assert not floor.rows

@pytest.mark.parametrize('kind',['absent','older-session','partial','multipart','revoked','forged'])
def test_no_guess_or_cross_session_leak_and_recheck_before_save(kind):
    floor=Floor()
    if kind=='older-session':retained(floor,'OTHER SESSION',sid=OTHER)
    elif kind=='partial':retained(floor,partial=True)
    elif kind=='multipart':retained(floor,'a'*3000)
    elif kind=='forged':
        retained(floor);next(iter(floor.rows.values()))['author_kind']='owner'
    elif kind=='revoked':retained(floor)
    before=dict(floor.rows);checks=[]
    def scope():
        checks.append(1)
        if kind=='revoked' and len(checks)>1:raise SessionRefused('scope revoked')
    with pytest.raises(SessionRefused):
        voice.copy_to_chat(ASK,floor,'op',session_id=SID,scope_check=scope)
    assert floor.rows==before


def test_real_context_hook_reads_only_current_fresh_owned_floor(monkeypatch):
    from minimoi_portal.guild_ui import api,mc_context,mc_registers,mc_regression,release_context
    import minimoi_portal.guild_ui as guild
    floor=Floor();retained(floor)
    conv={'id':'c-test','principal':'robert','notes_floor':'this','archived':False}
    monkeypatch.setattr(api,'cfg',lambda:{})
    monkeypatch.setattr(guild,'feature_environ',lambda:{})
    monkeypatch.setattr(release_context,'block',lambda _: '')
    monkeypatch.setattr(mc_registers,'host_context',lambda *a,**kw:'')
    monkeypatch.setattr(mc_regression,'block',lambda *a,**kw:'')
    monkeypatch.setattr(mc_context,'board_block',lambda *a,**kw:'')
    monkeypatch.setattr(mc_context,'github_block',lambda *a,**kw:'')
    monkeypatch.setattr(api,'_floor',lambda:floor)
    monkeypatch.setattr(api,'_conv_floor',lambda current:floor)
    fresh=dict(conv)
    monkeypatch.setattr(api,'_conversations',lambda:SimpleNamespace(get=lambda cid,principal:fresh))
    with Flask(__name__).app_context():
        result=api._with_context('did we do german practice','did we do german practice',conv,'robert')
        assert 'Für heute ist alles erledigt.' in result
        for changed in ({'principal':'foreign'},{'archived':True},{'record_mode':'off_record'},{'notes_floor':'another'}):
            fresh.clear();fresh.update(conv);fresh.update(changed)
            assert 'Für heute ist alles erledigt.' not in api._with_context('hi','hi',conv,'robert')
