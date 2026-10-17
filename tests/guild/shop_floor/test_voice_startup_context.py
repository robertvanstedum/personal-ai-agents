"""Real build_config and run_mc_turn path; synthetic stores/relay only."""
from test_mc_managed_realtime import ready, start
from core.realtime_voice.transcripts import note_parts
from minimoi_portal.guild_ui.stores import Author
from minimoi_portal.guild_ui import mc_voice_transcripts as voice

SID='8134d69e-218f-4f07-b14b-cb2da8088015'


def test_real_host_build_config_contains_same_conversation_prior_voice_at_start(ready):
    r=ready;configs=[]
    # Existing prior session generated evidence, without starting any new call.
    for key,text in note_parts(SID,'assistant','past',0,'Ich bin erledigt.',False,limit=2000):
        r.notes.add_note(key,text,Author(voice.AUTHOR_PREFIX+SID,'platform','Voice'),page=voice.PAGE)
    r.notes.add_note('typed','We practised German.',Author('owner','owner','Owner'))
    # Forged foreign owner row in this synthetic floor must not enter ordinary
    # kept-chat startup evidence. Real floors are independently owner-scoped.
    r.notes.add_note('foreign','FOREIGN PRIVATE',Author('foreign','owner','Foreign'))
    r.provider.create=lambda sdp,config,*args:(configs.append(config) or ('answer','rtc_synthetic'))
    s,_=start(r)
    instructions=configs[0]['instructions']
    assert 'Ich bin erledigt.' in instructions and 'We practised German.' in instructions
    assert 'FOREIGN PRIVATE' not in instructions and 'no new owner speech observed yet' in instructions
    assert 'HISTORICAL DATA' in instructions and 'playback unverified' in instructions
    assert len(instructions.encode())<=24000 and SID not in instructions
    assert not r.calls


def test_startup_scope_is_rechecked_after_read_and_never_constructs_provider_session(ready):
    r=ready
    original=r.notes.list_notes
    def archived(**kw):
        out=original(**kw);r.convs.archived=True;return out
    r.notes.list_notes=archived
    response=r.client.post('/guild-next/api/v1/mc/voice/managed/session',json={
        'provider':'openai','conversation_id':'c-123456abcdef','sdp':'synthetic-offer'},headers=r.headers)
    assert response.status_code==503 and not r.provider.sessions and not r.calls


# Import real portal+SQLite fixtures, not invented g/request/mode attestations.
from test_mc_turns import turned, _keep, _ask
from floor_helpers import load_portal
from floor_db_helpers import floor_db, floored


def test_real_typed_run_mc_turn_labels_current_input_separately_from_past_voice(turned):
    client=turned.owner();token=turned.csrf(client)
    note=_keep(client,token,'hi how are you')
    response=_ask(client,token,note['request_id'])
    assert response.status_code==200 and response.json['status']=='answered'
    content=turned.extra['runtime'].sent[-1]['body']['messages'][0]['content']
    assert 'hi how are you' in content
    assert 'CURRENT INPUT MODALITY: text submitted through chat' in content
    assert 'Keyboard versus Dictate is not verified' in content
    assert 'not this current input' in content
    assert 'CURRENT INPUT MODALITY: managed voice ASR' not in content
    assert response.json['reply_note']['text']=='Item 12 is in build.'



def test_startup_unicode_budget_fits_real_configuration():
    from test_voice_continuity import Floor,retained
    from core.realtime_voice.controlled_config import configuration
    floor=Floor()
    for i in range(10):retained(floor,'ä😀'*400,item='prior'+str(i))
    prior=voice.startup_context(floor,'owner',scope_check=lambda:None)
    assert len(prior.encode())<9000
    config=configuration('Existing voice partner.\n'+prior)
    assert len(config['instructions'].encode())<=24000
