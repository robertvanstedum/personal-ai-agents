"""
curator_config.py — Shared constants for the OpenClaw curator system.

Single source of truth for domain names and active domain.
Imported by x_import_archive.py (write) and curator_rss_v2.py (read)
so the domain label string never drifts between files.

When adding a new knowledge domain:
  1. Add its name to DOMAINS
  2. Add its X folder ID(s) to KNOWN_FOLDERS
  3. Update ACTIVE_DOMAIN if switching the daily briefing focus
  4. Build the domain-specific front-end + scheduler independently
"""

# ── Canonical knowledge domain names ──────────────────────────────────────
# These are the bucket names used in domain_signals in curator_preferences.json.
# Each domain is an independent product: own front-end, own schedule, own signals.

DOMAINS = [
    "Finance and Geopolitics",   # Active — morning briefing (investing decisions)
    "Health and Science",        # Future — different cadence
    "Tech and AI",               # Future
    "Language and Culture",      # Future
    "Career and Commercial",     # Future
    "Other",                     # Catch-all for unmapped folders
]

# ── Active domain for the current briefing job ────────────────────────────
# This is what curator_rss_v2.py reads from domain_signals.
# Change this when a second domain front-end is operational.
# Eventually becomes a CLI flag so domains run independently.

ACTIVE_DOMAIN = "Finance and Geopolitics"

# ── X bookmark folder ID → canonical domain name ─────────────────────────
# Discovered via: python3 x_adapter.py --list-folders
# Maps X's folder names (which the user controls) → our canonical domain labels.
# Unknown folder IDs fall back to ACTIVE_DOMAIN in x_import_archive.py.
#
# Add new folders here as you create them in X.
# Format: 'X_folder_id': 'Canonical Domain Name'

KNOWN_FOLDERS = {
    '1926124453714387081': 'Finance and Geopolitics',   # X folder: "Finance and geopolitics"
    '1881118951536538102': 'Language and Culture',      # X folder: "Learning 2025"
    '1926123095779078526': 'Health and Science',        # X folder: "Life and health"
    '1967313159158640645': 'Tech and AI',               # X folder: "Tech"
    '1992980059464876233': 'Career and Commercial',     # X folder: "Modular Construction"
}
