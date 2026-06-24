import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "domains" / "german"))


@pytest.fixture(scope="session")
def portal_client():
    from minimoi_portal.app import app
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as client:
        yield client


@pytest.fixture(scope="session")
def curator_client():
    from curator_server import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(scope="session")
def german_client():
    from html_server import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
