from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FlowOneModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


ServicePackage = Literal["DATA_ONLY", "SMS_ONLY", "DATA_SMS"]
RoamingPackage = Literal["HOME_ONLY", "DOMESTIC"]
NetworkElement = Literal["HSS", "POLICY", "SMSC", "AAA"]


class FlowOneActivationRequest(FlowOneModel):
    """Small northbound contract sent by WDH to FlowOne."""

    correlation_id: str = Field(min_length=8, max_length=80)
    imsi: str = Field(pattern=r"^\d{14,15}$")
    mdn: str = Field(pattern=r"^\+?\d{10,15}$")
    service_package: ServicePackage
    roaming_package: RoamingPackage
    private_apn: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._-]{1,80}$",
    )


class ElementResult(FlowOneModel):
    element: NetworkElement
    operation: str
    provisioning_status: Literal[
        "SUCCESS", "FAILURE", "NOT_ATTEMPTED", "SKIPPED_NOT_APPLICABLE"
    ]
    element_code: str
    message: str
    rollback_status: Literal["NOT_REQUIRED", "SUCCESS", "NOT_APPLICABLE"]
    applied_profile: str | None = None


class FlowOneActivationResponse(FlowOneModel):
    flowone_request_id: str
    correlation_id: str
    overall_status: Literal["SUCCESS", "FAILURE"]
    result_code: str
    message: str
    imsi: str
    mdn: str
    service_package: ServicePackage
    roaming_package: RoamingPackage
    rollback_status: Literal["NOT_REQUIRED", "COMPLETED"]
    element_results: list[ElementResult]
    started_at: str
    completed_at: str


class FailNextRequest(FlowOneModel):
    element: NetworkElement


class FailNextResponse(FlowOneModel):
    status: Literal["ARMED"]
    element: NetworkElement
    message: str
