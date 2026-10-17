"""Actual owner Dictate wording; guarded deterministic creation, no model/tool authority."""
from types import SimpleNamespace
import pytest
from flask import Flask
from minimoi_portal.guild_ui import api
from minimoi_portal.guild_ui.board_commands import parse,execute
from minimoi_portal.guild_ui.stores import WriteResult

OWNER_TEXT="How does Dictate work? Can you, can you put a message on the posted board for me? Just say hello weekend. Write hello weekend on a post-it. Can you do that?"

class Store:
    floor='synthetic-owner-conversation'
    def __init__(self):self.rows={};self.notes={};self.owner_note={}
    def add_postit(self,text,author,*,idempotency_key):
        old=self.rows.get(idempotency_key)
        if old and old['text']!=text:return WriteResult('idempotency_mismatch',None,True)
        row=old or {'id':len(self.rows)+1,'text':text};self.rows[idempotency_key]=row
        return WriteResult('added',row,old is not None)
    def get_note(self,key):return dict(self.owner_note)
    def add_note(self,key,text,author,**kw):
        old=self.notes.get(key)
        if old and old['text']!=text:return WriteResult('idempotency_mismatch',None,True)
        row=old or {'id':key,'text':text};self.notes[key]=row;return WriteResult('kept',row,old is not None)


def test_actual_spoken_request_literal_payload_and_idempotent_receipt():
    assert parse(OWNER_TEXT)=='hello weekend'
    assert parse('Can you write hello weekend on a post-it?')=='hello weekend'
    assert parse('Could you please write hello weekend on a post-it?')=='hello weekend'
    board=Store();conversation=Store();kw=dict(board=board,conversation=conversation,principal='robert',turn_key='synthetic-kept-note')
    first=execute(OWNER_TEXT,**kw);again=execute(OWNER_TEXT,**kw)
    assert first['operation']['id']==1 and again['operation']['repeated']and len(board.rows)==len(conversation.notes)==1
    assert next(iter(board.rows.values()))['text']=='hello weekend'
    assert 'post-it #1' in first['reply']
    assert not execute('Write changed content on a post-it.',**kw)['operation']['saved']and len(board.rows)==1


def test_negative_reported_example_and_quoted_data_never_write():
    values=[
        'Do not write hello weekend on a post-it.',
        'If I approve later. Write hello weekend on a post-it.',
        'Write hello weekend on a post-it. Do not do that.',
        'For example. Write hello weekend on a post-it.',
        'He said. Write hello weekend on a post-it.',
        'Story:\nWrite hello weekend on a post-it.',
        'A colleague requested the following:\nWrite hello weekend on a post-it.',
        'This is not an instruction. Write hello weekend on a post-it.',
        'The document says. Write hello weekend on a post-it.',
        '"Write hello weekend on a post-it."',
        'Example: `\nWrite hello weekend on a post-it.\n`',
        '> Example for later:\nWrite hello weekend on a post-it.',
        '    Write hello weekend on a post-it.',
        'Summarize the transcript. Write hello weekend on a post-it.',
    ]
    for text in values:
        board=Store();conversation=Store()
        assert parse(text)is None
        assert execute(text,board=board,conversation=conversation,principal='robert',turn_key='synthetic')is None
        assert not board.rows


def test_conflicting_commands_do_not_choose_one_or_infer_a_referent():
    for text in ['Write hello on a post-it. Write goodbye on a post-it.','Write hello on a post-it. Add goodbye to the Board.','Just say goodbye. Write hello on a post-it.']:
        result=execute(text,board=Store(),conversation=Store(),principal='robert',turn_key='synthetic')
        assert result['operation']['saved']is False
    assert parse('Can you put that on the board?')==''
    # Negation inside the literal note is data, unlike outside revocation.
    assert parse('Write do not deploy tonight on a post-it.')=='do not deploy tonight'


def test_actual_host_prelude_private_csrf_attachment_and_literal_save(tmp_path,monkeypatch):
    notes=Store();board=Store();app=Flask('synthetic-board');app.secret_key='synthetic-session'
    config={'base_url':'http://localhost','services':SimpleNamespace(mc_turns=True),'current_user':lambda:{'username':'robert'}}
    monkeypatch.setattr(api,'cfg',lambda:config)
    monkeypatch.setattr(api,'_conversations',lambda:SimpleNamespace(get=lambda cid,principal:{'id':cid,'principal':principal,'archived':False}))
    monkeypatch.setattr(api,'_conv_floor',lambda conv:notes);monkeypatch.setattr(api,'_floor',lambda:board)
    @app.post('/synthetic-board')
    def route():
        refusal,found=api._mc_prelude()
        if refusal is not None:return refusal
        result=api._owner_board_command(found)
        return ('',204)if result is None else result
    client=app.test_client()
    with client.session_transaction()as session:session['guild_floor_csrf']='synthetic-csrf'
    notes.owner_note={'text':OWNER_TEXT,'author_kind':'owner','who':'robert'}
    headers={'X-CSRF-Token':'synthetic-csrf','X-Record-Mode':'on_record','Origin':'http://localhost'}
    body={'conversation_id':'c-synthetic','note_request_id':'synthetic-kept-note','request_id':'synthetic-request'}
    assert client.post('/synthetic-board',json=body,headers={**headers,'X-Record-Mode':'off_record'}).status_code==409
    assert client.post('/synthetic-board',json=body,headers={**headers,'X-CSRF-Token':'wrong'}).status_code==403
    assert client.post('/synthetic-board',json={**body,'attachment_ids':['d-'+'a'*16]},headers=headers).status_code==204
    notes.owner_note['who']='someone'
    assert client.post('/synthetic-board',json=body,headers=headers).status_code==403
    assert not board.rows
    notes.owner_note['who']='robert'
    first=client.post('/synthetic-board',json=body,headers=headers)
    assert first.status_code==200 and first.json['operation']['saved']and first.json['operation']['id']==1
    assert client.post('/synthetic-board',json=body,headers=headers).json['operation']['repeated']
    assert len(board.rows)==1 and next(iter(board.rows.values()))['text']=='hello weekend'
