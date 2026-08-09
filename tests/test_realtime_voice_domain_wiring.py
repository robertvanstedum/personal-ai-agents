"""
tests/test_realtime_voice_domain_wiring.py — German and Portuguese both
import and register the same shared bootstrap module (build spec Section
5/14: "German and Portuguese importing the same shared module").

Each test uses exactly one domain's test-client fixture. Both fixtures are
session-scoped `with app.test_client() as client: yield client` (matching
conftest.py's existing german_client/curator_client/portal_client
convention) -- holding two such context managers open and issuing requests
through both in a single test corrupts Flask's global context stack
("Popped wrong app context"). That's a pytest/Flask fixture-interaction
hazard, not an application bug -- avoided here by never mixing clients
within one test.
"""


def test_german_endpoint_registered_and_backed_by_shared_module(german_client):
    resp = german_client.post("/api/realtime-voice/bootstrap", json={})
    # Unauthenticated -> 401, not 404. A 404 would mean the blueprint never
    # got registered.
    assert resp.status_code == 401
    view_fn = next(
        f for name, f in german_client.application.view_functions.items()
        if "bootstrap" in name
    )
    assert view_fn.__module__ == "core.realtime_voice.bootstrap"


def test_portuguese_endpoint_registered_and_backed_by_shared_module(portuguese_client):
    resp = portuguese_client.post("/api/realtime-voice/bootstrap", json={})
    assert resp.status_code == 401
    view_fn = next(
        f for name, f in portuguese_client.application.view_functions.items()
        if "bootstrap" in name
    )
    assert view_fn.__module__ == "core.realtime_voice.bootstrap"


def test_german_writing_uses_shared_memo_controller_not_browser_recognition(german_client):
    page = german_client.get("/schreiben").get_data(as_text=True)
    assert "realtime-memo-controller.js" in page
    assert "memo-voice-preference" in page
    assert "SpeechRecognition" not in page
    assert "webkitSpeechRecognition" not in page
    assert "./api/realtime-voice/memo/bootstrap" in page


def test_german_reading_notes_use_shared_memo_controller_not_browser_recognition(german_client):
    page = german_client.get("/lesen").get_data(as_text=True)
    assert "realtime-memo-controller.js" in page
    assert "voice-notizen" in page
    assert "notizen-original" in page
    assert "SpeechRecognition" not in page
    assert "webkitSpeechRecognition" not in page
    assert "./api/realtime-voice/memo/bootstrap" in page


def test_portuguese_writing_uses_shared_memo_controller_not_browser_recognition(portuguese_client):
    page = portuguese_client.get("/escrita").get_data(as_text=True)
    assert "realtime-memo-controller.js" in page
    assert "memo-voice-preference" in page
    assert "SpeechRecognition" not in page
    assert "webkitSpeechRecognition" not in page
    assert "./api/realtime-voice/memo/bootstrap" in page


def test_portuguese_reading_notes_use_shared_memo_controller_not_browser_recognition(portuguese_client):
    page = portuguese_client.get("/leitura").get_data(as_text=True)
    assert "realtime-memo-controller.js" in page
    assert "voice-notizen" in page
    assert "notizen-original" in page
    assert "SpeechRecognition" not in page
    assert "webkitSpeechRecognition" not in page
    assert "./api/realtime-voice/memo/bootstrap" in page


def test_german_personas_have_explicit_legacy_and_realtime_voices():
    from domains.german.html_server import _PERSONA_VOICES

    expected = {
        "Maria", "Frau Berger", "Herr Fischer", "Dr. Huber", "Stefan",
        "Frau Novak", "Klaus", "Georg", "Anna",
    }
    assert set(_PERSONA_VOICES) == expected
    assert all(set(profile) == {"legacy", "openai", "xai"}
               for profile in _PERSONA_VOICES.values())
    assert _PERSONA_VOICES["Stefan"] == {
        "legacy": "onyx", "openai": "cedar", "xai": "rex",
    }


def test_portuguese_personas_have_explicit_legacy_and_realtime_voices():
    from domains.portuguese.html_server import _PERSONA_VOICES

    expected = {"Maria", "Carlos", "Lucas", "Juliana"}
    assert set(_PERSONA_VOICES) == expected
    assert all(set(profile) == {"legacy", "openai", "xai"}
               for profile in _PERSONA_VOICES.values())
    assert _PERSONA_VOICES["Carlos"] == {
        "legacy": "onyx", "openai": "cedar", "xai": "rex",
    }
    assert _PERSONA_VOICES["Juliana"] == {
        "legacy": "nova", "openai": "marin", "xai": "ara",
    }


def test_german_dev_ui_makes_realtime_primary(monkeypatch, german_client):
    monkeypatch.setenv("VOICE_REALTIME_UI_ENABLED", "1")

    resp = german_client.get(
        "/gesprache",
        headers={"X-Minimoi-Display-Name": "Isabella"},
    )
    page = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Realtime-Gespräch" in page
    assert "Die Persona spricht zuerst" in page
    assert "Ältere Sitzung als Fallback" in page
    assert "realtime-transcript" in page
    # These URLs must remain relative. Absolute /static and /api paths bypass
    # the portal's /app/german proxy prefix, leaving the Start button inert.
    assert (
        'from "./static/realtime-voice/realtime-voice-controller.js'
        '?v=20260809-ga1"' in page
    )
    assert "bootstrapUrl: './api/realtime-voice/bootstrap'" in page
    assert "source: session.source || 'ki_sitzung'" in page
    assert "result.provider === 'xai' ? 'grok' : result.provider" in page
    assert "Transkript unvollständig" in page
    assert "gesprache:realtime-session-ended" in page
    assert "„Analysieren“ speichert die Sitzung; ✕ verwirft sie." in page
    assert '<div class="session-card-transcript-wrap">' in page
    assert "Transkript ▲" in page
    assert 'const learnerName = "Isabella";' in page
    assert 'from "/static/realtime-voice/realtime-voice-controller.js"' not in page


def test_portuguese_dev_ui_makes_realtime_primary(monkeypatch, portuguese_client):
    monkeypatch.setenv("VOICE_REALTIME_UI_ENABLED", "1")

    resp = portuguese_client.get(
        "/conversas",
        headers={"X-Minimoi-Display-Name": "Isabella"},
    )
    page = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Conversa em tempo real" in page
    assert "A persona fala primeiro" in page
    assert "Sessão anterior como alternativa" in page
    assert "realtime-transcript" in page
    assert (
        'from "./static/realtime-voice/realtime-voice-controller.js'
        '?v=20260809-ga1"' in page
    )
    assert "bootstrapUrl: './api/realtime-voice/bootstrap'" in page
    assert "source: session.source || 'ki_sessao'" in page
    assert "result.provider === 'xai' ? 'grok' : result.provider" in page
    assert "Transcrição incompleta" in page
    assert "conversas:realtime-session-ended" in page
    assert "“Analisar” salva a sessão; ✕ descarta." in page
    assert '<div class="session-card-transcript-wrap">' in page
    assert "Transcrição ▲" in page
    assert 'const learnerName = "Isabella";' in page
    assert 'from "/static/realtime-voice/realtime-voice-controller.js"' not in page


def test_portuguese_review_prompt_rejects_likely_speech_recognition_errors():
    from domains.portuguese.review_router import (
        PORTUGUESE_REVIEW_PROMPT,
        build_review_prompt,
    )

    assert "speech-recognition artifacts" in PORTUGUESE_REVIEW_PROMPT
    assert 'omit the item from\n"errors"' in PORTUGUESE_REVIEW_PROMPT
    assert "statement-versus-question intent" in PORTUGUESE_REVIEW_PROMPT
    assert "every material change" in PORTUGUESE_REVIEW_PROMPT
    assert "incomplete preceding turn" in PORTUGUESE_REVIEW_PROMPT
    assert '"Sim"' in PORTUGUESE_REVIEW_PROMPT
    assert '"Ouve"' in PORTUGUESE_REVIEW_PROMPT
    assert '"carta"' in PORTUGUESE_REVIEW_PROMPT
    assert "based only on confirmed errors" in PORTUGUESE_REVIEW_PROMPT
    built = build_review_prompt("Isabella")
    assert "Isabella's performance" in built
    assert "LEARNER_NAME" not in built
    assert 'not as "the student"' in built


def test_german_review_prompt_uses_runtime_learner_name():
    from domains.german.german_domain import build_review_system_prompt

    built = build_review_system_prompt("Isabella")
    assert "Isabella's turns" in built
    assert "LEARNER_NAME" not in built
    assert 'not as "the student"' in built
    assert "incomplete preceding turn" in built
    assert "independently clear transcript evidence" in built


def test_carlos_prompt_requests_short_natural_turns_without_global_slowdown():
    prompt = open(
        "domains/portuguese/personas/carlos_uber.txt", encoding="utf-8"
    ).read()
    assert "relaxed, natural, slightly" in prompt
    assert "not exaggeratedly slow" in prompt
    assert "one or two sentences" in prompt
    assert "do not\nrestart the greeting" in prompt


def test_portuguese_data_directory_can_be_mounted_outside_code():
    source = open(
        "domains/portuguese/html_server.py", encoding="utf-8"
    ).read()
    assert 'os.environ.get("PORTUGUESE_DATA_DIR"' in source
