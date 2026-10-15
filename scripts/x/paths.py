"""Stable repository and data paths shared by the X bookmark utilities."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIGNALS_FILE = PROJECT_ROOT / "data" / "curator" / "curator_signals.json"
# Defaults to the repo root (dev). Production sets CURATOR_X_PULL_STATE_PATH
# into the mounted data/curator folder (#305). It must be a folder mount, not a
# single-file bind: the cursor is saved by writing a temp file beside it and
# renaming it over the old one.
STATE_FILE = Path(os.environ.get("CURATOR_X_PULL_STATE_PATH") or PROJECT_ROOT / "x_pull_state.json")
