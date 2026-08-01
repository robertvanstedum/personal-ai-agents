from unittest.mock import patch

from flask import Flask

from minimoi_portal.proxy import proxy_to


class _BackendResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"ok": true}'
    text = '{"ok": true}'


class _HtmlBackendResponse:
    status_code = 200
    headers = {"content-type": "text/html"}
    content = b"""<!doctype html><html><head><style>
      body::before { background: url('/static/curator/landing-bg.jpg'); }
      body::after { background: url('//cdn.example.test/background.jpg'); }
    </style></head><body><main style="background:url('/static/card.jpg')"></main></body></html>"""
    text = content.decode("utf-8")


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


def test_proxy_rewrites_asset_urls_inside_style_blocks_and_attributes():
    app = Flask(__name__)

    with app.test_request_context("/app/curator"), patch(
        "minimoi_portal.proxy.requests.request",
        return_value=_HtmlBackendResponse(),
    ):
        response = proxy_to(
            "http://curator:8766",
            "",
            "/app/curator",
            user={"username": "robert", "tier": "owner"},
        )

    html = response.get_data(as_text=True)
    assert "url('/app/curator/static/curator/landing-bg.jpg')" in html
    assert "url('/app/curator/static/card.jpg')" in html
    assert "url('//cdn.example.test/background.jpg')" in html
