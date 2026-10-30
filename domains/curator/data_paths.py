"""Curator data-root configuration without filesystem side effects."""
import os
from pathlib import Path


def data_directory(repo_root: Path) -> Path:
    value = os.environ.get("CURATOR_DATA_DIR")
    if value is None:
        return repo_root / "data" / "curator"
    if not value.strip():
        raise ValueError("CURATOR_DATA_DIR must be a nonempty path when set")
    return Path(value)
