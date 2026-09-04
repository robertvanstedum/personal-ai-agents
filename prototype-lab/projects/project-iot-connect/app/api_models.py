from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.amdocs_middleware import AmdocsSubscriptionActionResponse
from contracts.flowone import FlowOneActivationResponse, RoamingPackage, ServicePackage


class StrictModel(BaseModel):
    """Shared API policy: trim strings and reject undocumented fields."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class AccountCreateRequest(StrictModel):
    customer_name: str = Field(min_length=3, max_length=80)
    account_name: str = Field(min_length=3, max_length=80)
    external_billing_account_number: str = Field(
        pattern=r"^[A-Z0-9-]{3,40}", examples=["LEG-ACCT-300"]
    )
    external_customer_ref: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="Prototype Lab account setup", min_length=3, max_length=240)


class AccountUpdateRequest(StrictModel):
    customer_name: str = Field(min_length=3, max_length=80)
    account_name: str = Field(min_length=3, max_length=80)
    external_customer_ref: str | None = Field(default=None, max_length=80)
    private_apn_name: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._-]{1,80}$",
    )
    reason: str = Field(min_length=3, max_length=240)


class BillingModeChangeRequest(StrictModel):
    billing_mode: Literal["DETAILED", "SUMMARIZED"]
    reason: str = Field(min_length=3, max_length=240)


class BillRunRequest(StrictModel):
    bill_cycle: str = Field(default="2026-08", pattern=r"^\d{4}-\d{2}$")


class BillCycleRunResponse(StrictModel):
    bill_cycle: str
    status: Literal["COMPLETED", "COMPLETED_WITH_SKIPS"]
    accounts_evaluated: int
    accounts_billed: int
    accounts_skipped: int
    runs: list[dict[str, Any]]
    skipped: list[dict[str, str]]


class NetworkActivationRequest(StrictModel):
    """The core business fields WDH supplies to FlowOne."""

    imsi: str = Field(pattern=r"^\d{14,15}$", examples=["310150123456789"])
    mdn: str = Field(pattern=r"^\+?\d{10,15}$", examples=["+13125550101"])
    service_package: ServicePackage = Field(examples=["DATA_SMS"])
    roaming_package: RoamingPackage = Field(examples=["DOMESTIC"])
    private_apn: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._-]{1,80}$",
        examples=["BOREAL_IOT_PRIVATE"],
    )


class NetworkActivationResponse(StrictModel):
    activation_id: str
    correlation_id: str
    wdh_service_status: Literal["ACTIVE", "ACTIVATION_FAILED"]
    flowone: FlowOneActivationResponse


class LegacySubscriptionActionRequest(StrictModel):
    """The five fields WDH sends to the Amdocs-facing middleware."""

    amdocs_account_number: str = Field(
        pattern=r"^[A-Z0-9-]{3,40}$", examples=["AMD-45001"]
    )
    wdh_account_reference: str = Field(
        pattern=r"^[A-Z0-9-]{3,40}$", examples=["WDH-200"]
    )
    mdn: str = Field(pattern=r"^\+?\d{10,15}$", examples=["+13125550101"])
    imsi: str = Field(pattern=r"^\d{14,15}$", examples=["310150123456789"])
    action: Literal["CREATE", "DEACTIVATE"] = Field(examples=["CREATE"])


class LegacySubscriptionActionResponse(StrictModel):
    compatibility_action_id: str
    wdh_status: Literal["SUBMITTED"]
    middleware: AmdocsSubscriptionActionResponse


class AccountResponse(StrictModel):
    account_id: str
    account_number: str
    account_name: str
    customer_id: str
    customer_number: str
    customer_name: str
    contract_id: str
    contract_number: str
    external_customer_ref: str | None
    external_billing_account_number: str
    send_subscriptions_to_amdocs: bool
    private_apn_name: str | None
    legacy_account_ref: str
    billing_mode: Literal["DETAILED", "SUMMARIZED"]
    status: str
    iot_subscription_count: int
    legacy_standard_line_count: int
    updated_by: str
    updated_at: str


class BillingPolicyResponse(StrictModel):
    """Focused evidence view for the summarized-billing account policy."""

    account_id: str
    account_number: str
    account_name: str
    billing_mode: Literal["DETAILED", "SUMMARIZED"]
    summarized_billing_enabled: bool
    send_subscriptions_to_legacy_billing: bool
    posting_scope: Literal["SUBSCRIPTION", "ACCOUNT"]
    external_billing_account_number: str
    updated_by: str
    updated_at: str


class RatePlanResponse(StrictModel):
    rate_plan_id: str
    product_offering_id: str
    rate_plan_code: str
    name: str
    monthly_price: str
    gl_code: str
    currency: str
    status: str


class UploadResponse(StrictModel):
    batch_id: str
    batch_number: str
    account_id: str
    account_number: str
    contract_id: str
    billing_mode: str
    rows_received: int
    iot_created: int
    legacy_created: int
    legacy_skipped_by_policy: int
    rate_plan_counts: dict[str, int]
    errors: int


class BillRunResponse(StrictModel):
    bill_run_id: str
    bill_run_number: str
    account_id: str
    account_number: str
    contract_id: str
    account_name: str
    billing_mode: str
    bill_cycle: str
    status: str
    source_charge_count: int
    output_row_count: int
    source_total: str
    output_total: str
    variance: str
    unrepresented_source_records: int
    duplicate_source_representations: int
    invalid_target_lines: int
    actor: str
    created_at: str


class ResetResponse(StrictModel):
    status: str
    backend: str
    accounts_seeded: int
    contracts_seeded: int
    golden_lines_seeded: int
    sim_resources_seeded: int = 0
    mdn_resources_seeded: int = 0


class ProductOfferingResponse(StrictModel):
    product_offering_id: str
    offering_code: str
    name: str
    fulfillment_type: str
    status: str


class NetworkProfileResponse(StrictModel):
    technical_profile_id: str
    profile_code: str
    name: str
    service_package: str
    roaming_package: str
    status: str


class SimAssignmentRequest(StrictModel):
    # Inventory allocation is an operator stock movement, not network activation.
    # Interactive activation remains capped at 50 below.
    sim_resource_ids: list[str] = Field(min_length=1, max_length=1000)


class ActivationBatchItemRequest(StrictModel):
    source_order_ref: str = Field(pattern=r"^[A-Za-z0-9._-]{1,80}$")
    sim_resource_id: str
    product_offering_id: str = "OFFER-IOT-CONNECTIVITY"
    price_plan_id: str
    technical_profile_id: str
    private_apn: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._-]{1,80}$",
    )


class ActivationBatchCreateRequest(StrictModel):
    items: list[ActivationBatchItemRequest] = Field(min_length=1, max_length=50)


class AccountResourceResponse(StrictModel):
    sim_resource_id: str
    iccid: str
    imsi: str
    mdn_resource_id: str | None
    mdn: str | None
    subscription_id: str | None
    subscription_number: str | None
    price_plan_id: str | None
    rate_plan_name: str | None
    status: str
    last_change: str


class AccountSummaryResponse(StrictModel):
    account: AccountResponse
    available_sims: int
    active_subscriptions: int
    suspended_subscriptions: int
    pending_subscriptions: int
    retired_subscriptions: int
    total_resources: int
    rate_plan_counts: dict[str, int]
    latest_bill_run: BillRunResponse | None


class ErrorResponse(StrictModel):
    code: str
    message: str
    request_id: str
    details: Any | None = None
