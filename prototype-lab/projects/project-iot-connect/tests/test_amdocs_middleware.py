import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.amdocs_middleware import AmdocsMiddlewareClient
from app.integrations.flowone import FlowOneClient
from app.main import create_app
from app.repositories.memory import MemoryRepository
from integration_stubs.main import create_flowone_stub_app


VALID_ACTION = {
    "amdocs_account_number": "AMD-45001",
    "wdh_account_reference": "WDH-200",
    "mdn": "+13125550121",
    "imsi": "310150123456789",
    "action": "CREATE",
}


@pytest.fixture
def compatibility_system():
    stub_app = create_flowone_stub_app()
    transport = httpx.ASGITransport(app=stub_app)
    flowone = FlowOneClient("http://integrations.test", transport=transport)
    middleware = AmdocsMiddlewareClient(
        "http://integrations.test", transport=transport
    )
    wdh_client = TestClient(create_app(MemoryRepository(), flowone, middleware))
    return wdh_client


@pytest.mark.parametrize("action", ["CREATE", "DEACTIVATE"])
def test_wdh_stops_when_middleware_returns_ok(compatibility_system, action):
    response = compatibility_system.post(
        "/api/v1/legacy-subscription-actions",
        json={**VALID_ACTION, "action": action},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["wdh_status"] == "SUBMITTED"
    assert result["middleware"]["status"] == "OK"
    assert result["middleware"]["action"] == action
    assert result["middleware"]["subscription_key"] == (
        "AMD-45001:310150123456789"
    )

    evidence = compatibility_system.get(
        "/api/v1/legacy-subscription-actions/"
        + result["compatibility_action_id"]
    )
    assert evidence.status_code == 200
    assert evidence.json() == result


def test_account_and_imsi_form_the_stable_legacy_subscription_key(
    compatibility_system,
):
    created = compatibility_system.post(
        "/api/v1/legacy-subscription-actions", json=VALID_ACTION
    ).json()
    deactivated = compatibility_system.post(
        "/api/v1/legacy-subscription-actions",
        json={**VALID_ACTION, "action": "DEACTIVATE", "mdn": "+13125550999"},
    ).json()

    assert (
        created["middleware"]["subscription_key"]
        == deactivated["middleware"]["subscription_key"]
    )


def test_contract_numbers_are_not_accepted_on_the_middleware_boundary(
    compatibility_system,
):
    response = compatibility_system.post(
        "/api/v1/legacy-subscription-actions",
        json={**VALID_ACTION, "contract_number": "CONTRACT-DO-NOT-SEND"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
