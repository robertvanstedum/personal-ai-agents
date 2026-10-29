"""Shared date and sequence rules for German session filenames.

This selects a candidate name from existing entries; it does not reserve the
name or serialize concurrent writers. Callers own directories and publication.
"""
from datetime import datetime
from pathlib import Path
import re


def next_session_id(date_str: str, sessions_dir: Path) -> str:
    """Return date_NNN using the highest numeric suffix, with three digits minimum."""
    message = "Session date must be a real calendar date in YYYY-MM-DD format."
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_str):
        raise ValueError(message)
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError(message) from error

    pattern = re.compile(re.escape(date_str) + r"_([0-9]+)")
    highest = 0
    for path in sessions_dir.glob(f"{date_str}_*.json"):
        match = pattern.fullmatch(path.stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{date_str}_{highest + 1:03d}"
