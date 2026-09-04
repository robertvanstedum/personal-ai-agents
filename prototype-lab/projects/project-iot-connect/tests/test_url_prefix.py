"""IOTCONNECT_ROOT_PATH: run under a reverse-proxy path prefix.

Unset → pages are served byte-for-byte from disk and no meta tag is injected.
Set → FastAPI root_path makes /docs and /openapi.json prefix-correct, every
served page carries one <meta name="iotconnect-root-path"> tag, and every
root-absolute href/src is prefixed. Served JavaScript must not hardcode
root-absolute /static, /api/v1, href="/ or src="/ references.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.main import STATIC_DIR, create_app, prefix_html, resolve_root_path
from app.repositories.memory import MemoryRepository

PREFIX = "/app/iotconnect"
PAGES = {
    "/": "iotconnect/gateway.html",
    "/presentation": "iotconnect/presentation.html",
    "/admin": "admin.html",
    "/workbench": "index.html",
    "/artifacts/invoice-comparison": "invoice-comparison.html",
    "/artifacts/statement": "statement.html",
    "/portal": "iotconnect/portal.html",
    "/portal/subscriptions": "iotconnect/portal-subscriptions.html",
    "/portal/actions": "iotconnect/portal-actions.html",
    "/portal/billing": "iotconnect/portal-billing.html",
    "/operator": "iotconnect/operator.html",
    "/operator/accounts": "iotconnect/operator-accounts.html",
    "/operator/accounts/new": "iotconnect/operator-account-new.html",
    "/operator/account": "iotconnect/operator-account.html",
    "/operator/account/configuration": "iotconnect/operator-account-configuration.html",
    "/operator/subscriptions": "iotconnect/operator-subscriptions.html",
    "/operator/inventory": "iotconnect/operator-inventory.html",
    "/operator/bill-cycles": "iotconnect/operator-bill-cycles.html",
    "/operator/catalog": "iotconnect/operator-catalog.html",
    "/operator/api-activity": "iotconnect/operator-api-activity.html",
}
SCRIPTS = [
    "/static/api-client.js",
    "/static/iotconnect/iotconnect.js",
    "/static/admin.js",
    "/static/demo.js",
    "/static/invoice-comparison.js",
    "/static/statement.js",
]
ROOT_ABSOLUTE = re.compile(r'(?:href|src|action)="/[^"]*"|["\'`]/static/[^"\'`]*|["\'`]/api/v1[^"\'`]*')


def unprefixed_references(document: str, prefix: str) -> list[str]:
    found = []
    for match in ROOT_ABSOLUTE.finditer(document):
        reference = match.group(0)
        value = reference.split("=", 1)[1] if "=" in reference[:8] else reference
        value = value.strip("\"'`")
        if not (value == prefix or value.startswith(prefix + "/")):
            found.append(reference)
    return sorted(set(found))


@pytest.fixture
def prefixed():
    return TestClient(create_app(MemoryRepository(), root_path=PREFIX))


@pytest.fixture
def plain(monkeypatch):
    monkeypatch.delenv("IOTCONNECT_ROOT_PATH", raising=False)
    return TestClient(create_app(MemoryRepository()))


def test_openapi_reports_the_prefixed_server_and_docs_load(prefixed):
    schema = prefixed.get(f"{PREFIX}/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["servers"] == [{"url": PREFIX}]
    docs = prefixed.get(f"{PREFIX}/docs")
    assert docs.status_code == 200
    assert f"{PREFIX}/openapi.json" in docs.text
    assert prefixed.get(f"{PREFIX}/api/v1/health").json()["service"] == "IoT Connect"


def test_every_page_is_prefixed_and_carries_one_meta_tag(prefixed):
    for route in PAGES:
        page = prefixed.get(f"{PREFIX}{route}")
        assert page.status_code == 200, route
        assert page.text.count(f'<meta name="iotconnect-root-path" content="{PREFIX}">') == 1, route
        assert unprefixed_references(page.text, PREFIX) == [], route
    # /project-design renders docs/ARCHITECTURE.md as escaped prose (its
    # backticked path mentions are documentation, not references); its only
    # server-built link is the header link, which must be prefixed.
    design = prefixed.get(f"{PREFIX}/project-design")
    assert design.status_code == 200
    assert f'href="{PREFIX}/"' in design.text
    assert 'href="/"' not in design.text
    assert prefixed.get(f"{PREFIX}/presentation/assets/slide-1.png").status_code == 200
    assert prefixed.get(f"{PREFIX}/static/api-client.js").status_code == 200


def test_served_javascript_has_no_hardcoded_root_absolute_references(prefixed):
    for script in SCRIPTS:
        body = prefixed.get(f"{PREFIX}{script}").text
        assert unprefixed_references(body, PREFIX) == [], script
    client = prefixed.get(f"{PREFIX}/static/api-client.js").text
    assert 'meta[name="iotconnect-root-path"]' in client
    assert "function appPath(" in client


def test_default_case_serves_pages_byte_for_byte_from_disk(plain):
    for route, relative in PAGES.items():
        page = plain.get(route)
        assert page.status_code == 200, route
        assert page.content == (STATIC_DIR / relative).read_bytes(), route
        assert "iotconnect-root-path" not in page.text, route
    assert "servers" not in plain.get("/openapi.json").json()
    assert plain.get("/api/v1/health").status_code == 200


def test_root_path_normalisation_and_validation(monkeypatch):
    assert resolve_root_path("") == ""
    assert resolve_root_path("/app/iotconnect/") == "/app/iotconnect"
    monkeypatch.setenv("IOTCONNECT_ROOT_PATH", "/x")
    assert resolve_root_path(None) == "/x"
    with pytest.raises(RuntimeError, match="must start with '/'"):
        resolve_root_path("app/iotconnect")


def test_prefix_html_rewrites_attributes_and_injects_meta_once():
    document = '<html><head><title>t</title></head><body><a href="/">x</a><img src="/s.png"><a href="//cdn/x">y</a><a href="http://h/">z</a></body></html>'
    rendered = prefix_html(document, PREFIX)
    assert rendered.count('<meta name="iotconnect-root-path"') == 1
    assert f'href="{PREFIX}/"' in rendered and f'src="{PREFIX}/s.png"' in rendered
    assert 'href="//cdn/x"' in rendered and 'href="http://h/"' in rendered
