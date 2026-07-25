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
