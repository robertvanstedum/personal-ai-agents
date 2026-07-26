"""Stable repository and data paths shared by the X bookmark utilities."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIGNALS_FILE = PROJECT_ROOT / "data" / "curator" / "curator_signals.json"
# Preserve the existing runtime location in this structural slice. Moving the
# state file into mounted storage is a separate persistence migration.
STATE_FILE = PROJECT_ROOT / "x_pull_state.json"
