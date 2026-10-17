"""Actual managed worker/provider-event hook with synthetic provider only."""
from test_mc_managed_realtime import ready, start

USAGE={'input_tokens':2,'output_tokens':2,
       'input_token_details':{'text_tokens':1,'audio_tokens':1,'image_tokens':0},
       'output_token_details':{'text_tokens':1,'audio_tokens':1}}
ASR={'type':'tokens','input_tokens':1,'output_tokens':1,
     'input_token_details':{'text_tokens':0,'audio_tokens':1}}


def test_actual_asr_routes_chat_copy_keeps_exact_assistant_without_native(ready):
    r=ready
    original=r.notes.add_note
    def add(key,text,author,**context):
        result=original(key,text,author,**context)
        result.value['context']=context
        return result
    r.notes.add_note=add
    r.notes.list_notes=lambda **kw:type('Result',(),{'ok':True,'data':{'notes':list(r.notes.rows.values())[-200:]}})()
    r.notes.get_note=lambda key:r.notes.rows.get(key)
    s,_=start(r)
    s.observe({'type':'input_audio_buffer.committed','item_id':'first'})
    s.observe({'type':'conversation.item.input_audio_transcription.completed','item_id':'first',
               'transcript':'How do you say all done for now?','usage':ASR})
    s.observe({'type':'response.created','response':{'id':'reply'}})
    s.observe({'type':'response.done','response':{'id':'reply','status':'completed','usage':USAGE,
        'output':[{'id':'spoken-assistant','type':'message','role':'assistant',
                   'content':[{'type':'output_audio','transcript':'Für heute ist alles erledigt.'}]}]}})
    assert not s.closed
    assert s.canonical_command_match('Can you write that in the chat for me?','c-123456abcdef')
    s.observe({'type':'input_audio_buffer.committed','item_id':'copy'})
    event={'type':'conversation.item.input_audio_transcription.completed','item_id':'copy',
           'transcript':'Can you write that in the chat for me?','usage':ASR}
    s.observe(event);s.worker.submit(lambda:None).result(timeout=2)
    copied=[row for row in r.notes.rows.values() if row['who']=='voice_chat_copy']
    assert len(copied)==1 and copied[0]['text'].endswith('Für heute ist alles erledigt.')
    assert not r.calls and not s.closed
    before=len(r.notes.rows)
    s.observe(event);s.worker.submit(lambda:None).result(timeout=2)
    assert len(r.notes.rows)==before
