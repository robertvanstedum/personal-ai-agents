from types import SimpleNamespace
import pytest
from minimoi_portal.guild_ui.board_commands import parse, execute
from minimoi_portal.guild_ui.stores import Author

class Store:
    floor="test-owner-floor"
    def __init__(self): self.rows={}; self.notes={}
    def add_postit(self,text,author,*,idempotency_key):
        old=self.rows.get(idempotency_key)
        if old and old['text']!=text:return SimpleNamespace(outcome='idempotency_mismatch',value=None,repeated=True)
        row=old or {'id':len(self.rows)+1,'text':text,'author':author.id}
        self.rows[idempotency_key]=row
        return SimpleNamespace(outcome='added',value=row,repeated=old is not None)
    def add_note(self,key,text,author,**kwargs):
        self.notes[key]={'text':text,'request_id':key,'author':author.id}
        return SimpleNamespace(outcome='kept',value=self.notes[key])

@pytest.mark.parametrize('text,payload',[
 ('Please make a post-it: Test voice tomorrow','Test voice tomorrow'),
 ('Can you make a note on the Board: Test voice tomorrow','Test voice tomorrow'),
 ('put a post it on the board: Ruf mich morgen an','Ruf mich morgen an'),
 ('Bitte erstelle eine Notiz auf dem Board: Morgen weiterbauen','Morgen weiterbauen'),
])
def test_literal_owner_commands(text,payload):assert parse(text)==payload

@pytest.mark.parametrize('text',[
 'He said make a post-it: test','Do not make a post-it: test',
 'Summarize this document: make a post-it: test','"make a post-it: test"',
 'Can you tell me about Board notes?','save a note: ordinary conversation note',
])
def test_conversation_or_reported_instructions_do_not_create(text):assert parse(text) is None

def test_board_save_returns_real_id_and_replay_never_duplicates():
    board=Store();conversation=Store();kw=dict(board=board,conversation=conversation,principal='robert',turn_key='kept-owner-turn')
    first=execute('make a post-it: Retest voice',**kw)
    again=execute('make a post-it: Retest voice',**kw)
    assert first['operation']['saved'] and first['operation']['id']==1
    assert again['operation']['repeated'] and len(board.rows)==1
    assert 'post-it #1' in first['reply'] and len(conversation.notes)==1
    conflict=execute('make a post-it: Different text',**kw)
    assert conflict['operation']['saved'] is False and len(board.rows)==1

def test_limit_and_redaction_before_board_write():
    board=Store();conversation=Store();kw=dict(board=board,conversation=conversation,principal='robert',turn_key='owner-turn')
    assert not execute('make a post-it: '+'X'*281,**kw)['operation']['saved']
    assert not board.rows
    execute('make a post-it: card 4242 4242 4242 4242',**kw)
    assert '4242' not in next(iter(board.rows.values()))['text']

def test_no_spoofed_author_or_missing_owner():
    kw=dict(board=Store(),conversation=Store(),principal='robert',turn_key='owner-turn')
    with pytest.raises(ValueError):execute('make a post-it: hello',author=Author('other','owner','Other'),**kw)
    with pytest.raises(ValueError):execute('make a post-it: hello',**{**kw,'principal':''})

@pytest.mark.parametrize('text',['Make a post-it on the Board','Please put that on the Board','make a post-it'])
def test_missing_or_ambiguous_payload_asks_without_writing(text):
    board=Store();conversation=Store()
    out=execute(text,board=board,conversation=conversation,principal='robert',turn_key='turn')
    assert out is not None and not out['operation']['saved'] and not board.rows

@pytest.mark.parametrize('text',['make a post-itinerary for tomorrow','erstelle eine Notiz auf dem Boardwalk morgen'])
def test_noun_prefix_is_not_a_board_command(text):assert parse(text) is None

def test_secondary_history_failure_keeps_actual_board_receipt():
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    class Down(Store):
        def add_note(self,*args,**kwargs):raise FloorStoreUnavailable('synthetic')
    board=Store()
    out=execute('make a post-it: Test',board=board,conversation=Down(),principal='robert',turn_key='turn')
    assert out['operation']['saved'] and out['operation']['id']==1 and not out['history_saved']

def test_same_note_key_in_two_conversations_is_two_distinct_owner_requests():
    board=Store();a=Store();b=Store();a.floor='conversation-a';b.floor='conversation-b'
    kw=dict(board=board,principal='robert',turn_key='same-locally-valid-note-key')
    first=execute('make a post-it: First',conversation=a,**kw)
    second=execute('make a post-it: Second',conversation=b,**kw)
    assert first['operation']['id']!=second['operation']['id'] and len(board.rows)==2
