from conftest import ADMIN_HEADERS


def test_operator_can_create_and_edit_an_account_without_using_the_workbench(client):
    available = client.get(
        "/api/v1/admin/legacy-accounts/available",
        headers=ADMIN_HEADERS,
    )
    assert available.status_code == 200
    assert [row["legacy_account_ref"] for row in available.json()] == [
        "LEG-ACCT-300",
        "LEG-ACCT-301",
        "LEG-ACCT-302",
    ]

    created = client.post(
        "/api/v1/admin/accounts",
        headers=ADMIN_HEADERS,
        json={
            "customer_name": "Cedar Logistics",
            "account_name": "Cedar Logistics IoT",
            "external_billing_account_number": "LEG-ACCT-300",
            "external_customer_ref": "CRM-CEDAR-001",
            "reason": "Approved customer account setup",
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["account_number"] == "ACCT-000300"
    assert account["contract_number"] == "CTR-000300"
    assert account["external_billing_account_number"] == "LEG-ACCT-300"
    assert account["billing_mode"] == "DETAILED"

    remaining = client.get(
        "/api/v1/admin/legacy-accounts/available",
        headers=ADMIN_HEADERS,
    ).json()
    assert "LEG-ACCT-300" not in {
        row["legacy_account_ref"] for row in remaining
    }

    updated = client.patch(
        f"/api/v1/admin/accounts/{account['account_id']}",
        headers=ADMIN_HEADERS,
        json={
            "customer_name": "Cedar Logistics Group",
            "account_name": "Cedar Connected Operations",
            "external_customer_ref": "CRM-CEDAR-002",
            "private_apn_name": "CEDAR_PRIVATE_IOT",
            "reason": "Approved customer account correction",
        },
    )
    assert updated.status_code == 200, updated.text
    result = updated.json()
    assert result["customer_name"] == "Cedar Logistics Group"
    assert result["account_name"] == "Cedar Connected Operations"
    assert result["external_customer_ref"] == "CRM-CEDAR-002"
    assert result["private_apn_name"] == "CEDAR_PRIVATE_IOT"
    assert result["external_billing_account_number"] == "LEG-ACCT-300"


def test_enterprise_customer_cannot_create_or_edit_accounts(client):
    customer_headers = {
        "X-Demo-Role": "ENTERPRISE_CUSTOMER",
        "X-Demo-Account-ID": "not-an-admin-session",
    }
    create = client.post(
        "/api/v1/admin/accounts",
        headers=customer_headers,
        json={
            "customer_name": "Unauthorized Customer",
            "account_name": "Unauthorized Account",
            "external_billing_account_number": "LEG-ACCT-300",
            "reason": "This operation must be rejected",
        },
    )
    assert create.status_code == 403

    account = client.get("/api/v1/accounts").json()[0]
    update = client.patch(
        f"/api/v1/admin/accounts/{account['account_id']}",
        headers=customer_headers,
        json={
            "customer_name": account["customer_name"],
            "account_name": account["account_name"],
            "external_customer_ref": None,
            "private_apn_name": None,
            "reason": "This operation must also be rejected",
        },
    )
    assert update.status_code == 403


def test_operator_account_pages_do_not_route_configuration_to_the_workbench(client):
    accounts = client.get("/operator/accounts")
    overview = client.get("/operator/account")
    configuration = client.get("/operator/account/configuration")
    create = client.get("/operator/accounts/new")

    assert accounts.status_code == overview.status_code == 200
    assert configuration.status_code == create.status_code == 200
    assert 'href="/operator/accounts/new"' in accounts.text
    assert 'href="/operator/account/configuration"' in overview.text
    assert 'href="/admin"' not in overview.text
    assert 'data-iotconnect-page="operator-account-configuration"' in configuration.text
    assert 'data-iotconnect-page="operator-account-new"' in create.text
