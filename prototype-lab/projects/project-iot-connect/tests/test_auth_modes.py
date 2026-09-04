"""IOTCONNECT_AUTH_MODE: local_demo (default) vs minimoi_proxy (hosted).

local_demo keeps the X-Demo-Role / X-Demo-Account-ID behaviour byte-for-byte
(the existing suite runs against it unchanged). minimoi_proxy ignores X-Demo-*
entirely and trusts only the portal-verified X-Minimoi-* headers: the owner
tier acts as the administrator and as every customer persona; anything else
is 403. Any other mode value fails at startup.
"""
import pytest
from fastapi.testclient import TestClient

from app.dependencies import ADMIN_ROLE, resolve_auth_mode
from app.main import create_app
from app.repositories.memory import MemoryRepository

OWNER = {"X-Minimoi-User-Tier": "owner", "X-Minimoi-Username": "demo-owner", "X-Minimoi-Auth-Id": "auth-0001"}
ADMIN_URL = "/api/v1/admin/legacy-accounts/available"


def aster_id(client):
    return next(r["account_id"] for r in client.get("/api/v1/accounts").json() if r["account_number"] == "ACCT-000100")


@pytest.fixture
def proxy():
    return TestClient(create_app(MemoryRepository(), auth_mode="minimoi_proxy"))


@pytest.fixture
def local(monkeypatch):
    monkeypatch.delenv("IOTCONNECT_AUTH_MODE", raising=False)
    return TestClient(create_app(MemoryRepository()))


def test_default_mode_is_local_demo_and_keeps_x_demo_contract(local):
    assert local.app.state.auth_mode == "local_demo"
    assert local.get(ADMIN_URL, headers={"X-Demo-Role": ADMIN_ROLE}).status_code == 200
    assert local.get(ADMIN_URL).status_code == 422
    assert local.get(ADMIN_URL, headers={"X-Demo-Role": "ENTERPRISE_CUSTOMER"}).status_code == 403
    # X-Minimoi-* headers mean nothing in local_demo
    assert local.get(ADMIN_URL, headers=OWNER).status_code == 422


def test_proxy_owner_is_admin_and_every_customer_with_only_x_minimoi_headers(proxy):
    assert proxy.app.state.auth_mode == "minimoi_proxy"
    assert proxy.get(ADMIN_URL, headers=OWNER).status_code == 200
    account = aster_id(proxy)
    scoped = proxy.get(f"/api/v1/accounts/{account}/activation-batches", headers=OWNER)
    assert scoped.status_code == 200 and scoped.json() == []
    reset = proxy.post("/api/v1/admin/demo:reset", headers=OWNER)
    assert reset.status_code == 200


def test_proxy_missing_identity_is_403(proxy):
    for response in (
        proxy.get(ADMIN_URL),
        proxy.get(f"/api/v1/accounts/{aster_id(proxy)}/activation-batches"),
        proxy.get(ADMIN_URL, headers={"X-Minimoi-User-Tier": ""}),
        proxy.get(ADMIN_URL, headers={"X-Minimoi-Username": "demo-owner"}),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("tier", ["guest", "admin", "family", "Owner", "owner "])
def test_proxy_non_owner_tier_is_403_even_with_spoofed_x_demo_admin(proxy, tier):
    headers = {"X-Minimoi-User-Tier": tier, "X-Demo-Role": ADMIN_ROLE}
    response = proxy.get(ADMIN_URL, headers=headers)
    if tier.strip() == "owner":
        assert response.status_code == 200  # surrounding whitespace is tolerated
    else:
        assert response.status_code == 403
        assert proxy.post("/api/v1/admin/demo:reset", headers=headers).status_code == 403


def test_proxy_x_demo_headers_alone_never_elevate(proxy):
    account = aster_id(proxy)
    assert proxy.get(ADMIN_URL, headers={"X-Demo-Role": ADMIN_ROLE}).status_code == 403
    assert proxy.get(
        f"/api/v1/accounts/{account}/activation-batches",
        headers={"X-Demo-Role": "ENTERPRISE_CUSTOMER", "X-Demo-Account-ID": account},
    ).status_code == 403


def test_proxy_customer_scope_follows_the_requested_account(proxy):
    """The owner is every customer persona: the scoped context is the URL's account."""
    account = aster_id(proxy)
    sims = proxy.get("/api/v1/inventory/sims/available").json()[:1]
    assert proxy.post(
        f"/api/v1/admin/accounts/{account}/sim-assignments",
        json={"sim_resource_ids": [row["sim_resource_id"] for row in sims]},
        headers=OWNER,
    ).status_code == 200
    created = proxy.post(
        f"/api/v1/accounts/{account}/activation-batches",
        json={"items": [{"source_order_ref": "PROXY-001", "sim_resource_id": sims[0]["sim_resource_id"], "price_plan_id": "PLAN-IOT-001", "technical_profile_id": "NET-DATA-SMS-DOM"}]},
        headers=OWNER,
    )
    assert created.status_code == 201, created.text
    assert created.json()["account_id"] == account


def test_proxy_openapi_documents_x_minimoi_and_not_x_demo(proxy, local):
    proxied = proxy.get("/openapi.json").json()
    params = proxied["paths"][ADMIN_URL]["get"]["parameters"]
    assert any(p["name"] == "X-Minimoi-User-Tier" and p["in"] == "header" and p["required"] for p in params)
    assert not any(p["name"].startswith("X-Demo") for p in params)
    local_params = local.get("/openapi.json").json()["paths"][ADMIN_URL]["get"]["parameters"]
    assert any(p["name"] == "X-Demo-Role" and p["required"] for p in local_params)


def test_invalid_auth_mode_fails_fast(monkeypatch):
    monkeypatch.setenv("IOTCONNECT_AUTH_MODE", "oauth")
    with pytest.raises(RuntimeError, match="IOTCONNECT_AUTH_MODE=oauth is not supported in IoT Connect v0.9"):
        create_app(MemoryRepository())
    with pytest.raises(RuntimeError):
        create_app(MemoryRepository(), auth_mode="basic")
    assert resolve_auth_mode("") == "local_demo"
    assert resolve_auth_mode("MINIMOI_PROXY") == "minimoi_proxy"
