"""The copied CoS Board keeps notes separate and retains Board write guards."""
from floor_helpers import load_portal, write_headers
from floor_db_helpers import floor_db, floored, keyed

COS = '/guild-next/cos-board'


def test_cos_board_isolated_and_guarded(floored, floor_db):
    floored.app.extensions['cos_board']['services'].floor = floor_db.store('cos-board')
    client = floored.owner()
    token = floored.csrf(client)
    page = client.get(COS)
    assert page.status_code == 200
    assert b'Board \xc2\xb7 CoS' in page.data
    assert b'data-bd-grid' in page.data
    assert "frame-ancestors 'self'" in page.headers['Content-Security-Policy']
    assert b'/guild-next/cos-board/api/v1' in page.data
    headers = write_headers(token)
    saved = client.post(COS + '/api/v1/postits', json=keyed(text='CoS private board note'), headers=headers)
    assert saved.status_code == 200, saved.json
    note = saved.json['postit']
    assert len(client.get(COS + '/api/v1/board').json['active']) == 1
    assert client.get('/guild-next/api/v1/board').json['active'] == []
    # MC cannot mutate a CoS card by guessing its id.
    denied = client.post('/guild-next/api/v1/postits/' + str(note['id']) + '/done',
                         json=keyed(version=note['version']), headers=headers)
    assert denied.status_code == 404
    assert client.post(COS + '/api/v1/postits', json=keyed(text='missing csrf')).status_code == 403
    assert client.post(COS + '/api/v1/postits', json=keyed(text='off'),
                       headers=write_headers(token, mode='off_record')).status_code == 409
    assert client.post(COS + '/api/v1/mc/turns', json={}).status_code in (404, 405)
    assert client.get(COS + '/popout').status_code == 200
    assert client.get(COS + '/assets/js/cos_board.js').status_code == 200


def test_cos_board_requires_owner(floored):
    client = floored.app.test_client()
    assert client.get(COS).status_code in (302, 401, 403)
    assert client.get(COS + '/api/v1/board').status_code in (401, 403)
