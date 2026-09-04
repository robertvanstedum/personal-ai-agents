from __future__ import annotations

import os
import re
from html import escape
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import build_api_router
from app.dependencies import AuthPolicy, resolve_auth_mode
from app.domain.errors import (
    AuthorizationError,
    ConflictError,
    DomainError,
    IntegrationError,
    NotFoundError,
    ValidationError,
)
from app.repositories import UNSUPPORTED_STORE_MESSAGE
from app.repositories.memory import MemoryRepository
from app.integrations.amdocs_middleware import AmdocsMiddlewareClient
from app.integrations.flowone import FlowOneClient
from app.services.demo import DemoService
from app.services.legacy_compatibility import LegacyCompatibilityService
from app.services.provisioning import ProvisioningService
from app.services.ordering import OrderingService


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
FIXTURE_DIR = BASE_DIR / "fixtures"
# Presentation slides are packaged with the application as stable PNG assets.
PRESENTATION_SLIDE_DIR = STATIC_DIR / "iotconnect" / "presentation"

# URL prefix support (IOTCONNECT_ROOT_PATH, e.g. "/app/iotconnect" behind a reverse
# proxy). Empty = standalone; pages are then served byte-for-byte from disk.
ROOT_PATH_META = "iotconnect-root-path"
_ROOT_ABSOLUTE_ATTRIBUTE = re.compile(r'(\b(?:href|src|action)=")/(?!/)')


def resolve_root_path(value: str | None) -> str:
    root_path = (value if value is not None else os.getenv("IOTCONNECT_ROOT_PATH", "")).strip()
    root_path = root_path.rstrip("/")
    if root_path and not root_path.startswith("/"):
        raise RuntimeError(
            f"IOTCONNECT_ROOT_PATH must start with '/' (got {root_path!r}); "
            "example: /app/iotconnect"
        )
    return root_path


def prefix_html(document: str, root_path: str) -> str:
    """Rewrite a static page for a URL prefix: one meta tag for the JavaScript,
    and every root-absolute href/src/action attribute prefixed."""
    meta = f'<meta name="{ROOT_PATH_META}" content="{escape(root_path)}">'
    document = re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + meta, document, count=1)
    return _ROOT_ABSOLUTE_ATTRIBUTE.sub(lambda m: f'{m.group(1)}{root_path}/', document)


def page_response(path: Path, root_path: str, headers: dict | None = None):
    if not root_path:
        return FileResponse(path, headers=headers)
    return HTMLResponse(
        prefix_html(path.read_text(encoding="utf-8"), root_path), headers=headers
    )


def create_repository():
    """Select the repository. PostgreSQL operates the demo; memory is for tests.

    Any other value — including ``snowflake``, whose adapter source is retained
    in ``app/repositories/snowflake.py`` but is unsupported as an application
    database in v0.9 (decision 2026-09-03) — fails immediately at startup.
    """
    backend = os.getenv("IOTCONNECT_STORE", "memory").lower()
    if backend == "memory":
        return MemoryRepository()
    if backend in {"postgres", "postgresql"}:
        from app.repositories.postgres import PostgresRepository

        return PostgresRepository.from_environment()
    raise RuntimeError(UNSUPPORTED_STORE_MESSAGE.format(value=backend))


def create_app(
    repository=None,
    flowone_gateway=None,
    amdocs_middleware_gateway=None,
    root_path: str | None = None,
    auth_mode: str | None = None,
) -> FastAPI:
    repo = repository or create_repository()
    prefix = resolve_root_path(root_path)
    auth = AuthPolicy(resolve_auth_mode(auth_mode))
    service = DemoService(repo)
    provisioning = ProvisioningService(
        flowone_gateway or FlowOneClient.from_environment(), repo
    )
    legacy_compatibility = LegacyCompatibilityService(
        amdocs_middleware_gateway or AmdocsMiddlewareClient.from_environment(), repo
    )
    ordering = OrderingService(repo, provisioning, legacy_compatibility)
    application = FastAPI(
        title="IoT Connect",
        version="1.0.0",
        root_path=prefix,
        description=(
            "Enterprise IoT activation, legacy-compatibility, billing, and "
            "reconciliation demonstration built on a reusable "
            "UI-to-API-to-service-to-repository pattern."
        ),
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_payload(request: Request, code: str, message: str, details=None) -> dict:
        return {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", "unknown"),
            "details": details,
        }

    @application.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        if isinstance(exc, NotFoundError):
            status_code, code = 404, "NOT_FOUND"
        elif isinstance(exc, ConflictError):
            status_code, code = 409, "CONFLICT"
        elif isinstance(exc, AuthorizationError):
            status_code, code = 403, "FORBIDDEN"
        elif isinstance(exc, ValidationError):
            status_code, code = 422, "VALIDATION_ERROR"
        elif isinstance(exc, IntegrationError):
            status_code, code = 502, "INTEGRATION_ERROR"
        else:
            status_code, code = 400, "DOMAIN_ERROR"
        return JSONResponse(
            status_code=status_code,
            content=error_payload(request, code, str(exc)),
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_payload(
                request,
                "REQUEST_VALIDATION_ERROR",
                "The request does not match the documented API contract",
                exc.errors(),
            ),
        )

    application.include_router(
        build_api_router(service, provisioning, legacy_compatibility, ordering, auth)
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.mount("/fixtures", StaticFiles(directory=FIXTURE_DIR), name="fixtures")

    def page(relative: str, **headers):
        return page_response(STATIC_DIR / relative, prefix, headers or None)

    @application.get("/", include_in_schema=False)
    def project_gateway():
        return page("iotconnect/gateway.html")

    @application.get("/presentation", include_in_schema=False)
    def project_presentation():
        return page("iotconnect/presentation.html", **{"Cache-Control": "no-store"})

    @application.get(
        "/presentation/assets/slide-{slide_number}.png",
        include_in_schema=False,
    )
    def presentation_slide_image(slide_number: int):
        slide_path = PRESENTATION_SLIDE_DIR / f"slide-{slide_number}.png"
        if slide_number not in range(1, 6) or not slide_path.exists():
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return FileResponse(
            slide_path,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/workbench", include_in_schema=False)
    def billing_workbench():
        return page("index.html")

    @application.get("/project-design", include_in_schema=False)
    def architecture_reference() -> HTMLResponse:
        content = escape((BASE_DIR / "docs" / "ARCHITECTURE.md").read_text())
        return HTMLResponse(
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>IoT Connect · Architecture</title>"
            "<style>body{margin:0;background:#f3f7f8;color:#13213a;"
            "font-family:ui-monospace,SFMono-Regular,Menlo,monospace}"
            "header{background:#0b2b42;color:white;padding:18px 28px}"
            "header a{color:#8de1d6;text-decoration:none}"
            "main{max-width:1080px;margin:24px auto;padding:0 24px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;"
            "border:1px solid #d9e3e7;border-radius:8px;padding:24px;"
            "font-size:13px;line-height:1.55}</style></head><body>"
            f"<header><a href=\"{prefix}/\">← Project home</a></header>"
            f"<main><pre>{content}</pre></main></body></html>"
        )

    @application.get("/admin", include_in_schema=False)
    def admin_console():
        return page("admin.html")

    @application.get("/artifacts/invoice-comparison", include_in_schema=False)
    def invoice_comparison():
        return page("invoice-comparison.html")

    @application.get("/artifacts/statement", include_in_schema=False)
    def account_statement():
        return page("statement.html")

    role_pages = {
        "/portal": "portal.html",
        "/portal/subscriptions": "portal-subscriptions.html",
        "/portal/actions": "portal-actions.html",
        "/portal/billing": "portal-billing.html",
        "/operator": "operator.html",
        "/operator/accounts": "operator-accounts.html",
        "/operator/accounts/new": "operator-account-new.html",
        "/operator/account": "operator-account.html",
        "/operator/account/configuration": "operator-account-configuration.html",
        "/operator/subscriptions": "operator-subscriptions.html",
        "/operator/actions": "portal-actions.html",
        "/operator/billing": "portal-billing.html",
        "/operator/inventory": "operator-inventory.html",
        "/operator/bill-cycles": "operator-bill-cycles.html",
        "/operator/catalog": "operator-catalog.html",
        "/operator/api-activity": "operator-api-activity.html",
    }

    def role_page(filename: str):
        def serve_role_page():
            return page(f"iotconnect/{filename}", **{"Cache-Control": "no-store"})

        return serve_role_page

    for route, filename in role_pages.items():
        application.add_api_route(
            route,
            role_page(filename),
            include_in_schema=False,
        )

    if auth.mode == "minimoi_proxy":
        # The tier header is declared optional so a missing header is a 403
        # (AuthorizationError) rather than a 422 contract error; document it
        # honestly as required in the OpenAPI schema.
        default_openapi = application.openapi

        def proxy_openapi():
            schema = default_openapi()
            for path_item in schema.get("paths", {}).values():
                for operation in path_item.values():
                    for parameter in operation.get("parameters", []) if isinstance(operation, dict) else []:
                        if parameter.get("in") == "header" and parameter.get("name") == "X-Minimoi-User-Tier":
                            parameter["required"] = True
            return schema

        application.openapi = proxy_openapi

    application.state.root_path = prefix
    application.state.auth_mode = auth.mode
    application.state.repository = repo
    application.state.service = service
    application.state.provisioning = provisioning
    application.state.legacy_compatibility = legacy_compatibility
    application.state.ordering = ordering
    return application


app = create_app()
