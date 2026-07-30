"""
Regression coverage for research_routes.py's import and Flask blueprint
registration. curator_server.py wraps the blueprint registration in a
try/except specifically so a failure there can't crash the whole server —
which means a broken import silently disables every /research/* route
without raising anywhere a normal test would notice. This test asserts the
blueprint actually registered, not just that curator_server.py imported
without an exception.
"""

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

from domains.curator import research_routes
from domains.curator.research_routes import research_bp, save_annotation, _get_recent_annotations


ROOT = Path(__file__).resolve().parents[1]


def test_research_routes_imports_annotations_module():
    assert callable(save_annotation)
    assert callable(_get_recent_annotations)


def test_research_blueprint_has_deferred_routes():
    # deferred_functions holds every @research_bp.route(...) registered at
    # import time; a nonzero count confirms the module's route decorators
    # ran. The real end-to-end check is registration on the live app, below.
    assert len(research_bp.deferred_functions) > 0


def test_research_blueprint_registers_on_curator_app(curator_client):
    app = curator_client.application
    research_routes = [
        str(rule) for rule in app.url_map.iter_rules()
        if str(rule).startswith("/api/research/") or str(rule).startswith("/research/")
    ]
    assert "/api/research/annotate" in research_routes, (
        "Research Intelligence blueprint did not register — curator_server.py "
        "silently swallows this failure at startup (see the try/except around "
        "`from research_routes import research_bp`), so this must be checked "
        "explicitly rather than relying on import-time exceptions."
    )


def test_generate_dive_uses_shared_secret_lookup(monkeypatch):
    script = (
        ROOT
        / "_NewDomains"
        / "research-intelligence"
        / "scripts"
        / "generate_dive.py"
    )
    spec = importlib.util.spec_from_file_location("test_generate_dive", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = {}

    monkeypatch.setattr(
        module,
        "get_secret",
        lambda *args: calls.setdefault("secret_args", args) and "test-key",
    )

    class FakeAnthropic:
        def __init__(self, *, api_key):
            calls["api_key"] = api_key

    monkeypatch.setitem(
        sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic)
    )

    module.get_anthropic_client()

    assert calls["secret_args"] == (
        "ANTHROPIC_API_KEY",
        "anthropic",
        "api_key",
    )
    assert calls["api_key"] == "test-key"


def test_scan_research_directions_use_scan_questions():
    dd = {
        "title": "Ukraine Security Guarantees",
        "metadata": {
            "interest": "Assess whether the guarantees are credible.",
            "focus": "Prioritize primary agreements and official statements.",
        },
        "analysis_html": """
            <h2>5. Next Questions</h2>
            <ul>
              <li>Which commitments are legally binding?</li>
              <li>What enforcement mechanisms exist?</li>
            </ul>
        """,
    }

    searches, targets = research_routes._scan_research_directions(dd)

    assert searches == [
        "Ukraine Security Guarantees",
        "Which commitments are legally binding?",
        "What enforcement mechanisms exist?",
    ]
    assert targets == [
        "Assess whether the guarantees are credible.",
        "Prioritize primary agreements and official statements.",
        "Which commitments are legally binding?",
        "What enforcement mechanisms exist?",
    ]
    assert all("Mackinder" not in item for item in searches + targets)


def test_scan_parser_keeps_focus_out_of_interest(tmp_path):
    scan = tmp_path / "abc12-test.md"
    scan.write_text("""# Test Scan

**Source:** Example
**URL:** https://example.com
**Date:** 2026-07-30
**Hash ID:** abc12

## Your Interest

Understand the core claim.

**Focus:** Prefer primary evidence.

---

## Scan Analysis

### 1. Why This Matters

This is a sufficiently detailed finding for the parser to retain.
""")

    parsed = research_routes._parse_scan_md(scan)

    assert parsed["metadata"]["interest"] == "Understand the core claim."
    assert parsed["metadata"]["focus"] == "Prefer primary evidence."


def test_inbox_add_creates_missing_feedback_directory(
    curator_client, tmp_path, monkeypatch
):
    research_root = tmp_path / "research-intelligence"
    monkeypatch.setattr(research_routes, "RESEARCH_ROOT", research_root)

    response = curator_client.post("/api/research/inbox/add", json={
        "title": "A source worth researching",
        "url": "https://example.com/source",
        "has_url": True,
        "scan_id": "abc12",
    })

    assert response.status_code == 200
    candidates = (
        research_root / "data" / "feedback" / "query_candidates.json"
    )
    assert candidates.exists()
    assert json.loads(candidates.read_text())[0]["scan_id"] == "abc12"


def test_dive_status_surfaces_logged_failure(curator_client, tmp_path, monkeypatch):
    state_path = tmp_path / "dd_run_state.json"
    log_path = tmp_path / "failed-dive.log"
    output_path = tmp_path / "missing-dive.md"
    log_path.write_text("Error: Anthropic API key not found")
    state_path.write_text(json.dumps({
        "topic": "ukraine-security",
        "output_path": str(output_path),
        "log_path": str(log_path),
        "pid": 4242,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    class FailedProcess:
        def poll(self):
            return 1

    monkeypatch.setattr(research_routes, "_DD_STATE_PATH", state_path)
    monkeypatch.setattr(research_routes, "_dd_proc", FailedProcess())

    response = curator_client.get("/api/research/generate-dive/status")

    assert response.status_code == 500
    assert response.get_json()["failed"] is True
    assert "Anthropic API key not found" in response.get_json()["error"]
    assert not state_path.exists()


def test_scan_page_waits_for_dive_and_opens_result():
    source = (ROOT / "domains" / "curator" / "research_routes.py").read_text()

    assert "await fetch('/api/research/generate-dive/status')" in source
    assert "window.location.href = result.view_url" in source
    assert "Dive started — redirecting to Desk" not in source
