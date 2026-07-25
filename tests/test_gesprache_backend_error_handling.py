from pathlib import Path


TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "domains"
    / "german"
    / "templates"
    / "german_gesprache.html"
)


def _template_source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_ai_turn_rejects_http_and_application_errors():
    source = _template_source()

    assert "async function parseBackendResponse" in source
    assert "!response.ok || data.ok === false" in source
    assert "await parseBackendResponse(res, 'ai_turn')" in source


def test_ai_turn_failure_is_visible_and_logged():
    source = _template_source()

    assert "console.error('[Gespräche] Backend request failed'" in source
    assert "document.getElementById('session-listen-status')" in source
    assert "Backend-Fehler — bitte später erneut versuchen." in source
    assert "if (!openingTurn.ok)" in source


def test_transcription_failure_stops_the_listening_loop():
    source = _template_source()

    assert "await parseBackendResponse(res, 'transcribe')" in source
    assert "Transkription nicht verfügbar — bitte später erneut versuchen." in source
    assert "loopRunning = false;" in source
