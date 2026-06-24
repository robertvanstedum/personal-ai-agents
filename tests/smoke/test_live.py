"""
Post-deploy smoke tests against live EC2 endpoints.
Run manually after deploy: pytest tests/smoke/ --base-url https://minimoi.ai
Not part of the CI regression suite (no --base-url in CI).
"""

import pytest
import requests


def pytest_addoption(parser):
    parser.addoption("--base-url", default=None, help="Base URL for live smoke tests")


@pytest.fixture
def base_url(request):
    url = request.config.getoption("--base-url")
    if not url:
        pytest.skip("--base-url not provided")
    return url.rstrip("/")


def test_portal_live_health(base_url):
    r = requests.get(f"{base_url}/health", timeout=10)
    assert r.status_code == 200


def test_curator_live_health(base_url):
    r = requests.get(f"{base_url}/app/curator/health", timeout=10)
    assert r.status_code in [200, 302]


def test_german_live_health(base_url):
    r = requests.get(f"{base_url}/app/german/health", timeout=10)
    assert r.status_code in [200, 302]
