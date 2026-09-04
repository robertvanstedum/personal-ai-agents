from __future__ import annotations

import os
from typing import Protocol

import httpx

from app.domain.errors import IntegrationError
from contracts.flowone import FlowOneActivationRequest, FlowOneActivationResponse
from contracts.flowone_soap import (
    build_activation_envelope,
    parse_activation_response_envelope,
)


class FlowOneGateway(Protocol):
    async def activate(
        self, request: FlowOneActivationRequest
    ) -> FlowOneActivationResponse: ...


class FlowOneClient:
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
    def from_environment(cls) -> "FlowOneClient":
        return cls(
            os.getenv("FLOWONE_BASE_URL", "http://127.0.0.1:8096"),
            timeout_seconds=float(os.getenv("FLOWONE_TIMEOUT_SECONDS", "10")),
        )

    async def activate(
        self, request: FlowOneActivationRequest
    ) -> FlowOneActivationResponse:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/mock/flowone/v1/FlowOneProvisioningService",
                    content=build_activation_envelope(request),
                    headers={
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": "ActivateSubscriber",
                        "X-Correlation-ID": request.correlation_id,
                    },
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise IntegrationError("FlowOne did not respond before the timeout") from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(f"FlowOne HTTP call failed: {exc}") from exc

        try:
            return parse_activation_response_envelope(response.content)
        except (ValueError, TypeError) as exc:
            raise IntegrationError(
                f"FlowOne returned an invalid SOAP response: {exc}"
            ) from exc
