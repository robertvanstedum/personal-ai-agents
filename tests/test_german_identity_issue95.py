import json
from pathlib import Path

import pytest


@pytest.fixture
def german_server(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET", "issue-95-test-secret")
    import html_server

    html_server.app.config.update(TESTING=True)
    return html_server


@pytest.mark.parametrize(
    ("endpoint", "payload", "writer_name"),
    [
        (
            "/api/write-save",
            {"mode": "tagebuch", "text_original": "Hallo"},
            "save_writing_entry",
        ),
        (
            "/api/note-save",
            {"article_id": "a1", "original": "Hallo"},
            "save_note",
        ),
        (
            "/api/save-phrase",
            {"german": "Guten Tag", "english": "Hello"},
            "save_lesen_phrase",
        ),
        (
            "/api/drill-result",
            {"phrase_id": "ph_1", "result": "correct"},
            "save_drill_result",
        ),
        (
            "/api/phrase-update",
            {"id": "ph_1", "status": "practice"},
            "update_phrase_status",
        ),
        (
            "/api/lesen/drill-save",
            {"article_id": "a1", "original": "Hallo"},
            "save_lesen_drill",
        ),
        (
            "/api/round-action",
            {"persona_slug": "anna", "action": "close"},
            "close_round",
        ),
    ],
)
def test_personal_write_routes_reject_unresolved_identity(
    german_server, monkeypatch, endpoint, payload, writer_name
):
    called = False

    def unexpected_writer(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("personal writer must not run without numeric identity")

    monkeypatch.setattr(german_server, writer_name, unexpected_writer)
    response = german_server.app.test_client().post(endpoint, json=payload)

    assert response.status_code == 401
    assert response.get_json() == {"ok": False, "error": "identity required"}
    assert called is False


def test_legacy_username_header_cannot_create_new_personal_history(
    german_server, monkeypatch
):
    called = False

    def unexpected_writer(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("legacy string identity must not be persisted")

    monkeypatch.setattr(german_server, "save_writing_entry", unexpected_writer)
    response = german_server.app.test_client().post(
        "/api/write-save",
        json={"text_original": "Hallo"},
        headers={"X-Minimoi-Username": "robert"},
    )

    assert response.status_code == 401
    assert called is False


def test_numeric_identity_is_passed_to_personal_writer(german_server, monkeypatch):
    observed = {}

    def fake_writer(**kwargs):
        observed.update(kwargs)
        return {"id": "ws_test"}

    monkeypatch.setattr(german_server, "save_writing_entry", fake_writer)
    response = german_server.app.test_client().post(
        "/api/write-save",
        json={"mode": "tagebuch", "text_original": "Hallo"},
        headers={"X-Minimoi-Auth-Id": "7"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "id": "ws_test"}
    assert observed["user_id"] == 7


def test_analyse_transcript_checks_identity_before_ai(german_server, monkeypatch):
    called = False

    def unexpected_analysis(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("AI analysis must not start for unresolved owner request")

    monkeypatch.setattr(german_server, "analyse_session", unexpected_analysis)
    response = german_server.app.test_client().post(
        "/api/analyse-transcript",
        json={"transcript": "Robert: Hallo", "persona_name": "Anna"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"ok": False, "error": "identity required"}
    assert called is False


def test_review_reports_persistence_failure_without_losing_feedback(
    german_server, monkeypatch, tmp_path
):
    import german_domain
    from providers import review_router

    monkeypatch.setattr(german_domain, "_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(
        review_router,
        "run_review",
        lambda *args, **kwargs: {"feedback": {"overall_summary": "Useful"}},
    )

    def failed_save(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(german_server, "save_review_session", failed_save, raising=False)
    response = german_server.app.test_client().post(
        "/api/review",
        json={"transcript": "Robert: Hallo", "persona_name": "Anna"},
        headers={"X-Minimoi-Auth-Id": "7"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["feedback"]["overall_summary"] == "Useful"
    assert payload["saved"] is False
    assert payload["save_error"] == "history persistence failed"
    assert list((tmp_path / "sessions").glob("*.json")) == []


def test_review_checks_owner_identity_before_ai(german_server, monkeypatch):
    from providers import review_router

    called = False

    def unexpected_review(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("AI review must not start for unresolved owner request")

    monkeypatch.setattr(review_router, "run_review", unexpected_review)
    response = german_server.app.test_client().post(
        "/api/review",
        json={"transcript": "Robert: Hallo", "persona_name": "Anna"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"ok": False, "error": "identity required"}
    assert called is False


def test_guest_review_returns_feedback_without_owner_history(
    german_server, monkeypatch, tmp_path
):
    import german_domain
    from providers import review_router

    monkeypatch.setattr(german_domain, "_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(
        review_router,
        "run_review",
        lambda *args, **kwargs: {"feedback": {"overall_summary": "Guest feedback"}},
    )
    response = german_server.app.test_client().post(
        "/api/review",
        json={"transcript": "Gast: Hallo", "persona_name": "Anna"},
        headers={"X-Minimoi-User-Tier": "guest"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["guest"] is True
    assert payload["saved"] is False
    assert not (tmp_path / "sessions").exists()


def test_analyse_session_defends_against_unresolved_identity(monkeypatch, tmp_path):
    import german_domain

    monkeypatch.setattr(german_domain, "_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(
        german_domain, "_PERSONA_MEMORY_FILE", tmp_path / "persona_memory.json"
    )

    with pytest.raises(ValueError, match="numeric user identity required"):
        german_domain.analyse_session("Robert: Hallo", "Anna", "cafe", user_id=None)

    assert not (tmp_path / "sessions").exists()


@pytest.mark.parametrize("unresolved", [None, "robert", "7", 0, -1, True])
def test_domain_personal_writers_defend_against_non_numeric_identity(
    unresolved, monkeypatch, tmp_path
):
    import german_domain

    monkeypatch.setattr(german_domain, "GERMAN_DIR", tmp_path)
    calls = [
        lambda: german_domain.save_lesen_phrase("Hallo", "Hello", "", "", user_id=unresolved),
        lambda: german_domain.save_note("a1", "", "Hallo", "", "", user_id=unresolved),
        lambda: german_domain.save_writing_entry("tagebuch", "Hallo", user_id=unresolved),
        lambda: german_domain.save_lesen_drill("a1", "", "de_in", "Hallo", "", "", "", False, user_id=unresolved),
        lambda: german_domain.save_drill_result("p1", "correct", user_id=unresolved),
        lambda: german_domain.update_phrase_status("p1", "practice", user_id=unresolved),
    ]

    for call in calls:
        with pytest.raises(ValueError, match="numeric user identity required"):
            call()


def test_unresolved_gesprache_page_uses_non_persisting_persona_defaults(
    german_server, monkeypatch
):
    calls = []
    real_personas = german_server.get_personas()

    def fake_memory(user, persona_slug, create=True):
        calls.append((user, persona_slug, create))
        return {
            "current_round": 1,
            "round_default": 5,
            "round_extended": False,
            "ready_to_archive": False,
            "active_memory": {
                "round_number": 1,
                "sessions_this_round": 0,
                "topics_discussed": [],
                "errors_noted": [],
                "strengths": [],
                "vocabulary_introduced": [],
                "notes": "",
            },
            "archived_rounds": [],
        }

    monkeypatch.setattr(german_server, "get_personas", lambda: real_personas)
    monkeypatch.setattr(german_server, "get_persona_memory", fake_memory)
    response = german_server.app.test_client().get("/gesprache")

    assert response.status_code == 200
    assert calls
    assert all(create is False for _, _, create in calls)
