from __future__ import annotations

import os
from typing import Protocol

import httpx

from app.domain.errors import IntegrationError
from contracts.amdocs_middleware import (
    AmdocsSubscriptionActionRequest,
    AmdocsSubscriptionActionResponse,
)


class AmdocsMiddlewareGateway(Protocol):
    async def submit(
        self, request: AmdocsSubscriptionActionRequest
    ) -> AmdocsSubscriptionActionResponse: ...


class AmdocsMiddlewareClient:
    """REST boundary to middleware; downstream Amdocs processing is opaque."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "AmdocsMiddlewareClient":
        return cls(
            os.getenv("AMDOCS_MIDDLEWARE_BASE_URL", "http://127.0.0.1:8096"),
            timeout_seconds=float(
                os.getenv("AMDOCS_MIDDLEWARE_TIMEOUT_SECONDS", "10")
            ),
        )

    async def submit(
        self, request: AmdocsSubscriptionActionRequest
    ) -> AmdocsSubscriptionActionResponse:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/mock/amdocs-middleware/v1/subscription-actions",
                    json=request.model_dump(),
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise IntegrationError(
                "Amdocs middleware did not respond before the timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Amdocs middleware HTTP call failed: {exc}") from exc

        try:
            result = AmdocsSubscriptionActionResponse.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise IntegrationError(
                "Amdocs middleware returned an invalid response contract"
            ) from exc
        if result.status != "OK":
            raise IntegrationError("Amdocs middleware did not accept the request")
        return result
