from unittest.mock import patch

from flask import Flask

from minimoi_portal.proxy import proxy_to


class _BackendResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"ok": true}'
    text = '{"ok": true}'


def test_proxy_replaces_client_identity_with_authenticated_portal_identity():
    app = Flask(__name__)
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _BackendResponse()

    with app.test_request_context(
        "/app/german/gesprache",
        headers={
            "X-Minimoi-Auth-Id": "999",
            "X-Minimoi-Display-Name": "Spoofed",
        },
    ), patch("minimoi_portal.proxy.requests.request", side_effect=fake_request):
        response = proxy_to(
            "http://german:8767",
            "gesprache",
            "/app/german",
            user={
                "auth_id": 3,
                "username": "robert",
                "display_name": "Robert",
                "tier": "owner",
            },
        )

    assert response.status_code == 200
    assert captured["headers"]["X-Minimoi-Auth-Id"] == "3"
    assert captured["headers"]["X-Minimoi-Display-Name"] == "Robert"
    assert captured["headers"]["X-Minimoi-Username"] == "robert"


def test_managed_voice_verification_marker_is_only_portal_derived():
    app=Flask(__name__)
    for verified,expected in ((False,False),(True,True)):
        captured={}
        def forward(**kwargs):
            captured.update(kwargs);return _BackendResponse()
        with app.test_request_context('/app/cos/api/realtime-voice/managed/session',method='POST',
                headers={'X-Minimoi-Voice-Write-Verified':'1','X-CSRF-Token':'private-token'}), \
                patch('minimoi_portal.proxy.requests.request',side_effect=forward):
            proxy_to('http://cos:8769','api/realtime-voice/managed/session','/app/cos',
                     user={'tier':'owner','auth_id':3},verified_cos_voice_write=verified)
        assert ('X-Minimoi-Voice-Write-Verified' in captured['headers']) is expected
        assert not any(k.lower()=='x-csrf-token' for k in captured['headers'])


def test_cos_guildhall_link_stays_at_portal_root_while_backend_links_are_prefixed():
    from bs4 import BeautifulSoup
    app=Flask(__name__)
    body='<a data-portal-link href="/guild-next/guild/rooms">Guildhall</a><a href="/ui/confer">Confer</a><script src="/static/cos-history-ui.js"></script>'
    response=type('HTMLResponse',(),{'status_code':200,'headers':{'content-type':'text/html'},'content':body.encode(),'text':body})()
    with app.test_request_context('/app/cos/ui/confer'), patch('minimoi_portal.proxy.requests.request',return_value=response):
        result=proxy_to('http://cos:8769','ui/confer','/app/cos',user={'tier':'owner','username':'robert'})
    soup=BeautifulSoup(result.get_data(as_text=True),'html.parser')
    assert soup.find('a',string='Guildhall')['href']=='/guild-next/guild/rooms'
    assert soup.find('a',string='Confer')['href']=='/app/cos/ui/confer'
    assert soup.find('script',src=True)['src']=='/app/cos/static/cos-history-ui.js'
