"""Read-only display projection of receipt-backed connector messages."""
import copy
import json
import pytest
from test_cos_agent_responder import agent_setup, turn
from test_cos_records_bridge import bridge
from contribution_view import connector_view


def test_receipt_backed_reply_preserves_raw_record(agent_setup):
    s = agent_setup
    result = turn(s)
    before = s.store.export('robert', s.room)
    event = s.store.room('robert', s.room)['events'][-1]
    view = event['presentation']
    assert view['text'] == json.loads(event['body'])['execution']['text']
    assert view['evidence']['receipt_id'] == result['operation']['receipt_id']
    assert view['evidence']['record_id'] == event['id']
    assert view['evidence']['source_record_ids']
    assert 'not independently attested' in view['evidence']['assurance']
    assert 'not enforced' in view['warning']
    after = s.store.export('robert', s.room)
    before.pop('exported_at'); after.pop('exported_at')
    assert after == before
    with s.store.connect() as db:
        assert db.execute('SELECT body FROM events WHERE id=?', (event['id'],)).fetchone()['body'] == event['body']


@pytest.mark.parametrize('case', ['plain', 'json_list', 'missing', 'request', 'runtime', 'source', 'text', 'actor', 'kind', 'class', 'origin', 'synthetic', 'policy', 'receipt_missing', 'receipt_mismatch'])
def test_invalid_or_unmatched_evidence_falls_back(agent_setup, case):
    s = agent_setup
    turn(s)
    event = copy.deepcopy(s.store.room('robert', s.room)['events'][-1])
    body = json.loads(event['body'])
    if case == 'plain': event['body'] = 'ordinary message'
    elif case == 'json_list': event['body'] = '[]'
    elif case == 'missing': body.pop('execution')
    elif case == 'request': body['execution']['coordination_request_id'] = 'invalid'
    elif case == 'runtime': body['execution']['openclaw_run_id'] = 'claimed runtime'
    elif case == 'source': body['source_record_ids'] = ['invalid']
    elif case == 'text': body['execution']['text'] = 42
    elif case == 'actor': event['actor'] = 'robert'
    elif case == 'kind': event['kind'] = 'proposal'
    elif case == 'class': event['context_class'] = 'external_source'
    elif case == 'origin': event['origin'] = None
    elif case == 'synthetic': body['synthetic_only'] = False
    elif case == 'policy': body['tool_policy_enforced'] = True
    if body != json.loads(s.store.room('robert', s.room)['events'][-1]['body']): event['body'] = json.dumps(body)
    with s.store.connect() as db:
        if case == 'receipt_missing': db.execute("DELETE FROM operations WHERE actor='cos-dev'")
        if case == 'receipt_mismatch': event['id'] = s.room
        assert connector_view(db, event) is None


def test_ordinary_room_does_not_require_connector_evidence(agent_setup):
    s = agent_setup
    assert all('presentation' not in e for e in s.store.room('robert', s.room)['events'])


def test_executive_snapshot_copies_readable_reply_and_warning(agent_setup):
    from coordination import Coordination
    from uuid import uuid4
    s=agent_setup;turn(s)
    event=s.store.room('robert',s.room)['events'][-1]
    destination=s.store.create_room('robert',str(uuid4()),dict(title='Executive briefing',purpose='Synthetic',mode='meeting',recording_acknowledged=True))['result']['id']
    copied=Coordination(s.store).snapshot('robert','copy-reply',destination,dict(source=s.room,event_ids=[event['id']],disclosure_acknowledged=True))['result']['records'][0]
    assert copied['text']==event['presentation']['text']
    assert copied['warning']==event['presentation']['warning']
    assert 'request_fingerprint' not in copied['text']
