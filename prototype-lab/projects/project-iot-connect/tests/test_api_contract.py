from uuid import UUID

from conftest import ADMIN_HEADERS


def test_health_and_catalog_are_public_and_documented(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["api_version"] == "v1"

    plans = client.get("/api/v1/catalog/rate-plans")
    assert plans.status_code == 200
    assert [row["rate_plan_id"] for row in plans.json()] == [
        "PLAN-IOT-001",
        "PLAN-IOT-002",
        "PLAN-IOT-003",
        "PLAN-IOT-004",
        "PLAN-VAS-NETFLIX-PREMIUM",
        "PLAN-IOT-SHARED-100GB",
    ]
    assert [row["monthly_price"] for row in plans.json()] == [
        "2.00", "3.00", "5.00", "8.00", "22.99", "100.00"
    ]

    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "1.0.0"
    assert "/api/v1/admin/accounts" in schema["paths"]

    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.get("/static/api-client.js").status_code == 200


def test_admin_header_is_required_and_errors_have_one_shape(client):
    payload = {
        "customer_name": "Cedar Logistics",
        "account_name": "Cedar Logistics",
        "external_billing_account_number": "LEG-ACCT-300",
        "reason": "API contract test",
    }
    missing = client.post("/api/v1/admin/accounts", json=payload)
    assert missing.status_code == 422
    assert missing.json()["code"] == "REQUEST_VALIDATION_ERROR"
    UUID(missing.json()["request_id"])

    forbidden = client.post(
        "/api/v1/admin/accounts",
        json=payload,
        headers={"X-Demo-Role": "Enterprise Sales"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"


def test_undocumented_fields_are_rejected(client):
    response = client.post(
        "/api/v1/admin/accounts",
        json={
            "account_name": "Cedar Logistics",
            "customer_name": "Cedar Logistics",
            "external_billing_account_number": "LEG-ACCT-300",
            "reason": "Strict request model",
            "golden_line_ref": "SHOULD-NOT-BE-HERE",
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"


def test_create_account_returns_unique_account_and_contract_keys(client):
    created = []
    for name, external_account in [
        ("Cedar Logistics", "LEG-ACCT-300"),
        ("Dover Industrial", "LEG-ACCT-301"),
    ]:
        response = client.post(
            "/api/v1/admin/accounts",
            json={
                "customer_name": name,
                "account_name": name,
                "external_billing_account_number": external_account,
                "reason": "Create unique business identities",
            },
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 201
        created.append(response.json())

    assert created[0]["account_id"] != created[1]["account_id"]
    assert created[0]["account_number"] != created[1]["account_number"]
    assert created[0]["contract_id"] != created[1]["contract_id"]
    assert created[0]["contract_number"] != created[1]["contract_number"]
    UUID(created[0]["account_id"])
    UUID(created[0]["contract_id"])
    assert created[0]["billing_mode"] == "DETAILED"
    assert "golden_line_ref" not in created[0]


def test_admin_exposes_inventory_allocation_and_activation_contract_limits(client):
    admin_html = client.get("/admin").text
    assert 'id="selectAllAvailableSims"' in admin_html
    assert 'id="availableSimList"' in admin_html
    assert 'id="selectAllAssignedSims"' in admin_html
    assert 'id="assignedSimList"' in admin_html
    assert "Assign selected SIMs to account" in admin_html
    assert "Create batch and reserve MDNs" in admin_html
    assert 'id="usePrivateApn"' in admin_html
    assert 'id="privateApnSelect"' in admin_html

    schema = client.get("/openapi.json").json()
    assignment_ids = schema["components"]["schemas"]["SimAssignmentRequest"]["properties"]["sim_resource_ids"]
    activation_items = schema["components"]["schemas"]["ActivationBatchCreateRequest"]["properties"]["items"]
    assert assignment_ids["maxItems"] == 1000
    assert activation_items["maxItems"] == 50
