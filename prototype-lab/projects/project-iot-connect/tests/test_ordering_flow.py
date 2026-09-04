import httpx
from fastapi.testclient import TestClient

from app.integrations.amdocs_middleware import AmdocsMiddlewareClient
from app.integrations.flowone import FlowOneClient
from app.main import create_app
from integration_stubs.main import FlowOneScenarioState, create_flowone_stub_app
from conftest import ADMIN_HEADERS, repository_for_tests


def build_system():
    repository = repository_for_tests()
    state = FlowOneScenarioState()
    stub = create_flowone_stub_app(state)
    transport = httpx.ASGITransport(app=stub)
    flowone = FlowOneClient("http://integrations.test", transport=transport)
    amdocs = AmdocsMiddlewareClient("http://integrations.test", transport=transport)
    return TestClient(create_app(repository, flowone, amdocs)), repository, state


def account(client, number):
    return next(
        row for row in client.get("/api/v1/accounts").json()
        if row["account_number"] == number
    )


def assign_and_order(client, account_id, count=1, private_apn=None):
    sims = client.get("/api/v1/inventory/sims/available").json()[:count]
    assigned = client.post(
        f"/api/v1/admin/accounts/{account_id}/sim-assignments",
        json={"sim_resource_ids": [row["sim_resource_id"] for row in sims]},
        headers=ADMIN_HEADERS,
    )
    assert assigned.status_code == 200, assigned.text
    created = client.post(
        f"/api/v1/admin/accounts/{account_id}/activation-batches",
        json={
            "items": [
                {
                    "source_order_ref": f"ORDER-{index:03d}",
                    "sim_resource_id": row["sim_resource_id"],
                    "product_offering_id": "OFFER-IOT-CONNECTIVITY",
                    "price_plan_id": "PLAN-IOT-001",
                    "technical_profile_id": "NET-DATA-SMS-DOM",
                    "private_apn": private_apn,
                }
                for index, row in enumerate(sims, start=1)
            ]
        },
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201, created.text
    return created.json(), sims


def test_order_reserves_separate_sim_and_mdn_then_activates_network_before_amdocs():
    client, repository, _ = build_system()
    aster = account(client, "ACCT-000100")

    created, sims = assign_and_order(client, aster["account_id"], 2)

    assert created["status"] == "DRAFT"
    assert created["item_count"] == 2
    assert all(row["network_status"] == "PENDING" for row in created["items"])
    assert {row["sim"]["sim_resource_id"] for row in created["items"]} == {
        row["sim_resource_id"] for row in sims
    }
    assert all(row["mdn"]["status"] == "RESERVED" for row in created["items"])
    subscriptions = repository.list_subscriptions(aster["account_id"])
    assert all(row["status"] == "PENDING_ACTIVATION" for row in subscriptions)
    assert all("sim_id" not in row and "mdn" not in row for row in subscriptions)

    submitted = client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    )
    assert submitted.status_code == 200, submitted.text
    result = submitted.json()
    assert result["status"] == "COMPLETED"
    assert result["success_count"] == 2
    assert all(row["network_status"] == "ACTIVE" for row in result["items"])
    assert all(row["legacy_status"] == "SUBMITTED" for row in result["items"])
    assert all(len(row["flowone_element_results"]) == 4 for row in result["items"])
    assert all(
        row["flowone_element_results"][-1]["provisioning_status"]
        == "SKIPPED_NOT_APPLICABLE"
        for row in result["items"]
    )
    assert all(row["status"] == "ACTIVE" for row in repository.list_subscriptions(aster["account_id"]))
    assert len(repository.list_legacy_lines("LEG-ACCT-100")) == 2


def test_latest_customer_activation_is_a_header_free_evidence_view():
    client, _, _ = build_system()
    aster = account(client, "ACCT-000100")

    empty = client.get(
        "/api/v1/demo-evidence/accounts/ACCT-000100/latest-activation"
    )
    assert empty.status_code == 404

    created, _ = assign_and_order(client, aster["account_id"], 2)
    submitted = client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    ).json()

    evidence = client.get(
        "/api/v1/demo-evidence/accounts/ACCT-000100/latest-activation"
    )
    assert evidence.status_code == 200, evidence.text
    body = evidence.json()
    assert body["batch_id"] == submitted["batch_id"]
    assert body["status"] == "COMPLETED"
    assert body["success_count"] == 2
    assert len(body["items"]) == 2
    assert all(row["network_status"] == "ACTIVE" for row in body["items"])
    assert all(row["legacy_status"] == "SUBMITTED" for row in body["items"])
    assert all(row["flowone_element_results"] for row in body["items"])


def test_existing_application_reads_accept_visible_account_number():
    client, _, _ = build_system()
    aster = account(client, "ACCT-000100")
    created, _ = assign_and_order(client, aster["account_id"], 2)
    client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    )

    subscriptions = client.get(
        "/api/v1/accounts/ACCT-000100/subscriptions?system=iot"
    )
    summary = client.get("/api/v1/accounts/ACCT-000100/summary")
    batches = client.get(
        "/api/v1/admin/activation-batches?account_id=ACCT-000100",
        headers=ADMIN_HEADERS,
    )

    assert subscriptions.status_code == 200, subscriptions.text
    assert len(subscriptions.json()) == 2
    assert all(row["status"] == "ACTIVE" for row in subscriptions.json())
    assert summary.status_code == 200, summary.text
    assert summary.json()["active_subscriptions"] == 2
    assert batches.status_code == 200, batches.text
    assert batches.json()[0]["batch_id"] == created["batch_id"]


def test_billing_policy_evidence_tracks_summarized_mode_without_headers():
    client, _, _ = build_system()
    boreal = account(client, "ACCT-000200")

    before = client.get(
        "/api/v1/demo-evidence/accounts/ACCT-000200/billing-policy"
    )
    assert before.status_code == 200, before.text
    assert before.json()["summarized_billing_enabled"] is False
    assert before.json()["send_subscriptions_to_legacy_billing"] is True
    assert before.json()["posting_scope"] == "SUBSCRIPTION"

    enabled = client.post(
        f"/api/v1/admin/accounts/{boreal['account_id']}/billing-mode",
        json={"billing_mode": "SUMMARIZED", "reason": "Interview evidence"},
        headers=ADMIN_HEADERS,
    )
    assert enabled.status_code == 200, enabled.text

    after = client.get(
        "/api/v1/demo-evidence/accounts/ACCT-000200/billing-policy"
    )
    assert after.status_code == 200, after.text
    assert after.json()["billing_mode"] == "SUMMARIZED"
    assert after.json()["summarized_billing_enabled"] is True
    assert after.json()["send_subscriptions_to_legacy_billing"] is False
    assert after.json()["posting_scope"] == "ACCOUNT"


def test_mass_activation_draft_accepts_50_sims_and_rejects_51():
    client, _, _ = build_system()
    aster = account(client, "ACCT-000100")

    created, _ = assign_and_order(client, aster["account_id"], 50)
    assert created["status"] == "DRAFT"
    assert created["item_count"] == 50
    assert len(created["items"]) == 50
    assert len({row["source_order_ref"] for row in created["items"]}) == 50
    assert len({row["mdn_resource_id"] for row in created["items"]}) == 50

    additional_sims = client.get("/api/v1/inventory/sims/available").json()[:51]
    allocated = client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/sim-assignments",
        json={"sim_resource_ids": [row["sim_resource_id"] for row in additional_sims]},
        headers=ADMIN_HEADERS,
    )
    assert allocated.status_code == 200, allocated.text

    rejected = client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/activation-batches",
        json={
            "items": [
                {
                    "source_order_ref": f"ORDER-OVER-LIMIT-{index:03d}",
                    "sim_resource_id": row["sim_resource_id"],
                    "product_offering_id": "OFFER-IOT-CONNECTIVITY",
                    "price_plan_id": "PLAN-IOT-001",
                    "technical_profile_id": "NET-DATA-SMS-DOM",
                }
                for index, row in enumerate(additional_sims, start=1)
            ]
        },
        headers=ADMIN_HEADERS,
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "REQUEST_VALIDATION_ERROR"


def test_summarized_account_skips_amdocs_after_successful_network_activation():
    client, repository, _ = build_system()
    boreal = account(client, "ACCT-000200")
    enabled = client.post(
        f"/api/v1/admin/accounts/{boreal['account_id']}/billing-mode",
        json={"billing_mode": "SUMMARIZED", "reason": "IoT Connect account policy"},
        headers=ADMIN_HEADERS,
    )
    assert enabled.status_code == 200
    assert enabled.json()["send_subscriptions_to_amdocs"] is False

    created, _ = assign_and_order(client, boreal["account_id"])
    submitted = client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    ).json()

    assert submitted["items"][0]["network_status"] == "ACTIVE"
    assert submitted["items"][0]["legacy_status"] == "SKIPPED_BY_ACCOUNT_POLICY"
    assert repository.list_legacy_lines("LEG-ACCT-200") == []


def test_network_failure_releases_mdn_and_never_calls_amdocs():
    client, repository, state = build_system()
    aster = account(client, "ACCT-000100")
    created, _ = assign_and_order(client, aster["account_id"])
    mdn_resource_id = created["items"][0]["mdn_resource_id"]
    state.arm("SMSC")

    submitted = client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    ).json()

    item = submitted["items"][0]
    assert submitted["status"] == "COMPLETED_WITH_ERRORS"
    assert item["network_status"] == "FAILED_ROLLED_BACK"
    assert item["legacy_status"] == "NOT_ELIGIBLE_NETWORK_FAILURE"
    assert repository.get_mdn(mdn_resource_id)["status"] == "AVAILABLE"
    assert len(repository.list_legacy_lines("LEG-ACCT-100")) == 0
    subscription = repository.list_subscriptions(aster["account_id"])[0]
    assert subscription["status"] == "ACTIVATION_FAILED"


def test_failed_item_can_be_retried_alone_without_resubmitting_successful_items():
    client, repository, state = build_system()
    aster = account(client, "ACCT-000100")
    created, _ = assign_and_order(client, aster["account_id"], 2)
    state.arm("SMSC")

    submitted = client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    ).json()
    assert submitted["success_count"] == 1
    assert submitted["failure_count"] == 1
    failed = next(row for row in submitted["items"] if row["overall_status"] == "FAILED")

    retried = client.post(
        f"/api/v1/admin/activation-batches/{submitted['batch_id']}/items/{failed['batch_item_id']}:retry",
        headers=ADMIN_HEADERS,
    )
    assert retried.status_code == 200, retried.text
    retry_batch = retried.json()
    assert retry_batch["batch_id"] != submitted["batch_id"]
    assert retry_batch["item_count"] == 1
    assert retry_batch["status"] == "COMPLETED"
    assert retry_batch["items"][0]["network_status"] == "ACTIVE"
    assert retry_batch["items"][0]["legacy_status"] == "SUBMITTED"
    assert len(repository.list_legacy_lines("LEG-ACCT-100")) == 2

    second_retry = client.post(
        f"/api/v1/admin/activation-batches/{submitted['batch_id']}/items/{failed['batch_item_id']}:retry",
        headers=ADMIN_HEADERS,
    )
    assert second_retry.status_code == 409


def test_account_private_apn_must_match_and_executes_conditional_aaa():
    client, _, _ = build_system()
    boreal = account(client, "ACCT-000200")
    assert boreal["private_apn_name"] == "BOREAL_IOT_PRIVATE"

    created, _ = assign_and_order(
        client,
        boreal["account_id"],
        private_apn="BOREAL_IOT_PRIVATE",
    )
    submitted = client.post(
        f"/api/v1/admin/activation-batches/{created['batch_id']}:submit",
        headers=ADMIN_HEADERS,
    ).json()
    aaa = submitted["items"][0]["flowone_element_results"][-1]
    assert aaa["element"] == "AAA"
    assert aaa["provisioning_status"] == "SUCCESS"
    assert aaa["applied_profile"] == "ENT_APN_BOREAL_IOT_PRIVATE"


def test_private_apn_is_rejected_for_account_without_configuration():
    client, _, _ = build_system()
    aster = account(client, "ACCT-000100")
    sims = client.get("/api/v1/inventory/sims/available").json()[:1]
    client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/sim-assignments",
        json={"sim_resource_ids": [sims[0]["sim_resource_id"]]},
        headers=ADMIN_HEADERS,
    )
    rejected = client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/activation-batches",
        json={"items": [{
            "source_order_ref": "ORDER-PRIVATE-001",
            "sim_resource_id": sims[0]["sim_resource_id"],
            "product_offering_id": "OFFER-IOT-CONNECTIVITY",
            "price_plan_id": "PLAN-IOT-001",
            "technical_profile_id": "NET-DATA-SMS-DOM",
            "private_apn": "UNCONFIGURED_APN",
        }]},
        headers=ADMIN_HEADERS,
    )
    assert rejected.status_code == 422
    assert "no private APN configured" in rejected.json()["message"]


def test_resource_free_product_can_exist_without_sim_or_mdn_associations():
    _, repository, _ = build_system()
    aster = next(
        row for row in repository.list_accounts()
        if row["account_number"] == "ACCT-000100"
    )
    subscription = {
        "subscription_id": "sub-vas-1",
        "subscription_number": "SUB-VAS-0001",
        "source_subscription_ref": "VAS-ORDER-1",
        "account_id": aster["account_id"],
        "account_number": aster["account_number"],
        "contract_id": aster["contract_id"],
        "product_offering_id": "OFFER-NETFLIX-PREMIUM",
        "price_plan_id": "PLAN-VAS-NETFLIX-PREMIUM",
        "technical_profile_id": None,
        "status": "ACTIVE",
        "start_date": "2026-08-28",
        "end_date": None,
        "activated_at": "2026-08-28T12:00:00+00:00",
        "source_batch_id": "vas-order-batch",
        "source_batch_number": "VAS-0001",
        "created_at": "2026-08-28T12:00:00+00:00",
        "updated_at": "2026-08-28T12:00:00+00:00",
    }
    repository.insert_subscription(subscription)
    assert repository.list_subscription_resources("sub-vas-1") == []
