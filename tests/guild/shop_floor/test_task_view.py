"""Real scoped task records, reload/recovery, and the portal's owner guard."""
import pytest
from test_tasks_owner_api import rig, post
from floor_helpers import load_portal
from minimoi_portal.guild_ui import task_view as V, tasks_owner_api as T
from services.partnership_tasks.client import TaskServiceUnavailable

@pytest.fixture
def view(rig):
    svc, c = rig
    for rule, endpoint, fn, methods in V.RULES:
        c.application.add_url_rule(rule, endpoint, fn, methods=methods)
    return svc, c


def test_view_confirm_reload_cancel(view):
    svc, c = view
    pending = c.get('/tasks/view').json
    p = pending['proposals'][0]
    assert pending['work'] == []
    assert p['sources'][0]['content'].startswith('Track a small test')
    result = post(c, '/tasks/confirm', interpretation_id=p['interpretation_id'], proposal_digest=p['proposal_digest']).json
    tid = result['task_id']
    rows = c.get('/tasks/view').json
    assert not rows['proposals']
    opened = next(r for r in rows['work'] if r['id'] == tid)
    assert opened['outcome'] == p['proposed']['outcome']
    assert opened['created_at'] and opened['updated_at'] and opened['done_when']
    assert c.get('/tasks/item/' + tid).json['task']['lead'] == 'mc'
    assert post(c, '/tasks/cancel', task_id=tid, text='Cancel this synthetic task.').status_code == 200
    assert c.get('/tasks/item/' + tid).json['task']['state'] == 'canceled'
    assert all(r['id'] != tid for r in c.get('/tasks/view').json['work'])


def test_reload_of_partial_confirmation_does_not_resume_it(view, monkeypatch):
    svc, c = view
    p = c.get('/tasks/view').json['proposals'][0]
    real = T._send
    def stop(client, command):
        if command['kind'] == 'request.record':
            return None, T.json_error('unconfirmed', 'Synthetic interruption', 503)
        return real(client, command)
    monkeypatch.setattr(T, '_send', stop)
    args = dict(interpretation_id=p['interpretation_id'], proposal_digest=p['proposal_digest'])
    assert post(c, '/tasks/confirm', **args).status_code == 503
    monkeypatch.setattr(T, '_send', lambda *a: pytest.fail('GET must not issue commands'))
    recovered = c.get('/tasks/recover', query_string=args)
    assert recovered.status_code == 200 and recovered.json['proposal']['finishing']
    assert c.get('/tasks/view').json['work'] == []
    assert c.get('/tasks/recover', query_string={**args, 'proposal_digest': 'sha256:' + '0'*64}).status_code == 409


def test_service_failure_is_not_empty_work(view, monkeypatch):
    _, c = view
    class Offline:
        def pending(self):
            raise TaskServiceUnavailable('offline')
    monkeypatch.setattr(T, '_client', lambda: Offline())
    assert c.get('/tasks/view').status_code == 503
    assert c.get('/tasks/item/not-an-id').status_code == 422


def test_real_portal_owner_guard_and_task_page(load_portal, monkeypatch):
    portal = load_portal(reserve_pages=False)
    monkeypatch.setattr(T, '_client', lambda: None)
    for path in ['/guild-next/api/v1/tasks/view', '/guild-next/api/v1/tasks/item/TK-test-0001', '/guild-next/api/v1/tasks/recover']:
        assert portal.client().get(path).status_code == 401
        assert portal.guest().get(path).status_code == 403
    c = portal.owner()
    page = c.get('/guild-next/guild/tasks')
    assert page.status_code == 200
    assert b'data-task-view' in page.data and b'css/tasks.css' in page.data
    assert c.get('/guild-next/api/v1/tasks/view').status_code == 409


def test_open_rows_carry_the_dates_that_tell_two_health_checks_apart(view, monkeypatch):
    _, c = view
    class Rows:
        def pending(self): return []
        def open_work(self):
            return [{"type": "task", "id": "TK-hc-one", "state": "working"},
                    {"type": "task", "id": "TK-hc-two", "state": "working"}]
        def get(self, kind, rid):
            day = "2026-11-14" if rid.endswith("one") else "2026-11-15"
            return {"state": "working", "outcome": "Daily job-health note", "lead": "mc", "holder": "apprentice",
                    "done_when": '{"date": "%s"}' % day, "created_at": day + "T15:00:00+00:00",
                    "updated_at": day + "T16:00:00+00:00"}
    monkeypatch.setattr(T, "_client", lambda: Rows())
    rows = c.get("/tasks/view").json["work"]
    assert [r["id"] for r in rows] == ["TK-hc-one", "TK-hc-two"]
    assert rows[0]["done_when"] == '{"date": "2026-11-14"}' and rows[1]["done_when"] == '{"date": "2026-11-15"}'
    assert rows[0]["created_at"].startswith("2026-11-14") and rows[1]["updated_at"].startswith("2026-11-15")


def test_open_list_reconciles_new_terminal_state(view, monkeypatch):
    _, c = view
    class Changed:
        def pending(self): return []
        def open_work(self): return [{'type':'request','id':'RQ-test-0001','state':'active'}]
        def get(self, *args): return {'state':'answered','intent':{'text':'already answered'}}
    monkeypatch.setattr(T, '_client', lambda: Changed())
    data=c.get('/tasks/view').json
    assert data['work'] == [] and data['more'] is False
