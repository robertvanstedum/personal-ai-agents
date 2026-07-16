"""
minimoi_portal/config.py — Portal configuration.

Override via environment variables on Mac Mini.
"""

import os

# Backend app URLs (all run locally on Mac Mini)
CURATOR_BACKEND    = os.environ.get("CURATOR_BACKEND",    "http://localhost:8766")
GERMAN_BACKEND     = os.environ.get("GERMAN_BACKEND",     "http://localhost:8767")
PORTUGUESE_BACKEND = os.environ.get("PORTUGUESE_BACKEND", "http://localhost:8770")
COS_BACKEND        = os.environ.get("COS_BACKEND",        "http://localhost:8769")

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
