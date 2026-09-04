"""Direct integration-action resources survive an application restart.

``POST/GET /api/v1/network-activations`` and
``POST/GET /api/v1/legacy-subscription-actions`` must be served from the
repository, not from process memory. A restart is simulated by building a
second application (fresh services) over the same repository instance; on the
PostgreSQL path (``IOTCONNECT_TEST_POSTGRES_DSN``) that is a genuinely different
process state reading the same database.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.amdocs_middleware import AmdocsMiddlewareClient
from app.integrations.flowone import FlowOneClient
from app.main import create_app
from app.repositories.snowflake import SnowflakeRepository
from conftest import ADMIN_HEADERS, repository_for_tests
from integration_stubs.main import FlowOneScenarioState, create_flowone_stub_app


ACTIVATION = {
    "imsi": "310150123456789",
    "mdn": "+13125550101",
    "service_package": "DATA_SMS",
    "roaming_package": "DOMESTIC",
}
LEGACY_ACTION = {
    "amdocs_account_number": "AMD-45001",
    "wdh_account_reference": "WDH-200",
    "mdn": "+13125550121",
    "imsi": "310150123456789",
    "action": "CREATE",
}


@pytest.fixture
def system():
    repository = repository_for_tests()
    state = FlowOneScenarioState()
    stub = create_flowone_stub_app(state)
    transport = httpx.ASGITransport(app=stub)

    def new_process():
        """A fresh application over the same repository = restarted app."""
        flowone = FlowOneClient("http://integrations.test", transport=transport)
        amdocs = AmdocsMiddlewareClient("http://integrations.test", transport=transport)
        return TestClient(create_app(repository, flowone, amdocs))

    return new_process, state


def test_direct_network_activation_resolves_after_restart(system):
    new_process, _ = system
    first = new_process()
    created = first.post("/api/v1/network-activations", json=ACTIVATION)
    assert created.status_code == 201, created.text
    body = created.json()
    assert first.get(f"/api/v1/network-activations/{body['activation_id']}").json() == body

    restarted = new_process()
    after = restarted.get(f"/api/v1/network-activations/{body['activation_id']}")

    assert after.status_code == 200, after.text
    assert after.json() == body


def test_direct_network_activation_failure_is_also_persisted(system):
    new_process, state = system
    first = new_process()
    state.arm("POLICY")
    created = first.post("/api/v1/network-activations", json=ACTIVATION)
    assert created.status_code == 201
    body = created.json()
    assert body["wdh_service_status"] == "ACTIVATION_FAILED"

    after = new_process().get(f"/api/v1/network-activations/{body['activation_id']}")
    assert after.status_code == 200
    assert after.json() == body


def test_direct_legacy_subscription_action_resolves_after_restart(system):
    new_process, _ = system
    first = new_process()
    created = first.post("/api/v1/legacy-subscription-actions", json=LEGACY_ACTION)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["wdh_status"] == "SUBMITTED"

    after = new_process().get(
        f"/api/v1/legacy-subscription-actions/{body['compatibility_action_id']}"
    )
    assert after.status_code == 200, after.text
    assert after.json() == body


def test_unknown_direct_action_ids_still_return_404(system):
    new_process, _ = system
    client = new_process()
    assert client.get("/api/v1/network-activations/does-not-exist").status_code == 404
    assert client.get("/api/v1/legacy-subscription-actions/does-not-exist").status_code == 404


def test_batch_workflow_action_ids_resolve_after_restart(system):
    new_process, _ = system
    first = new_process()
    aster = next(
        row for row in first.get("/api/v1/accounts").json()
        if row["account_number"] == "ACCT-000100"
    )
    sims = first.get("/api/v1/inventory/sims/available").json()[:2]
    assert first.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/sim-assignments",
        json={"sim_resource_ids": [row["sim_resource_id"] for row in sims]},
        headers=ADMIN_HEADERS,
    ).status_code == 200
    created = first.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/activation-batches",
        json={
            "items": [
                {
                    "source_order_ref": f"PERSIST-{index:03d}",
                    "sim_resource_id": row["sim_resource_id"],
                    "product_offering_id": "OFFER-IOT-CONNECTIVITY",
                    "price_plan_id": "PLAN-IOT-001",
                    "technical_profile_id": "NET-DATA-SMS-DOM",
                }
                for index, row in enumerate(sims, start=1)
            ]
        },
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201, created.text
    submitted = first.post(
        f"/api/v1/admin/activation-batches/{created.json()['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    )
    assert submitted.status_code == 200, submitted.text
    items = submitted.json()["items"]
    assert all(row["flowone_activation_id"] and row["legacy_action_id"] for row in items)

    before = {
        row["batch_item_id"]: (
            first.get(f"/api/v1/network-activations/{row['flowone_activation_id']}").json(),
            first.get(f"/api/v1/legacy-subscription-actions/{row['legacy_action_id']}").json(),
        )
        for row in items
    }

    restarted = new_process()
    for row in items:
        network = restarted.get(f"/api/v1/network-activations/{row['flowone_activation_id']}")
        legacy = restarted.get(f"/api/v1/legacy-subscription-actions/{row['legacy_action_id']}")
        assert network.status_code == 200 and legacy.status_code == 200
        assert (network.json(), legacy.json()) == before[row["batch_item_id"]]
        assert network.json()["activation_id"] == row["flowone_activation_id"]
        assert network.json()["flowone"]["imsi"] == row["sim"]["imsi"]
        assert legacy.json()["middleware"]["mdn"] == row["mdn"]["mdn"]


def test_reset_clears_persisted_direct_actions(system):
    new_process, _ = system
    client = new_process()
    body = client.post("/api/v1/network-activations", json=ACTIVATION).json()
    assert client.post("/api/v1/admin/demo:reset", headers=ADMIN_HEADERS).status_code == 200
    assert client.get(f"/api/v1/network-activations/{body['activation_id']}").status_code == 404


def test_snowflake_adapter_declares_direct_actions_unsupported():
    repository = SnowflakeRepository(client=object())
    with pytest.raises(NotImplementedError, match="does not persist direct integration-action"):
        repository.get_network_activation("any")
    with pytest.raises(NotImplementedError):
        repository.insert_legacy_subscription_action({"compatibility_action_id": "x"})
