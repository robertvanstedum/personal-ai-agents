import csv
import io

from conftest import ADMIN_HEADERS, fixture_text


def account_by_number(client, account_number):
    return next(
        row for row in client.get("/api/v1/accounts").json()
        if row["account_number"] == account_number
    )


def upload(client, account_id, fixture_name):
    response = client.post(
        f"/api/v1/admin/accounts/{account_id}/subscriptions:upload",
        content=fixture_text(fixture_name),
        headers={**ADMIN_HEADERS, "Content-Type": "text/csv"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def run_bill(client, account_id):
    response = client.post(
        f"/api/v1/admin/accounts/{account_id}/bill-runs",
        json={"bill_cycle": "2026-08"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_aster_detailed_flow(client):
    aster = account_by_number(client, "ACCT-000100")
    result = upload(client, aster["account_id"], "aster_5_subscriptions.csv")
    assert result["iot_created"] == 5
    assert result["legacy_created"] == 5
    assert result["legacy_skipped_by_policy"] == 0

    run = run_bill(client, aster["account_id"])
    assert run["source_charge_count"] == 6
    assert run["output_row_count"] == 6
    assert run["source_total"] == "65.00"
    assert run["variance"] == "0.00"
    assert run["status"] == "PASSED"
    statement = client.get(
        f"/api/v1/artifacts/accounts/{aster['account_id']}/legacy-statement/2026-08"
    ).json()
    assert statement["legacy_service_line_count"] == 5
    assert statement["source_charge_count"] == 6
    assert statement["statement_charge_item_count"] == 6
    assert statement["amount_due"] == "65.00"
    rows = statement["line_items"]
    subscription_rows = [row for row in rows if row["posting_scope"] == "SUBSCRIPTION"]
    account_rows = [row for row in rows if row["posting_scope"] == "ACCOUNT"]
    assert len(subscription_rows) == 5
    assert all(row["source_charge_level"] == "SUBSCRIPTION" and row["mdn"] for row in subscription_rows)
    assert all(row["mdn"].isdigit() and len(row["mdn"]) == 10 for row in subscription_rows)
    assert len(account_rows) == 1
    assert account_rows[0]["source_charge_level"] == "ACCOUNT"
    assert account_rows[0]["mdn"] is None

    csv_response = client.get(
        f"/api/v1/bill-runs/{run['bill_run_id']}/file.csv"
    )
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "ACCT-000100" in csv_response.headers["content-disposition"]
    exported = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(exported) == 6
    assert {row["posting_scope"] for row in exported} == {
        "ACCOUNT",
        "SUBSCRIPTION",
    }
    assert sum(1 for row in exported if row["mdn"]) == 5

    duplicate = client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/bill-runs",
        json={"bill_cycle": "2026-08"},
        headers=ADMIN_HEADERS,
    )
    assert duplicate.status_code == 409
    assert "already has" in duplicate.json()["message"]


def test_boreal_summarized_flow(client):
    boreal = account_by_number(client, "ACCT-000200")
    enabled = client.post(
        f"/api/v1/admin/accounts/{boreal['account_id']}/billing-mode",
        json={"billing_mode": "SUMMARIZED", "reason": "Approved IoT Connect proof configuration"},
        headers=ADMIN_HEADERS,
    )
    assert enabled.status_code == 200

    result = upload(client, boreal["account_id"], "boreal_50_subscriptions.csv")
    assert result["iot_created"] == 50
    assert result["legacy_created"] == 0
    assert result["legacy_skipped_by_policy"] == 50
    assert result["rate_plan_counts"] == {
        "PLAN-IOT-001": 25,
        "PLAN-IOT-002": 20,
        "PLAN-IOT-003": 5,
    }

    run = run_bill(client, boreal["account_id"])
    assert run["source_charge_count"] == 53
    assert run["output_row_count"] == 6
    assert run["source_total"] == "310.00"
    assert run["output_total"] == "310.00"
    assert run["variance"] == "0.00"
    assert run["status"] == "PASSED"

    reconciliation = client.get(
        f"/api/v1/bill-runs/{run['bill_run_id']}/reconciliation"
    ).json()
    assert all(reconciliation["acceptance_checks"].values())
    invoice = client.get(
        f"/api/v1/artifacts/accounts/{boreal['account_id']}/legacy-statement/2026-08"
    ).json()
    assert invoice["total"] == "310.00"
    assert invoice["generated_by"] == "Legacy Billing (simulated artifact)"
    assert invoice["legacy_service_line_count"] == 0
    assert {row["quantity"] for row in invoice["line_items"][:3]} == {5, 20, 25}
    assert {row["target_line_ref"] for row in invoice["line_items"]} == {"LEG-ACCT-200"}
    assert {row["posting_scope"] for row in invoice["line_items"]} == {"ACCOUNT"}
    assert {row["mdn"] for row in invoice["line_items"]} == {None}
    assert {row["source_charge_level"] for row in invoice["line_items"]} == {
        "ACCOUNT",
        "SUBSCRIPTION",
    }
