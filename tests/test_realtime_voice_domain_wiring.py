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


def test_german_dev_ui_makes_realtime_primary(monkeypatch, german_client):
    monkeypatch.setenv("VOICE_REALTIME_UI_ENABLED", "1")

    resp = german_client.get("/gesprache")
    page = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Realtime-Gespräch" in page
    assert "Die Persona spricht zuerst" in page
    assert "Ältere Sitzung als Fallback" in page
    assert "realtime-transcript" in page
    # These URLs must remain relative. Absolute /static and /api paths bypass
    # the portal's /app/german proxy prefix, leaving the Start button inert.
    assert 'from "./static/realtime-voice/realtime-voice-controller.js"' in page
    assert "bootstrapUrl: './api/realtime-voice/bootstrap'" in page
    assert "fetch('./api/review'" in page
    assert 'from "/static/realtime-voice/realtime-voice-controller.js"' not in page


def test_portuguese_realtime_ui_uses_proxy_safe_relative_urls(portuguese_client):
    resp = portuguese_client.get("/conversas?realtime_voice=1")
    page = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert 'from "./static/realtime-voice/realtime-voice-controller.js"' in page
    assert "bootstrapUrl: './api/realtime-voice/bootstrap'" in page
    assert "fetch('./api/pt/review'" in page
    assert 'from "/static/realtime-voice/realtime-voice-controller.js"' not in page
