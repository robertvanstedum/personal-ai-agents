"""
minimoi_portal/config.py — Portal configuration.

Override via environment variables on Mac Mini.
"""

import os

# Backend app URLs (all run locally on Mac Mini)
CURATOR_BACKEND    = os.environ.get("CURATOR_BACKEND",    "http://localhost:8766")
GERMAN_BACKEND     = os.environ.get("GERMAN_BACKEND",     "http://localhost:8767")
PORTUGUESE_BACKEND = os.environ.get("PORTUGUESE_BACKEND", "http://localhost:8770")

# Flask session secret — MUST be set in environment on Mac Mini
# Generate: python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "dev-only-change-in-production")

# Session lifetime for permanent users (Owner/Family)
SESSION_LIFETIME_DAYS = 30

# Portal runs on this port (Cloudflare Tunnel points here)
PORT = int(os.environ.get("PORTAL_PORT", 5001))
