from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AmdocsMiddlewareModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class AmdocsSubscriptionActionRequest(AmdocsMiddlewareModel):
    """The complete WDH-to-middleware compatibility contract for the demo."""

    amdocs_account_number: str = Field(pattern=r"^[A-Z0-9-]{3,40}$")
    wdh_account_reference: str = Field(pattern=r"^[A-Z0-9-]{3,40}$")
    mdn: str = Field(pattern=r"^\+?\d{10,15}$")
    imsi: str = Field(pattern=r"^\d{14,15}$")
    action: Literal["CREATE", "DEACTIVATE"]


class AmdocsSubscriptionActionResponse(AmdocsMiddlewareModel):
    middleware_request_id: str
    status: Literal["OK"]
    subscription_key: str
    amdocs_account_number: str
    wdh_account_reference: str
    mdn: str
    imsi: str
    action: Literal["CREATE", "DEACTIVATE"]
    accepted_at: str
