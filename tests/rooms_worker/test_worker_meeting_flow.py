"""Exact owner examples exercise actual Records scheduling and worker delivery."""
from datetime import datetime, timezone

import pytest

from test_worker import env, _load, BACKEND
from rooms_helpers import RECORDS_DIR
from worker_helpers import FakeRelay, Session
from services.rooms_worker.clients import Records
from services.rooms_worker.journal import TurnJournal
from services.rooms_worker.worker import Worker


@pytest.fixture
def pair(env, tmp_path, monkeypatch):
    monkeypatch.setenv('RECORDS_DISCUSSION_HANDOFFS', '1')
    env.prove(); env.join()
    out = tmp_path / 'cos-secrets'
    manage = _load('records_poc_manage_for_tests', 'manage.py')
    manage.provision_rooms(env.store, 'cos', 'Chief of Staff', out, worker='rooms-worker-cos')
    with env.store.connect() as db:
        db.execute("UPDATE teammates SET proven_at=? WHERE principal='cos'", (datetime.now(timezone.utc).isoformat(),))
    relay = FakeRelay()
    cos = Worker(Records(BACKEND, (out/'rooms-worker-cos.token').read_text().strip(), session=Session(env.app)),
                 Records(BACKEND, (out/'cos.token').read_text().strip(), session=Session(env.app)),
                 relay, TurnJournal(tmp_path/'cos-journal'), teammate='cos', agent_id='cos-agent-a')
    assert env.call('POST', f'/rooms/{env.room}/invite', {'actor':'cos'})[0] == 201
    cos.housekeeping()
    env.cos = cos
    env.cos_relay = relay
    env.discussion = FakeRelay()
    env.worker.discussion = env.discussion
    env.cos_discussion = FakeRelay()
    env.cos.discussion = env.cos_discussion
    return env


def rows(e):
    with e.store.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM turns WHERE room=?', (e.room,))]


def test_open_question_is_independent_and_explicit_picker_wins(pair):
    e = pair
    e.say('What should we work on today?')
    # FF-09: Automatic with nobody named is one responder; planning goes to CoS.
    assert {r['addressee'] for r in rows(e) if r['state']=='queued'} == {'cos'}
    code, _ = e.call('POST', f'/rooms/{e.room}/events', {'body':'MC is unavailable; can you help?', 'target':'cos'})
    assert code == 201
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['cos']
    e.say('CoS, please help me plan tomorrow.')
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['cos']


def test_cos_question_does_not_replace_unclaimed_mc_turn(pair):
    e = pair
    e.say('@mc @cos\nMC, please listen. CoS can you ask a question for MC and MC answer please')
    original = next(r for r in rows(e) if r['addressee']=='mc')
    e.cos_relay.script=[{'outcome':'done','text':'MC, what would you check before calling production healthy?'}]
    assert e.cos.run_once()
    mc = [r for r in rows(e) if r['addressee']=='mc']
    assert len(mc)==2
    assert next(r for r in mc if r['id']==original['id'])['state']=='queued'
    assert e.worker.run_once()  # owner request through ordinary agent, once
    assert e.worker.run_once()  # peer question through separate discussion transport
    assert len(e.relay.calls)==1
    assert len(e.discussion.calls)==1
    assert e.discussion.calls[0]['messages']==[]
    assert not e.worker.run_once()  # no unbounded conversation
    messages=[r for r in e.events() if r['actor'] in ('mc','cos') and r['kind']=='message']
    assert len(messages)==3
    assert messages[-1]['reference']==messages[0]['id']


def test_new_owner_message_cancels_pending_peer_response(pair):
    e=pair
    e.say('MC discuss readiness. CoS ask MC a follow-up question')
    assert e.worker.run_once()
    e.cos_relay.script=[{'outcome':'done','text':'MC, what would you check first?'}]
    assert e.cos.run_once()
    e.call('POST', f'/rooms/{e.room}/events', {'body':'Actually, CoS, pause that topic.', 'target':'cos'})
    assert not e.worker.run_once()
    assert not e.discussion.calls
    assert len(e.relay.calls)==1


def test_plain_language_question_reaches_mc_as_discussion_only(pair):
    e=pair
    e.say('CoS can you ask a question for MC')
    assert [r['addressee'] for r in rows(e)]==['cos']
    e.cos_relay.script=[{'outcome':'done','text':'MC, what would you check first?'}]
    assert e.cos.run_once()
    assert e.worker.run_once()
    assert not e.relay.calls
    assert len(e.discussion.calls)==1


def test_unaddressed_action_cannot_reach_either_executor(pair):
    e=pair
    e.cos.discussion=FakeRelay()
    e.say('File an issue about this')
    assert e.worker.run_once()
    assert not e.cos.run_once()          # FF-09: build work goes to MC alone; CoS stays quiet
    assert not e.relay.calls
    assert not e.cos_relay.calls
    assert len(e.discussion.calls)==1 and not e.cos.discussion.calls


def test_old_worker_cannot_claim_discussion_and_retry_cannot_change_mode(pair):
    e=pair
    e.call('POST', f'/rooms/{e.room}/events', {'body':'What should we check?', 'recipients':['mc','cos']})
    status,data=e.worker.work.call('POST','/turns/claim', {'addressees':['mc']},key='old-worker-claim')
    assert status==200 and data['turn'] is None
    discussion=next(r for r in rows(e) if r['addressee']=='mc')
    with e.store.connect() as db:
        db.execute("UPDATE turns SET state='failed' WHERE id=?",(discussion['id'],))
    count=len(rows(e))
    from domains.records.store import Problem
    with pytest.raises(Problem,match='Send a new message'):
        e.store.meetings.owner_retry('robert','retry-discussion',e.room,discussion['id'],{'confirm':True})
    assert len(rows(e))==count


def test_owner_correction_after_claim_prevents_obsolete_discussion_dispatch(pair):
    e = pair
    e.call('POST', f'/rooms/{e.room}/events', {'body':'What should we check first?', 'recipients':['mc','cos']})
    status, data = e.worker.work.call('POST', '/turns/claim',
        {'addressees': ['mc'], 'discussion_protocol': 1}, key='claim-before-correction')
    assert status == 200 and data['turn']
    turn = data['turn']
    e.say('CoS, please pause that topic.')
    assert e.worker.infer(turn) is None
    assert not e.discussion.calls
    assert not e.relay.calls
    with e.store.connect() as db:
        actual = dict(db.execute('SELECT * FROM turns WHERE id=?', (turn['id'],)).fetchone())
    assert actual['state'] == 'cancelled'
    assert actual['disposition'] == 'newer_message'


@pytest.mark.parametrize('mc_first', [True, False])
def test_courtesy_question_cannot_steal_other_teammates_followup(pair, mc_first):
    """Robert's live failure: MC says 'over to you?' before CoS asks MC."""
    e = pair
    e.cos.discussion = FakeRelay()
    e.say('@mc @cos\nso, I see you both, so I think that means we have a meeting. '
          'MC, please listen. CoS can you ask a question for MC and MC answer please')
    e.relay.script = [{'outcome': 'done', 'text': 'CoS, over to you—what would you like to ask me?'}]
    e.cos_relay.script = [{'outcome': 'done', 'text': 'MC, what would you check before calling production healthy?'}]
    first, second = (e.worker, e.cos) if mc_first else (e.cos, e.worker)
    assert first.run_once()
    assert second.run_once()
    # Both actual questions get one distinct tool-free response. A response
    # containing another question cannot start an autonomous ping-pong.
    e.discussion.script = [{'outcome': 'done', 'text': 'I would verify the deployed version. CoS, what else?'}]
    e.cos.discussion.script = [{'outcome': 'done', 'text': 'My question is already above. MC, did you see it?'}]
    assert e.worker.run_once()
    assert e.cos.run_once()
    assert not e.worker.run_once()
    assert not e.cos.run_once()
    assert len(e.relay.calls) == len(e.cos_relay.calls) == 1
    assert len(e.discussion.calls) == len(e.cos.discussion.calls) == 1
    messages = [r for r in e.events() if r['actor'] in ('mc', 'cos') and r['kind'] == 'message']
    by_id = {r['id']: r for r in messages}
    for reply in messages[2:]:
        question = by_id[reply['reference']]
        assert question['actor'] != reply['actor']
    with e.store.connect() as db:
        assert db.execute('SELECT count(*) FROM round_peer_replies').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM round_handoffs').fetchone()[0] == 1
        # Restart/migration imports the old row without duplicating either.
        from domains.records.meetings import ensure_schema
        ensure_schema(db)
        assert db.execute('SELECT count(*) FROM round_peer_replies').fetchone()[0] == 2


def test_legacy_handoff_is_imported_before_new_recipient_is_scheduled(pair):
    e = pair
    e.say('CoS, ask MC what to check first.')
    e.cos_relay.script = [{'outcome': 'done', 'text': 'MC, what would you check first?'}]
    assert e.cos.run_once()
    from domains.records.meetings import ensure_schema
    with e.store.connect() as db:
        legacy = tuple(db.execute('SELECT * FROM round_handoffs').fetchone())
        db.execute('DROP TABLE round_peer_replies')
        ensure_schema(db)
        assert [tuple(r) for r in db.execute('SELECT * FROM round_peer_replies')] == [legacy]
        ensure_schema(db)
        assert db.execute('SELECT count(*) FROM round_peer_replies').fetchone()[0] == 1
    assert e.worker.run_once()
    assert len(e.discussion.calls) == 1
    assert not e.worker.run_once()


@pytest.mark.parametrize('body,target', [
    ('Let me try this again on automatic and not everyone. MC, can you create the issue I mentioned,', None),
    ('MC you here and working on it?', None),
    ('@mc @cos\nMC, create an issue for me.', ['mc', 'cos']),
    ('MC, create an issue for me.', ['mc', 'cos']),
    # Dev 4 Nov, Robert's own words: no comma, or MC named in the third person. Both got a talk-only turn.
    ('MC you are the builder.  Create a github issue to track the setup.', None),
    ('MC you are the builder.  Create a github issue to track the setup.', ['mc', 'cos']),
    ("We have a zoho email for access requests.  I'd like MC to double check that.", None),
    ('Have MC open a defect for the flicker.', ['mc', 'cos']),
    ('MC should create the issue.', ['mc', 'cos']),
])
def test_named_owner_requests_keep_tools_and_the_others_step_back(pair, body, target):
    payload = {'body': body}
    if target is not None: payload['recipients'] = target
    assert pair.call('POST', f'/rooms/{pair.room}/events', payload)[0] == 201
    queued = [r for r in rows(pair) if r['state'] == 'queued']
    # Robert, Dev 4 Nov: "MC do this and you pick it up and CoS steps back", whatever To says.
    assert {r['addressee'] for r in queued} == {'mc'}
    with pair.store.connect() as db:
        assert not db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?', (queued[0]['id'],)).fetchone()
    assert pair.worker.run_once()
    assert len(pair.relay.calls) == 1 and not pair.discussion.calls
    assert not pair.cos.run_once()
    assert not pair.cos_relay.calls


@pytest.mark.parametrize('body', ['He said "MC, create an issue."', '> MC, create an issue.', '```\nMC, create an issue.\n```', 'MC is unavailable. File an issue.',
                                  'MC seems slow to join', 'I think MC is right about this.', "I'd like to discuss MC's plan.",
                                  'MC you know what, never mind.'])
def test_quoted_or_incidental_names_do_not_enable_tools(pair, body):
    pair.say(body)
    with pair.store.connect() as db:
        turns = [r for r in rows(pair) if r['state'] == 'queued']
        assert turns
        assert all(db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?', (r['id'],)).fetchone() for r in turns)


def test_wait_persists_for_broadcast_and_peer_then_direct_request_resumes(pair):
    e = pair
    assert e.call('POST', f'/rooms/{e.room}/events', {'body':'CoS, just wait. This was addressed to MC.', 'recipients':['mc','cos']})[0] == 201
    assert [r['addressee'] for r in rows(e) if r['state']=='queued']==[]  # a wait for CoS is not a request to MC
    e.say('Any thoughts?')
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['mc']
    e.say('MC, what do you think?')
    e.relay.script=[{'outcome':'done','text':'CoS, what would you check?'}]
    assert e.worker.run_once()
    assert not e.cos.run_once()
    e.say('CoS, can you report now?')
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['cos']
    assert e.cos.run_once()


def test_explicit_mention_resumes_waiting_agent(pair):
    pair.say('CoS, wait.')
    pair.say('@cos investigate this')
    assert [r['addressee'] for r in rows(pair) if r['state']=='queued'] == ['cos']
    assert pair.cos.run_once()


def test_proof_never_uses_discussion_transport(pair):
    with pair.store.connect() as db:
        db.execute("UPDATE meetings SET kind='proof' WHERE room=?", (pair.room,))
    pair.say('Please introduce yourself.')
    with pair.store.connect() as db:
        queued=[r for r in rows(pair) if r['state']=='queued']
        assert queued
        assert not any(db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?', (r['id'],)).fetchone() for r in queued)


def test_wait_fences_claimed_turn_through_normal_cancel_path(pair):
    pair.say('CoS, can you check this?')
    status,data=pair.cos.work.call('POST','/turns/claim', {'addressees':['cos'],'discussion_protocol':1},key='wait-claimed')
    assert status==200 and data['turn']
    pair.say('CoS, wait.')
    with pair.store.connect() as db:
        assert db.execute('SELECT state FROM turns WHERE id=?',(data['turn']['id'],)).fetchone()['state']=='cancel_requested'
    assert pair.cos.infer(data['turn']) is None
    assert not pair.cos_relay.calls


def test_participation_controls_persist_without_dispatch_and_reject_nonowner(pair):
    e = pair
    path = f'/rooms/{e.room}/participation'
    before = len(rows(e))
    assert e.call('POST', path, {'actor':'cos','waiting':True})[0] == 200
    assert next(p for p in e.call('GET', f'/rooms/{e.room}/turns')[1]['participants'] if p['id']=='cos')['waiting'] is True
    assert len(rows(e)) == before
    e.say('Any thoughts?')
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['mc']
    before = len(rows(e))
    assert e.call('POST', path, {'actor':'cos','waiting':False})[0] == 200
    assert len(rows(e)) == before
    assert next(p for p in e.call('GET', f'/rooms/{e.room}/turns')[1]['participants'] if p['id']=='cos')['waiting'] is False
    assert not e.cos.run_once()
    assert e.call('POST', path, {'actor':'robert','waiting':True})[0] == 404
    assert e.call('POST', path, {'actor':'cos','waiting':'yes'})[0] == 400
    with pytest.raises(Exception) as err:
        e.store.meetings.participation('cos','cannot-control',e.room,{'actor':'mc','waiting':True})
    assert getattr(err.value, 'status', None) == 403


def test_wait_control_fences_claimed_reply_and_resume_does_not_restart_it(pair):
    e = pair
    e.say('MC, please check this.')
    status, data = e.worker.work.call('POST','/turns/claim', {'addressees':['mc'],'discussion_protocol':1},key='control-claimed')
    assert status == 200 and data['turn']
    claim = data['turn']
    assert e.call('POST', f'/rooms/{e.room}/participation', {'actor':'mc','waiting':True})[0] == 200
    assert next(r for r in rows(e) if r['id']==claim['id'])['state'] == 'cancel_requested'
    assert e.call('POST', f'/rooms/{e.room}/participation', {'actor':'mc','waiting':False})[0] == 200
    assert next(r for r in rows(e) if r['id']==claim['id'])['state'] == 'cancel_requested'
    assert not [r for r in rows(e) if r['state']=='queued']


@pytest.mark.parametrize('text,actor', [
    ('MC, wait for CI to finish and then tell me the result', 'mc'),
    ('CoS, stop worrying about the calendar and summarise my week', 'cos'),
    ('MC, hold off on the merge but draft the PR description', 'mc'),
    ('MC pause the deploy job please', 'mc'),
])
def test_task_verbs_do_not_mute_addressed_agent(pair, text, actor):
    pair.say(text)
    with pair.store.connect() as db:
        assert not db.execute('SELECT * FROM meeting_waits WHERE room=?', (pair.room,)).fetchall()
    assert actor in [r['addressee'] for r in rows(pair) if r['state']=='queued']


@pytest.mark.parametrize('text', ['MC, wait.', 'MC, please wait until I ask', 'MC, stay quiet'])
def test_standalone_wait_instructions_persist(pair, text):
    pair.say(text)
    with pair.store.connect() as db:
        assert db.execute('SELECT 1 FROM meeting_waits WHERE room=? AND actor=?', (pair.room,'mc')).fetchone()
    assert not pair.worker.run_once()


def test_retry_cannot_bypass_durable_wait(pair):
    pair.say('MC, inspect this')
    original=next(r for r in rows(pair) if r['addressee']=='mc')
    pair.call('POST', f'/rooms/{pair.room}/participation', {'actor':'mc','waiting':True})
    pair.store.meetings.owner_retry('robert','retry-while-waiting',pair.room,original['id'],{'confirm':True})
    assert not pair.worker.run_once()
    assert not pair.relay.calls
    assert any(r['disposition']=='waiting_by_request' for r in rows(pair))


def test_hard_newline_wait_then_address_other_agent(pair):
    pair.say('MC, please wait\nCoS, what do you think?')
    assert [r['addressee'] for r in rows(pair) if r['state']=='queued']==['cos']
    with pair.store.connect() as db:
        assert db.execute('SELECT 1 FROM meeting_waits WHERE room=? AND actor=?',(pair.room,'mc')).fetchone()


@pytest.mark.parametrize('text', ['CoS, wait, this was addressed to MC', 'CoS, wait, this one is for MC'])
def test_ambiguous_wait_withholds_turn_and_records_choice(pair, text):
    pair.say(text)
    assert not [r for r in rows(pair) if r['state']=='queued' and r['addressee']=='cos']
    with pair.store.connect() as db:
        assert db.execute("SELECT 1 FROM routing_notes WHERE room=? AND addressee='cos' AND disposition='wait_needs_choice'",(pair.room,)).fetchone()
        assert not db.execute('SELECT 1 FROM meeting_waits WHERE room=?',(pair.room,)).fetchone()
    assert not pair.cos.run_once()


def test_ambiguous_wait_cannot_be_bypassed_by_peer_handoff(pair):
    pair.say('CoS, wait, this is for MC.\nMC, review readiness')
    pair.relay.script=[{'outcome':'done','text':'CoS, what do you think?'}]
    assert pair.worker.run_once()
    assert not pair.cos.run_once()
    assert not [r for r in rows(pair) if r['addressee']=='cos']


@pytest.mark.parametrize('body,actors', [
    ('MC open a defect for the flicker', {'mc'}),
    ('Okay MC open a defect', {'mc'}),
    ('Chief of Staff add that to the board', {'cos'}),
    ('M.C. open a defect', {'mc'}),
    ('emcee open a defect', {'mc'}),
    ('Cos add that to the board', {'cos'}),
    ('C.O.S. add that to the board', {'cos'}),
    ('MC open a defect. CoS add that to the board', {'mc','cos'}),
    ('MC open a defect and CoS add that to the board', {'mc','cos'}),
    ('MC is slow today', set()),
    ('MC said that yesterday', set()),
    ('MC can open a defect', set()),
    ('CoS could help with the board', set()),
    ('MC would open a defect', set()),
    ('MC can you open a defect', {'mc'}),
    ('MC should you open a defect', {'mc'}),
    ('Everyone what do you think', set()),
])
@pytest.mark.parametrize('target', [None, ['mc','cos']])
def test_voice_shaped_text_uses_normal_authority_rules(pair, body, actors, target):
    payload={'body':body}
    if target: payload['recipients']=target
    assert pair.call('POST', f'/rooms/{pair.room}/events',payload)[0]==201
    queued=[r for r in rows(pair) if r['state']=='queued']
    with pair.store.connect() as db:
        actions={r['addressee'] for r in queued if not db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?',(r['id'],)).fetchone()}
    assert actions==actors
    if actors or target or body.startswith('Everyone'):
        assert {r['addressee'] for r in queued} == (actors or {'mc','cos'})
    else:
        assert len(queued) == 1          # FF-09: unaddressed under Automatic is one responder


@pytest.mark.parametrize('body', ['Thanks, CoS.', 'Hi CoS', 'CoS thanks', 'Thank you CoS'])
@pytest.mark.parametrize('target',[None,'cos'])
def test_courtesy_does_not_resume_wait(pair, body, target):
    pair.say('CoS, wait.')
    pair.say(body, target=target)
    with pair.store.connect() as db:
        assert db.execute("SELECT 1 FROM meeting_waits WHERE room=? AND actor='cos'",(pair.room,)).fetchone()
    assert not pair.cos.run_once()


def test_single_mention_prefix_resumes_and_routes_consistently(pair):
    pair.say('CoS, wait.')
    pair.say('@cos\nplease look')
    assert pair.cos.run_once()
    assert len(pair.cos_relay.calls)==1


def test_named_voice_request_overrides_single_picker(pair):
    pair.call('POST', f'/rooms/{pair.room}/events',{'body':'MC open a defect','target':'cos'})
    assert [r['addressee'] for r in rows(pair) if r['state']=='queued']==['mc']


def test_everyone_unnamed_cannot_gain_tools_when_handoff_flag_off(pair,monkeypatch):
    monkeypatch.setenv('RECORDS_DISCUSSION_HANDOFFS','0')
    pair.call('POST', f'/rooms/{pair.room}/events',{'body':'Open a defect','recipients':['mc','cos']})
    with pair.store.connect() as db:
        assert all(db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?',(r['id'],)).fetchone() for r in rows(pair) if r['state']=='queued')


def test_courtesy_with_selected_recipient_does_not_resume(pair):
    pair.say('CoS wait')
    pair.call('POST', f'/rooms/{pair.room}/events',{'body':'Thanks, CoS.','target':'cos'})
    assert not pair.cos.run_once()
    with pair.store.connect() as db:
        assert db.execute("SELECT 1 FROM meeting_waits WHERE room=? AND actor='cos'",(pair.room,)).fetchone()




def test_aliases_are_directory_driven_and_apply_to_waits(pair, monkeypatch):
    from domains.records import meetings
    monkeypatch.setitem(meetings.DIRECTORY, 'mc', {'spoken_aliases':['Workshop Lead']})
    pair.say('Workshop Lead wait')
    assert not pair.worker.run_once()
    pair.say('Okay Workshop Lead open a defect')
    assert pair.worker.run_once()
    assert len(pair.relay.calls)==1


def test_everyone_unnamed_executes_only_discussion_transports(pair):
    pair.call('POST',f'/rooms/{pair.room}/events',{'body':'What should we do next?','recipients':['mc','cos']})
    assert pair.worker.run_once()
    assert pair.cos.run_once()
    assert not pair.relay.calls and not pair.cos_relay.calls
    assert len(pair.discussion.calls)==1 and len(pair.cos_discussion.calls)==1


@pytest.mark.parametrize('body', [
    'MC, is the build green?', 'MC, did you file the issue?',
    'MC, are the tests passing?', 'MC, have you checked CI?',
    'MC, does the deploy look healthy?', "What's the CI status, MC?",
    'MC is the build green?', 'MC: is the build green?', 'MC — is the build green?',
    'Thanks, CoS. What is the CI status, MC?', 'Thanks. What is the CI status, MC?',
])
@pytest.mark.parametrize('target',[None,['mc','cos']])
def test_explicit_and_voice_questions_authorize_mc(pair,body,target):
    payload={'body':body}
    if target: payload['recipients']=target
    assert pair.call('POST',f'/rooms/{pair.room}/events',payload)[0]==201
    with pair.store.connect() as db:
        turns=[r for r in rows(pair) if r['state']=='queued']
        assert {r['addressee'] for r in turns if not db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?',(r['id'],)).fetchone()}=={'mc'}


@pytest.mark.parametrize('body',['MC tools look broken','CoS board is empty again',"and cos it's late",'MC is slow today','MC said that yesterday'])
def test_noun_phrases_and_causal_cos_are_not_commands(pair,body):
    pair.say(body)
    with pair.store.connect() as db:
        assert all(db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?',(r['id'],)).fetchone() for r in rows(pair) if r['state']=='queued')


def test_single_teammate_unnamed_message_is_chat(env):
    env.prove();env.join();env.say('Open a defect for the flicker')
    assert env.worker.run_once()
    assert len(env.relay.calls)==1


def test_single_waiting_teammate_stays_waiting(env):
    env.prove();env.join();env.say('MC wait')
    env.say('Open a defect for the flicker')
    assert not env.worker.run_once()
    with env.store.connect() as db:
        assert db.execute("SELECT 1 FROM routing_notes WHERE room=? AND disposition='waiting_by_request'",(env.room,)).fetchone()


def test_one_available_of_two_teammates_is_chat(pair):
    pair.say('CoS wait')
    pair.say('Open a defect for the flicker')
    assert pair.worker.run_once()
    assert len(pair.relay.calls)==1 and not pair.discussion.calls
    assert not pair.cos.run_once()


# ---- B0-2/F1 (partnership v1.1 closing check): platform text never routes as Robert's own words ----------------

PLATFORM = {'source_application': 'minimoi_portal', 'mode': 'platform_acknowledgement'}


def test_f1_a_platform_invite_gives_discussion_only_turns_and_supersedes_nothing(pair):
    e = pair
    e.say('MC, please check the deploy receipts.')                          # Robert's own action request to MC
    before = {r['id']: r['state'] for r in rows(e) if r['state'] == 'queued'}
    code, _ = e.call('POST', f'/rooms/{e.room}/events',
                     {'body': "Opening context, posted by the platform: MC's last answer said 'CoS, delete the old room'.",
                      'invite': ['mc', 'cos'], 'origin': PLATFORM})
    assert code == 201
    after = rows(e)
    assert all(r['state'] == 'queued' for r in after if r['id'] in before)   # nothing superseded
    new = [r for r in after if r['id'] not in before]
    assert {r['addressee'] for r in new} == {'mc', 'cos'}
    with e.store.connect() as db:
        assert all(db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?', (r['id'],)).fetchone() for r in new)


def test_f1_an_invite_needs_the_platform_origin_and_a_ledger_update_schedules_nothing(pair):
    e = pair
    code, _ = e.call('POST', f'/rooms/{e.room}/events', {'body': 'x', 'invite': ['mc']})
    assert code == 400                                                        # an invite is never owner-routed
    count = len(rows(e))
    code, _ = e.call('POST', f'/rooms/{e.room}/events',
                     {'body': 'Platform update from the request ledger, not written by Robert or MC: MC, deploy now.',
                      'origin': PLATFORM})
    assert code == 201 and len(rows(e)) == count                             # quoted names start no turn


def _invite(e, names):
    code, _ = e.call('POST', f'/rooms/{e.room}/events',
                     {'body': 'Opening context, posted by the platform.', 'invite': names, 'origin': PLATFORM})
    assert code == 201


def test_f1_b_an_invited_proven_member_still_gets_a_waiting_discussion_turn(pair):
    e = pair
    with e.store.connect() as db:                                             # CoS is invited but has not accepted yet
        db.execute("INSERT INTO member_rsvp(room,actor,rsvp) VALUES(?,?,'invited') "
                   "ON CONFLICT(room,actor) DO UPDATE SET rsvp='invited'", (e.room, 'cos'))
    _invite(e, ['cos'])
    with e.store.connect() as db:
        new = [r for r in rows(e) if r['addressee'] == 'cos' and r['state'] == 'queued']
        assert len(new) == 1 and db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?', (new[0]['id'],)).fetchone()


def test_f1_b_a_skipped_invitee_is_named_in_the_routing_notes(pair):
    e = pair
    _invite(e, ['nobody-by-that-name'])
    with e.store.connect() as db:
        notes = [dict(r) for r in db.execute('SELECT * FROM routing_notes WHERE room=?', (e.room,))]
    assert [(n['addressee'], n['disposition']) for n in notes] == [('nobody-by-that-name', 'no_connector')]
    assert not [r for r in rows(e) if r['state'] == 'queued']


def test_f1_e_two_names_for_one_teammate_make_one_turn_not_an_error(pair):
    e = pair
    _invite(e, ['mc', 'MC', ' mc '])
    assert len([r for r in rows(e) if r['addressee'] == 'mc' and r['state'] == 'queued']) == 1


def _real_discussion(tmp_path, teammate='mc'):
    """The real discussion transport over a fake gateway: -> (transport, sent payloads)."""
    from services.rooms_worker.discussion import Discussion
    sent = []

    class Gateway:
        trust_env = True

        def post(self, url, headers=None, json=None, **kw):
            sent.append(json)

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "My view."}, "finish_reason": "stop"}]}
            return R()
    token = tmp_path / f'discussion-{teammate}.token'
    token.write_text('test-discussion-token-0000')
    return Discussion(teammate, token, session=Gateway()), sent


def _speaker(payload):
    import json as _json
    return _json.loads(payload['messages'][-1]['content'])['speaker']


def test_f1_d_a_platform_post_reaches_the_teammate_attributed_to_the_platform(pair, tmp_path):
    e = pair
    e.worker.discussion, sent = _real_discussion(tmp_path)
    _invite(e, ['mc'])
    assert e.worker.run_once()
    assert len(sent) == 1 and _speaker(sent[0]) == 'platform'


def test_f1_d_a_teammates_question_keeps_the_teammate_as_speaker(pair, tmp_path):
    e = pair                                                   # N1: an agent reply carries an origin too
    e.worker.discussion, sent = _real_discussion(tmp_path)
    e.say('@mc @cos\nMC, please listen. CoS can you ask a question for MC and MC answer please')
    e.cos_relay.script = [{'outcome': 'done', 'text': 'MC, what would you check before calling production healthy?'}]
    assert e.cos.run_once()
    assert e.worker.run_once() and e.worker.run_once()
    assert len(sent) == 1 and _speaker(sent[0]) == 'cos'


def test_n5_an_invite_does_not_resume_a_teammate_he_asked_to_wait(pair):
    e = pair
    e.say('MC wait')
    before = len([r for r in rows(e) if r['addressee'] == 'mc' and r['state'] == 'queued'])
    _invite(e, ['mc'])
    assert len([r for r in rows(e) if r['addressee'] == 'mc' and r['state'] == 'queued']) == before
    with e.store.connect() as db:
        assert db.execute("SELECT 1 FROM routing_notes WHERE room=? AND addressee='mc' AND disposition='waiting_by_request'",
                          (e.room,)).fetchone()

@pytest.mark.parametrize('body', ['Thanks, CoS.', 'Thank you CoS', 'CoS thanks'])
def test_courtesy_to_a_waiting_teammate_is_not_answered_by_the_other(pair, body):
    """Dev E2E 3 Nov: with CoS waiting, "Thanks, CoS." fell through the one-available rule and MC answered it."""
    pair.say('CoS, wait.')
    pair.say(body)
    assert not [r for r in rows(pair) if r['state'] == 'queued']
    assert not pair.worker.run_once() and not pair.cos.run_once()
    with pair.store.connect() as db:
        assert db.execute("SELECT 1 FROM meeting_waits WHERE room=? AND actor='cos'", (pair.room,)).fetchone()
        assert db.execute("SELECT 1 FROM routing_notes WHERE room=? AND addressee='cos' AND disposition='waiting_by_request' "
                          "AND trigger_seq=(SELECT MAX(trigger_seq) FROM routing_notes WHERE room=?)", (pair.room, pair.room)).fetchone()


def test_unnamed_request_still_reaches_the_sole_available_teammate(pair):
    pair.say('CoS, wait.')
    pair.say('Open a defect for the flicker')
    assert pair.worker.run_once() and len(pair.relay.calls) == 1


@pytest.mark.parametrize('body', ['MC and Cos, please join', 'CoS and MC, what do you think?', 'MC, CoS: check this'])
def test_stacked_names_reach_every_named_teammate(pair, body):
    """Dev 4 Nov: "MC and Cos, please join" reached only CoS, so MC looked slow to join."""
    pair.say(body)
    with pair.store.connect() as db:
        queued = [r for r in rows(pair) if r['state'] == 'queued']
        assert {r['addressee'] for r in queued} == {'mc', 'cos'}
        assert not any(db.execute('SELECT 1 FROM discussion_turns WHERE turn_id=?', (r['id'],)).fetchone() for r in queued)


# ---- FF-09 (1.2 fast-follow, A6): "To: Automatic" makes one agent answer, not both (see #472, #471) -------------

def _queued(e):
    return {r['addressee'] for r in rows(e) if r['state'] == 'queued'}


@pytest.mark.parametrize('body', ['What should we work on today?', 'Can you help me plan next week?',
                                  'How does the meeting record work?', 'What are our priorities this week?'])
def test_ff09_automatic_planning_and_how_things_work_go_to_cos_alone(pair, body):
    pair.say(body)
    assert _queued(pair) == {'cos'}
    assert not pair.worker.run_once()


@pytest.mark.parametrize('body', ['Is the build green?', 'The deploy failed again, can someone look at the logs?',
                                  'Open a defect for the flicker', "What's the CI status?"])
def test_ff09_automatic_build_and_technical_go_to_mc_alone(pair, body):
    pair.say(body)
    assert _queued(pair) == {'mc'}
    assert not pair.cos.run_once()


@pytest.mark.parametrize('body', ['Any thoughts?', 'Hmm.', 'Good morning'])
def test_ff09_automatic_unclear_goes_to_cos(pair, body):
    pair.say(body)
    assert _queued(pair) == {'cos'}


def test_ff09_automatic_pick_stays_discussion_only(pair):
    """A keyword pick is not someone being named: it answers, but gets no action turn."""
    pair.say('Open a defect for the flicker')
    assert pair.worker.run_once()
    assert not pair.relay.calls and len(pair.discussion.calls) == 1


@pytest.mark.parametrize('body,target,expected', [
    ('MC, what should we plan for tomorrow?', None, {'mc'}),             # named wins over the topic
    ('CoS, is the build green?', None, {'cos'}),
    ('Is the build green?', 'cos', {'cos'}),                              # To: one name
    ('What should we plan today?', 'mc', {'mc'}),
])
def test_ff09_explicit_addressing_is_unchanged(pair, body, target, expected):
    pair.say(body, target=target)
    assert _queued(pair) == expected


@pytest.mark.parametrize('body,recipients', [
    ('What should we work on today?', ['mc', 'cos']),                    # To: Everyone (both)
    ('Is the build green?', ['mc', 'cos']),
    ('What does everyone think?', None),                                  # said in the message
    ('Both of you, any thoughts?', None),
])
def test_ff09_everyone_or_both_still_asks_both(pair, body, recipients):
    payload = {'body': body}
    if recipients: payload['recipients'] = recipients
    assert pair.call('POST', f'/rooms/{pair.room}/events', payload)[0] == 201
    assert _queued(pair) == {'mc', 'cos'}


def test_ff09_automatic_falls_back_to_the_other_when_the_pick_is_waiting(pair):
    pair.say('MC, wait.')
    pair.say('Is the build green?')
    assert _queued(pair) == {'cos'}                                       # sole available, as before
    with pair.store.connect() as db:
        assert db.execute("SELECT 1 FROM meeting_waits WHERE room=? AND actor='mc'", (pair.room,)).fetchone()


def test_ff09_auto_responder_is_deterministic_and_prefers_cos_on_ties():
    from domains.records.meetings import auto_responder
    assert auto_responder('Plan the deploy', ['mc', 'cos']) == 'cos'      # one each: unclear, so CoS
    assert auto_responder('Fix the failing tests', ['mc', 'cos']) == 'mc'
    assert auto_responder('Fix the failing tests', ['cos', 'codex']) == 'cos'
    assert auto_responder('Plan tomorrow', ['mc', 'codex']) == 'mc'
    assert auto_responder('Anything', ['codex', 'claude-code']) == 'codex'
    assert auto_responder('> deploy the build\nWhat do you think?', ['mc', 'cos']) == 'cos'   # quoted text does not count


# ---- FF-05 (1.2 part): a teammate with no route on this host is "not available here", not an invite that times out ----

def _claude_card(e, tmp_path, *, hosted=True):
    manage = _load('records_poc_manage_for_tests', 'manage.py')
    manage.provision_rooms(e.store, 'claude-code', 'Claude Code', tmp_path / 'claude-secrets', worker='rooms-connector')
    if not hosted:
        with e.store.connect() as db:
            db.execute("DELETE FROM hosted_teammates WHERE principal='claude-code'")


def _teammate(e, principal):
    return next(c for c in e.call('GET', '/teammates')[1]['teammates'] if c['principal'] == principal)


@pytest.mark.parametrize('hosted', [True, False])
def test_ff05_claude_with_no_live_route_is_not_offered(pair, tmp_path, hosted):
    """Production: Claude Code has a card, but no connector ever answered or checks in there."""
    _claude_card(pair, tmp_path, hosted=hosted)
    assert _teammate(pair, 'claude-code')['available_here'] is False
    assert _teammate(pair, 'mc')['available_here'] is True and _teammate(pair, 'cos')['available_here'] is True
    code, body = pair.call('POST', f'/rooms/{pair.room}/invite', {'actor': 'claude-code'})
    assert code == 409 and 'not available here' in str(body)
    with pair.store.connect() as db:
        assert not db.execute("SELECT 1 FROM members WHERE room=? AND actor='claude-code'", (pair.room,)).fetchone()


def test_ff05_claude_checking_in_or_proven_is_invited_as_today(pair, tmp_path):
    """Dev and the Mac: the connector checks in (or Claude is proven), so the invite works as before."""
    from domains.records.meetings import set_presence
    _claude_card(pair, tmp_path)
    with pair.store.connect() as db:
        set_presence(db, '*', 'claude-code', 'reachable', 180, 'worker_readyz')
    assert _teammate(pair, 'claude-code')['available_here'] is True
    assert pair.call('POST', f'/rooms/{pair.room}/invite', {'actor': 'claude-code'})[0] == 201
    with pair.store.connect() as db:
        db.execute("DELETE FROM presence WHERE actor='claude-code'")
        db.execute("UPDATE teammates SET proven_at=? WHERE principal='claude-code'", (datetime.now(timezone.utc).isoformat(),))
    assert _teammate(pair, 'claude-code')['available_here'] is True       # proven: a route exists even while the Mac sleeps
    with pair.store.connect() as db:
        db.execute("UPDATE teammates SET proven_at=NULL WHERE principal='claude-code'")
        set_presence(db, '*', 'claude-code', 'checked_in', 180, 'worker_readyz')
    assert _teammate(pair, 'claude-code')['available_here'] is True       # unproven but its connector checks in: Prove works
    status = pair.call('GET', f'/rooms/{pair.room}/turns')[1]
    assert next(p for p in status['participants'] if p['id'] == 'claude-code')['available_here'] is True
