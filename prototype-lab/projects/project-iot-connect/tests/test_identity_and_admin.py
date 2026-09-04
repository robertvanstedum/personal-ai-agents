from uuid import UUID

import pytest

from app.domain.errors import ConflictError
from app.repositories.memory import MemoryRepository
from app.services.demo import DemoService
from conftest import ADMIN_HEADERS, fixture_text


def create_account(client, name, external_account=None):
    if external_account is None:
        external_account = (
            "LEG-ACCT-300" if name == "Cedar Logistics" else "LEG-ACCT-301"
        )
    response = client.post(
        "/api/v1/admin/accounts",
        json={
            "customer_name": name,
            "account_name": name,
            "external_billing_account_number": external_account,
            "reason": "Hands-on v3 test",
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    return response.json()


def upload(client, account_id, fixture_name):
    return client.post(
        f"/api/v1/admin/accounts/{account_id}/subscriptions:upload",
        content=fixture_text(fixture_name),
        headers={**ADMIN_HEADERS, "Content-Type": "text/csv"},
    )


def test_two_accounts_can_reuse_source_refs_without_sharing_subscriptions(client):
    first = create_account(client, "Cedar Logistics")
    second = create_account(client, "Dover Industrial")
    first_upload = upload(client, first["account_id"], "backup_4_subscriptions.csv")
    second_upload = upload(
        client, second["account_id"], "backup_second_account_4_subscriptions.csv"
    )
    assert first_upload.status_code == 201
    assert second_upload.status_code == 201

    first_rows = client.get(
        f"/api/v1/accounts/{first['account_id']}/subscriptions?system=iot"
    ).json()
    second_rows = client.get(
        f"/api/v1/accounts/{second['account_id']}/subscriptions?system=iot"
    ).json()
    assert len(first_rows) == len(second_rows) == 4
    assert {row["source_subscription_ref"] for row in first_rows} == {
        row["source_subscription_ref"] for row in second_rows
    }
    assert {row["subscription_id"] for row in first_rows}.isdisjoint(
        {row["subscription_id"] for row in second_rows}
    )
    assert {row["subscription_number"] for row in first_rows}.isdisjoint(
        {row["subscription_number"] for row in second_rows}
    )
    assert all(row["account_id"] == first["account_id"] for row in first_rows)
    assert all(row["contract_id"] == first["contract_id"] for row in first_rows)
    for row in first_rows + second_rows:
        UUID(row["subscription_id"])


def test_repeat_source_ref_on_same_account_is_conflict(client):
    account = create_account(client, "Cedar Logistics")
    assert upload(client, account["account_id"], "backup_4_subscriptions.csv").status_code == 201
    repeated = upload(client, account["account_id"], "backup_4_subscriptions.csv")
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "CONFLICT"
    assert "DEVICE-001" in repeated.json()["message"]


def test_summarized_mode_posts_to_account_without_golden_line(client):
    account = create_account(client, "Cedar Logistics")
    enabled = client.post(
        f"/api/v1/admin/accounts/{account['account_id']}/billing-mode",
        json={"billing_mode": "SUMMARIZED", "reason": "Accounting approved account posting"},
        headers=ADMIN_HEADERS,
    )
    assert enabled.status_code == 200
    assert enabled.json()["billing_mode"] == "SUMMARIZED"
    assert enabled.json()["send_subscriptions_to_amdocs"] is False
    assert "golden_line_ref" not in enabled.json()


class FailingContractRepository(MemoryRepository):
    def insert_contract(self, contract):
        raise RuntimeError("synthetic database failure")


def test_account_and_contract_creation_roll_back_together():
    repository = FailingContractRepository()
    service = DemoService(repository)
    before_accounts = repository.list_accounts()
    before_sequences = dict(repository.sequences)
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        service.create_account(
            customer_name="Cedar Logistics",
            account_name="Cedar Logistics",
            external_billing_account_number="LEG-ACCT-300",
            external_customer_ref=None,
            actor="BUSINESS_OPS_ADMIN",
            reason="Prove transaction rollback",
        )
    assert repository.list_accounts() == before_accounts
    assert repository.sequences == before_sequences
