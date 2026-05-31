"""
curator_research.py — Curator research-area data layer.

Manages Sources, Topics (Step 2), and Groups (Step 4).
This module is pure data: load, save, query. No Flask, no LLM calls.

Data files (all under _NewDomains/research-intelligence/data/):
  sources/sources.json   — Source records (append-only, one flat array)
  tag_aliases.json       — Hand-edited alias map: {"prc": "china", "quad-security": "quad"}
  threads/{slug}/thread.json  — Topic records (existing, extended in Step 2)

Governing constraint: spend follows attention.
Nothing in this module triggers an AI call or background process.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO_ROOT    = Path(__file__).parent
_RESEARCH_DIR = _REPO_ROOT / "_NewDomains" / "research-intelligence" / "data"
_SOURCES_FILE = _RESEARCH_DIR / "sources" / "sources.json"
_ALIASES_FILE = _RESEARCH_DIR / "tag_aliases.json"

# ── Schema constants ──────────────────────────────────────────────────────────
SOURCE_TYPES      = {"article", "post", "paper", "book"}
SOURCE_ORIGINS    = {"curator-found", "session-find", "added-by-robert"}
COST_TO_ACT       = {"free", "dive", "book"}
DATE_PRECISIONS   = {"day", "month", "year", "unknown"}

SCHEMA_VERSION    = "1.0"


# ── Internal I/O ──────────────────────────────────────────────────────────────

def _load_sources() -> list[dict]:
    """Load all Source records. Returns empty list if file missing or invalid."""
    try:
        return json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_sources(sources: list[dict]) -> None:
    """Write source list back to disk."""
    _SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SOURCES_FILE.write_text(
        json.dumps(sources, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_tag_aliases() -> dict[str, str]:
    """
    Load tag alias map from tag_aliases.json.
    Format: {"alias": "canonical", ...}
    Robert hand-edits this file. No auto-merge, no inference.
    """
    try:
        return json.loads(_ALIASES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ── ID generation ─────────────────────────────────────────────────────────────

def _source_next_id(sources: list[dict]) -> str:
    """
    Return next Source ID in format src_YYYYMMDD_NNN.
    Increments per-day across all existing IDs for that date.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"src_{today}_"
    existing = [
        int(s["id"][len(prefix):])
        for s in sources
        if s.get("id", "").startswith(prefix) and s["id"][len(prefix):].isdigit()
    ]
    nxt = max(existing, default=0) + 1
    return f"{prefix}{nxt:03d}"


# ── Tag resolution ────────────────────────────────────────────────────────────

def resolve_tags(tags: list[str]) -> list[str]:
    """
    Resolve tags through alias map at read-time. Deterministic file lookup only.
    e.g. ["prc", "china-rise"] → ["china", "china-rise"] if aliases={"prc":"china"}
    Tags with no alias entry pass through unchanged.
    Deduplicates after resolution, preserves order of first occurrence.
    """
    aliases = _load_tag_aliases()
    seen: set[str] = set()
    resolved: list[str] = []
    for tag in tags:
        canonical = aliases.get(tag.strip().lower(), tag.strip().lower())
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)
    return resolved


# ── Source CRUD ───────────────────────────────────────────────────────────────

def add_source(
    *,
    type: str,
    title: str,
    url: Optional[str] = None,
    reference: Optional[str] = None,
    origin: str,
    tags: Optional[list[str]] = None,
    note: Optional[str] = None,
    topics: Optional[list[str]] = None,
    cost_to_act: str = "free",
    origin_session: Optional[str] = None,
    date: Optional[str] = None,
    date_precision: str = "unknown",
    saved_at: Optional[str] = None,
) -> dict:
    """
    Create and persist a new Source record. Returns the saved record.

    Tags are stored exactly as entered — alias resolution is read-time only.
    Either url or reference must be provided (not both required, but at least one).
    """
    if type not in SOURCE_TYPES:
        raise ValueError(f"type must be one of {SOURCE_TYPES}, got {type!r}")
    if origin not in SOURCE_ORIGINS:
        raise ValueError(f"origin must be one of {SOURCE_ORIGINS}, got {origin!r}")
    if cost_to_act not in COST_TO_ACT:
        raise ValueError(f"cost_to_act must be one of {COST_TO_ACT}, got {cost_to_act!r}")
    if date_precision not in DATE_PRECISIONS:
        raise ValueError(f"date_precision must be one of {DATE_PRECISIONS}, got {date_precision!r}")
    if not url and not reference:
        raise ValueError("At least one of url or reference must be provided")

    sources = _load_sources()
    source_id = _source_next_id(sources)

    record: dict = {
        "id":              source_id,
        "type":            type,
        "title":           title,
        "url":             url,
        "reference":       reference,
        "origin":          origin,
        "origin_session":  origin_session,
        "date":            date,
        "date_precision":  date_precision,
        "tags":            list(tags) if tags else [],
        "note":            note,
        "cost_to_act":     cost_to_act,
        "topics":          list(topics) if topics else [],
        "groups":          [],
        "saved_at":        saved_at or datetime.now(timezone.utc).isoformat(),
        "schema_version":  SCHEMA_VERSION,
    }

    sources.append(record)
    _save_sources(sources)
    return record


def get_sources(
    *,
    tags: Optional[list[str]] = None,
    topics: Optional[list[str]] = None,
    groups: Optional[list[str]] = None,
    source_type: Optional[str] = None,
    resolve: bool = True,
) -> list[dict]:
    """
    Return Sources matching all provided filters (AND logic).
    If resolve=True, tag matching uses alias-resolved tags.

    tags   — Source must have at least one matching tag
    topics — Source must be linked to at least one matching topic
    groups — Source must be linked to at least one matching group
    """
    sources = _load_sources()

    if tags:
        filter_tags = set(resolve_tags(tags) if resolve else tags)
        sources = [
            s for s in sources
            if filter_tags & set(resolve_tags(s.get("tags", [])) if resolve else s.get("tags", []))
        ]

    if topics:
        filter_topics = set(topics)
        sources = [s for s in sources if filter_topics & set(s.get("topics", []))]

    if groups:
        filter_groups = set(groups)
        sources = [s for s in sources if filter_groups & set(s.get("groups", []))]

    if source_type:
        sources = [s for s in sources if s.get("type") == source_type]

    return sources


def get_source_by_id(source_id: str) -> Optional[dict]:
    """Return a single Source by ID, or None if not found."""
    for s in _load_sources():
        if s.get("id") == source_id:
            return s
    return None


def update_source_tags(source_id: str, tags: list[str]) -> Optional[dict]:
    """
    Replace the tag list on an existing Source. Stored as entered.
    Returns updated record or None if not found.
    """
    sources = _load_sources()
    for s in sources:
        if s.get("id") == source_id:
            s["tags"] = list(tags)
            _save_sources(sources)
            return s
    return None


def attach_source_to_topic(source_id: str, topic_slug: str) -> Optional[dict]:
    """Add a Topic ID to a Source's topics list (idempotent). Returns updated record."""
    sources = _load_sources()
    for s in sources:
        if s.get("id") == source_id:
            if topic_slug not in s.get("topics", []):
                s.setdefault("topics", []).append(topic_slug)
                _save_sources(sources)
            return s
    return None


def attach_source_to_group(source_id: str, group_id: str) -> Optional[dict]:
    """Add a Group ID to a Source's groups list (idempotent). Returns updated record."""
    sources = _load_sources()
    for s in sources:
        if s.get("id") == source_id:
            if group_id not in s.get("groups", []):
                s.setdefault("groups", []).append(group_id)
                _save_sources(sources)
            return s
    return None


# ── Migration: article_signals → Sources ──────────────────────────────────────

def migrate_article_signals(dry_run: bool = True) -> list[dict]:
    """
    One-time migration: promote existing article_signals.json save-signals
    to first-class Source records.

    Only migrates records with signal == "save".
    Skips any URL already present in sources.json.
    Does NOT delete article_signals.json — it's kept as read-only history.

    dry_run=True (default): returns what would be created, writes nothing.
    dry_run=False: creates Source records and saves.
    """
    signals_file = _RESEARCH_DIR / "feedback" / "article_signals.json"
    if not signals_file.exists():
        print("No article_signals.json found.")
        return []

    try:
        signals = json.loads(signals_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("Could not parse article_signals.json.")
        return []

    existing_sources = _load_sources()
    existing_urls = {s.get("url") for s in existing_sources if s.get("url")}

    to_create: list[dict] = []
    for sig in signals:
        if sig.get("signal") != "save":
            continue
        url = sig.get("url", "")
        if url in existing_urls:
            print(f"  ⏭  Already exists: {sig.get('title', url)[:60]}")
            continue

        # Infer cost_to_act from URL: book references are costly, articles are free
        cost = "free"

        record = {
            "id":             None,          # assigned on write
            "type":           "article",
            "title":          sig.get("title", ""),
            "url":            url,
            "reference":      None,
            "origin":         "session-find",
            "origin_session": sig.get("session_id"),
            "date":           sig.get("timestamp", "")[:10] or None,
            "date_precision": "day" if sig.get("timestamp") else "unknown",
            "tags":           [],             # Robert tags after migration
            "note":           sig.get("note"),
            "cost_to_act":    cost,
            "topics":         [],             # Robert links after migration
            "groups":         [],
            "saved_at":       sig.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "schema_version": SCHEMA_VERSION,
        }
        to_create.append(record)

    if dry_run:
        print(f"\n{'DRY RUN' :─^60}")
        print(f"Would create {len(to_create)} Sources from article_signals.json:")
        for r in to_create:
            print(f"  [{r['type']}] {r['title'][:65]}")
        print(f"{'─'*60}")
        return to_create

    # Write
    for r in to_create:
        existing_sources = _load_sources()            # re-load each time for correct ID seq
        r["id"] = _source_next_id(existing_sources)
        existing_sources.append(r)
        _save_sources(existing_sources)
        print(f"  ✅ Created {r['id']}: {r['title'][:60]}")

    return to_create


# ── Quick summary ─────────────────────────────────────────────────────────────

def sources_summary() -> dict:
    """Return a summary dict: counts by type, origin, cost_to_act."""
    sources = _load_sources()
    summary: dict = {
        "total": len(sources),
        "by_type":        {},
        "by_origin":      {},
        "by_cost_to_act": {},
        "tag_count":      len({t for s in sources for t in s.get("tags", [])}),
    }
    for s in sources:
        summary["by_type"][s.get("type", "?")] = summary["by_type"].get(s.get("type", "?"), 0) + 1
        summary["by_origin"][s.get("origin", "?")] = summary["by_origin"].get(s.get("origin", "?"), 0) + 1
        summary["by_cost_to_act"][s.get("cost_to_act", "?")] = summary["by_cost_to_act"].get(s.get("cost_to_act", "?"), 0) + 1
    return summary
