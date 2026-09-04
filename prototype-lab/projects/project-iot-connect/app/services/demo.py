from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from copy import deepcopy
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.domain.catalog import (
    DETAILED_ACCOUNT_CHARGES,
    RATE_PLANS,
    SUMMARIZED_ACCOUNT_CHARGES,
    public_network_profiles,
    public_product_offerings,
    public_rate_plans,
)
from app.domain.csv_input import plan_counts
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.identity import IdentityFactory, new_uuid, seeded_uuid
from app.repositories.protocols import Repository


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def money(value: Decimal | str | int | float) -> str:
    return f"{Decimal(str(value)):.2f}"


class DemoService:
    """All IoT Connect use cases. HTTP and database syntax stop at this boundary."""

    def __init__(self, repository: Repository):
        self.repository = repository
        self.identities = IdentityFactory(repository)

    def reset(self) -> dict:
        return self.repository.reset()

    def rate_plans(self) -> list[dict]:
        return public_rate_plans()

    def product_offerings(self) -> list[dict]:
        return public_product_offerings()

    def network_profiles(self) -> list[dict]:
        return public_network_profiles()

    def list_accounts(self) -> list[dict]:
        return [self._account_view(row) for row in self.repository.list_accounts()]

    def get_account(self, account_id: str) -> dict:
        return self._account_view(self._account(account_id))

    def get_account_by_number(self, account_number: str) -> dict:
        account = next(
            (
                row
                for row in self.repository.list_accounts()
                if row["account_number"] == account_number
            ),
            None,
        )
        if not account:
            raise NotFoundError(f"Account {account_number} not found")
        return self._account_view(account)

    def billing_policy(self, account_id: str) -> dict:
        """Return the small policy projection used as interview evidence."""
        account = self._account(account_id)
        summarized = account["billing_mode"] == "SUMMARIZED"
        return {
            "account_id": account["account_id"],
            "account_number": account["account_number"],
            "account_name": account["account_name"],
            "billing_mode": account["billing_mode"],
            "summarized_billing_enabled": summarized,
            "send_subscriptions_to_legacy_billing": account[
                "send_subscriptions_to_amdocs"
            ],
            "posting_scope": "ACCOUNT" if summarized else "SUBSCRIPTION",
            "external_billing_account_number": account[
                "external_billing_account_number"
            ],
            "updated_by": account["updated_by"],
            "updated_at": account["updated_at"],
        }

    def account_resources(self, account_id: str) -> list[dict]:
        account = self._account(account_id)
        canonical_account_id = account["account_id"]
        rows: list[dict] = []
        for resource in self.repository.list_account_resource_views(canonical_account_id):
            plan = RATE_PLANS.get(resource["price_plan_id"]) if resource["price_plan_id"] else None
            rows.append(
                {
                    "sim_resource_id": resource["sim_resource_id"],
                    "iccid": resource["iccid"],
                    "imsi": resource["imsi"],
                    "mdn_resource_id": resource["mdn_resource_id"],
                    "mdn": resource["mdn"],
                    "subscription_id": resource["subscription_id"],
                    "subscription_number": resource["subscription_number"],
                    "price_plan_id": resource["price_plan_id"],
                    "rate_plan_name": plan["name"] if plan else None,
                    "status": resource["subscription_status"] or "AVAILABLE",
                    "last_change": resource["subscription_updated_at"] or resource["updated_at"],
                }
            )
        return sorted(rows, key=lambda row: (row["status"], row["iccid"]))

    def account_summary(self, account_id: str) -> dict:
        account = self.get_account(account_id)
        resources = self.account_resources(account_id)
        statuses = Counter(row["status"] for row in resources)
        plan_counts = Counter(
            row["price_plan_id"]
            for row in resources
            if row["price_plan_id"] and row["status"] == "ACTIVE"
        )
        runs = self.repository.list_bill_runs(account["account_id"])
        return {
            "account": account,
            "available_sims": statuses["AVAILABLE"],
            "active_subscriptions": statuses["ACTIVE"],
            "suspended_subscriptions": statuses["SUSPENDED"],
            "pending_subscriptions": statuses["PENDING_ACTIVATION"],
            "retired_subscriptions": statuses["RETIRED"],
            "total_resources": len(resources),
            "rate_plan_counts": dict(sorted(plan_counts.items())),
            "latest_bill_run": runs[0] if runs else None,
        }

    def list_account_summaries(self) -> list[dict]:
        """Return one operator-ready projection without browser-side N+1 requests."""
        return [
            self.account_summary(account["account_id"])
            for account in self.repository.list_accounts()
        ]

    def create_account(
        self,
        *,
        customer_name: str,
        account_name: str,
        external_billing_account_number: str,
        external_customer_ref: str | None,
        actor: str,
        reason: str,
    ) -> dict:
        now = utc_now()
        legacy_account = self.repository.get_legacy_account(
            external_billing_account_number
        )
        if not legacy_account or legacy_account["status"] != "ACTIVE":
            raise ValidationError(
                "The external billing account must already exist and be active in Amdocs"
            )
        if any(
            row["external_billing_account_number"] == external_billing_account_number
            for row in self.repository.list_accounts()
        ):
            raise ConflictError(
                "The external billing account is already linked to a WDH account"
            )
        with self.repository.transaction():
            customer_id, customer_number = self.identities.customer()
            account_id, account_number = self.identities.account()
            contract_id, contract_number = self.identities.contract()
            customer = {
                "customer_id": customer_id,
                "customer_number": customer_number,
                "customer_name": customer_name,
                "status": "ACTIVE",
                "created_at": now,
            }
            account = {
                "account_id": account_id,
                "account_number": account_number,
                "account_name": account_name,
                "customer_id": customer_id,
                "contract_id": contract_id,
                "external_customer_ref": external_customer_ref,
                "external_billing_account_number": external_billing_account_number,
                "send_subscriptions_to_amdocs": True,
                "private_apn_name": None,
                "billing_mode": "DETAILED",
                "status": "ACTIVE",
                "updated_by": actor,
                "updated_at": now,
            }
            contract = {
                "contract_id": contract_id,
                "contract_number": contract_number,
                "account_id": account_id,
                "contract_name": f"{account_name} Enterprise IoT Agreement",
                "status": "ACTIVE",
                "effective_date": date.today().isoformat(),
            }
            self.repository.insert_customer(customer)
            self.repository.insert_account(account)
            self.repository.insert_contract(contract)
            self.repository.insert_audit_event(
                self._audit(
                    account_id,
                    "ACCOUNT_CREATED",
                    actor,
                    reason,
                    {
                        "account_number": account_number,
                        "contract_id": contract_id,
                        "contract_number": contract_number,
                        "external_billing_account_number": external_billing_account_number,
                    },
                )
            )
        return self.get_account(account_id)

    def available_legacy_accounts(self) -> list[dict]:
        linked = {
            row["external_billing_account_number"]
            for row in self.repository.list_accounts()
        }
        return [
            row
            for row in self.repository.list_legacy_accounts()
            if row["status"] == "ACTIVE"
            and row["legacy_account_ref"] not in linked
        ]

    def update_account_configuration(
        self,
        *,
        account_id: str,
        customer_name: str,
        account_name: str,
        external_customer_ref: str | None,
        private_apn_name: str | None,
        actor: str,
        reason: str,
    ) -> dict:
        account = self._account(account_id)
        customer = self.repository.get_customer(account["customer_id"])
        if not customer:
            raise NotFoundError(f"Customer for account {account['account_number']} not found")
        prior = {
            "customer_name": customer["customer_name"],
            "account_name": account["account_name"],
            "external_customer_ref": account["external_customer_ref"],
            "private_apn_name": account.get("private_apn_name"),
        }
        now = utc_now()
        with self.repository.transaction():
            customer["customer_name"] = customer_name
            self.repository.update_customer(customer)
            account["account_name"] = account_name
            account["external_customer_ref"] = external_customer_ref
            account["private_apn_name"] = private_apn_name
            account["updated_by"] = actor
            account["updated_at"] = now
            self.repository.update_account(account)
            self.repository.insert_audit_event(
                self._audit(
                    account_id,
                    "ACCOUNT_CONFIGURATION_CHANGED",
                    actor,
                    reason,
                    {"before": prior, "after": {
                        "customer_name": customer_name,
                        "account_name": account_name,
                        "external_customer_ref": external_customer_ref,
                        "private_apn_name": private_apn_name,
                    }},
                )
            )
        return self.get_account(account_id)

    def set_billing_mode(
        self, *, account_id: str, billing_mode: str, actor: str, reason: str
    ) -> dict:
        account = self._account(account_id)
        previous_mode = account["billing_mode"]

        # Summarized billing is a one-way transition.
        #
        # While summarized, network-successful subscriptions are skipped by policy and
        # never created in Amdocs. Reverting to detailed would therefore leave every
        # subscription activated during the summarized period with no Amdocs record to
        # post against -- restoring them is a retroactive migration, not a mode change.
        # A bill cycle also cannot be split across two posting models without producing
        # an invoice that neither reconciles nor presents coherently.
        #
        # Enforced here rather than only in the UI so the guarantee holds for any caller.
        # demo:reset restores seed state; that is a demo affordance, not a business path.
        if previous_mode == "SUMMARIZED" and billing_mode == "DETAILED":
            raise ConflictError(
                f"Account {account['account_number']} is already in summarized billing and "
                "cannot be returned to detailed. Subscriptions activated under summarized "
                "posting have no Amdocs subscription records to restore; reverting requires "
                "a retroactive migration rather than a configuration change."
            )

        if previous_mode == billing_mode:
            return self.get_account(account_id)

        with self.repository.transaction():
            account["billing_mode"] = billing_mode
            account["send_subscriptions_to_amdocs"] = billing_mode == "DETAILED"
            account["updated_by"] = actor
            account["updated_at"] = utc_now()
            self.repository.update_account(account)
            self.repository.insert_audit_event(
                self._audit(
                    account_id,
                    "BILLING_MODE_CHANGED",
                    actor,
                    reason,
                    {
                        "previous_mode": previous_mode,
                        "new_mode": billing_mode,
                        "posting_strategy": (
                            "ACCOUNT" if billing_mode == "SUMMARIZED" else "SUBSCRIPTION"
                        ),
                        "irreversible": billing_mode == "SUMMARIZED",
                    },
                )
            )
        return self.get_account(account_id)

    def upload_subscriptions(
        self, *, account_id: str, rows: list[dict], actor: str
    ) -> dict:
        account = self._account(account_id)
        for row in rows:
            if self.repository.find_subscription_by_source(
                account_id, row["source_subscription_ref"]
            ):
                raise ConflictError(
                    f"source_subscription_ref {row['source_subscription_ref']} already exists for {account['account_number']}"
                )
            existing_sim = self.repository.find_subscription_by_sim(row["sim_id"])
            if existing_sim:
                raise ConflictError(
                    f"sim_id {row['sim_id']} already belongs to {existing_sim['subscription_number']}"
                )

        available_mdns = self.repository.list_mdns("AVAILABLE")
        if len(available_mdns) < len(rows):
            raise ConflictError("There are not enough available MDNs for this batch")

        with self.repository.transaction():
            batch_id, batch_number = self.identities.batch()
            now = utc_now()
            created: list[dict] = []
            legacy_created = 0
            skipped = 0
            for position, source in enumerate(rows):
                subscription_id, subscription_number = self.identities.subscription()
                subscription = {
                    "subscription_id": subscription_id,
                    "subscription_number": subscription_number,
                    "source_subscription_ref": source["source_subscription_ref"],
                    "account_id": account_id,
                    "account_number": account["account_number"],
                    "contract_id": account["contract_id"],
                    "product_offering_id": "OFFER-IOT-CONNECTIVITY",
                    "price_plan_id": source["rate_plan_id"],
                    "technical_profile_id": "NET-DATA-SMS-DOM",
                    "status": "ACTIVE",
                    "start_date": date.today().isoformat(),
                    "end_date": None,
                    "activated_at": now,
                    "source_batch_id": batch_id,
                    "source_batch_number": batch_number,
                    "created_at": now,
                    "updated_at": now,
                }
                self.repository.insert_subscription(subscription)
                sim_resource_id = seeded_uuid(
                    "compatibility-sim", f"{account_id}:{source['sim_id']}"
                )
                if not self.repository.get_sim(sim_resource_id):
                    self.repository.insert_sim(
                        {
                            "sim_resource_id": sim_resource_id,
                            "iccid": f"COMPAT-{source['sim_id']}",
                            "imsi": (
                                "310150"
                                + f"{int(sim_resource_id.replace('-', '')[:12], 16) % 1_000_000_000:09d}"
                            ),
                            "current_owner_type": "ACCOUNT",
                            "current_owner_ref": account_id,
                            "resource_status": "ACTIVE",
                            "updated_at": now,
                        }
                    )
                self.repository.insert_subscription_resource(
                    {
                        "subscription_resource_id": new_uuid(),
                        "subscription_id": subscription_id,
                        "resource_type": "SIM",
                        "resource_id": sim_resource_id,
                        "resource_role": "PRIMARY_SIM",
                        "status": "ACTIVE",
                        "effective_from": now,
                        "effective_to": None,
                    }
                )
                mdn = available_mdns[position]
                mdn["status"] = "ASSIGNED"
                mdn["assigned_account_id"] = account_id
                mdn["updated_at"] = now
                self.repository.update_mdn(mdn)
                self.repository.insert_subscription_resource(
                    {
                        "subscription_resource_id": new_uuid(),
                        "subscription_id": subscription_id,
                        "resource_type": "MDN",
                        "resource_id": mdn["mdn_resource_id"],
                        "resource_role": "PRIMARY_MDN",
                        "status": "ACTIVE",
                        "effective_from": now,
                        "effective_to": None,
                    }
                )
                created.append(subscription)

                if account["billing_mode"] == "DETAILED":
                    legacy_ref = f"LL-{subscription_number}"
                    self.repository.insert_legacy_line(
                        {
                            "legacy_line_id": new_uuid(),
                            "legacy_line_ref": legacy_ref,
                            "legacy_account_ref": account["external_billing_account_number"],
                            "source_subscription_id": subscription_id,
                            "mdn": mdn["mdn"],
                            "line_type": "STANDARD",
                            "status": "ACTIVE",
                            "created_at": now,
                        }
                    )
                    legacy_outcome = "CREATED"
                    legacy_created += 1
                else:
                    legacy_outcome = "SKIPPED_BY_SUMMARIZED_MODE"
                    skipped += 1

                self.repository.insert_activation_event(
                    {
                        "event_id": new_uuid(),
                        "batch_id": batch_id,
                        "batch_number": batch_number,
                        "account_id": account_id,
                        "contract_id": account["contract_id"],
                        "subscription_id": subscription_id,
                        "source_subscription_ref": source["source_subscription_ref"],
                        "iot_outcome": "CREATED",
                        "legacy_outcome": legacy_outcome,
                        "actor": actor,
                        "created_at": now,
                    }
                )

        return {
            "batch_id": batch_id,
            "batch_number": batch_number,
            "account_id": account_id,
            "account_number": account["account_number"],
            "contract_id": account["contract_id"],
            "billing_mode": account["billing_mode"],
            "rows_received": len(rows),
            "iot_created": len(created),
            "legacy_created": legacy_created,
            "legacy_skipped_by_policy": skipped,
            "rate_plan_counts": plan_counts(rows),
            "errors": 0,
        }

    def list_subscriptions(self, account_id: str, system: str) -> list[dict]:
        account = self._account(account_id)
        if system == "iot":
            rows = self.repository.list_subscriptions(account["account_id"])
            for row in rows:
                plan = RATE_PLANS[row["price_plan_id"]]
                row["rate_plan_id"] = row["price_plan_id"]
                row["rate_plan_code"] = plan["rate_plan_code"]
                row["rate_plan_name"] = plan["name"]
            return rows
        if system == "legacy":
            return self.repository.list_legacy_lines(
                account["external_billing_account_number"]
            )
        raise ValidationError("system must be iot or legacy")

    def run_billing(self, *, account_id: str, bill_cycle: str, actor: str) -> dict:
        account = self._account(account_id)
        existing = self.repository.latest_bill_run(account_id, bill_cycle)
        if existing:
            raise ConflictError(
                f"{account['account_number']} already has {existing['bill_run_number']} "
                f"for {bill_cycle}; reset the prepared demo before rerunning that cycle"
            )
        subscriptions = [
            row for row in self.repository.list_subscriptions(account_id)
            if row["status"] == "ACTIVE"
        ]
        if not subscriptions:
            raise ConflictError("No active IoT subscriptions exist for this account")
        with self.repository.transaction():
            bill_run_id, bill_run_number = self.identities.bill_run()
            charges = self._charges(account, subscriptions, bill_cycle, bill_run_id)
            rows = self._billing_rows(account, charges, bill_run_id)
            for charge in charges:
                self.repository.insert_charge(charge)
            for row in rows:
                self.repository.insert_billing_row(row)
            run = self._reconcile(
                account,
                charges,
                rows,
                bill_run_id,
                bill_run_number,
                bill_cycle,
                actor,
            )
            self.repository.insert_bill_run(run)
        return deepcopy(run)

    def get_bill_run(self, bill_run_id: str) -> dict:
        run = self.repository.get_bill_run(bill_run_id)
        if not run:
            raise NotFoundError(f"Bill run {bill_run_id} not found")
        return run

    def list_bill_runs(self, account_id: str | None = None) -> list[dict]:
        if account_id is not None:
            self._account(account_id)
        return self.repository.list_bill_runs(account_id)

    def run_bill_cycle(self, *, bill_cycle: str, actor: str) -> dict:
        runs: list[dict] = []
        skipped: list[dict] = []
        accounts = self.repository.list_accounts()
        for account in accounts:
            active = [
                row
                for row in self.repository.list_subscriptions(account["account_id"])
                if row["status"] == "ACTIVE"
            ]
            if not active:
                skipped.append(
                    {
                        "account_id": account["account_id"],
                        "account_number": account["account_number"],
                        "reason": "No active subscriptions",
                    }
                )
                continue
            try:
                runs.append(
                    self.run_billing(
                        account_id=account["account_id"],
                        bill_cycle=bill_cycle,
                        actor=actor,
                    )
                )
            except ConflictError as exc:
                reason = str(exc)
                if "already has" in reason and "for" in reason:
                    existing = self.repository.latest_bill_run(
                        account["account_id"], bill_cycle
                    )
                    reason = (
                        "Account already has a bill run for this cycle"
                        + (f" ({existing['bill_run_number']})" if existing else "")
                    )
                skipped.append(
                    {
                        "account_id": account["account_id"],
                        "account_number": account["account_number"],
                        "reason": reason,
                    }
                )
        return {
            "bill_cycle": bill_cycle,
            "status": "COMPLETED_WITH_SKIPS" if skipped else "COMPLETED",
            "accounts_evaluated": len(accounts),
            "accounts_billed": len(runs),
            "accounts_skipped": len(skipped),
            "runs": runs,
            "skipped": skipped,
        }

    def get_charges(self, bill_run_id: str) -> list[dict]:
        self.get_bill_run(bill_run_id)
        return self.repository.list_charges(bill_run_id)

    def get_billing_file(self, bill_run_id: str) -> list[dict]:
        self.get_bill_run(bill_run_id)
        return self.repository.list_billing_rows(bill_run_id)

    def get_billing_file_csv(self, bill_run_id: str) -> str:
        run = self.get_bill_run(bill_run_id)
        rows = self.repository.list_billing_rows(bill_run_id)
        if not rows:
            raise NotFoundError(
                f"No billing output rows exist for {run['bill_run_number']}"
            )
        columns = [
            "row_number",
            "bill_cycle",
            "account_number",
            "legacy_account_ref",
            "target_line_ref",
            "posting_scope",
            "mdn",
            "charge_code",
            "rate_plan_code",
            "description",
            "charge_type",
            "quantity",
            "unit_price",
            "amount",
            "currency",
            "gl_code",
            "source_record_count",
        ]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
        return stream.getvalue()

    def get_reconciliation(self, bill_run_id: str) -> dict:
        run = self.get_bill_run(bill_run_id)
        return {
            "bill_run_id": bill_run_id,
            "bill_run_number": run["bill_run_number"],
            "status": run["status"],
            "source_total": run["source_total"],
            "output_total": run["output_total"],
            "variance": run["variance"],
            "acceptance_checks": {
                "amounts_balance": Decimal(run["variance"]) == Decimal("0"),
                "all_sources_represented_once": run["unrepresented_source_records"] == 0,
                "no_duplicate_source_representations": run["duplicate_source_representations"] == 0,
                "all_posting_targets_valid": run["invalid_target_lines"] == 0,
            },
        }

    def legacy_statement(self, account_id: str, bill_cycle: str) -> dict:
        """Illustrative artifact representing output produced by legacy billing.

        The new IoT platform supplies reconciled charges and billing rows. It
        does not own invoice production, accounts receivable, payments, or
        collections.
        """
        account = self._account(account_id)
        contract = self.repository.get_contract(account["contract_id"])
        run = self.repository.latest_bill_run(account_id, bill_cycle)
        if not run:
            raise NotFoundError(
                f"No completed bill run for {account['account_number']} in {bill_cycle}"
            )
        year, month = (int(value) for value in bill_cycle.split("-"))
        period_start = date(year, month, 1)
        period_end = date(year, month, monthrange(year, month)[1])
        statement_date = (
            date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        )
        due_date = statement_date + timedelta(days=20)
        line_items = self.repository.list_billing_rows(run["bill_run_id"])
        service_line_count = len(
            {
                row["mdn"]
                for row in line_items
                if row["posting_scope"] == "SUBSCRIPTION" and row["mdn"]
            }
        )
        return {
            "statement_number": f"INV-{bill_cycle.replace('-', '')}-{account['account_number'].split('-')[-1]}",
            "generated_by": "Legacy Billing (simulated artifact)",
            "artifact_disclaimer": (
                "Illustrative customer statement generated from the reconciled "
                "legacy billing output; invoicing and AR remain in Legacy Billing."
            ),
            "account_id": account_id,
            "account_number": account["account_number"],
            "contract_id": account["contract_id"],
            "contract_number": (
                contract["contract_number"] if contract else account["contract_id"]
            ),
            "account_name": account["account_name"],
            "legacy_account_ref": account["external_billing_account_number"],
            "bill_cycle": bill_cycle,
            "billing_period_start": period_start.isoformat(),
            "billing_period_end": period_end.isoformat(),
            "statement_date": statement_date.isoformat(),
            "due_date": due_date.isoformat(),
            "billing_mode": account["billing_mode"],
            "status": "ISSUED — SIMULATED",
            "legacy_service_line_count": service_line_count,
            "previous_balance": "0.00",
            "payments_received": "0.00",
            "adjustments": "0.00",
            "current_charges": run["output_total"],
            "total": run["output_total"],
            "amount_due": run["output_total"],
            "source_charge_count": run["source_charge_count"],
            "statement_charge_item_count": run["output_row_count"],
            "line_items": line_items,
        }

    def _account(self, account_id: str) -> dict:
        account = self.repository.get_account(account_id)
        if not account:
            account = next(
                (
                    row
                    for row in self.repository.list_accounts()
                    if row["account_number"] == account_id
                ),
                None,
            )
        if not account:
            raise NotFoundError(f"Account {account_id} not found")
        return account

    def _account_view(self, account: dict) -> dict:
        contract = self.repository.get_contract(account["contract_id"])
        customer = self.repository.get_customer(account["customer_id"])
        subscriptions = self.repository.list_subscriptions(account["account_id"])
        legacy_lines = self.repository.list_legacy_lines(
            account["external_billing_account_number"]
        )
        return {
            **account,
            "legacy_account_ref": account["external_billing_account_number"],
            "customer_number": customer["customer_number"] if customer else "MISSING",
            "customer_name": customer["customer_name"] if customer else "MISSING",
            "contract_number": contract["contract_number"] if contract else "MISSING",
            "iot_subscription_count": len(subscriptions),
            "legacy_standard_line_count": sum(
                1 for row in legacy_lines if row["line_type"] == "STANDARD"
            ),
        }

    @staticmethod
    def _audit(account_id: str, event_type: str, actor: str, reason: str, details: dict) -> dict:
        return {
            "audit_id": new_uuid(),
            "account_id": account_id,
            "event_type": event_type,
            "actor": actor,
            "reason": reason,
            "details": deepcopy(details),
            "created_at": utc_now(),
        }

    @staticmethod
    def _charges(
        account: dict, subscriptions: list[dict], bill_cycle: str, bill_run_id: str
    ) -> list[dict]:
        charges = []
        for subscription in subscriptions:
            plan = RATE_PLANS[subscription["price_plan_id"]]
            charges.append(
                {
                    "charge_id": new_uuid(),
                    "bill_run_id": bill_run_id,
                    "account_id": account["account_id"],
                    "contract_id": account["contract_id"],
                    "subscription_id": subscription["subscription_id"],
                    "subscription_number": subscription["subscription_number"],
                    "bill_cycle": bill_cycle,
                    "charge_level": "SUBSCRIPTION",
                    "charge_code": plan["rate_plan_code"],
                    "rate_plan_id": plan["rate_plan_id"],
                    "rate_plan_code": plan["rate_plan_code"],
                    "description": plan["name"],
                    "charge_type": "RECURRING",
                    "quantity": 1,
                    "unit_price": money(plan["monthly_price"]),
                    "amount": money(plan["monthly_price"]),
                    "gl_code": plan["gl_code"],
                    "currency": plan["currency"],
                }
            )
        definitions = (
            SUMMARIZED_ACCOUNT_CHARGES
            if account["billing_mode"] == "SUMMARIZED"
            else DETAILED_ACCOUNT_CHARGES
        )
        for definition in definitions:
            charges.append(
                {
                    "charge_id": new_uuid(),
                    "bill_run_id": bill_run_id,
                    "account_id": account["account_id"],
                    "contract_id": account["contract_id"],
                    "subscription_id": None,
                    "subscription_number": None,
                    "bill_cycle": bill_cycle,
                    "charge_level": "ACCOUNT",
                    "charge_code": definition["charge_code"],
                    "rate_plan_id": None,
                    "rate_plan_code": None,
                    "description": definition["description"],
                    "charge_type": definition["charge_type"],
                    "quantity": 1,
                    "unit_price": money(definition["amount"]),
                    "amount": money(definition["amount"]),
                    "gl_code": definition["gl_code"],
                    "currency": "USD",
                }
            )
        return charges

    def _billing_rows(self, account: dict, charges: list[dict], bill_run_id: str) -> list[dict]:
        rows: list[dict] = []
        legacy_lines = self.repository.list_legacy_lines(
            account["external_billing_account_number"]
        )
        if account["billing_mode"] == "DETAILED":
            line_map = {
                row["source_subscription_id"]: row
                for row in legacy_lines
                if row["line_type"] == "STANDARD" and row["status"] == "ACTIVE"
            }
            if not line_map:
                raise ConflictError("Detailed billing requires legacy subscription lines")
            for charge in charges:
                if charge["charge_level"] == "SUBSCRIPTION":
                    line = line_map.get(charge["subscription_id"])
                    if not line:
                        raise ConflictError(
                            f"Subscription {charge['subscription_number']} has no active Amdocs line"
                        )
                    mdn = self._subscription_mdn(charge["subscription_id"])
                    if line.get("mdn") != mdn:
                        raise ConflictError(
                            f"Subscription {charge['subscription_number']} MDN does not match Amdocs"
                        )
                    posting_scope = "SUBSCRIPTION"
                    target = line["legacy_line_ref"]
                else:
                    posting_scope = "ACCOUNT"
                    mdn = None
                    target = account["external_billing_account_number"]
                rows.append(
                    self._billing_row(
                        bill_run_id, len(rows) + 1, account, charge,
                        posting_scope, mdn, target, [charge]
                    )
                )
            return rows

        groups: dict[tuple, list[dict]] = defaultdict(list)
        account_charges = []
        for charge in charges:
            if charge["charge_level"] == "ACCOUNT":
                account_charges.append(charge)
            else:
                groups[
                    (
                        charge["rate_plan_id"],
                        charge["charge_type"],
                        charge["charge_code"],
                        charge["currency"],
                    )
                ].append(charge)
        for _, source_charges in sorted(groups.items()):
            representative = deepcopy(source_charges[0])
            representative["quantity"] = len(source_charges)
            representative["amount"] = money(sum(Decimal(row["amount"]) for row in source_charges))
            representative["description"] = f"{representative['description']} — summarized"
            rows.append(
                self._billing_row(
                    bill_run_id,
                    len(rows) + 1,
                    account,
                    representative,
                    "ACCOUNT",
                    None,
                    account["external_billing_account_number"],
                    source_charges,
                )
            )
        for charge in account_charges:
            rows.append(
                self._billing_row(
                    bill_run_id,
                    len(rows) + 1,
                    account,
                    charge,
                    "ACCOUNT",
                    None,
                    account["external_billing_account_number"],
                    [charge],
                )
            )
        return rows

    @staticmethod
    def _billing_row(
        bill_run_id: str,
        row_number: int,
        account: dict,
        charge: dict,
        posting_scope: str,
        mdn: str | None,
        target_line_ref: str,
        source_charges: list[dict],
    ) -> dict:
        return {
            "billing_row_id": new_uuid(),
            "bill_run_id": bill_run_id,
            "row_number": row_number,
            "bill_cycle": charge["bill_cycle"],
            "account_id": account["account_id"],
            "account_number": account["account_number"],
            "contract_id": account["contract_id"],
            "legacy_account_ref": account["external_billing_account_number"],
            "target_line_ref": target_line_ref,
            "source_charge_level": charge["charge_level"],
            "posting_scope": posting_scope,
            "mdn": mdn,
            "charge_code": charge["charge_code"],
            "rate_plan_id": charge["rate_plan_id"],
            "rate_plan_code": charge["rate_plan_code"],
            "description": charge["description"],
            "charge_type": charge["charge_type"],
            "quantity": charge["quantity"],
            "unit_price": charge["unit_price"],
            "amount": charge["amount"],
            "gl_code": charge["gl_code"],
            "currency": charge["currency"],
            "source_record_count": len(source_charges),
            "source_charge_ids": [row["charge_id"] for row in source_charges],
        }

    def _reconcile(
        self,
        account: dict,
        charges: list[dict],
        rows: list[dict],
        bill_run_id: str,
        bill_run_number: str,
        bill_cycle: str,
        actor: str,
    ) -> dict:
        source_total = sum(Decimal(row["amount"]) for row in charges)
        output_total = sum(Decimal(row["amount"]) for row in rows)
        represented = [source_id for row in rows for source_id in row["source_charge_ids"]]
        source_ids = {row["charge_id"] for row in charges}
        duplicate_count = sum(
            count - 1 for count in Counter(represented).values() if count > 1
        )
        unrepresented = source_ids - set(represented)
        valid_mdns = {
            row.get("mdn")
            for row in self.repository.list_legacy_lines(
                account["external_billing_account_number"]
            )
            if row["line_type"] == "STANDARD" and row["status"] == "ACTIVE"
        }
        legacy_account = self.repository.get_legacy_account(
            account["external_billing_account_number"]
        )
        invalid_targets = 0
        for row in rows:
            if not legacy_account or legacy_account["status"] != "ACTIVE":
                invalid_targets += 1
            elif row["posting_scope"] not in {"ACCOUNT", "SUBSCRIPTION"}:
                invalid_targets += 1
            elif not row["charge_code"]:
                invalid_targets += 1
            elif row["posting_scope"] == "ACCOUNT" and (
                row["mdn"] is not None
                or row["target_line_ref"] != account["external_billing_account_number"]
            ):
                invalid_targets += 1
            elif row["posting_scope"] == "SUBSCRIPTION" and row["mdn"] not in valid_mdns:
                invalid_targets += 1
            elif Decimal(row["amount"]) != (
                Decimal(str(row["quantity"])) * Decimal(row["unit_price"])
            ):
                invalid_targets += 1
        status = (
            "PASSED"
            if source_total == output_total
            and not unrepresented
            and duplicate_count == 0
            and invalid_targets == 0
            else "FAILED"
        )
        return {
            "bill_run_id": bill_run_id,
            "bill_run_number": bill_run_number,
            "account_id": account["account_id"],
            "account_number": account["account_number"],
            "contract_id": account["contract_id"],
            "account_name": account["account_name"],
            "billing_mode": account["billing_mode"],
            "bill_cycle": bill_cycle,
            "status": status,
            "source_charge_count": len(charges),
            "output_row_count": len(rows),
            "source_total": money(source_total),
            "output_total": money(output_total),
            "variance": money(output_total - source_total),
            "unrepresented_source_records": len(unrepresented),
            "duplicate_source_representations": duplicate_count,
            "invalid_target_lines": invalid_targets,
            "actor": actor,
            "created_at": utc_now(),
        }

    def _subscription_mdn(self, subscription_id: str) -> str:
        resources = self.repository.list_subscription_resources(subscription_id)
        mdn_resources = [
            row for row in resources
            if row["resource_type"] == "MDN" and row["status"] == "ACTIVE"
        ]
        if len(mdn_resources) != 1:
            raise ConflictError(
                f"Subscription {subscription_id} must have exactly one active MDN"
            )
        mdn = self.repository.get_mdn(mdn_resources[0]["resource_id"])
        if not mdn or mdn["status"] != "ASSIGNED":
            raise ConflictError(f"Subscription {subscription_id} MDN is not assigned")
        return mdn["mdn"]
