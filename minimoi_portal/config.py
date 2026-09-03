"""
minimoi_portal/config.py — Portal configuration.

Override via environment variables on Mac Mini.
"""

import os
import re
from pathlib import Path

# Backend app URLs (all run locally on Mac Mini)
CURATOR_BACKEND    = os.environ.get("CURATOR_BACKEND",    "http://localhost:8766")
GERMAN_BACKEND     = os.environ.get("GERMAN_BACKEND",     "http://localhost:8767")
PORTUGUESE_BACKEND = os.environ.get("PORTUGUESE_BACKEND", "http://localhost:8770")
COS_BACKEND        = os.environ.get("COS_BACKEND",        "http://localhost:8769")
# Connect HQ reference demo (Guild / Prototype Lab). Owner-only in v0.9; see
# docs/specs/spec_154_connect_hq_minimoi_production_hosting_2026-09-03.md
CONNECTHQ_BACKEND  = os.environ.get("CONNECTHQ_BACKEND",  "http://localhost:8095")
CONNECTHQ_RELEASE  = os.environ.get("CONNECTHQ_RELEASE",  "connecthq-v0.9.0-beta.1")
CONNECTHQ_SPEC     = "spec_154_connect_hq_minimoi_production_hosting_2026-09-03.md"

# ── Guild Experiment workspace (Spec #155) ───────────────────────────────────
# Non-authoritative projection of Planning Studio initiative records. Planning
# Studio in the Central Personal Repository owns the durable record; Guild only
# renders this generated file (spec §3.3).
GUILD_EXPERIMENT_PROJECTION = os.environ.get(
    "GUILD_EXPERIMENT_PROJECTION",
    str(Path(__file__).resolve().parent.parent / "data" / "guild" / "experiment_projection.json"),
)

# Per-environment surface configuration for the IoT Connect (Connect HQ)
# reference demo — deployment-owned, never derived from repository content
# (spec §4.2). Defaults are the AWS values; the Mac overrides them locally.
CONNECTHQ_SURFACE_BASE_URL = os.environ.get("CONNECTHQ_SURFACE_BASE_URL", "/app/connecthq")
CONNECTHQ_RELEASE_LABEL    = os.environ.get("CONNECTHQ_RELEASE_LABEL", "revision 4 candidate")

# The projected row that receives the runtime join (launch, workbenches, health).
# Matched against `initiative_id`, not the domain tag: the domain tag is
# repository content, the initiative id is the identity the two sources are
# joined by (spec §3.2). Default is the §5.3 placeholder.
CONNECTHQ_INITIATIVE_ID    = os.environ.get("CONNECTHQ_INITIATIVE_ID", "INIT-2026-0004")

# Stale indicator threshold in days (spec §5.2) — an indicator, not a status.
GUILD_EXPERIMENT_STALE_DAYS = int(os.environ.get("GUILD_EXPERIMENT_STALE_DAYS", "30"))

# Fixed, reviewed relative paths. Not overridable by environment or repository
# content — the surfaces are joined safely to CONNECTHQ_SURFACE_BASE_URL.
CONNECTHQ_SURFACE_PATHS = {
    "launch": "/",
    "admin": "/admin",
    "billing_workbench": "/workbench",
    "swagger": "/docs",
}

_LOOPBACK_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$")


def _validate_surface_base_url(value: str) -> str:
    """Only a relative portal path or a loopback origin is accepted (spec §4.2).

    Anything else fails at startup rather than letting the page send the
    browser to an unreviewed origin.
    """
    if value.startswith("/") and not value.startswith("//"):
        return value.rstrip("/")
    if _LOOPBACK_ORIGIN.match(value.rstrip("/")):
        return value.rstrip("/")
    raise RuntimeError(
        "CONNECTHQ_SURFACE_BASE_URL must be a relative portal path (starting "
        "with '/') or a loopback origin (http(s)://127.0.0.1[:port] or "
        f"http(s)://localhost[:port]) — got {value!r}."
    )


CONNECTHQ_SURFACE_BASE_URL = _validate_surface_base_url(CONNECTHQ_SURFACE_BASE_URL)


def _derive_health_url() -> str:
    """Health URL for the probe (spec §4.2).

    An explicit CONNECTHQ_HEALTH_URL always wins. Otherwise: when the surface
    base URL is an absolute loopback origin the demo is the standalone Mac
    stack, so health lives on that same origin; when it is a relative portal
    path the demo is the hosted container behind the proxy, so health is
    derived from CONNECTHQ_BACKEND as it was before.
    """
    explicit = os.environ.get("CONNECTHQ_HEALTH_URL")
    if explicit:
        return explicit
    if CONNECTHQ_SURFACE_BASE_URL.startswith("http"):
        return f"{CONNECTHQ_SURFACE_BASE_URL}/api/v1/health"
    return f"{CONNECTHQ_BACKEND}/api/v1/health"


CONNECTHQ_HEALTH_URL = _derive_health_url()


def connecthq_surfaces() -> dict:
    """The four demo surfaces, built only from configuration (spec §4.2, §6).

    base_url is already validated to a relative portal path or a loopback
    origin; the paths are the fixed table above. Nothing here reads repository
    content, so the projection file can never inject a URL.
    """
    base = CONNECTHQ_SURFACE_BASE_URL.rstrip("/")  # validated; no trailing slash
    surfaces = {}
    for key, path in CONNECTHQ_SURFACE_PATHS.items():
        if not path.startswith("/") or path.startswith("//") or ".." in path:
            raise RuntimeError(f"unsafe Connect HQ surface path for {key!r}: {path!r}")
        surfaces[key] = f"{base}{path}"
    return surfaces


def connecthq_surface_is_external() -> bool:
    """True when the surfaces are an absolute origin (open in a new tab)."""
    return CONNECTHQ_SURFACE_BASE_URL.startswith("http")

# Flask session secret — MUST be set in the environment.
# Generate: python3 -c "import secrets; print(secrets.token_hex(32))"
# SECURITY: in production, refuse to boot without an explicitly-set strong key.
# The fallback below is dev-only and would let anyone forge session cookies —
# a hard-fail here prevents that from ever silently reaching production again.
_secret_key = os.environ.get("PORTAL_SECRET_KEY")
if os.environ.get("FLASK_ENV") == "production" and (not _secret_key or len(_secret_key) < 32):
    raise RuntimeError(
        "PORTAL_SECRET_KEY must be explicitly set to a strong (>=32 char) value "
        "in production — refusing to boot with the insecure default."
    )
SECRET_KEY = _secret_key or "dev-only-change-in-production"

# Session lifetime for permanent users (Owner/Family)
SESSION_LIFETIME_DAYS = 30

# Portal runs on this port (Cloudflare Tunnel points here)
PORT = int(os.environ.get("PORTAL_PORT", 5001))

# Public base URL — used in outbound emails; override in dev to avoid sending prod links
BASE_URL = os.environ.get("BASE_URL", "https://minimoi.ai")

# Email (Zoho SMTP) — all configurable via env vars, no hardcoded values
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "no-reply@minimoi.ai")
SMTP_FROM = os.environ.get("SMTP_FROM", "mini-moi <no-reply@minimoi.ai>")
