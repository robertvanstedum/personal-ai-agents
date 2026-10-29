"""Rejected Research identifiers stay text in real Flask error responses."""
import html
from html.parser import HTMLParser
from urllib.parse import quote

from flask import Flask
import pytest
from domains.curator import research_routes as routes


class Tags(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.tags = []
        self.feed(value)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, 'SCANS_DIR', tmp_path / 'scans')
    monkeypatch.setattr(routes, 'DIVES_DIR', tmp_path / 'dives')
    app = Flask('synthetic-research-errors')
    app.register_blueprint(routes.research_bp)
    return app.test_client(), tmp_path


@pytest.mark.parametrize('value', ['<img src=x onerror=alert(1)>', '<svg onload=alert(1)>',
                                  '<a href=javascript:alert(1)>', "\" & ' <b>"])
def test_scan_error_escapes_encoded_identifier(client, value):
    http, root = client
    response = http.get('/research/scan/' + quote(value, safe=''))
    text = response.get_data(as_text=True)
    assert response.status_code == 404
    assert Tags(text).tags == []
    assert html.unescape(text) == f"Scan '{value}' not found"
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('value', ['<img src=x onerror=alert(1)>', '<svg onload=alert(1)>'])
def test_dive_rejects_markup_before_missing_file_response(client, value):
    http, root = client
    response = http.get('/research/dive-result/' + quote(value, safe=''))
    assert response.status_code == 400
    assert response.get_data(as_text=True) == 'invalid filename'
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('path,expected', [('/research/scan/abc12', "Scan 'abc12' not found"),
    ('/research/dive-result/synthetic-missing', "Dive 'synthetic-missing' not found")])
def test_ordinary_missing_identifier_message_is_unchanged(client, path, expected):
    http, root = client
    response = http.get(path)
    assert response.status_code == 404 and response.get_data(as_text=True) == expected
    assert list(root.iterdir()) == []
