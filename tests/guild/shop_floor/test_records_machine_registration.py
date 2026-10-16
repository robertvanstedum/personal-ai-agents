"""Actual portal bootstrap registers machine transport only for explicit production."""
import pytest
from floor_helpers import load_portal  # noqa: F401

FLAGS = {'MINIMOI_ROLE': 'production', 'MINIMOI_RECORDS_PROFILE': 'production',
         'MINIMOI_RECORDS_PRODUCTION': '1', 'MINIMOI_RECORDS_MACHINE_BRIDGE': '1',
         'RECORDS_BACKEND': 'http://minimoi-records:18880'}

@pytest.mark.parametrize('origin,missing', [
    ('https://dev.minimoi.ai', None),
    ('https://minimoi.ai', 'MINIMOI_ROLE'),
    ('https://minimoi.ai', 'MINIMOI_RECORDS_PROFILE'),
    ('https://minimoi.ai', 'MINIMOI_RECORDS_PRODUCTION'),
    ('https://minimoi.ai', 'MINIMOI_RECORDS_MACHINE_BRIDGE'),
])
def test_actual_portal_refuses_partial_or_dev_machine_activation(load_portal, monkeypatch, origin, missing):
    for name, value in FLAGS.items():
        if name == missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    portal = load_portal(base_url=origin)
    assert not [r for r in portal.app.url_map.iter_rules() if r.rule.startswith('/internal/records/')]


def test_actual_portal_machine_route_has_no_cookie_or_owner_login_fallback(load_portal, monkeypatch):
    import requests
    for name, value in FLAGS.items():
        monkeypatch.setenv(name, value)
    def forbidden(*args, **kwargs):
        pytest.fail('Refused request must not contact an upstream')
    monkeypatch.setattr(requests, 'Session', forbidden)
    portal = load_portal(base_url='https://minimoi.ai')
    rules = [r.rule for r in portal.app.url_map.iter_rules() if r.rule.startswith('/internal/records/')]
    assert rules == ['/internal/records/<path:path>']
    path = '/internal/records/api/v1/turns/claim'
    response = portal.client().post(path, base_url='https://minimoi.ai', json={})
    assert response.status_code == 401 and 'Location' not in response.headers
    owner = portal.client()
    with owner.session_transaction(base_url='https://minimoi.ai') as session:
        session['user'] = {'username': 'robert', 'tier': 'owner', 'auth_id': 1}
    response = owner.post(path, base_url='https://minimoi.ai', json={})
    assert response.status_code == 403 and response.get_json()['error'] == 'machine_channel_required'
    assert portal.client().post(path, base_url='https://dev.minimoi.ai', json={}).status_code == 404
