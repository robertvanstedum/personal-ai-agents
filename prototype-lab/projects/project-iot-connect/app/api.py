from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api_models import (
    AccountCreateRequest,
    AccountUpdateRequest,
    AccountResourceResponse,
    AccountResponse,
    AccountSummaryResponse,
    ActivationBatchCreateRequest,
    BillRunRequest,
    BillRunResponse,
    BillCycleRunResponse,
    BillingPolicyResponse,
    BillingModeChangeRequest,
    LegacySubscriptionActionRequest,
    LegacySubscriptionActionResponse,
    NetworkActivationRequest,
    NetworkActivationResponse,
    NetworkProfileResponse,
    ProductOfferingResponse,
    RatePlanResponse,
    ResetResponse,
    SimAssignmentRequest,
    UploadResponse,
)
from app.dependencies import ActorContext, AuthPolicy
from app.domain.csv_input import parse_subscription_csv
from app.domain.errors import AuthorizationError, ValidationError
from app.services.demo import DemoService
from app.services.legacy_compatibility import LegacyCompatibilityService
from app.services.provisioning import ProvisioningService
from app.services.ordering import OrderingService


def build_api_router(
    service: DemoService,
    provisioning: ProvisioningService,
    legacy_compatibility: LegacyCompatibilityService,
    ordering: OrderingService,
    auth: AuthPolicy | None = None,
) -> APIRouter:
    auth = auth or AuthPolicy("local_demo")
    require_admin = auth.require_admin
    require_account_customer = auth.require_account_customer
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", tags=["System"])
    def health() -> dict:
        return {
            "status": "ok",
            "service": "IoT Connect",
            "api_version": "v1",
            "data_backend": service.repository.backend_name,
        }

    @router.get(
        "/catalog/rate-plans",
        response_model=list[RatePlanResponse],
        tags=["Catalog"],
    )
    def list_rate_plans() -> list[dict]:
        return service.rate_plans()

    @router.get(
        "/catalog/product-offerings",
        response_model=list[ProductOfferingResponse],
        tags=["Catalog"],
    )
    def list_product_offerings() -> list[dict]:
        return service.product_offerings()

    @router.get(
        "/catalog/network-profiles",
        response_model=list[NetworkProfileResponse],
        tags=["Catalog"],
    )
    def list_network_profiles() -> list[dict]:
        return service.network_profiles()

    @router.post(
        "/network-activations",
        response_model=NetworkActivationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Provisioning"],
    )
    async def activate_network(payload: NetworkActivationRequest) -> dict:
        return await provisioning.activate(
            imsi=payload.imsi,
            mdn=payload.mdn,
            service_package=payload.service_package,
            roaming_package=payload.roaming_package,
            private_apn=payload.private_apn,
        )

    @router.get(
        "/network-activations/{activation_id}",
        response_model=NetworkActivationResponse,
        tags=["Provisioning"],
    )
    def get_network_activation(activation_id: str) -> dict:
        return provisioning.get(activation_id)

    @router.post(
        "/legacy-subscription-actions",
        response_model=LegacySubscriptionActionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Legacy compatibility"],
    )
    async def submit_legacy_subscription_action(
        payload: LegacySubscriptionActionRequest,
    ) -> dict:
        return await legacy_compatibility.submit(**payload.model_dump())

    @router.get(
        "/legacy-subscription-actions/{compatibility_action_id}",
        response_model=LegacySubscriptionActionResponse,
        tags=["Legacy compatibility"],
    )
    def get_legacy_subscription_action(compatibility_action_id: str) -> dict:
        return legacy_compatibility.get(compatibility_action_id)

    @router.get(
        "/accounts", response_model=list[AccountResponse], tags=["Accounts"]
    )
    def list_accounts() -> list[dict]:
        return service.list_accounts()

    @router.get(
        "/account-summaries",
        response_model=list[AccountSummaryResponse],
        tags=["Accounts"],
    )
    def list_account_summaries() -> list[dict]:
        return service.list_account_summaries()

    @router.get(
        "/accounts/{account_id}", response_model=AccountResponse, tags=["Accounts"]
    )
    def get_account(account_id: str) -> dict:
        return service.get_account(account_id)

    @router.get(
        "/demo-evidence/accounts/{account_number}/billing-policy",
        response_model=BillingPolicyResponse,
        tags=["Demo evidence"],
        summary="Get customer summarized-billing policy",
        description=(
            "Shows whether summarized billing is enabled, whether subscription "
            "records are still sent to Legacy Billing, and the resulting posting scope."
        ),
    )
    def get_billing_policy(account_number: str) -> dict:
        account = service.get_account_by_number(account_number)
        return service.billing_policy(account["account_id"])

    @router.get(
        "/accounts/{account_id}/summary",
        response_model=AccountSummaryResponse,
        tags=["Accounts"],
    )
    def get_account_summary(account_id: str) -> dict:
        return service.account_summary(account_id)

    @router.get(
        "/accounts/{account_id}/resources",
        response_model=list[AccountResourceResponse],
        tags=["Resource inventory"],
    )
    def list_account_resources(account_id: str) -> list[dict]:
        return service.account_resources(account_id)

    @router.post(
        "/admin/accounts",
        response_model=AccountResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Admin setup"],
    )
    def create_account(
        payload: AccountCreateRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return service.create_account(
            customer_name=payload.customer_name,
            account_name=payload.account_name,
            external_billing_account_number=payload.external_billing_account_number,
            external_customer_ref=payload.external_customer_ref,
            actor=actor.role,
            reason=payload.reason,
        )

    @router.get("/admin/legacy-accounts/available", tags=["Admin setup"])
    def list_available_legacy_accounts(
        actor: ActorContext = Depends(require_admin),
    ) -> list[dict]:
        del actor
        return service.available_legacy_accounts()

    @router.patch(
        "/admin/accounts/{account_id}",
        response_model=AccountResponse,
        tags=["Admin setup"],
    )
    def update_account(
        account_id: str,
        payload: AccountUpdateRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return service.update_account_configuration(
            account_id=account_id,
            customer_name=payload.customer_name,
            account_name=payload.account_name,
            external_customer_ref=payload.external_customer_ref,
            private_apn_name=payload.private_apn_name,
            actor=actor.role,
            reason=payload.reason,
        )

    @router.get("/inventory/sims/available", tags=["Resource inventory"])
    def list_available_sims() -> list[dict]:
        return ordering.list_available_sims()

    @router.get(
        "/accounts/{account_id}/inventory/sims", tags=["Resource inventory"]
    )
    def list_account_sims(account_id: str) -> list[dict]:
        return ordering.list_account_sims(account_id)

    @router.post(
        "/admin/accounts/{account_id}/sim-assignments",
        tags=["Resource inventory"],
    )
    def assign_sims(
        account_id: str,
        payload: SimAssignmentRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> list[dict]:
        return ordering.assign_sims(
            account_id=account_id,
            sim_resource_ids=payload.sim_resource_ids,
            actor=actor.role,
        )

    @router.post(
        "/admin/accounts/{account_id}/activation-batches",
        status_code=status.HTTP_201_CREATED,
        tags=["Ordering and activation"],
    )
    def create_activation_batch(
        account_id: str,
        payload: ActivationBatchCreateRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return ordering.create_activation_batch(
            account_id=account_id,
            items=[row.model_dump() for row in payload.items],
            actor=actor.role,
        )

    @router.post(
        "/accounts/{account_id}/activation-batches",
        status_code=status.HTTP_201_CREATED,
        tags=["Ordering and activation"],
    )
    def create_customer_activation_batch(
        account_id: str,
        payload: ActivationBatchCreateRequest,
        actor: ActorContext = Depends(require_account_customer),
    ) -> dict:
        return ordering.create_activation_batch(
            account_id=account_id,
            items=[row.model_dump() for row in payload.items],
            actor=actor.role,
        )

    @router.post(
        "/admin/activation-batches/{batch_id}:submit",
        tags=["Ordering and activation"],
    )
    async def submit_activation_batch(
        batch_id: str,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        del actor
        return await ordering.submit_activation_batch(batch_id)

    @router.post(
        "/accounts/{account_id}/activation-batches/{batch_id}:submit",
        tags=["Ordering and activation"],
    )
    async def submit_customer_activation_batch(
        account_id: str,
        batch_id: str,
        actor: ActorContext = Depends(require_account_customer),
    ) -> dict:
        batch = ordering.get_activation_batch(batch_id)
        if batch["account_id"] != account_id or actor.account_id != account_id:
            raise AuthorizationError(
                "The activation batch does not belong to the customer account session"
            )
        return await ordering.submit_activation_batch(batch_id)

    @router.post(
        "/admin/activation-batches/{batch_id}/items/{batch_item_id}:retry",
        tags=["Ordering and activation"],
    )
    async def retry_failed_activation_item(
        batch_id: str,
        batch_item_id: str,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return await ordering.retry_failed_activation_item(
            batch_id=batch_id,
            batch_item_id=batch_item_id,
            actor=actor.role,
        )

    @router.get(
        "/activation-batches/{batch_id}", tags=["Ordering and activation"]
    )
    def get_activation_batch(batch_id: str) -> dict:
        return ordering.get_activation_batch(batch_id)

    @router.get(
        "/demo-evidence/accounts/{account_number}/latest-activation",
        tags=["Demo evidence"],
        summary="Get latest customer activation",
        description=(
            "Returns the newest activation batch for the account, including every SIM, "
            "assigned MDN, FlowOne result, Legacy Billing policy result, and network-element outcome."
        ),
    )
    def get_latest_customer_activation(account_number: str) -> dict:
        account = service.get_account_by_number(account_number)
        return ordering.latest_activation_batch(account["account_id"])

    @router.get(
        "/admin/activation-batches", tags=["Ordering and activation"]
    )
    def list_activation_batches(
        account_id: str | None = Query(default=None),
        actor: ActorContext = Depends(require_admin),
    ) -> list[dict]:
        del actor
        return ordering.list_activation_batches(account_id)

    @router.get(
        "/accounts/{account_id}/activation-batches",
        tags=["Ordering and activation"],
    )
    def list_customer_activation_batches(
        account_id: str,
        actor: ActorContext = Depends(require_account_customer),
    ) -> list[dict]:
        if actor.account_id != account_id:
            raise AuthorizationError(
                "Activation-batch history is limited to the customer account session"
            )
        return ordering.list_activation_batches(account_id)

    @router.post(
        "/admin/accounts/{account_id}/billing-mode",
        response_model=AccountResponse,
        tags=["Admin setup"],
    )
    def set_billing_mode(
        account_id: str,
        payload: BillingModeChangeRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return service.set_billing_mode(
            account_id=account_id,
            billing_mode=payload.billing_mode,
            actor=actor.role,
            reason=payload.reason,
        )

    @router.post(
        "/admin/accounts/{account_id}/subscriptions:upload",
        response_model=UploadResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Admin setup"],
    )
    async def upload_subscriptions(
        account_id: str,
        request: Request,
        actor: ActorContext = Depends(require_admin),
        content_type: str = Header(alias="Content-Type"),
    ) -> dict:
        if "text/csv" not in content_type and "application/csv" not in content_type:
            raise ValidationError("Send the subscription file as raw text/csv")
        try:
            raw_text = (await request.body()).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV must be UTF-8") from exc
        return service.upload_subscriptions(
            account_id=account_id,
            rows=parse_subscription_csv(raw_text),
            actor=actor.role,
        )

    @router.get("/accounts/{account_id}/subscriptions", tags=["Subscriptions"])
    def list_subscriptions(
        account_id: str,
        system: str = Query(default="iot", pattern="^(iot|legacy)$"),
    ) -> list[dict]:
        return service.list_subscriptions(account_id, system)

    @router.post(
        "/admin/accounts/{account_id}/bill-runs",
        response_model=BillRunResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Billing"],
    )
    def run_billing(
        account_id: str,
        payload: BillRunRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return service.run_billing(
            account_id=account_id,
            bill_cycle=payload.bill_cycle,
            actor=actor.role,
        )

    @router.get(
        "/bill-runs/{bill_run_id}", response_model=BillRunResponse, tags=["Billing"]
    )
    def get_bill_run(bill_run_id: str) -> dict:
        return service.get_bill_run(bill_run_id)

    @router.get(
        "/bill-runs", response_model=list[BillRunResponse], tags=["Billing"]
    )
    def list_bill_runs(account_id: str | None = None) -> list[dict]:
        return service.list_bill_runs(account_id)

    @router.post(
        "/admin/bill-cycles",
        response_model=BillCycleRunResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Billing"],
    )
    def run_bill_cycle(
        payload: BillRunRequest,
        actor: ActorContext = Depends(require_admin),
    ) -> dict:
        return service.run_bill_cycle(
            bill_cycle=payload.bill_cycle,
            actor=actor.role,
        )

    @router.get("/bill-runs/{bill_run_id}/charges", tags=["Evidence"])
    def get_charges(bill_run_id: str) -> list[dict]:
        return service.get_charges(bill_run_id)

    @router.get("/bill-runs/{bill_run_id}/file", tags=["Evidence"])
    def get_billing_file(bill_run_id: str) -> list[dict]:
        return service.get_billing_file(bill_run_id)

    @router.get("/bill-runs/{bill_run_id}/file.csv", tags=["Evidence"])
    def download_billing_file(bill_run_id: str) -> Response:
        run = service.get_bill_run(bill_run_id)
        filename = (
            f"iot-connect-{run['account_number']}-{run['bill_cycle']}-"
            f"{run['bill_run_number']}.csv"
        )
        return Response(
            content=service.get_billing_file_csv(bill_run_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/bill-runs/{bill_run_id}/reconciliation", tags=["Evidence"])
    def get_reconciliation(bill_run_id: str) -> dict:
        return service.get_reconciliation(bill_run_id)

    @router.get(
        "/artifacts/accounts/{account_id}/legacy-statement/{bill_cycle}",
        tags=["Artifacts"],
    )
    def get_legacy_statement(account_id: str, bill_cycle: str) -> dict:
        return service.legacy_statement(account_id, bill_cycle)

    @router.post(
        "/admin/demo:reset",
        response_model=ResetResponse,
        tags=["Admin setup"],
    )
    def reset_demo(actor: ActorContext = Depends(require_admin)) -> dict:
        return service.reset()

    return router
