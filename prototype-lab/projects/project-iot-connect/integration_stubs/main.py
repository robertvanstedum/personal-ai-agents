from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError as PydanticValidationError

from contracts.amdocs_middleware import (
    AmdocsSubscriptionActionRequest,
    AmdocsSubscriptionActionResponse,
)
from contracts.flowone import (
    ElementResult,
    FailNextRequest,
    FailNextResponse,
    FlowOneActivationRequest,
    FlowOneActivationResponse,
    NetworkElement,
)
from contracts.flowone_soap import (
    build_activation_response_envelope,
    build_soap_fault,
    parse_activation_envelope,
)


ELEMENT_ORDER: tuple[NetworkElement, ...] = (
    "HSS",
    "POLICY",
    "SMSC",
    "AAA",
)

ELEMENT_OPERATIONS = {
    "HSS": "CREATE_SUBSCRIBER_AND_APN_PROFILE",
    "POLICY": "ASSOCIATE_POLICY_PROFILE",
    "SMSC": "PROVISION_MSISDN",
    "AAA": "AUTHORIZE_PRIVATE_APN",
}

ELEMENT_FAILURE_CODES = {
    "HSS": "FLOW-401",
    "POLICY": "FLOW-402",
    "SMSC": "FLOW-403",
    "AAA": "FLOW-404",
}

WSDL_PATH = Path(__file__).resolve().parent / "wsdl" / "FlowOneProvisioningService.wsdl"
SOAP_MEDIA_TYPE = "text/xml; charset=utf-8"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FlowOneScenarioState:
    """One-shot failure control kept outside the four-field business request."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._fail_next: NetworkElement | None = None

    def arm(self, element: NetworkElement) -> None:
        with self._lock:
            self._fail_next = element

    def consume(self) -> NetworkElement | None:
        with self._lock:
            element = self._fail_next
            self._fail_next = None
            return element

    def reset(self) -> None:
        with self._lock:
            self._fail_next = None


def applied_profile(
    element: NetworkElement, request: FlowOneActivationRequest
) -> str | None:
    service = request.service_package
    roaming = request.roaming_package
    profiles = {
        "HSS": f"HSS_{service}_{roaming}",
        "POLICY": "SPR_UDR_IOT_STANDARD_DATA" if "DATA" in service else "SPR_UDR_NO_DATA",
        "SMSC": "SMSC_ENABLED" if "SMS" in service else "SMSC_DISABLED",
        "AAA": f"ENT_APN_{request.private_apn}" if request.private_apn else None,
    }
    return profiles[element]


def orchestrate_activation(
    payload: FlowOneActivationRequest,
    failed_element: NetworkElement | None,
) -> FlowOneActivationResponse:
    """Expand the small SOAP request and return one atomic black-box outcome."""

    started_at = utc_now()
    raw_results: list[dict] = []
    failure_seen = False

    for element in ELEMENT_ORDER:
        if failure_seen:
            raw_results.append(
                {
                    "element": element,
                    "operation": ELEMENT_OPERATIONS[element],
                    "provisioning_status": "NOT_ATTEMPTED",
                    "element_code": "FLOW-499",
                    "message": "Not attempted after an earlier element failure",
                    "rollback_status": "NOT_APPLICABLE",
                    "applied_profile": None,
                }
            )
            continue

        if element == "AAA" and not payload.private_apn:
            raw_results.append(
                {
                    "element": element,
                    "operation": ELEMENT_OPERATIONS[element],
                    "provisioning_status": "SKIPPED_NOT_APPLICABLE",
                    "element_code": "AAA-000",
                    "message": "Not applicable: account uses the public/default APN",
                    "rollback_status": "NOT_REQUIRED",
                    "applied_profile": None,
                }
            )
            continue

        if element == failed_element:
            raw_results.append(
                {
                    "element": element,
                    "operation": ELEMENT_OPERATIONS[element],
                    "provisioning_status": "FAILURE",
                    "element_code": ELEMENT_FAILURE_CODES[element],
                    "message": f"{element} rejected the provisioning operation",
                    "rollback_status": "NOT_APPLICABLE",
                    "applied_profile": applied_profile(element, payload),
                }
            )
            failure_seen = True
            continue

        raw_results.append(
            {
                "element": element,
                "operation": ELEMENT_OPERATIONS[element],
                "provisioning_status": "SUCCESS",
                "element_code": f"{element}-200",
                "message": f"{element} provisioning completed",
                "rollback_status": "NOT_REQUIRED",
                "applied_profile": applied_profile(element, payload),
            }
        )

    if failed_element:
        for result in raw_results:
            if result["provisioning_status"] == "SUCCESS":
                result["rollback_status"] = "SUCCESS"
                result["message"] += "; rolled back after downstream failure"

    element_results = [ElementResult.model_validate(row) for row in raw_results]
    if failed_element:
        return FlowOneActivationResponse(
            flowone_request_id=str(uuid4()),
            correlation_id=payload.correlation_id,
            overall_status="FAILURE",
            result_code="FLOW-400",
            message=(
                f"Provisioning failed at {failed_element}; all completed "
                "network-element changes were rolled back"
            ),
            imsi=payload.imsi,
            mdn=payload.mdn,
            service_package=payload.service_package,
            roaming_package=payload.roaming_package,
            rollback_status="COMPLETED",
            element_results=element_results,
            started_at=started_at,
            completed_at=utc_now(),
        )

    return FlowOneActivationResponse(
        flowone_request_id=str(uuid4()),
        correlation_id=payload.correlation_id,
        overall_status="SUCCESS",
        result_code="FLOW-200",
        message="All required network elements were provisioned successfully",
        imsi=payload.imsi,
        mdn=payload.mdn,
        service_package=payload.service_package,
        roaming_package=payload.roaming_package,
        rollback_status="NOT_REQUIRED",
        element_results=element_results,
        started_at=started_at,
        completed_at=utc_now(),
    )


def create_flowone_stub_app(state: FlowOneScenarioState | None = None) -> FastAPI:
    scenario = state or FlowOneScenarioState()
    application = FastAPI(
        title="FlowOne Provisioning Mock",
        version="1.0.0-alpha",
        description=(
            "Deterministic mock of the synchronous WDH-to-FlowOne provisioning "
            "boundary. Network-element mappings are illustrative, not a replica "
            "of a carrier production configuration."
        ),
    )

    @application.get("/mock/flowone/v1/health", tags=["System"])
    def health() -> dict:
        return {"status": "ok", "service": "FlowOne Provisioning Mock"}

    @application.post(
        "/mock/flowone/v1/demo/fail-next",
        response_model=FailNextResponse,
        tags=["Demo controls"],
    )
    def fail_next(payload: FailNextRequest) -> FailNextResponse:
        scenario.arm(payload.element)
        return FailNextResponse(
            status="ARMED",
            element=payload.element,
            message=f"The next activation will fail at {payload.element}",
        )

    @application.post("/mock/flowone/v1/demo:reset", tags=["Demo controls"])
    def reset() -> dict:
        scenario.reset()
        return {"status": "RESET"}

    @application.get(
        "/mock/flowone/v1/FlowOneProvisioningService",
        include_in_schema=False,
    )
    def flowone_wsdl() -> FileResponse:
        return FileResponse(WSDL_PATH, media_type="text/xml")

    @application.post(
        "/mock/flowone/v1/FlowOneProvisioningService",
        include_in_schema=False,
    )
    async def activate_soap(request: Request) -> Response:
        try:
            payload = parse_activation_envelope(await request.body())
        except (ValueError, PydanticValidationError) as exc:
            return Response(
                build_soap_fault(f"Invalid ActivateSubscriber request: {exc}"),
                status_code=500,
                media_type=SOAP_MEDIA_TYPE,
            )
        result = orchestrate_activation(payload, scenario.consume())
        return Response(
            build_activation_response_envelope(result),
            status_code=200,
            media_type=SOAP_MEDIA_TYPE,
        )

    @application.post(
        "/mock/amdocs-middleware/v1/subscription-actions",
        response_model=AmdocsSubscriptionActionResponse,
        tags=["Amdocs middleware"],
    )
    def submit_amdocs_action(
        payload: AmdocsSubscriptionActionRequest,
    ) -> AmdocsSubscriptionActionResponse:
        return AmdocsSubscriptionActionResponse(
            middleware_request_id=str(uuid4()),
            status="OK",
            subscription_key=f"{payload.amdocs_account_number}:{payload.imsi}",
            **payload.model_dump(),
            accepted_at=utc_now(),
        )

    application.state.flowone_scenario = scenario
    return application


app = create_flowone_stub_app()
