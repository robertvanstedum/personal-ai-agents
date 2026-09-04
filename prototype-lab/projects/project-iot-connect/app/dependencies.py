from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header

from app.domain.errors import AuthorizationError


ADMIN_ROLE = "BUSINESS_OPS_ADMIN"
CUSTOMER_ROLE = "ENTERPRISE_CUSTOMER"

# Trusted-identity modes (hosting spec #154 §4.3, §5).
#   local_demo    — default; the browser/Postman send X-Demo-Role / X-Demo-Account-ID
#                   (unchanged demo behaviour).
#   minimoi_proxy — hosted behind the mini-moi portal, which authenticates the user,
#                   strips any X-Demo-* headers and forwards portal-verified
#                   X-Minimoi-User-Tier / X-Minimoi-Username / X-Minimoi-Auth-Id.
#                   Only the "owner" tier reaches the demo; the owner acts as the
#                   seeded administrator and as every seeded customer persona.
AUTH_MODES = ("local_demo", "minimoi_proxy")
DEFAULT_AUTH_MODE = "local_demo"
OWNER_TIER = "owner"
UNSUPPORTED_AUTH_MODE_MESSAGE = (
    "IOTCONNECT_AUTH_MODE={value} is not supported in IoT Connect v0.9; use local_demo "
    "(default, X-Demo-* demo headers) or minimoi_proxy (portal-verified "
    "X-Minimoi-* headers behind the mini-moi portal)."
)


def resolve_auth_mode(value: str | None) -> str:
    mode = (value if value is not None else os.getenv("IOTCONNECT_AUTH_MODE", DEFAULT_AUTH_MODE)).strip().lower()
    if not mode:
        mode = DEFAULT_AUTH_MODE
    if mode not in AUTH_MODES:
        raise RuntimeError(UNSUPPORTED_AUTH_MODE_MESSAGE.format(value=mode))
    return mode


@dataclass(frozen=True)
class ActorContext:
    role: str
    account_id: str | None = None
    # Informational identity forwarded by the portal in minimoi_proxy mode.
    username: str | None = None
    auth_id: str | None = None


# --- local_demo -------------------------------------------------------------
def require_admin(
    actor_role: str = Header(
        alias="X-Demo-Role",
        description="Stable demo role identifier. Required value: BUSINESS_OPS_ADMIN",
        examples=[ADMIN_ROLE],
    ),
) -> ActorContext:
    if actor_role != ADMIN_ROLE:
        raise AuthorizationError(
            "This operation requires the BUSINESS_OPS_ADMIN role"
        )
    return ActorContext(role=actor_role)


def require_account_customer(
    account_id: str,
    actor_role: str = Header(alias="X-Demo-Role"),
    actor_account_id: str = Header(alias="X-Demo-Account-ID"),
) -> ActorContext:
    if actor_role != CUSTOMER_ROLE or actor_account_id != account_id:
        raise AuthorizationError(
            "This operation requires an ENTERPRISE_CUSTOMER session for the requested account"
        )
    return ActorContext(role=actor_role, account_id=actor_account_id)


# --- minimoi_proxy ------------------------------------------------------------
_TIER_HEADER = Header(
    default=None,
    alias="X-Minimoi-User-Tier",
    description=(
        "Portal-verified user tier (minimoi_proxy mode). Required; only 'owner' "
        "is authorised. X-Demo-* headers are ignored in this mode."
    ),
    examples=[OWNER_TIER],
)
_USERNAME_HEADER = Header(default=None, alias="X-Minimoi-Username", description="Portal-verified username (informational).")
_AUTH_ID_HEADER = Header(default=None, alias="X-Minimoi-Auth-Id", description="Portal-verified auth id (informational).")


def _verified_owner(tier: str | None) -> None:
    if tier is None or not tier.strip():
        raise AuthorizationError(
            "This operation requires a portal-verified identity (X-Minimoi-User-Tier is missing)"
        )
    if tier.strip() != OWNER_TIER:
        raise AuthorizationError(
            "This operation requires the portal owner tier"
        )


def proxy_require_admin(
    tier: str | None = _TIER_HEADER,
    username: str | None = _USERNAME_HEADER,
    auth_id: str | None = _AUTH_ID_HEADER,
) -> ActorContext:
    _verified_owner(tier)
    return ActorContext(role=ADMIN_ROLE, username=username, auth_id=auth_id)


def proxy_require_account_customer(
    account_id: str,
    tier: str | None = _TIER_HEADER,
    username: str | None = _USERNAME_HEADER,
    auth_id: str | None = _AUTH_ID_HEADER,
) -> ActorContext:
    _verified_owner(tier)
    return ActorContext(role=CUSTOMER_ROLE, account_id=account_id, username=username, auth_id=auth_id)


class AuthPolicy:
    """The pair of FastAPI dependencies for the selected trusted-identity mode."""

    def __init__(self, mode: str) -> None:
        self.mode = resolve_auth_mode(mode)
        if self.mode == "minimoi_proxy":
            self.require_admin = proxy_require_admin
            self.require_account_customer = proxy_require_account_customer
        else:
            self.require_admin = require_admin
            self.require_account_customer = require_account_customer
