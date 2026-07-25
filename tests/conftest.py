import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "domains" / "german"))

# Importing the German Flask application must not create a repository-local
# .flask_secret during tests.
os.environ.setdefault("FLASK_SECRET", "minimoi-test-secret")

# NOTE on all four fixtures below: none use `with app.test_client() as
# client:`. That form preserves each request's context on Flask's global
# context stack for later inspection (e.g. flask.g) -- safe with a single
# such client, but multiple session-scoped preserving clients (one per
# domain app under test in the same pytest session) corrupt the shared
# stack once their requests interleave across test functions ("Popped
# wrong app context" / "Popped wrong request context"). Confirmed directly
# when adding portuguese_client alongside the pre-existing three: the
# collision reproduced between german_client and portal_client, not
# portuguese_client itself, and depends on pytest's test collection order
# rather than any one fixture. A plain (non-`with`) test client pushes and
# pops its own context per request instead, which is self-contained and
# safe to interleave. No existing test relies on post-request context
# inspection -- every usage only reads the response object or `.application`
# -- so this changes no test's observable behavior.


@pytest.fixture(scope="session")
def portal_client():
    from minimoi_portal.app import app
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


@pytest.fixture(scope="session")
def curator_client():
    from domains.curator.curator_server import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(scope="session")
def german_client():
    from html_server import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(scope="session")
def portuguese_client():
    # German's and Portuguese's Flask apps are both literally named
    # html_server.py -- a plain `from html_server import app` after this
    # fixture would return the already-cached German module from
    # sys.modules, not load Portuguese's file. Load it under a distinct
    # module name via importlib to avoid that collision.
    import importlib.util

    pt_path = ROOT / "domains" / "portuguese" / "html_server.py"
    spec = importlib.util.spec_from_file_location("portuguese_html_server", pt_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["portuguese_html_server"] = module
    spec.loader.exec_module(module)

    app = module.app
    app.config["TESTING"] = True
    return app.test_client()
