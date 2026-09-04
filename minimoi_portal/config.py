"""
minimoi_portal/config.py — Portal configuration.

Override via environment variables on Mac Mini.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

# Backend app URLs (all run locally on Mac Mini)
CURATOR_BACKEND    = os.environ.get("CURATOR_BACKEND",    "http://localhost:8766")
GERMAN_BACKEND     = os.environ.get("GERMAN_BACKEND",     "http://localhost:8767")
PORTUGUESE_BACKEND = os.environ.get("PORTUGUESE_BACKEND", "http://localhost:8770")
COS_BACKEND        = os.environ.get("COS_BACKEND",        "http://localhost:8769")
# IoT Connect reference demo (Guild / Experiment).
# Owner-only in v0.9; see spec_154_iot_connect_minimoi_production_hosting and
# spec_155_guild_experiment_workspace under docs/specs/.
#
# One-release environment compatibility (naming decision N3, 2026-09-03): every
# IOTCONNECT_* setting below is read through _env(), which takes the new name
# first and falls back to the retired CONNECTHQ_* name. The fallback exists so
# a host whose .env has not been updated yet keeps working across exactly one
# release; remove _env() and the legacy names after that release.
_LEGACY_ENV_PREFIX = "CONNECTHQ_"
_ENV_PREFIX = "IOTCONNECT_"


def _env(name: str, default=None):
    """Read IOTCONNECT_<name>, falling back to CONNECTHQ_<name> for one release.

    The new name always wins when both are set, so a host that has been
    migrated is never overridden by a stale legacy value left behind.
    """
    value = os.environ.get(_ENV_PREFIX + name)
    if value is not None:
        return value
    value = os.environ.get(_LEGACY_ENV_PREFIX + name)
    if value is not None:
        return value
    return default


IOTCONNECT_BACKEND = _env("BACKEND", "http://localhost:8095")

# ── Guild Experiment workspace (Spec #155) ───────────────────────────────────
# Non-authoritative projection of Planning Studio initiative records. Planning
# Studio in the Central Personal Repository owns the durable record; Guild only
# renders this generated file (spec §3.3).
GUILD_EXPERIMENT_PROJECTION = os.environ.get(
    "GUILD_EXPERIMENT_PROJECTION",
    str(Path(__file__).resolve().parent.parent / "data" / "guild" / "experiment_projection.json"),
)

# Per-environment surface configuration for the IoT Connect reference demo —
# deployment-owned, never derived from repository content (spec §4.2).
# Defaults are the AWS values; the Mac overrides them locally.
#
# HOSTED_PORTAL_PATH is the single reviewed hosted portal path and the whole
# allow-list for the relative form. LEGACY_PORTAL_PATH is the retired path,
# kept only so the portal can 301 it to the current one for one release.
HOSTED_PORTAL_PATH = "/app/iotconnect"
LEGACY_PORTAL_PATH = "/app/connecthq"
IOTCONNECT_SURFACE_BASE_URL = _env("SURFACE_BASE_URL", HOSTED_PORTAL_PATH)
IOTCONNECT_RELEASE_LABEL    = _env("RELEASE_LABEL", "revision 7 candidate")

# The projected row that receives the runtime join (launch, workbenches, health).
# Matched against `initiative_id`, not the domain tag: the domain tag is
# repository content, the initiative id is the identity the two sources are
# joined by (spec §3.2). Default is the §5.3 placeholder.
IOTCONNECT_INITIATIVE_ID   = _env("INITIATIVE_ID", "INIT-2026-0004")

# Stale indicator threshold in days (spec §5.2) — an indicator, not a status.
GUILD_EXPERIMENT_STALE_DAYS = int(os.environ.get("GUILD_EXPERIMENT_STALE_DAYS", "30"))

# Fixed, reviewed relative paths. Not overridable by environment or repository
# content — the surfaces are joined safely to IOTCONNECT_SURFACE_BASE_URL.
IOTCONNECT_SURFACE_PATHS = {
    "launch": "/",
    "admin": "/admin",
    "billing_workbench": "/workbench",
    "swagger": "/docs",
}

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def _is_loopback_origin(value: str) -> bool:
    """True only for a bare http(s) loopback origin with a valid port.

    Parsed with urlsplit rather than matched by prefix, so credentials, a
    path, a query, or a fragment can never ride along on the origin.
    """
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    if parts.path not in ("", "/"):
        return False
    if parts.hostname not in _LOOPBACK_HOSTS:
        return False
    try:
        port = parts.port  # raises ValueError for a non-numeric or out-of-range port
    except ValueError:
        return False
    if port is not None and not (1 <= port <= 65535):
        return False
    # netloc must be exactly host[:port] — no userinfo remnant.
    expected = parts.hostname if port is None else f"{parts.hostname}:{port}"
    return parts.netloc == expected


def _validate_surface_base_url(value: str) -> str:
    """Exactly the hosted portal path, or a loopback origin (spec §4.2).

    An arbitrary relative path is NOT accepted: the allow-list is exact, so
    traversal, scheme-relative paths, query strings, fragments, credentials,
    and invalid ports all fail at startup rather than letting the page send
    the browser somewhere unreviewed.
    """
    candidate = value[:-1] if value.endswith("/") and value != "/" else value
    if candidate == HOSTED_PORTAL_PATH:
        return HOSTED_PORTAL_PATH
    if _is_loopback_origin(candidate):
        return candidate
    raise RuntimeError(
        "IOTCONNECT_SURFACE_BASE_URL must be the hosted portal path "
        f"{HOSTED_PORTAL_PATH!r} or a loopback origin "
        "(http(s)://127.0.0.1[:port] or http(s)://localhost[:port]) — "
        f"got {value!r}."
    )


IOTCONNECT_SURFACE_BASE_URL = _validate_surface_base_url(IOTCONNECT_SURFACE_BASE_URL)


def _derive_health_url() -> str:
    """Health URL for the probe (spec §4.2).

    An explicit IOTCONNECT_HEALTH_URL always wins (the retired
    CONNECTHQ_HEALTH_URL is still accepted for one release). Otherwise: when
    the surface base URL is an absolute loopback origin the demo is the
    standalone Mac stack, so health lives on that same origin; when it is a
    relative portal path the demo is the hosted container behind the proxy, so
    health is derived from IOTCONNECT_BACKEND as it was before.
    """
    explicit = _env("HEALTH_URL")
    if explicit:
        return explicit
    if IOTCONNECT_SURFACE_BASE_URL.startswith("http"):
        return f"{IOTCONNECT_SURFACE_BASE_URL}/api/v1/health"
    return f"{IOTCONNECT_BACKEND}/api/v1/health"


IOTCONNECT_HEALTH_URL = _derive_health_url()


def iotconnect_surfaces() -> dict:
    """The four demo surfaces, built only from configuration (spec §4.2, §6).

    base_url is already validated to a relative portal path or a loopback
    origin; the paths are the fixed table above. Nothing here reads repository
    content, so the projection file can never inject a URL.
    """
    base = IOTCONNECT_SURFACE_BASE_URL.rstrip("/")  # validated; no trailing slash
    surfaces = {}
    for key, path in IOTCONNECT_SURFACE_PATHS.items():
        if not path.startswith("/") or path.startswith("//") or ".." in path:
            raise RuntimeError(f"unsafe IoT Connect surface path for {key!r}: {path!r}")
        surfaces[key] = f"{base}{path}"
    return surfaces


def iotconnect_surface_is_external() -> bool:
    """True when the surfaces are an absolute origin (open in a new tab)."""
    return IOTCONNECT_SURFACE_BASE_URL.startswith("http")

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
