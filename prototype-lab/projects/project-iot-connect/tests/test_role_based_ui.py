from conftest import ADMIN_HEADERS, fixture_text


def account_by_number(client, account_number):
    return next(
        row
        for row in client.get("/api/v1/accounts").json()
        if row["account_number"] == account_number
    )


def upload(client, account_id, fixture_name):
    response = client.post(
        f"/api/v1/admin/accounts/{account_id}/subscriptions:upload",
        content=fixture_text(fixture_name),
        headers={**ADMIN_HEADERS, "Content-Type": "text/csv"},
    )
    assert response.status_code == 201, response.text


def test_all_role_based_primary_routes_are_served(client):
    routes = [
        "/portal",
        "/portal/subscriptions",
        "/portal/actions",
        "/portal/billing",
        "/operator",
        "/operator/accounts",
        "/operator/account",
        "/operator/subscriptions",
        "/operator/billing",
        "/operator/inventory",
        "/operator/actions",
        "/operator/bill-cycles",
        "/operator/catalog",
        "/operator/api-activity",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        assert response.headers["cache-control"] == "no-store", route
        assert "/static/iotconnect/iotconnect.css" in response.text
        assert "/static/iotconnect/iotconnect.js" in response.text


def test_all_role_based_pages_link_the_brand_to_the_project_gateway(client):
    routes = [
        "/portal",
        "/portal/subscriptions",
        "/portal/actions",
        "/portal/billing",
        "/operator",
        "/operator/accounts",
        "/operator/account",
        "/operator/subscriptions",
        "/operator/billing",
        "/operator/inventory",
        "/operator/actions",
        "/operator/bill-cycles",
        "/operator/catalog",
        "/operator/api-activity",
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        assert 'class="brand brand-home-link"' in response.text, route
        assert 'href="/"' in response.text, route
        assert 'aria-label="Return to IoT Connect gateway"' in response.text, route
        assert "IoT Connect" in response.text, route
        assert "Project IoT Connect" not in response.text, route


def test_operator_portfolio_uses_live_poc_counts_not_illustrative_account_totals(client):
    response = client.get("/operator")

    assert response.status_code == 200
    assert 'id="portfolioContext"' in response.text
    assert 'id="portfolioAccountRows"' in response.text
    assert "Live POC database" in response.text
    assert "49,940" not in response.text
    assert "32,400" not in response.text


def test_account_resource_api_returns_more_than_fifty_assigned_sims(client):
    boreal = account_by_number(client, "ACCT-000200")
    available = client.get("/api/v1/inventory/sims/available").json()[:75]

    assigned = client.post(
        f"/api/v1/admin/accounts/{boreal['account_id']}/sim-assignments",
        json={"sim_resource_ids": [row["sim_resource_id"] for row in available]},
        headers=ADMIN_HEADERS,
    )
    resources = client.get(f"/api/v1/accounts/{boreal['account_id']}/resources")

    assert assigned.status_code == 200
    assert resources.status_code == 200
    assert len(resources.json()) == 75
    assert all(row["status"] == "AVAILABLE" for row in resources.json())


def test_project_gateway_routes_each_seeded_customer_explicitly(client):
    gateway = client.get("/")
    presentation = client.get("/presentation")
    workbench = client.get("/workbench")
    admin = client.get("/admin")
    architecture = client.get("/project-design")

    assert gateway.status_code == 200
    assert "IoT Connect" in gateway.text
    assert "Project IoT Connect" not in gateway.text
    assert "A working enterprise connectivity platform." in gateway.text
    assert "This POC runs a real database, internal APIs, synthetic external APIs" in gateway.text
    assert 'href="/operator"' in gateway.text
    assert 'href="/portal?account=ACCT-000100"' in gateway.text
    assert 'href="/portal?account=ACCT-000200"' in gateway.text
    assert "Choose a customer" in gateway.text
    assert gateway.text.count('class="account-link"') == 2
    assert 'href="/presentation"' in gateway.text
    assert "About this POC" in gateway.text
    assert "Enterprise Connectivity Management" in gateway.text
    assert 'href="/project-design"' in gateway.text
    assert 'href="/docs"' in gateway.text
    assert "github.com" not in gateway.text
    assert "IoT Connect API (8095)" in gateway.text
    assert presentation.status_code == 200
    assert presentation.headers["cache-control"] == "no-store"
    assert "IoT Connect" in presentation.text
    assert "Project IoT Connect" not in presentation.text
    assert presentation.text.count('<section class="slide') == 5
    assert 'src="/presentation/assets/slide-1.png"' in presentation.text
    assert 'src="/presentation/assets/slide-2.png"' in presentation.text
    assert 'src="/presentation/assets/slide-3.png"' in presentation.text
    assert 'src="/presentation/assets/slide-4.png"' in presentation.text
    assert 'src="/presentation/assets/slide-5.png"' in presentation.text
    assert "Meeting agenda" in presentation.text
    assert "Toggle the Summarized Billing feature on" in presentation.text
    assert "Order management stops sending subscriptions to Legacy Billing" in presentation.text
    assert "production POC" in presentation.text
    assert "9 months from now" in presentation.text
    assert "7,000" in presentation.text
    assert "900,000" in presentation.text
    assert "2.4 million" in presentation.text
    assert "/presentation/download." not in presentation.text
    assert "Open live POC" in presentation.text
    assert workbench.status_code == 200
    assert "Billing verification workbench" in workbench.text
    assert "Provision, activate, and reconcile enterprise IoT service." not in workbench.text
    assert 'href="/workbench"' in workbench.text
    assert 'href="/"' in workbench.text
    assert admin.status_code == 200
    assert 'href="/"' in admin.text
    assert "Project home" in admin.text
    assert architecture.status_code == 200
    assert architecture.headers["content-type"].startswith("text/html")
    assert "IoT Connect · Architecture" in architecture.text
    assert "← Project home" in architecture.text
    assert "# Reusable Prototype Lab Application Skeleton" in architecture.text


def test_presentation_serves_versioned_slide_assets_without_cache(client):
    # Dated draft SVG sources are not part of the standalone tree; the five
    # packaged PNG slides are the stable presentation assets.
    assert client.get("/presentation/assets/before.svg").status_code == 404
    assert client.get("/presentation/assets/after.svg").status_code == 404

    for slide_number in range(1, 6):
        slide = client.get(f"/presentation/assets/slide-{slide_number}.png")
        assert slide.status_code == 200
        assert slide.headers["content-type"].startswith("image/png")
        assert slide.headers["cache-control"] == "no-store"

    assert client.get("/presentation/assets/slide-6.png").status_code == 404


def test_account_summaries_return_one_joined_view_per_live_account(client):
    aster = account_by_number(client, "ACCT-000100")
    upload(client, aster["account_id"], "aster_5_subscriptions.csv")

    response = client.get("/api/v1/account-summaries")

    assert response.status_code == 200
    summaries = {
        row["account"]["account_number"]: row for row in response.json()
    }
    assert set(summaries) == {"ACCT-000100", "ACCT-000200"}
    assert summaries["ACCT-000100"]["active_subscriptions"] == 5
    assert summaries["ACCT-000100"]["available_sims"] == 0
    assert summaries["ACCT-000200"]["active_subscriptions"] == 0


def test_account_summary_and_resource_grid_contract_share_live_data(client):
    aster = account_by_number(client, "ACCT-000100")
    upload(client, aster["account_id"], "aster_5_subscriptions.csv")

    summary = client.get(f"/api/v1/accounts/{aster['account_id']}/summary")
    assert summary.status_code == 200
    assert summary.json()["active_subscriptions"] == 5
    assert summary.json()["available_sims"] == 0
    assert summary.json()["rate_plan_counts"] == {
        "PLAN-IOT-001": 2,
        "PLAN-IOT-002": 2,
        "PLAN-IOT-003": 1,
    }

    resources = client.get(f"/api/v1/accounts/{aster['account_id']}/resources")
    assert resources.status_code == 200
    assert len(resources.json()) == 5
    assert set(resources.json()[0]) == {
        "sim_resource_id",
        "iccid",
        "imsi",
        "mdn_resource_id",
        "mdn",
        "subscription_id",
        "subscription_number",
        "price_plan_id",
        "rate_plan_name",
        "status",
        "last_change",
    }
    assert all(row["status"] == "ACTIVE" for row in resources.json())


def test_multi_account_bill_cycle_preserves_account_level_runs(client):
    aster = account_by_number(client, "ACCT-000100")
    boreal = account_by_number(client, "ACCT-000200")
    upload(client, aster["account_id"], "aster_5_subscriptions.csv")
    upload(client, boreal["account_id"], "boreal_50_subscriptions.csv")

    cycle = client.post(
        "/api/v1/admin/bill-cycles",
        json={"bill_cycle": "2026-08"},
        headers=ADMIN_HEADERS,
    )
    assert cycle.status_code == 201, cycle.text
    result = cycle.json()
    assert result["status"] == "COMPLETED"
    assert result["accounts_evaluated"] == 2
    assert result["accounts_billed"] == 2
    assert result["accounts_skipped"] == 0
    assert {row["account_id"] for row in result["runs"]} == {
        aster["account_id"],
        boreal["account_id"],
    }
    assert all(row["status"] == "PASSED" for row in result["runs"])

    aster_runs = client.get(
        f"/api/v1/bill-runs?account_id={aster['account_id']}"
    ).json()
    assert len(aster_runs) == 1
    assert aster_runs[0]["account_id"] == aster["account_id"]

    repeated = client.post(
        "/api/v1/admin/bill-cycles",
        json={"bill_cycle": "2026-08"},
        headers=ADMIN_HEADERS,
    )
    assert repeated.status_code == 201
    repeated_result = repeated.json()
    assert repeated_result["accounts_billed"] == 0
    assert repeated_result["accounts_skipped"] == 2
    assert all("already has" in row["reason"] for row in repeated_result["skipped"])
    assert len(client.get("/api/v1/bill-runs").json()) == 2
