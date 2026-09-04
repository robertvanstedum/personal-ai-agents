from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy

from app.domain.demo_seed import build_demo_seed
from app.domain.errors import ConflictError


class MemoryRepository:
    """Deterministic adapter for development, testing, and panel contingency."""

    backend_name = "memory"

    def __init__(self) -> None:
        self.reset()

    @contextmanager
    def transaction(self):
        snapshot = deepcopy(self.__dict__)
        try:
            yield
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(snapshot)
            raise

    def reset(self) -> dict:
        seed = build_demo_seed()
        self.sequences = {
            "customer": 299,
            "account": 299,
            "contract": 299,
            "subscription": 0,
            "batch": 0,
            "bill_run": 0,
        }
        self.customers = {row["customer_id"]: row for row in seed["customers"]}
        self.accounts = {row["account_id"]: row for row in seed["accounts"]}
        self.contracts = {row["contract_id"]: row for row in seed["contracts"]}
        self.legacy_accounts = {
            row["legacy_account_ref"]: row for row in seed["legacy_accounts"]
        }
        self.legacy_lines: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self.subscription_resources: dict[str, dict] = {}
        self.sims = {row["sim_resource_id"]: row for row in seed["sims"]}
        self.mdns = {row["mdn_resource_id"]: row for row in seed["mdns"]}
        self.activation_batches: dict[str, dict] = {}
        self.activation_batch_items: dict[str, dict] = {}
        self.flowone_element_results: list[dict] = []
        self.audit_events: list[dict] = []
        self.activation_events: list[dict] = []
        self.network_activations: dict[str, dict] = {}
        self.legacy_subscription_actions: dict[str, dict] = {}
        self.charges: dict[str, dict] = {}
        self.billing_rows: list[dict] = []
        self.bill_runs: dict[str, dict] = {}
        return {
            "status": "reset",
            "backend": self.backend_name,
            "accounts_seeded": 2,
            "contracts_seeded": 2,
            "golden_lines_seeded": 0,
            "sim_resources_seeded": 1000,
            "mdn_resources_seeded": 1000,
        }

    def next_number(self, sequence_name: str) -> int:
        if sequence_name not in self.sequences:
            raise KeyError(f"Unknown sequence {sequence_name}")
        self.sequences[sequence_name] += 1
        return self.sequences[sequence_name]

    def list_accounts(self) -> list[dict]:
        return deepcopy(sorted(self.accounts.values(), key=lambda row: row["account_number"]))

    def get_account(self, account_id: str) -> dict | None:
        row = self.accounts.get(account_id)
        return deepcopy(row) if row else None

    def insert_account(self, account: dict) -> None:
        if account["account_id"] in self.accounts:
            raise ConflictError("account_id already exists")
        if any(row["account_number"] == account["account_number"] for row in self.accounts.values()):
            raise ConflictError("account_number already exists")
        self.accounts[account["account_id"]] = deepcopy(account)

    def update_account(self, account: dict) -> None:
        self.accounts[account["account_id"]] = deepcopy(account)

    def insert_customer(self, customer: dict) -> None:
        if customer["customer_id"] in self.customers:
            raise ConflictError("customer_id already exists")
        if any(row["customer_number"] == customer["customer_number"] for row in self.customers.values()):
            raise ConflictError("customer_number already exists")
        self.customers[customer["customer_id"]] = deepcopy(customer)

    def get_customer(self, customer_id: str) -> dict | None:
        row = self.customers.get(customer_id)
        return deepcopy(row) if row else None

    def update_customer(self, customer: dict) -> None:
        self.customers[customer["customer_id"]] = deepcopy(customer)

    def get_contract(self, contract_id: str) -> dict | None:
        row = self.contracts.get(contract_id)
        return deepcopy(row) if row else None

    def insert_contract(self, contract: dict) -> None:
        if contract["contract_id"] in self.contracts:
            raise ConflictError("contract_id already exists")
        if any(row["contract_number"] == contract["contract_number"] for row in self.contracts.values()):
            raise ConflictError("contract_number already exists")
        self.contracts[contract["contract_id"]] = deepcopy(contract)

    def insert_legacy_account(self, legacy_account: dict) -> None:
        ref = legacy_account["legacy_account_ref"]
        if ref in self.legacy_accounts:
            raise ConflictError("legacy_account_ref already exists")
        self.legacy_accounts[ref] = deepcopy(legacy_account)

    def get_legacy_account(self, legacy_account_ref: str) -> dict | None:
        row = self.legacy_accounts.get(legacy_account_ref)
        return deepcopy(row) if row else None

    def list_legacy_accounts(self) -> list[dict]:
        return deepcopy(sorted(self.legacy_accounts.values(), key=lambda row: row["legacy_account_ref"]))

    def insert_legacy_line(self, legacy_line: dict) -> None:
        ref = legacy_line["legacy_line_ref"]
        if ref in self.legacy_lines:
            raise ConflictError(f"legacy_line_ref {ref} already exists")
        self.legacy_lines[ref] = deepcopy(legacy_line)

    def get_legacy_line(self, legacy_line_ref: str) -> dict | None:
        row = self.legacy_lines.get(legacy_line_ref)
        return deepcopy(row) if row else None

    def list_legacy_lines(self, legacy_account_ref: str) -> list[dict]:
        return deepcopy(
            sorted(
                [row for row in self.legacy_lines.values() if row["legacy_account_ref"] == legacy_account_ref],
                key=lambda row: row["legacy_line_ref"],
            )
        )

    def find_subscription_by_source(self, account_id: str, source_subscription_ref: str) -> dict | None:
        for row in self.subscriptions.values():
            if row["account_id"] == account_id and row["source_subscription_ref"] == source_subscription_ref:
                return deepcopy(row)
        return None

    def find_subscription_by_sim(self, sim_id: str) -> dict | None:
        for resource in self.subscription_resources.values():
            if resource["resource_type"] == "SIM" and resource["resource_id"] == sim_id:
                row = self.subscriptions.get(resource["subscription_id"])
                return deepcopy(row) if row else None
        return None

    def insert_subscription(self, subscription: dict) -> None:
        if subscription["subscription_id"] in self.subscriptions:
            raise ConflictError("subscription_id already exists")
        if any(row["subscription_number"] == subscription["subscription_number"] for row in self.subscriptions.values()):
            raise ConflictError("subscription_number already exists")
        if self.find_subscription_by_source(subscription["account_id"], subscription["source_subscription_ref"]):
            raise ConflictError("source_subscription_ref already exists for this account")
        self.subscriptions[subscription["subscription_id"]] = deepcopy(subscription)

    def update_subscription(self, subscription: dict) -> None:
        self.subscriptions[subscription["subscription_id"]] = deepcopy(subscription)

    def list_subscriptions(self, account_id: str) -> list[dict]:
        return deepcopy(
            sorted(
                [row for row in self.subscriptions.values() if row["account_id"] == account_id],
                key=lambda row: row["subscription_number"],
            )
        )

    def list_sims(self, account_id: str | None = None) -> list[dict]:
        rows = list(self.sims.values())
        if account_id is not None:
            rows = [
                row for row in rows
                if row["current_owner_type"] == "ACCOUNT"
                and row["current_owner_ref"] == account_id
            ]
        return deepcopy(sorted(rows, key=lambda row: row["iccid"]))

    def list_account_resource_views(self, account_id: str) -> list[dict]:
        """Return the account resource projection without per-SIM repository calls."""
        subscriptions_by_sim: dict[str, dict] = {}
        mdns_by_subscription: dict[str, dict] = {}
        for link in self.subscription_resources.values():
            if link["status"] != "ACTIVE":
                continue
            if link["resource_type"] == "SIM":
                subscription = self.subscriptions.get(link["subscription_id"])
                if subscription and subscription["account_id"] == account_id:
                    subscriptions_by_sim[link["resource_id"]] = subscription
            elif link["resource_type"] == "MDN":
                mdn = self.mdns.get(link["resource_id"])
                if mdn:
                    mdns_by_subscription[link["subscription_id"]] = mdn

        rows = []
        for sim in self.list_sims(account_id):
            subscription = subscriptions_by_sim.get(sim["sim_resource_id"])
            mdn = (
                mdns_by_subscription.get(subscription["subscription_id"])
                if subscription
                else None
            )
            rows.append(
                {
                    **sim,
                    "subscription_id": subscription["subscription_id"] if subscription else None,
                    "subscription_number": subscription["subscription_number"] if subscription else None,
                    "price_plan_id": subscription["price_plan_id"] if subscription else None,
                    "subscription_status": subscription["status"] if subscription else None,
                    "subscription_updated_at": subscription["updated_at"] if subscription else None,
                    "mdn_resource_id": mdn["mdn_resource_id"] if mdn else None,
                    "mdn": mdn["mdn"] if mdn else None,
                }
            )
        return deepcopy(rows)

    def get_sim(self, sim_resource_id: str) -> dict | None:
        row = self.sims.get(sim_resource_id)
        return deepcopy(row) if row else None

    def insert_sim(self, sim: dict) -> None:
        if sim["sim_resource_id"] in self.sims:
            raise ConflictError("sim_resource_id already exists")
        self.sims[sim["sim_resource_id"]] = deepcopy(sim)

    def update_sim(self, sim: dict) -> None:
        self.sims[sim["sim_resource_id"]] = deepcopy(sim)

    def list_mdns(self, status: str | None = None) -> list[dict]:
        rows = list(self.mdns.values())
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        return deepcopy(sorted(rows, key=lambda row: row["allocation_sequence"]))

    def get_mdn(self, mdn_resource_id: str) -> dict | None:
        row = self.mdns.get(mdn_resource_id)
        return deepcopy(row) if row else None

    def update_mdn(self, mdn: dict) -> None:
        self.mdns[mdn["mdn_resource_id"]] = deepcopy(mdn)

    def insert_subscription_resource(self, resource: dict) -> None:
        self.subscription_resources[resource["subscription_resource_id"]] = deepcopy(resource)

    def update_subscription_resource(self, resource: dict) -> None:
        self.subscription_resources[resource["subscription_resource_id"]] = deepcopy(resource)

    def list_subscription_resources(self, subscription_id: str) -> list[dict]:
        return deepcopy([
            row for row in self.subscription_resources.values()
            if row["subscription_id"] == subscription_id
        ])

    def insert_activation_batch(self, batch: dict) -> None:
        self.activation_batches[batch["batch_id"]] = deepcopy(batch)

    def update_activation_batch(self, batch: dict) -> None:
        self.activation_batches[batch["batch_id"]] = deepcopy(batch)

    def get_activation_batch(self, batch_id: str) -> dict | None:
        row = self.activation_batches.get(batch_id)
        return deepcopy(row) if row else None

    def list_activation_batches(self, account_id: str | None = None) -> list[dict]:
        rows = self.activation_batches.values()
        if account_id is not None:
            rows = [row for row in rows if row["account_id"] == account_id]
        return deepcopy(sorted(rows, key=lambda row: row["created_at"], reverse=True))

    def insert_activation_batch_item(self, item: dict) -> None:
        self.activation_batch_items[item["batch_item_id"]] = deepcopy(item)

    def update_activation_batch_item(self, item: dict) -> None:
        self.activation_batch_items[item["batch_item_id"]] = deepcopy(item)

    def list_activation_batch_items(self, batch_id: str) -> list[dict]:
        return deepcopy(sorted(
            [row for row in self.activation_batch_items.values() if row["batch_id"] == batch_id],
            key=lambda row: row["item_number"],
        ))

    def insert_flowone_element_result(self, result: dict) -> None:
        self.flowone_element_results.append(deepcopy(result))

    def list_flowone_element_results(self, batch_item_id: str) -> list[dict]:
        return deepcopy([
            row for row in self.flowone_element_results
            if row["batch_item_id"] == batch_item_id
        ])

    def insert_network_activation(self, row: dict) -> None:
        if row["activation_id"] in self.network_activations:
            raise ConflictError("activation_id already exists")
        self.network_activations[row["activation_id"]] = deepcopy(row)

    def get_network_activation(self, activation_id: str) -> dict | None:
        row = self.network_activations.get(activation_id)
        return deepcopy(row["payload"]) if row else None

    def insert_legacy_subscription_action(self, row: dict) -> None:
        if row["compatibility_action_id"] in self.legacy_subscription_actions:
            raise ConflictError("compatibility_action_id already exists")
        self.legacy_subscription_actions[row["compatibility_action_id"]] = deepcopy(row)

    def get_legacy_subscription_action(self, compatibility_action_id: str) -> dict | None:
        row = self.legacy_subscription_actions.get(compatibility_action_id)
        return deepcopy(row["payload"]) if row else None

    def insert_audit_event(self, event: dict) -> None:
        self.audit_events.append(deepcopy(event))

    def insert_activation_event(self, event: dict) -> None:
        self.activation_events.append(deepcopy(event))

    def delete_billing_for_account_cycle(self, account_id: str, bill_cycle: str) -> None:
        prior_runs = {
            run_id
            for run_id, run in self.bill_runs.items()
            if run["account_id"] == account_id and run["bill_cycle"] == bill_cycle
        }
        self.charges = {
            key: row for key, row in self.charges.items() if row["bill_run_id"] not in prior_runs
        }
        self.billing_rows = [row for row in self.billing_rows if row["bill_run_id"] not in prior_runs]
        self.bill_runs = {key: row for key, row in self.bill_runs.items() if key not in prior_runs}

    def insert_charge(self, charge: dict) -> None:
        self.charges[charge["charge_id"]] = deepcopy(charge)

    def insert_billing_row(self, row: dict) -> None:
        self.billing_rows.append(deepcopy(row))

    def insert_bill_run(self, run: dict) -> None:
        self.bill_runs[run["bill_run_id"]] = deepcopy(run)

    def get_bill_run(self, bill_run_id: str) -> dict | None:
        row = self.bill_runs.get(bill_run_id)
        return deepcopy(row) if row else None

    def list_bill_runs(self, account_id: str | None = None) -> list[dict]:
        rows = self.bill_runs.values()
        if account_id is not None:
            rows = [row for row in rows if row["account_id"] == account_id]
        return deepcopy(sorted(rows, key=lambda row: row["created_at"], reverse=True))

    def list_charges(self, bill_run_id: str) -> list[dict]:
        return deepcopy(sorted(
            [row for row in self.charges.values() if row["bill_run_id"] == bill_run_id],
            key=lambda row: (row["charge_level"], row["charge_id"]),
        ))

    def list_billing_rows(self, bill_run_id: str) -> list[dict]:
        return deepcopy(sorted(
            [row for row in self.billing_rows if row["bill_run_id"] == bill_run_id],
            key=lambda row: row["row_number"],
        ))

    def latest_bill_run(self, account_id: str, bill_cycle: str) -> dict | None:
        rows = [
            row for row in self.bill_runs.values()
            if row["account_id"] == account_id and row["bill_cycle"] == bill_cycle
        ]
        if not rows:
            return None
        return deepcopy(sorted(rows, key=lambda row: row["created_at"])[-1])
