from __future__ import annotations

from datetime import date, datetime, timezone

from app.domain.catalog import NETWORK_PROFILES, PRODUCT_OFFERINGS, RATE_PLANS
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.identity import IdentityFactory, new_uuid
from app.repositories.protocols import Repository
from app.services.legacy_compatibility import LegacyCompatibilityService
from app.services.provisioning import ProvisioningService


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderingService:
    """Canonical WDH connectivity order and activation workflow."""

    def __init__(
        self,
        repository: Repository,
        provisioning: ProvisioningService,
        legacy_compatibility: LegacyCompatibilityService,
    ) -> None:
        self.repository = repository
        self.provisioning = provisioning
        self.legacy_compatibility = legacy_compatibility
        self.identities = IdentityFactory(repository)

    def assign_sims(
        self, *, account_id: str, sim_resource_ids: list[str], actor: str
    ) -> list[dict]:
        self._account(account_id)
        sims = []
        for sim_resource_id in sim_resource_ids:
            sim = self.repository.get_sim(sim_resource_id)
            if not sim:
                raise NotFoundError(f"SIM resource {sim_resource_id} not found")
            if sim["current_owner_type"] == "ACCOUNT":
                if sim["current_owner_ref"] != account_id:
                    raise ConflictError(
                        f"SIM {sim['iccid']} is assigned to another account"
                    )
            elif sim["resource_status"] != "AVAILABLE":
                raise ConflictError(f"SIM {sim['iccid']} is not available")
            sims.append(sim)

        now = utc_now()
        with self.repository.transaction():
            for sim in sims:
                sim["current_owner_type"] = "ACCOUNT"
                sim["current_owner_ref"] = account_id
                sim["resource_status"] = "ASSIGNED"
                sim["updated_at"] = now
                self.repository.update_sim(sim)
            self.repository.insert_audit_event(
                {
                    "audit_id": new_uuid(),
                    "account_id": account_id,
                    "event_type": "SIM_RESOURCES_ASSIGNED",
                    "actor": actor,
                    "reason": "Assign operator inventory to enterprise account",
                    "details": {
                        "sim_resource_ids": sim_resource_ids,
                        "count": len(sim_resource_ids),
                    },
                    "created_at": now,
                }
            )
        return self.repository.list_sims(account_id)

    def create_activation_batch(
        self, *, account_id: str, items: list[dict], actor: str
    ) -> dict:
        account = self._account(account_id)
        if not items:
            raise ValidationError("An activation batch must contain at least one item")

        source_refs: set[str] = set()
        sim_ids: set[str] = set()
        validated: list[dict] = []
        available_mdns = self.repository.list_mdns("AVAILABLE")
        if len(available_mdns) < len(items):
            raise ConflictError("There are not enough available MDNs for this batch")

        for position, item in enumerate(items, start=1):
            source_ref = item["source_order_ref"]
            sim_resource_id = item["sim_resource_id"]
            if source_ref in source_refs:
                raise ConflictError(f"Duplicate source_order_ref {source_ref}")
            if sim_resource_id in sim_ids:
                raise ConflictError(f"Duplicate SIM resource {sim_resource_id}")
            if self.repository.find_subscription_by_source(account_id, source_ref):
                raise ConflictError(
                    f"source_order_ref {source_ref} already exists for this account"
                )

            offering = PRODUCT_OFFERINGS.get(item["product_offering_id"])
            if not offering or offering["status"] != "ACTIVE":
                raise ValidationError("Unknown or inactive product offering")
            if offering["fulfillment_type"] != "FLOWONE_NETWORK_ACTIVATION":
                raise ValidationError(
                    "This activation workflow only accepts connectivity offerings"
                )
            plan = RATE_PLANS.get(item["price_plan_id"])
            if not plan or plan["product_offering_id"] != offering["product_offering_id"]:
                raise ValidationError("Price plan does not belong to the product offering")
            profile = NETWORK_PROFILES.get(item["technical_profile_id"])
            if not profile or profile["status"] != "ACTIVE":
                raise ValidationError("Unknown or inactive network profile")

            requested_private_apn = item.get("private_apn")
            configured_private_apn = account.get("private_apn_name")
            if requested_private_apn and not configured_private_apn:
                raise ValidationError(
                    "Private APN cannot be requested because this account has no private APN configured"
                )
            if requested_private_apn and requested_private_apn != configured_private_apn:
                raise ValidationError(
                    "Requested private APN does not match the account configuration"
                )

            sim = self.repository.get_sim(sim_resource_id)
            if not sim:
                raise NotFoundError(f"SIM resource {sim_resource_id} not found")
            if (
                sim["current_owner_type"] != "ACCOUNT"
                or sim["current_owner_ref"] != account_id
                or sim["resource_status"] != "ASSIGNED"
            ):
                raise ConflictError(
                    f"SIM {sim['iccid']} must be assigned to this account before ordering"
                )
            if self.repository.find_subscription_by_sim(sim_resource_id):
                raise ConflictError(f"SIM {sim['iccid']} is already bound to a subscription")

            validated.append(
                {
                    **item,
                    "position": position,
                    "sim": sim,
                    "mdn": available_mdns[position - 1],
                    "profile": profile,
                }
            )
            source_refs.add(source_ref)
            sim_ids.add(sim_resource_id)

        now = utc_now()
        batch_id, batch_number = self.identities.batch()
        with self.repository.transaction():
            batch = {
                "batch_id": batch_id,
                "batch_number": batch_number,
                "account_id": account_id,
                "status": "DRAFT",
                "item_count": len(validated),
                "success_count": 0,
                "failure_count": 0,
                "actor": actor,
                "created_at": now,
                "submitted_at": None,
                "completed_at": None,
            }
            self.repository.insert_activation_batch(batch)

            for source in validated:
                subscription_id, subscription_number = self.identities.subscription()
                subscription = {
                    "subscription_id": subscription_id,
                    "subscription_number": subscription_number,
                    "source_subscription_ref": source["source_order_ref"],
                    "account_id": account_id,
                    "account_number": account["account_number"],
                    "contract_id": account["contract_id"],
                    "product_offering_id": source["product_offering_id"],
                    "price_plan_id": source["price_plan_id"],
                    "technical_profile_id": source["technical_profile_id"],
                    "status": "PENDING_ACTIVATION",
                    "start_date": date.today().isoformat(),
                    "end_date": None,
                    "activated_at": None,
                    "source_batch_id": batch_id,
                    "source_batch_number": batch_number,
                    "created_at": now,
                    "updated_at": now,
                }
                self.repository.insert_subscription(subscription)

                mdn = source["mdn"]
                mdn["status"] = "RESERVED"
                mdn["assigned_account_id"] = account_id
                mdn["updated_at"] = now
                self.repository.update_mdn(mdn)

                for resource_type, resource_id, role in (
                    ("SIM", source["sim"]["sim_resource_id"], "PRIMARY_SIM"),
                    ("MDN", mdn["mdn_resource_id"], "PRIMARY_MDN"),
                ):
                    self.repository.insert_subscription_resource(
                        {
                            "subscription_resource_id": new_uuid(),
                            "subscription_id": subscription_id,
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                            "resource_role": role,
                            "status": "RESERVED",
                            "effective_from": now,
                            "effective_to": None,
                        }
                    )

                self.repository.insert_activation_batch_item(
                    {
                        "batch_item_id": new_uuid(),
                        "batch_id": batch_id,
                        "item_number": source["position"],
                        "source_order_ref": source["source_order_ref"],
                        "subscription_id": subscription_id,
                        "sim_resource_id": source["sim"]["sim_resource_id"],
                        "mdn_resource_id": mdn["mdn_resource_id"],
                        "private_apn": source.get("private_apn"),
                        "network_status": "PENDING",
                        "flowone_activation_id": None,
                        "legacy_status": "NOT_ELIGIBLE_NOT_SUBMITTED",
                        "legacy_action_id": None,
                        "overall_status": "PENDING",
                        "message": "Resources reserved; ready for synchronous activation",
                        "created_at": now,
                        "completed_at": None,
                    }
                )
        return self.get_activation_batch(batch_id)

    async def submit_activation_batch(self, batch_id: str) -> dict:
        batch = self.repository.get_activation_batch(batch_id)
        if not batch:
            raise NotFoundError(f"Activation batch {batch_id} not found")
        if batch["status"] != "DRAFT":
            raise ConflictError("Only a DRAFT activation batch can be submitted")
        account = self._account(batch["account_id"])

        batch["status"] = "IN_PROGRESS"
        batch["submitted_at"] = utc_now()
        self.repository.update_activation_batch(batch)

        success_count = 0
        failure_count = 0
        for item in self.repository.list_activation_batch_items(batch_id):
            subscription = self._subscription(item["subscription_id"])
            sim = self._sim(item["sim_resource_id"])
            mdn = self._mdn(item["mdn_resource_id"])
            profile = NETWORK_PROFILES[subscription["technical_profile_id"]]
            result = await self.provisioning.activate(
                imsi=sim["imsi"],
                mdn=mdn["mdn"],
                service_package=profile["service_package"],
                roaming_package=profile["roaming_package"],
                private_apn=item.get("private_apn"),
            )
            now = utc_now()
            item["flowone_activation_id"] = result["activation_id"]

            with self.repository.transaction():
                for position, element in enumerate(
                    result["flowone"]["element_results"], start=1
                ):
                    self.repository.insert_flowone_element_result(
                        {
                            "element_result_id": new_uuid(),
                            "batch_item_id": item["batch_item_id"],
                            "sequence_number": position,
                            **element,
                            "recorded_at": now,
                        }
                    )

                resources = self.repository.list_subscription_resources(
                    subscription["subscription_id"]
                )
                if result["wdh_service_status"] == "ACTIVATION_FAILED":
                    subscription["status"] = "ACTIVATION_FAILED"
                    subscription["updated_at"] = now
                    self.repository.update_subscription(subscription)
                    mdn["status"] = "AVAILABLE"
                    mdn["assigned_account_id"] = None
                    mdn["updated_at"] = now
                    self.repository.update_mdn(mdn)
                    for resource in resources:
                        resource["status"] = (
                            "ASSIGNED" if resource["resource_type"] == "SIM" else "RELEASED"
                        )
                        self.repository.update_subscription_resource(resource)
                    item["network_status"] = "FAILED_ROLLED_BACK"
                    item["legacy_status"] = "NOT_ELIGIBLE_NETWORK_FAILURE"
                    item["overall_status"] = "FAILED"
                    item["message"] = result["flowone"]["message"]
                    item["completed_at"] = now
                    self.repository.update_activation_batch_item(item)
                    failure_count += 1
                    continue

                subscription["status"] = "ACTIVE"
                subscription["activated_at"] = now
                subscription["updated_at"] = now
                self.repository.update_subscription(subscription)
                sim["resource_status"] = "ACTIVE"
                sim["updated_at"] = now
                self.repository.update_sim(sim)
                mdn["status"] = "ASSIGNED"
                mdn["updated_at"] = now
                self.repository.update_mdn(mdn)
                for resource in resources:
                    resource["status"] = "ACTIVE"
                    self.repository.update_subscription_resource(resource)

            item["network_status"] = "ACTIVE"
            if account["send_subscriptions_to_amdocs"]:
                try:
                    legacy = await self.legacy_compatibility.submit(
                        amdocs_account_number=account[
                            "external_billing_account_number"
                        ],
                        wdh_account_reference=account["account_number"],
                        mdn=mdn["mdn"],
                        imsi=sim["imsi"],
                        action="CREATE",
                    )
                    item["legacy_status"] = "SUBMITTED"
                    item["legacy_action_id"] = legacy["compatibility_action_id"]
                    legacy_line_ref = f"LL-{subscription['subscription_number']}"
                    with self.repository.transaction():
                        self.repository.insert_legacy_line(
                            {
                                "legacy_line_id": new_uuid(),
                                "legacy_line_ref": legacy_line_ref,
                                "legacy_account_ref": account[
                                    "external_billing_account_number"
                                ],
                                "source_subscription_id": subscription[
                                    "subscription_id"
                                ],
                                "mdn": mdn["mdn"],
                                "line_type": "STANDARD",
                                "status": "ACTIVE",
                                "created_at": utc_now(),
                            }
                        )
                except Exception as exc:
                    item["legacy_status"] = "FAILED_OPERATIONS_FOLLOWUP"
                    item["message"] = (
                        "Network active; Amdocs compatibility requires operations "
                        f"follow-up: {exc}"
                    )
            else:
                item["legacy_status"] = "SKIPPED_BY_ACCOUNT_POLICY"

            item["overall_status"] = "ACTIVE"
            if not item["message"].startswith("Network active;"):
                item["message"] = "Network active; compatibility policy applied"
            item["completed_at"] = utc_now()
            self.repository.update_activation_batch_item(item)
            success_count += 1

        batch["success_count"] = success_count
        batch["failure_count"] = failure_count
        batch["status"] = (
            "COMPLETED" if failure_count == 0 else "COMPLETED_WITH_ERRORS"
        )
        batch["completed_at"] = utc_now()
        self.repository.update_activation_batch(batch)
        return self.get_activation_batch(batch_id)

    async def retry_failed_activation_item(
        self, *, batch_id: str, batch_item_id: str, actor: str
    ) -> dict:
        original_batch = self.repository.get_activation_batch(batch_id)
        if not original_batch:
            raise NotFoundError(f"Activation batch {batch_id} not found")
        original_item = next(
            (
                row
                for row in self.repository.list_activation_batch_items(batch_id)
                if row["batch_item_id"] == batch_item_id
            ),
            None,
        )
        if not original_item:
            raise NotFoundError(f"Activation batch item {batch_item_id} not found")
        if original_item["overall_status"] != "FAILED":
            raise ConflictError("Only a failed activation item can be retried")

        subscription = self._subscription(original_item["subscription_id"])
        if subscription["status"] != "ACTIVATION_FAILED":
            raise ConflictError("This failed activation has already been retried")
        sim = self._sim(original_item["sim_resource_id"])
        mdn = self._mdn(original_item["mdn_resource_id"])
        if sim["resource_status"] != "ASSIGNED":
            raise ConflictError("The SIM is not ready for another activation attempt")
        if mdn["status"] != "AVAILABLE":
            raise ConflictError("The released MDN is no longer available for retry")

        now = utc_now()
        retry_batch_id, retry_batch_number = self.identities.batch()
        retry_item_id = new_uuid()
        with self.repository.transaction():
            self.repository.insert_activation_batch(
                {
                    "batch_id": retry_batch_id,
                    "batch_number": retry_batch_number,
                    "account_id": original_batch["account_id"],
                    "status": "DRAFT",
                    "item_count": 1,
                    "success_count": 0,
                    "failure_count": 0,
                    "actor": actor,
                    "created_at": now,
                    "submitted_at": None,
                    "completed_at": None,
                }
            )
            subscription["status"] = "PENDING_ACTIVATION"
            subscription["updated_at"] = now
            self.repository.update_subscription(subscription)
            mdn["status"] = "RESERVED"
            mdn["assigned_account_id"] = original_batch["account_id"]
            mdn["updated_at"] = now
            self.repository.update_mdn(mdn)
            for resource in self.repository.list_subscription_resources(
                subscription["subscription_id"]
            ):
                if resource["resource_id"] in {
                    original_item["sim_resource_id"],
                    original_item["mdn_resource_id"],
                }:
                    resource["status"] = "RESERVED"
                    resource["effective_to"] = None
                    self.repository.update_subscription_resource(resource)
            self.repository.insert_activation_batch_item(
                {
                    "batch_item_id": retry_item_id,
                    "batch_id": retry_batch_id,
                    "item_number": 1,
                    "source_order_ref": original_item["source_order_ref"],
                    "subscription_id": subscription["subscription_id"],
                    "sim_resource_id": original_item["sim_resource_id"],
                    "mdn_resource_id": original_item["mdn_resource_id"],
                    "private_apn": original_item.get("private_apn"),
                    "network_status": "PENDING",
                    "flowone_activation_id": None,
                    "legacy_status": "NOT_ELIGIBLE_NOT_SUBMITTED",
                    "legacy_action_id": None,
                    "overall_status": "PENDING",
                    "message": (
                        f"One-item retry of {original_batch['batch_number']} item "
                        f"{original_item['item_number']}"
                    ),
                    "created_at": now,
                    "completed_at": None,
                }
            )
            self.repository.insert_audit_event(
                {
                    "audit_id": new_uuid(),
                    "account_id": original_batch["account_id"],
                    "event_type": "FAILED_ACTIVATION_ITEM_RETRIED",
                    "actor": actor,
                    "reason": "Retry one failed SIM without resubmitting successful batch items",
                    "details": {
                        "original_batch_id": batch_id,
                        "original_batch_item_id": batch_item_id,
                        "retry_batch_id": retry_batch_id,
                    },
                    "created_at": now,
                }
            )
        return await self.submit_activation_batch(retry_batch_id)

    def get_activation_batch(self, batch_id: str) -> dict:
        batch = self.repository.get_activation_batch(batch_id)
        if not batch:
            raise NotFoundError(f"Activation batch {batch_id} not found")
        items = self.repository.list_activation_batch_items(batch_id)
        for item in items:
            item["sim"] = self._sim(item["sim_resource_id"])
            item["mdn"] = self._mdn(item["mdn_resource_id"])
            item["flowone_element_results"] = (
                self.repository.list_flowone_element_results(item["batch_item_id"])
            )
        return {**batch, "items": items}

    def list_activation_batches(self, account_id: str | None = None) -> list[dict]:
        if account_id is not None:
            account_id = self._account(account_id)["account_id"]
        return self.repository.list_activation_batches(account_id)

    def latest_activation_batch(self, account_id: str) -> dict:
        """Return the newest customer batch with its per-SIM integration evidence."""
        batches = self.list_activation_batches(account_id)
        if not batches:
            account = self._account(account_id)
            raise NotFoundError(
                f"Account {account['account_number']} has no activation batches"
            )
        return self.get_activation_batch(batches[0]["batch_id"])

    def list_account_sims(self, account_id: str) -> list[dict]:
        account = self._account(account_id)
        return self.repository.list_sims(account["account_id"])

    def list_available_sims(self) -> list[dict]:
        return [
            row for row in self.repository.list_sims()
            if row["current_owner_type"] == "OPERATOR"
            and row["resource_status"] == "AVAILABLE"
        ]

    def _account(self, account_id: str) -> dict:
        row = self.repository.get_account(account_id)
        if not row:
            row = next(
                (
                    account
                    for account in self.repository.list_accounts()
                    if account["account_number"] == account_id
                ),
                None,
            )
        if not row:
            raise NotFoundError(f"Account {account_id} not found")
        return row

    def _subscription(self, subscription_id: str) -> dict:
        for account in self.repository.list_accounts():
            for row in self.repository.list_subscriptions(account["account_id"]):
                if row["subscription_id"] == subscription_id:
                    return row
        raise NotFoundError(f"Subscription {subscription_id} not found")

    def _sim(self, sim_resource_id: str) -> dict:
        row = self.repository.get_sim(sim_resource_id)
        if not row:
            raise NotFoundError(f"SIM resource {sim_resource_id} not found")
        return row

    def _mdn(self, mdn_resource_id: str) -> dict:
        row = self.repository.get_mdn(mdn_resource_id)
        if not row:
            raise NotFoundError(f"MDN resource {mdn_resource_id} not found")
        return row
