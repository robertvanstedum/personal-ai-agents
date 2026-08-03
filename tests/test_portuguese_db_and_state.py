"""
tests/test_portuguese_db_and_state.py — Portuguese parity fix (2026-08-02):
_db_conn() in both html_server.py and leitura_rss.py had a hardcoded
fallback DSN with a password rotated away 2026-07-16/17, and a
get_secret("DATABASE_URL") call with no keyring service/account -- which
skips Keychain and falls straight to AWS SSM, meaning a dev Mac with
boto3 installed could silently read production's database credential.
_get_vocabulary() also caught every exception (including a broken
connection) and returned [], making a DB outage look identical to
"you have no vocabulary yet".

These tests prove:
  - both _db_conn() functions require environment_scoped resolution (no
    hardcoded fallback, can't cross into production's SSM from dev)
  - a vocabulary fetch failure is distinguishable from a genuine empty
    result, and the /palavras page renders a visible error instead of a
    silently-empty list

Portuguese's html_server.py and German's html_server.py share a literal
filename, so -- exactly like the portuguese_client fixture in
conftest.py -- this file always loads Portuguese's copy under a distinct
sys.modules name rather than `import html_server`, which would silently
return whichever one was imported first.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PT_HTML_SERVER_PATH = ROOT / "domains" / "portuguese" / "html_server.py"
PT_LEITURA_RSS_PATH = ROOT / "domains" / "portuguese" / "leitura_rss.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pt_html_server():
    return _load_module(PT_HTML_SERVER_PATH, "portuguese_html_server_dbtest")


@pytest.fixture
def pt_leitura_rss():
    return _load_module(PT_LEITURA_RSS_PATH, "portuguese_leitura_rss_dbtest")


def test_html_server_db_conn_has_no_hardcoded_fallback(pt_html_server):
    import inspect
    source = inspect.getsource(pt_html_server._db_conn)
    assert "simple123" not in source
    assert "environment_scoped=True" in source


def test_leitura_rss_db_conn_has_no_hardcoded_fallback(pt_leitura_rss):
    import inspect
    source = inspect.getsource(pt_leitura_rss._db_conn)
    assert "simple123" not in source
    assert "environment_scoped=True" in source


def test_html_server_db_conn_requires_explicit_role(pt_html_server, monkeypatch):
    """environment_scoped=True refuses to guess an environment -- proven
    directly against get_secret.py's own contract in
    test_get_secret_environment_scoping.py; this just confirms
    html_server.py actually wired the call correctly rather than, say,
    still passing keyring_service=None."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MINIMOI_ROLE", raising=False)
    with pytest.raises(RuntimeError, match="MINIMOI_ROLE"):
        pt_html_server._db_conn()


def test_vocabulary_fetch_failure_raises_distinct_exception(pt_html_server, monkeypatch):
    def _broken_conn():
        raise RuntimeError("simulated connection failure")

    monkeypatch.setattr(pt_html_server, "_db_conn", _broken_conn)
    with pytest.raises(pt_html_server.VocabularyUnavailable):
        pt_html_server._get_vocabulary(user_id=2)


def test_vocabulary_none_user_id_returns_empty_not_unavailable(pt_html_server, monkeypatch):
    """A genuinely unresolved identity is a different, already-handled
    case (fail closed, no query at all) -- must not be confused with a
    DB outage."""
    def _forbidden():
        raise AssertionError("must not query the DB when user_id is None")

    monkeypatch.setattr(pt_html_server, "_db_conn", _forbidden)
    assert pt_html_server._get_vocabulary(user_id=None) == []


def test_palavras_route_shows_visible_error_on_db_failure(pt_html_server, monkeypatch):
    def _broken_conn():
        raise RuntimeError("simulated connection failure")

    monkeypatch.setattr(pt_html_server, "_db_conn", _broken_conn)
    monkeypatch.setattr(pt_html_server, "_request_user_id", lambda: 2)
    pt_html_server.app.config["TESTING"] = True
    client = pt_html_server.app.test_client()

    resp = client.get("/palavras")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "indispon" in page.lower()  # "indisponível" -- the visible error copy


def test_palavras_route_no_error_banner_on_healthy_empty_result(pt_html_server, monkeypatch):
    monkeypatch.setattr(pt_html_server, "_get_vocabulary", lambda *a, **k: [])
    monkeypatch.setattr(pt_html_server, "_request_user_id", lambda: 2)
    pt_html_server.app.config["TESTING"] = True
    client = pt_html_server.app.test_client()

    resp = client.get("/palavras")
    page = resp.get_data(as_text=True)
    assert "indispon" not in page.lower()


def test_save_phrase_error_response_never_leaks_exception_detail(pt_html_server, monkeypatch):
    def _broken_conn():
        raise RuntimeError("postgresql://minimoi:supersecret@host/db unreachable")

    monkeypatch.setattr(pt_html_server, "_db_conn", _broken_conn)
    monkeypatch.setattr(pt_html_server, "_request_user_id", lambda: 2)
    pt_html_server.app.config["TESTING"] = True
    client = pt_html_server.app.test_client()

    resp = client.post(
        "/api/pt/save-phrase",
        json={"portuguese": "teste", "english": "test"},
    )
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["ok"] is False
    assert "supersecret" not in data["error"]
    assert "postgresql://" not in data["error"]


@pytest.fixture
def isolated_pt_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "external-pt-state"
    state_dir.mkdir()
    monkeypatch.setenv("PORTUGUESE_DATA_DIR", str(state_dir))
    module = _load_module(PT_HTML_SERVER_PATH, "portuguese_html_server_statedirtest")
    return state_dir, module


def test_writing_sessions_path_never_touches_release_checkout(isolated_pt_state_dir):
    state_dir, pt_html_server = isolated_pt_state_dir
    path = pt_html_server._writing_sessions_path(2)
    assert str(path).startswith(str(state_dir))
    assert "domains/portuguese/data" not in str(path)


def test_conversas_sessions_path_never_touches_release_checkout(isolated_pt_state_dir):
    state_dir, pt_html_server = isolated_pt_state_dir
    path = pt_html_server._conversas_sessions_path(2)
    assert str(path).startswith(str(state_dir))
    assert "domains/portuguese/data" not in str(path)


def test_portuguese_health_reports_commit_and_dirty_state(portuguese_client):
    """Parity with German's dev-drift fix (2026-08-02): /health used to
    report nothing about which commit was actually running, or whether
    the tree had uncommitted changes. Both fields must be present, and
    'dirty' must not be None whenever a real commit SHA is reported."""
    r = portuguese_client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert "commit" in data
    assert "dirty" in data
    if data["commit"] is not None:
        assert data["dirty"] is not None


def test_config_paths_stay_with_checkout_even_when_state_dir_overridden(isolated_pt_state_dir):
    """leitura_sources.json and personas.json are application config, not
    personal state -- overriding PORTUGUESE_DATA_DIR must not move them."""
    state_dir, pt_html_server = isolated_pt_state_dir
    assert str(pt_html_server._LEITURA_SOURCES_FILE).endswith(
        "domains/portuguese/data/leitura_sources.json"
    )
    assert str(pt_html_server._PERSONAS_JSON).endswith(
        "domains/portuguese/data/personas.json"
    )
