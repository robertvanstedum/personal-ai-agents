"""Shared domain clients select explicit modules, never an ambiguous basename."""
import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Other test directories have their own conftest module. Exercise the exact
# shared fixture source regardless of which directory pytest collected first.
_fixture_spec = importlib.util.spec_from_file_location(
    "_renewal_domain_client_fixtures", Path(__file__).with_name("conftest.py")
)
shared_fixtures = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(shared_fixtures)


@pytest.mark.parametrize("fixture_name, module_name", [
    ("german_client", "domains.german.html_server"),
    ("portuguese_client", "domains.portuguese.html_server"),
])
def test_client_ignores_foreign_html_server(monkeypatch, fixture_name, module_name):
    # A different domain may already own the unqualified name in this process.
    foreign = SimpleNamespace(app=object())
    monkeypatch.setitem(sys.modules, "html_server", foreign)
    fixture = getattr(shared_fixtures, fixture_name)
    client = fixture.__wrapped__()
    module = importlib.import_module(module_name)
    assert client.application is module.app
    assert sys.modules["html_server"] is foreign
    assert client.application.import_name == module_name


def test_domain_clients_are_distinct_and_interleave(german_client, portuguese_client):
    assert german_client.application is not portuguese_client.application
    for client in (german_client, portuguese_client, german_client, portuguese_client):
        response = client.get("/static/realtime-voice/realtime-voice-controller.js")
        assert response.status_code == 200
