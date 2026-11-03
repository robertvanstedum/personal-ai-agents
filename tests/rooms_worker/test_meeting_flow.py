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
    return env


def rows(e):
    with e.store.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM turns WHERE room=?', (e.room,))]


def test_open_question_is_independent_and_explicit_picker_wins(pair):
    e = pair
    e.say('What should we work on today?')
    assert {r['addressee'] for r in rows(e) if r['state']=='queued'} == {'mc','cos'}
    code, _ = e.call('POST', f'/rooms/{e.room}/events', {'body':'MC is unavailable; can you help?', 'target':'cos'})
    assert code == 201
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['cos']
    e.say('CoS, please help me plan tomorrow.')
    assert [r['addressee'] for r in rows(e) if r['state']=='queued'] == ['cos']


def test_cos_question_does_not_replace_unclaimed_mc_turn(pair):
    e = pair
    e.say('@mc @cos\nCoS can you ask a question for MC and MC answer please')
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
    e.say('@mc @cos Please discuss readiness')
    assert e.worker.run_once()
    e.cos_relay.script=[{'outcome':'done','text':'MC, what would you check first?'}]
    assert e.cos.run_once()
    e.call('POST', f'/rooms/{e.room}/events', {'body':'Actually, CoS, pause that topic.', 'target':'cos'})
    assert not e.worker.run_once()
    assert not e.discussion.calls
    assert len(e.relay.calls)==1


def test_plain_language_question_reaches_mc_as_discussion_only(pair):
    e=pair
    e.say('CoS can you ask a question for MC and MC answer please')
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
    assert e.cos.run_once()
    assert not e.relay.calls
    assert not e.cos_relay.calls
    assert len(e.discussion.calls)==len(e.cos.discussion.calls)==1


def test_old_worker_cannot_claim_discussion_and_retry_cannot_change_mode(pair):
    e=pair
    e.say('What should we check?')
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
    e.say('What should we check first?')
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
          'CoS can you ask a question for MC and MC answer please')
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
