import httpx
from fastapi.testclient import TestClient

from app.integrations.amdocs_middleware import AmdocsMiddlewareClient
from app.integrations.flowone import FlowOneClient
from app.main import create_app
from conftest import ADMIN_HEADERS
from integration_stubs.main import create_flowone_stub_app


def customer_client(repository):
    stub = create_flowone_stub_app()
    transport = httpx.ASGITransport(app=stub)
    flowone = FlowOneClient("http://integrations.test", transport=transport)
    amdocs = AmdocsMiddlewareClient("http://integrations.test", transport=transport)
    return TestClient(create_app(repository, flowone, amdocs))


def test_customer_can_activate_only_with_matching_account_session(repository):
    client = customer_client(repository)
    boreal = next(
        row for row in client.get("/api/v1/accounts").json()
        if row["account_number"] == "ACCT-000200"
    )
    sim = client.get("/api/v1/inventory/sims/available").json()[0]
    assigned = client.post(
        f"/api/v1/admin/accounts/{boreal['account_id']}/sim-assignments",
        json={"sim_resource_ids": [sim["sim_resource_id"]]},
        headers=ADMIN_HEADERS,
    )
    assert assigned.status_code == 200
    payload = {
        "items": [
            {
                "source_order_ref": "BOREAL-CUSTOMER-001",
                "sim_resource_id": sim["sim_resource_id"],
                "product_offering_id": "OFFER-IOT-CONNECTIVITY",
                "price_plan_id": "PLAN-IOT-001",
                "technical_profile_id": "NET-DATA-SMS-DOM",
                "private_apn": boreal["private_apn_name"],
            }
        ]
    }
    customer_headers = {
        "X-Demo-Role": "ENTERPRISE_CUSTOMER",
        "X-Demo-Account-ID": boreal["account_id"],
    }
    created = client.post(
        f"/api/v1/accounts/{boreal['account_id']}/activation-batches",
        json=payload,
        headers=customer_headers,
    )
    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["status"] == "DRAFT"

    wrong_headers = {
        "X-Demo-Role": "ENTERPRISE_CUSTOMER",
        "X-Demo-Account-ID": "another-account",
    }
    rejected = client.post(
        f"/api/v1/accounts/{boreal['account_id']}/activation-batches",
        json=payload,
        headers=wrong_headers,
    )
    assert rejected.status_code == 403

    submitted = client.post(
        f"/api/v1/accounts/{boreal['account_id']}/activation-batches/{batch['batch_id']}:submit",
        headers=customer_headers,
    )
    assert submitted.status_code == 200, submitted.text
    result = submitted.json()
    assert result["status"] == "COMPLETED"
    assert result["success_count"] == 1
    assert result["items"][0]["network_status"] == "ACTIVE"
    assert result["items"][0]["legacy_status"] == "SUBMITTED"

    history = client.get(
        f"/api/v1/accounts/{boreal['account_id']}/activation-batches",
        headers=customer_headers,
    )
    assert history.status_code == 200
    assert [row["batch_id"] for row in history.json()] == [batch["batch_id"]]

    forbidden_history = client.get(
        f"/api/v1/accounts/{boreal['account_id']}/activation-batches",
        headers=wrong_headers,
    )
    assert forbidden_history.status_code == 403

    operator_history = client.get(
        f"/api/v1/admin/activation-batches?account_id={boreal['account_id']}",
        headers=ADMIN_HEADERS,
    )
    assert operator_history.status_code == 200
    assert [row["batch_id"] for row in operator_history.json()] == [batch["batch_id"]]


def test_statement_artifact_shell_is_account_scoped(client):
    response = client.get("/artifacts/statement?account=example&cycle=2026-08")
    assert response.status_code == 200
    assert "Legacy Billing statement artifact" in response.text
    assert "invoice-comparison" not in response.text
