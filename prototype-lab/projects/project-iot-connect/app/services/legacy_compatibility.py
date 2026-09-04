from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.errors import NotFoundError
from app.integrations.amdocs_middleware import AmdocsMiddlewareGateway
from app.repositories.protocols import Repository
from contracts.amdocs_middleware import AmdocsSubscriptionActionRequest


class LegacyCompatibilityService:
    """WDH submits a compatibility action and stops when middleware says OK.

    Every accepted action is persisted through the repository so that
    ``GET /api/v1/legacy-subscription-actions/{id}`` returns the same resource
    after an application restart.
    """

    def __init__(self, middleware: AmdocsMiddlewareGateway, repository: Repository) -> None:
        self.middleware = middleware
        self.repository = repository

    async def submit(
        self,
        *,
        amdocs_account_number: str,
        wdh_account_reference: str,
        mdn: str,
        imsi: str,
        action: str,
    ) -> dict:
        compatibility_action_id = str(uuid4())
        middleware_result = await self.middleware.submit(
            AmdocsSubscriptionActionRequest(
                amdocs_account_number=amdocs_account_number,
                wdh_account_reference=wdh_account_reference,
                mdn=mdn,
                imsi=imsi,
                action=action,
            )
        )
        result = {
            "compatibility_action_id": compatibility_action_id,
            "wdh_status": "SUBMITTED",
            "middleware": middleware_result.model_dump(),
        }
        self.repository.insert_legacy_subscription_action(
            {
                "compatibility_action_id": compatibility_action_id,
                "wdh_status": result["wdh_status"],
                "payload": result,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return result

    def get(self, compatibility_action_id: str) -> dict:
        result = self.repository.get_legacy_subscription_action(compatibility_action_id)
        if result is None:
            raise NotFoundError(
                f"Legacy compatibility action {compatibility_action_id} was not found"
            )
        return result
