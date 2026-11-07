"""F2 step 0d: Records' observe credential. It may heartbeat a turn its worker installation already holds (renew the
lease, re-check admission, read the authoritative snapshot with members) and nothing else."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from test_worker import env, _load, BACKEND  # noqa: F401  (env is a fixture)
from worker_helpers import FakeRelay, Session
from services.rooms_worker.journal import TurnJournal
from services.rooms_worker.worker import Worker
from services.rooms_worker.clients import Records
from domains.records.platform_access import AccessError


@pytest.fixture
def rig(env, tmp_path):
    env.prove(); env.join()
    out = tmp_path / 'cos-secrets'
    manage = _load('records_poc_manage_for_tests_observe', 'manage.py')
    issued = manage.provision_rooms(env.store, 'cos', 'Chief of Staff', out, worker='rooms-worker-cos')
    with env.store.connect() as db:
        db.execute("UPDATE teammates SET proven_at=? WHERE principal='cos'", (datetime.now(timezone.utc).isoformat(),))
    assert env.call('POST', f'/rooms/{env.room}/invite', {'actor': 'cos'})[0] == 201
    installation = issued['hosted']['installation_id']
    work = Records(BACKEND, (out / 'rooms-worker-cos.token').read_text().strip(), session=Session(env.app))
    member = Records(BACKEND, (out / 'cos.token').read_text().strip(), session=Session(env.app))
    Worker(work, member, FakeRelay(), TurnJournal(tmp_path / 'cos-journal'), teammate='cos',
           agent_id='cos-agent-a').housekeeping()   # accepts the invitation, as the real worker does
    observe = env.store.platform_access.issue('robert', dict(
        principal='rooms-worker-cos', label='cos-scheduler observe', scope='observe', installation_id=installation,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    observer = Records(BACKEND, observe['access_token'], session=Session(env.app))
    env.work, env.observer, env.installation = work, observer, installation
    return env


def _claimed_running(e):
    e.say('CoS, please help me plan tomorrow.')
    status, data = e.work.call('POST', '/turns/claim', {'addressees': ['cos'], 'discussion_protocol': 1}, key=str(uuid4()))
    assert status == 200 and data.get('turn'), (status, data)
    turn = data['turn']
    status, _ = e.work.call('POST', f"/turns/{turn['id']}/start", {'claim_id': turn['claim_id']}, key=str(uuid4()))
    assert status == 200
    return turn


def test_observer_heartbeats_and_reads_the_authoritative_snapshot(rig):
    turn = _claimed_running(rig)
    status, body = rig.observer.call('POST', f"/turns/{turn['id']}/heartbeat",
                                     {'claim_id': turn['claim_id'], 'intent': 'dispatch', 'snapshot': True},
                                     key=str(uuid4()))
    assert status == 200 and body['dispatch'] is True
    assert {m['id'] for m in body['turn']['room_members']} >= {'robert', 'cos'}


def test_observer_needs_the_current_claim(rig):
    turn = _claimed_running(rig)
    status, _ = rig.observer.call('POST', f"/turns/{turn['id']}/heartbeat", {'claim_id': 'not-the-claim'},
                                  key=str(uuid4()))
    assert status == 409
    status, _ = rig.observer.call('POST', f"/turns/{turn['id']}/heartbeat", {}, key=str(uuid4()))
    assert status == 409


@pytest.mark.parametrize('route', ['start', 'fail', 'cancel_ack', 'recover', 'reconciled'])
def test_observer_cannot_change_a_turn(rig, route):
    turn = _claimed_running(rig)
    body = {'claim_id': turn['claim_id'], 'outcome': 'failed', 'reason': 'test', 'late_output': False,
            'finding': 'nothing', 'prior_claim_id': turn['claim_id']}
    status, _ = rig.observer.call('POST', f"/turns/{turn['id']}/{route.replace('_', '-')}", body, key=str(uuid4()))
    assert status in (403, 404), (route, status)
    with rig.store.connect() as db:
        state = db.execute('SELECT state, claim_id FROM turns WHERE id=?', (turn['id'],)).fetchone()
    assert state['state'] == 'running' and state['claim_id'] == turn['claim_id']


def test_observer_cannot_claim_or_read_rooms(rig):
    rig.say('CoS, please help me plan tomorrow.')
    status, _ = rig.observer.call('POST', '/turns/claim', {'addressees': ['cos'], 'discussion_protocol': 1},
                                  key=str(uuid4()))
    assert status == 403
    assert rig.observer.call('GET', '/rooms')[0] == 403
    assert rig.observer.call('GET', f'/rooms/{rig.room}')[0] in (401, 403)
    assert rig.observer.call('POST', f'/rooms/{rig.room}/events', {'body': 'hi'}, key=str(uuid4()))[0] in (401, 403)


def test_observer_cannot_heartbeat_another_installations_or_an_unclaimed_turn(rig):
    rig.say('CoS, please help me plan tomorrow.')
    with rig.store.connect() as db:
        turn = db.execute("SELECT id FROM turns WHERE room=? AND addressee='cos' AND state='queued'",
                          (rig.room,)).fetchone()
    status, _ = rig.observer.call('POST', f"/turns/{turn['id']}/heartbeat", {'claim_id': 'x'}, key=str(uuid4()))
    assert status in (404, 409)


def test_issue_rules(rig):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    base = dict(principal='rooms-worker-cos', label='x', scope='observe', expires_at=future)
    with pytest.raises(AccessError):
        rig.store.platform_access.issue('robert', base)                                   # no installation
    with pytest.raises(AccessError):
        rig.store.platform_access.issue('robert', dict(base, installation_id=str(uuid4())))  # not a worker's
    with pytest.raises(AccessError):
        rig.store.platform_access.issue('robert', dict(base, installation_id=rig.installation, operations=['work']))
    with pytest.raises(AccessError):
        rig.store.platform_access.issue('robert', dict(base, principal='cos', installation_id=rig.installation))


@pytest.fixture
def adopted(env, tmp_path):
    """CoS the way Dev runs it: adopted through Approved Agents, so its worker credentials carry an approval generation
    and the observe credential joins that worker installation afterwards (Dev, 7 Nov 2026)."""
    env.prove(); env.join()
    manage = _load('records_poc_manage_for_tests_observe_adopted', 'manage.py')
    manage.provision_rooms(env.store, 'cos', 'Chief of Staff', tmp_path / 'cos-secrets', worker='rooms-worker-cos')
    with env.store.connect() as db:
        db.execute("UPDATE teammates SET proven_at=? WHERE principal='cos'", (datetime.now(timezone.utc).isoformat(),))
    agents = env.store.approved_agents
    generation = agents.change('robert', 'cos', True)['generation']
    issued = agents.issue('cos', generation)
    with env.store.connect() as db:
        brief_hash = db.execute("SELECT brief_hash FROM agent_approvals WHERE agent='cos'").fetchone()[0]
    agents.acknowledge('cos', dict(generation=generation, brief_hash=brief_hash,
                                   member_id=issued['credentials']['membership']['credential_id'],
                                   worker_id=issued['credentials']['work']['credential_id']))
    assert env.call('POST', f'/rooms/{env.room}/invite', {'actor': 'cos'})[0] == 201
    work = Records(BACKEND, issued['credentials']['work']['token'], session=Session(env.app))
    member = Records(BACKEND, issued['credentials']['membership']['token'], session=Session(env.app))
    Worker(work, member, FakeRelay(), TurnJournal(tmp_path / 'cos-journal'), teammate='cos',
           agent_id='cos-agent-a').housekeeping()
    observe = env.store.platform_access.issue('robert', dict(
        principal='approved-worker-cos', label='cos-scheduler observe', scope='observe',
        installation_id='approved:cos:work', expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    env.work, env.observer = work, Records(BACKEND, observe['access_token'], session=Session(env.app))
    return env


def test_observer_of_an_approved_agent_reads_the_snapshot(adopted):
    turn = _claimed_running(adopted)
    status, body = adopted.observer.call('POST', f"/turns/{turn['id']}/heartbeat",
                                         {'claim_id': turn['claim_id'], 'intent': 'dispatch', 'snapshot': True},
                                         key=str(uuid4()))
    assert status == 200 and body['dispatch'] is True
    assert body['turn']['trigger']['text'] == 'CoS, please help me plan tomorrow.'


def test_observer_of_an_approved_agent_is_still_only_an_observer(adopted):
    adopted.say('CoS, please help me plan tomorrow.')
    assert adopted.observer.call('POST', '/turns/claim', {'addressees': ['cos'], 'discussion_protocol': 1},
                                 key=str(uuid4()))[0] == 403
    assert adopted.observer.call('GET', f'/rooms/{adopted.room}')[0] in (401, 403)


def test_observer_ends_when_the_approval_ends(adopted):
    turn = _claimed_running(adopted)
    adopted.store.approved_agents.change('robert', 'cos', False)
    status, _ = adopted.observer.call('POST', f"/turns/{turn['id']}/heartbeat", {'claim_id': turn['claim_id']},
                                      key=str(uuid4()))
    assert status == 401


def test_observer_stops_while_the_approval_is_not_active(adopted):
    """Even with a credential not yet revoked: only an active approval's installation may observe."""
    turn = _claimed_running(adopted)
    with adopted.store.connect() as db:
        db.execute("UPDATE agent_approvals SET state='pending' WHERE agent='cos'")
    status, _ = adopted.observer.call('POST', f"/turns/{turn['id']}/heartbeat", {'claim_id': turn['claim_id']},
                                      key=str(uuid4()))
    assert status == 401
