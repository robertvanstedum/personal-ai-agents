from __future__ import annotations

import os

from app.domain.identity import seeded_uuid


# Fixed so that two resets produce byte-identical evidence. Override with the
# IOTCONNECT_SEED_TIMESTAMP environment variable or the ``now`` argument.
DEFAULT_SEED_TIMESTAMP = "2026-08-01T00:00:00+00:00"


def build_demo_seed(now: str | None = None) -> dict[str, list[dict]]:
    """One deterministic logical seed shared by every supported repository."""

    timestamp = now or os.getenv("IOTCONNECT_SEED_TIMESTAMP") or DEFAULT_SEED_TIMESTAMP
    aster_account_id = seeded_uuid("account", "ACCT-000100")
    boreal_account_id = seeded_uuid("account", "ACCT-000200")
    aster_customer_id = seeded_uuid("customer", "CUS-000100")
    boreal_customer_id = seeded_uuid("customer", "CUS-000200")
    aster_contract_id = seeded_uuid("contract", "CTR-000100")
    boreal_contract_id = seeded_uuid("contract", "CTR-000200")

    return {
        "customers": [
            {
                "customer_id": aster_customer_id,
                "customer_number": "CUS-000100",
                "customer_name": "Aster Field Systems",
                "status": "ACTIVE",
                "created_at": timestamp,
            },
            {
                "customer_id": boreal_customer_id,
                "customer_number": "CUS-000200",
                "customer_name": "Boreal Equipment Group",
                "status": "ACTIVE",
                "created_at": timestamp,
            },
        ],
        "accounts": [
            {
                "account_id": aster_account_id,
                "account_number": "ACCT-000100",
                "account_name": "Aster Field Systems",
                "customer_id": aster_customer_id,
                "contract_id": aster_contract_id,
                "external_customer_ref": "CRM-ASTER-100",
                "external_billing_account_number": "LEG-ACCT-100",
                "send_subscriptions_to_amdocs": True,
                "private_apn_name": None,
                "billing_mode": "DETAILED",
                "status": "ACTIVE",
                "updated_by": "seed",
                "updated_at": timestamp,
            },
            {
                "account_id": boreal_account_id,
                "account_number": "ACCT-000200",
                "account_name": "Boreal Equipment Group",
                "customer_id": boreal_customer_id,
                "contract_id": boreal_contract_id,
                "external_customer_ref": "CRM-BOREAL-200",
                "external_billing_account_number": "LEG-ACCT-200",
                "send_subscriptions_to_amdocs": True,
                "private_apn_name": "BOREAL_IOT_PRIVATE",
                "billing_mode": "DETAILED",
                "status": "ACTIVE",
                "updated_by": "seed",
                "updated_at": timestamp,
            },
        ],
        "contracts": [
            {
                "contract_id": aster_contract_id,
                "contract_number": "CTR-000100",
                "account_id": aster_account_id,
                "contract_name": "Aster Enterprise IoT Agreement",
                "status": "ACTIVE",
                "effective_date": "2026-08-01",
            },
            {
                "contract_id": boreal_contract_id,
                "contract_number": "CTR-000200",
                "account_id": boreal_account_id,
                "contract_name": "Boreal Enterprise IoT Agreement",
                "status": "ACTIVE",
                "effective_date": "2026-08-01",
            },
        ],
        "legacy_accounts": [
            {"legacy_account_ref": "LEG-ACCT-100", "account_name": "Aster Field Systems", "status": "ACTIVE"},
            {"legacy_account_ref": "LEG-ACCT-200", "account_name": "Boreal Equipment Group", "status": "ACTIVE"},
            {"legacy_account_ref": "LEG-ACCT-300", "account_name": "Available enterprise billing account", "status": "ACTIVE"},
            {"legacy_account_ref": "LEG-ACCT-301", "account_name": "Available enterprise billing account", "status": "ACTIVE"},
            {"legacy_account_ref": "LEG-ACCT-302", "account_name": "Available enterprise billing account", "status": "ACTIVE"},
        ],
        "sims": [
            {
                "sim_resource_id": seeded_uuid("sim", f"SIM-{number:06d}"),
                "iccid": f"8901410321111851{number:04d}",
                "imsi": f"3101501234{number:05d}",
                "current_owner_type": "OPERATOR",
                "current_owner_ref": "RVS",
                "resource_status": "AVAILABLE",
                "updated_at": timestamp,
            }
            for number in range(1, 1001)
        ],
        "mdns": [
            {
                "mdn_resource_id": seeded_uuid("mdn", f"MDN-{number:06d}"),
                "mdn": f"312555{number:04d}",
                "allocation_sequence": number,
                "status": "AVAILABLE",
                "assigned_account_id": None,
                "updated_at": timestamp,
            }
            for number in range(1, 1001)
        ],
    }
