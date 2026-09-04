from app.domain.demo_seed import build_demo_seed
from app.main import create_repository
from app.repositories.memory import MemoryRepository
from conftest import ADMIN_HEADERS


def _stable_seed_projection(repository):
    return {
        "accounts": [
            {
                "account_id": row["account_id"],
                "account_number": row["account_number"],
                "account_name": row["account_name"],
                "contract_id": row["contract_id"],
            }
            for row in repository.list_accounts()
        ],
        "sims": [
            (row["sim_resource_id"], row["iccid"], row["imsi"])
            for row in repository.list_sims()
        ],
        "mdns": [
            (row["mdn_resource_id"], row["mdn"], row["allocation_sequence"])
            for row in repository.list_mdns()
        ],
    }


def test_memory_demo_reset_restores_the_same_portable_dataset():
    repository = MemoryRepository()
    first = _stable_seed_projection(repository)

    sim = repository.list_sims()[0]
    sim["resource_status"] = "ASSIGNED"
    repository.update_sim(sim)
    repository.next_number("account")

    result = repository.reset()
    second = _stable_seed_projection(repository)

    assert result["backend"] == "memory"
    assert result["accounts_seeded"] == 2
    assert result["sim_resources_seeded"] == 1000
    assert result["mdn_resources_seeded"] == 1000
    assert first == second


def test_shared_seed_matches_memory_logical_inventory():
    seed = build_demo_seed(now="2026-08-29T12:00:00+00:00")
    repository = MemoryRepository()

    assert [row["account_number"] for row in repository.list_accounts()] == [
        row["account_number"] for row in seed["accounts"]
    ]
    assert [row["iccid"] for row in repository.list_sims()] == [
        row["iccid"] for row in seed["sims"]
    ]
    assert [row["mdn"] for row in repository.list_mdns()] == [
        row["mdn"] for row in seed["mdns"]
    ]
    assert {row["current_owner_ref"] for row in repository.list_sims()} == {"RVS"}
    assert all(row["mdn"].isdigit() and len(row["mdn"]) == 10 for row in seed["mdns"])


def test_no_store_configuration_defaults_to_portable_memory(monkeypatch):
    monkeypatch.delenv("IOTCONNECT_STORE", raising=False)

    repository = create_repository()

    assert isinstance(repository, MemoryRepository)
    assert repository.backend_name == "memory"


def test_demo_reset_endpoint_restores_prepared_accounts(client):
    def prepared_identity():
        return [
            (
                row["account_id"],
                row["account_number"],
                row["account_name"],
                row["contract_id"],
                row["contract_number"],
                row["billing_mode"],
            )
            for row in client.get("/api/v1/accounts").json()
        ]

    before = prepared_identity()
    created = client.post(
        "/api/v1/admin/accounts",
        json={
            "customer_name": "Cedar Logistics",
            "account_name": "Cedar Logistics",
            "external_billing_account_number": "LEG-ACCT-300",
            "reason": "Prove the presentation reset contract",
        },
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201
    assert len(client.get("/api/v1/accounts").json()) == 3

    reset = client.post(
        "/api/v1/admin/demo:reset",
        headers=ADMIN_HEADERS,
    )

    assert reset.status_code == 200
    assert reset.json()["backend"] in {"memory", "postgres"}
    assert prepared_identity() == before
