"""
annotations.py — Comment anywhere system
Writes to data/annotations/{domain}/{topic}/{YYYY-MM-DD}.json
Purely additive — no existing modules modified.
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Base path — same pattern as rest of project
BASE_DIR = Path(__file__).parent
ANNOTATIONS_DIR = BASE_DIR / "data" / "annotations"


def _get_annotation_path(domain: str, topic: str = None) -> Path:
    """
    Returns the path for today's annotation file.
    domain: "curator" or "research"
    topic: e.g. "empire-landpower", None → "general"
    """
    if domain == "curator":
        dir_path = ANNOTATIONS_DIR / "curator"
    else:
        topic_slug = topic if topic else "general"
        dir_path = ANNOTATIONS_DIR / "research" / topic_slug

    dir_path.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return dir_path / f"{date_str}.json"


def _load_annotations(path: Path) -> list:
    """Load existing annotations for today, or empty list."""
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _generate_note_id(note: str, timestamp: str) -> str:
    """Short hash ID for the note — same pattern as rest of project."""
    content = f"{note}{timestamp}"
    return hashlib.md5(content.encode()).hexdigest()[:8]


def save_annotation(
    note: str,
    domain: str,           # "curator" or "research"
    page: str,             # "daily", "observe", "sessions", etc.
    topic: str = None,     # research topic slug or None
    ref_type: str = None,  # "article", "finding", "session", None
    ref_id: str = None,    # hash ID of referenced item
    ref_title: str = None, # title truncated to 80 chars
    ref_text: str = None,  # selected text passage
    url: str = None,       # article URL if available
    annotation_type: str = "reaction"  # "reaction", "note", "direction_shift"
) -> dict:
    """
    Save a single annotation. Returns the saved record.
    Deduplication: skips exact (note + ref_id) duplicates.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    note_id = _generate_note_id(note, timestamp)

    record = {
        "note_id": note_id,
        "timestamp": timestamp,
        "domain": domain,
        "page": page,
        "type": annotation_type,
        "note": note.strip(),
        "topic": topic,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "ref_title": ref_title[:80] if ref_title else None,
        "ref_text": ref_text[:500] if ref_text else None,
        "url": url,
        "influenced_sessions": []  # reserved for graph layer
    }

    path = _get_annotation_path(domain, topic)
    existing = _load_annotations(path)

    # Dedup check — same note text + same ref
    for existing_record in existing:
        if (existing_record.get("note") == record["note"] and
                existing_record.get("ref_id") == record["ref_id"]):
            return existing_record  # already saved, return existing

    existing.append(record)

    with open(path, "w") as f:
        json.dump(existing, f, indent=2)

    return record


def get_recent_annotations(
    domain: str,
    topic: str = None,
    limit: int = 10
) -> list:
    """
    Returns most recent N annotations for a domain/topic.
    Reads today's file only for now — expand to multi-day later.
    """
    path = _get_annotation_path(domain, topic)
    annotations = _load_annotations(path)
    # Newest first
    return sorted(annotations, key=lambda x: x["timestamp"], reverse=True)[:limit]
