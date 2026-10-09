"""The reserve-pages switch (Guild 1.1): retired pages kept in git, not served.

The Workbench "wall", the old Build Queue page and the old Labs page are retained in reserve (git tag
workbench-reserve-2026-10-07; their templates and script are removed from a release image by
scripts/release/exclude_reserve_pages.py). With the switch off, which is the default and always the case in production,
their old addresses redirect to the pages that replaced them and no 1.1 page links to them.
"""
from __future__ import annotations

import os

RESERVE_PAGES_VAR = "MINIMOI_GUILD_RESERVE_PAGES"


def reserve_pages_enabled() -> bool:
    return str(os.environ.get(RESERVE_PAGES_VAR, "") or "").strip().lower() in ("1", "true", "on", "yes")
