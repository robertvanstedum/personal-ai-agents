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
CONNECTHQ_HEALTH_URL       = os.environ.get("CONNECTHQ_HEALTH_URL", f"{CONNECTHQ_BACKEND}/api/v1/health")
CONNECTHQ_RELEASE_LABEL    = os.environ.get("CONNECTHQ_RELEASE_LABEL", "revision 4 candidate")

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
