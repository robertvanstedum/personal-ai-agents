"""
source_utils.py — Shared source quality utilities for research sessions.
Phase 2: Novelty scoring only.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEN_URLS_DIR = ROOT / 'data' / 'seen_urls'


def load_seen_urls(topic: str) -> set[str]:
    """Load the set of already-seen URLs for a topic."""
    SEEN_URLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SEEN_URLS_DIR / f'{topic}.json'
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data.get('urls', []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen_urls(topic: str, new_urls: list[str]) -> None:
    """Append new URLs to the seen set for a topic."""
    SEEN_URLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SEEN_URLS_DIR / f'{topic}.json'
    existing = load_seen_urls(topic)
    merged = sorted(existing | set(new_urls))
    path.write_text(json.dumps({'topic': topic, 'urls': merged}, indent=2))


def apply_novelty_score(
    candidates: list[dict],
    seen_urls: set[str],
    discount: float = 0.3,
) -> list[dict]:
    """
    Apply novelty discount to already-seen URLs.

    Multiplies `score` by (1 - discount) for seen URLs.
    Adds `novelty_flag: True/False` for transparency in logs.
    """
    for c in candidates:
        url = c.get('url', '')
        if url and url in seen_urls:
            c['score'] = c.get('score', 0) * (1 - discount)
            c['novelty_flag'] = False
        else:
            c['novelty_flag'] = True
    return candidates
