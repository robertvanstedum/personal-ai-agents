import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from domains.curator import curator_server
from domains.curator import research_routes


ROOT = Path(__file__).resolve().parents[1]


def test_web_feedback_uses_submitted_article_without_ai_or_legacy_output(
    tmp_path, monkeypatch
):
    feedback = importlib.import_module("domains.curator.curator_feedback")
    prefs_path = tmp_path / "curator_preferences.json"
    logged = []

    monkeypatch.setattr(feedback, "PREFERENCES_FILE", prefs_path)
    monkeypatch.setattr(
        feedback,
        "extract_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("generic web feedback must not call an LLM")
        ),
    )
    monkeypatch.setattr(feedback, "log_feedback", lambda **kwargs: logged.append(kwargs))

    article = {
        "id": "abc12",
        "title": "A current article",
        "link": "https://example.com/current",
        "source": "Example",
        "category": "fiscal",
    }
    result = curator_server.record_feedback_with_article("save", 4, article)

    assert result == {
        "success": True,
        "message": "Article #4 saved",
        "duplicate": False,
    }
    saved = json.loads(prefs_path.read_text())
    entry = saved["feedback_history"][datetime.now().strftime("%Y-%m-%d")]["saved"][0]
    assert entry["article_id"] == "abc12"
    assert entry["url"] == "https://example.com/current"
    assert entry["your_words"] == "Saved from Daily briefing"
    assert entry["extracted_signals"]["content_type"] == []
    assert len(logged) == 1

    duplicate = curator_server.record_feedback_with_article("save", 4, article)
    saved_again = json.loads(prefs_path.read_text())
    assert duplicate["duplicate"] is True
    assert saved_again["learned_patterns"]["sample_size"] == 1
    assert len(logged) == 1


@pytest.mark.parametrize(
    ("action", "bucket", "message", "note"),
    [
        ("like", "liked", "Article #2 liked", "Liked from Daily briefing"),
        ("dislike", "disliked", "Article #2 passed", "Passed from Daily briefing"),
        ("save", "saved", "Article #2 saved", "Saved from Daily briefing"),
    ],
)
def test_each_daily_feedback_action_is_persisted(
    action, bucket, message, note, tmp_path, monkeypatch
):
    feedback = importlib.import_module("domains.curator.curator_feedback")
    prefs_path = tmp_path / "curator_preferences.json"
    monkeypatch.setattr(feedback, "PREFERENCES_FILE", prefs_path)
    monkeypatch.setattr(feedback, "log_feedback", lambda **kwargs: None)

    result = curator_server.record_feedback_with_article(action, 2, {
        "hash_id": f"{action}-article",
        "title": "A current article",
        "url": "https://example.com/current",
        "source": "Example",
        "category": "fiscal",
    })

    assert result["success"] is True
    assert result["message"] == message
    saved = json.loads(prefs_path.read_text())
    entry = saved["feedback_history"][datetime.now().strftime("%Y-%m-%d")][bucket][0]
    assert entry["article_id"] == f"{action}-article"
    assert entry["your_words"] == note


def test_feedback_templates_use_current_payload_and_background_scan_routes():
    daily = (ROOT / "domains/curator/templates/curator_briefing.html").read_text()
    library = (ROOT / "domains/curator/templates/curator_library.html").read_text()

    assert "hash_id:  hashId" in daily
    assert "url:      row.dataset.url" in daily
    assert "/api/research/generate-scan" in daily
    assert "/api/research/generate-scan/status" in daily
    assert "fetch('/deepdive" not in daily

    assert "Create scan →" in library
    assert "/api/research/generate-scan" in library
    assert "/api/research/generate-scan/status" in library
    assert "fetch(`/deepdive" not in library


def test_generate_scan_api_starts_and_reports_background_job(
    curator_client, tmp_path, monkeypatch
):
    research_root = tmp_path / "research-intelligence"
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir()
    state_path = research_root / "data" / "scan_run_state.json"

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(research_routes, "RESEARCH_ROOT", research_root)
    monkeypatch.setattr(research_routes, "SCANS_DIR", scans_dir)
    monkeypatch.setattr(research_routes, "_SCAN_STATE_PATH", state_path)
    monkeypatch.setattr(research_routes, "_scan_proc", None)
    monkeypatch.setattr(research_routes.subprocess, "Popen", lambda *a, **k: FakeProcess())

    response = curator_client.post("/api/research/generate-scan", json={
        "hash_id": "abc12",
        "interest": "Why this matters",
        "focus": "Primary evidence",
        "article": {
            "title": "A current article",
            "url": "https://example.com/current",
            "source": "Example",
            "category": "fiscal",
        },
    })
    assert response.status_code == 200
    assert response.get_json()["running"] is True

    payload = json.loads(
        (research_root / "data" / "scan_jobs" / "abc12.json").read_text()
    )
    assert payload["article"]["url"] == "https://example.com/current"
    assert payload["interest"] == "Why this matters"

    running = curator_client.get(
        "/api/research/generate-scan/status?hash_id=abc12"
    )
    assert running.status_code == 200
    assert running.get_json()["running"] is True

    (scans_dir / "abc12-current-article.md").write_text("# A current article")
    complete = curator_client.get(
        "/api/research/generate-scan/status?hash_id=abc12"
    )
    assert complete.status_code == 200
    assert complete.get_json()["view_url"] == "/research/scan/abc12"


def test_stale_briefing_uses_file_date_instead_of_today(tmp_path, monkeypatch):
    latest = tmp_path / "curator_latest.json"
    latest.write_text(json.dumps([{
        "title": "Stale but valid",
        "link": "https://example.com/stale",
        "source": "Example",
        "category": "other",
    }]))
    generated = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
    os.utime(latest, (generated.timestamp(), generated.timestamp()))
    monkeypatch.setattr(curator_server, "_DATA_DIR", tmp_path)

    _, day, date_label, _, briefing_date = curator_server._load_briefing_articles()

    assert day == "Thursday"
    assert date_label == "July 23, 2026"
    assert briefing_date == "2026-07-23"


def test_production_deploy_refreshes_host_curator_cron_scripts():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    curator_cron = (ROOT / "scripts/run_curator_cron_ec2.sh").read_text()

    for variable, path in (
        ("CURATOR_CRON", "scripts/run_curator_cron_ec2.sh"),
        ("INTELLIGENCE_CRON", "scripts/run_intelligence_cron_ec2.sh"),
        ("CURATOR_CRON_SETUP", "scripts/setup_ec2_cron.sh"),
    ):
        assert f"{variable}=$(base64 -w 0 {path})" in workflow

    assert "runuser -u ec2-user -- /opt/minimoi/scripts/setup_ec2_cron.sh" in workflow
    assert "python -m scripts.x.x_pull_incremental" in curator_cron
    assert "python domains/curator/curator_rss_v2.py" in curator_cron
    assert "python core/telegram/telegram_bot.py --send" in curator_cron
