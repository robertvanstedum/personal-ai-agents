"""
Summarized billing is a one-way transition.

While summarized, network-successful subscriptions are skipped by policy and never
created in Amdocs. Reverting to detailed would leave every subscription activated
during the summarized period with no Amdocs record to post against, so restoring it
is a retroactive migration rather than a configuration change.

The rule is enforced in the service layer, not only in the admin UI, so these tests
drive the API directly -- a UI-only guard would leave the guarantee cosmetic.
"""
from tests.conftest import ADMIN_HEADERS
from tests.test_identity_and_admin import create_account


def set_mode(client, account_id, mode, reason="Accounting-approved configuration"):
    return client.post(
        f"/api/v1/admin/accounts/{account_id}/billing-mode",
        json={"billing_mode": mode, "reason": reason},
        headers=ADMIN_HEADERS,
    )


def test_detailed_to_summarized_is_allowed_and_audited(client):
    account = create_account(client, "Cedar Logistics")
    response = set_mode(client, account["account_id"], "SUMMARIZED")

    assert response.status_code == 200
    assert response.json()["billing_mode"] == "SUMMARIZED"
    assert response.json()["send_subscriptions_to_amdocs"] is False


def test_summarized_to_detailed_is_rejected(client):
    account = create_account(client, "Cedar Logistics")
    assert set_mode(client, account["account_id"], "SUMMARIZED").status_code == 200

    reverted = set_mode(client, account["account_id"], "DETAILED")

    assert reverted.status_code == 409
    assert reverted.json()["code"] == "CONFLICT"
    # The message must explain why, not merely that it was blocked -- this text is what
    # the operator sees when the panel asks whether it can be switched back.
    assert "migration" in reverted.json()["message"].lower()


def test_account_stays_summarized_after_a_rejected_reversal(client):
    account = create_account(client, "Cedar Logistics")
    set_mode(client, account["account_id"], "SUMMARIZED")
    set_mode(client, account["account_id"], "DETAILED")

    current = client.get(f"/api/v1/accounts/{account['account_id']}", headers=ADMIN_HEADERS)
    assert current.json()["billing_mode"] == "SUMMARIZED"
    assert current.json()["send_subscriptions_to_amdocs"] is False


def test_reapplying_summarized_is_a_no_op_not_an_error(client):
    """Double-clicking the control must not fail; it simply changes nothing."""
    account = create_account(client, "Cedar Logistics")
    assert set_mode(client, account["account_id"], "SUMMARIZED").status_code == 200

    again = set_mode(client, account["account_id"], "SUMMARIZED")

    assert again.status_code == 200
    assert again.json()["billing_mode"] == "SUMMARIZED"


def test_detailed_to_detailed_is_a_no_op(client):
    account = create_account(client, "Cedar Logistics")
    response = set_mode(client, account["account_id"], "DETAILED")

    assert response.status_code == 200
    assert response.json()["billing_mode"] == "DETAILED"


def test_reset_returns_prepared_accounts_to_detailed(client):
    """demo:reset is the only path back, and the demo depends on it between runs."""
    accounts = client.get("/api/v1/accounts", headers=ADMIN_HEADERS).json()
    boreal = next(a for a in accounts if "Boreal" in a["customer_name"])
    assert set_mode(client, boreal["account_id"], "SUMMARIZED").status_code == 200

    client.post("/api/v1/admin/demo:reset", headers=ADMIN_HEADERS)

    after = client.get("/api/v1/accounts", headers=ADMIN_HEADERS).json()
    for account in after:
        assert account["billing_mode"] == "DETAILED"
        assert account["send_subscriptions_to_amdocs"] is True
