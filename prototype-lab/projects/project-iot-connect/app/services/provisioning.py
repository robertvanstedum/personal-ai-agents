from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.errors import NotFoundError
from app.integrations.flowone import FlowOneGateway
from app.repositories.protocols import Repository
from contracts.flowone import FlowOneActivationRequest


class ProvisioningService:
    """WDH orchestration boundary for the synchronous FlowOne call.

    Every activation result is persisted through the repository so that
    ``GET /api/v1/network-activations/{id}`` returns the same resource after an
    application restart.
    """

    def __init__(self, flowone: FlowOneGateway, repository: Repository) -> None:
        self.flowone = flowone
        self.repository = repository

    async def activate(
        self,
        *,
        imsi: str,
        mdn: str,
        service_package: str,
        roaming_package: str,
        private_apn: str | None = None,
    ) -> dict:
        activation_id = str(uuid4())
        correlation_id = f"WDH-{uuid4()}"
        flowone_result = await self.flowone.activate(
            FlowOneActivationRequest(
                correlation_id=correlation_id,
                imsi=imsi,
                mdn=mdn,
                service_package=service_package,
                roaming_package=roaming_package,
                private_apn=private_apn,
            )
        )
        result = {
            "activation_id": activation_id,
            "correlation_id": correlation_id,
            "wdh_service_status": (
                "ACTIVE"
                if flowone_result.overall_status == "SUCCESS"
                else "ACTIVATION_FAILED"
            ),
            "flowone": flowone_result.model_dump(),
        }
        self.repository.insert_network_activation(
            {
                "activation_id": activation_id,
                "correlation_id": correlation_id,
                "wdh_service_status": result["wdh_service_status"],
                "payload": result,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return result

    def get(self, activation_id: str) -> dict:
        result = self.repository.get_network_activation(activation_id)
        if result is None:
            raise NotFoundError(f"Network activation {activation_id} was not found")
        return result
