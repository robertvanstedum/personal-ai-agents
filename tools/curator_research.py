"""
tools/curator_research.py — Curator research-area data layer + CLI.

Manages Sources, Topics, and Groups for the Curator research gathering tier.
This module is pure data: load, save, query. No Flask, no LLM calls.

Usage (CLI):
  python3 tools/curator_research.py status
  python3 tools/curator_research.py topics --status active-pull
  python3 tools/curator_research.py pull narrow <slug>
  python3 tools/curator_research.py --help

Data files (all under _NewDomains/research-intelligence/data/):
  sources/sources.json   — Source records (append-only, one flat array)
  tag_aliases.json       — Hand-edited alias map: {"prc": "china", "quad-security": "quad"}
  threads/{slug}/thread.json  — Topic records
  groups/groups.json     — Group records

Governing constraint: spend follows attention.
Nothing in this module triggers an AI call or background process.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from datetime import timedelta
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO_ROOT    = Path(__file__).parent.parent   # tools/ is one level below repo root
_RESEARCH_DIR = _REPO_ROOT / "_NewDomains" / "research-intelligence" / "data"
_SOURCES_FILE = _RESEARCH_DIR / "sources" / "sources.json"
_ALIASES_FILE = _RESEARCH_DIR / "tag_aliases.json"
_TOPICS_DIR   = _RESEARCH_DIR / "threads"   # reuses existing thread dir

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


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Topic state machine
# ═══════════════════════════════════════════════════════════════════════════════
"""
Topic schema (extends existing thread.json, schema_version 2.0):

  slug          — URL-safe identifier, directory name under threads/
  status        — dormant | active-pull | one-shot | paused | closed
  tags          — list of strings, stored as entered (alias-resolved at read time)
  duration_days — engagement gate duration (default 14); used when activating
  activated_at  — ISO timestamp of last move to active-pull (None if never)
  expires       — YYYY-MM-DD computed from activated_at + duration_days (None if dormant)
  paused_at     — ISO timestamp when paused (None if not paused)
  remaining_days — days left at pause time; used to recompute expires on resume
  state_history  — list of {from, to, at, by, note} — full audit trail

Inherits from thread.json (preserved, not overwritten):
  motivation, prior_belief, session_count, links_to, links_from, wrap_up, etc.
"""

# ── Topic constants ───────────────────────────────────────────────────────────
TOPIC_STATES = {"dormant", "active-pull", "one-shot", "paused", "closed"}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "dormant":      {"active-pull", "one-shot", "closed"},
    "active-pull":  {"dormant", "paused", "closed"},
    "one-shot":     {"closed"},
    "paused":       {"active-pull", "closed"},
    "closed":       set(),   # terminal — no exits
}

DEFAULT_DURATION_DAYS = 14
TOPIC_SCHEMA_VERSION  = "2.0"


# ── Topic I/O ─────────────────────────────────────────────────────────────────

def _topic_path(slug: str) -> Path:
    return _TOPICS_DIR / slug / "thread.json"


def _load_topic(slug: str) -> Optional[dict]:
    p = _topic_path(slug)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_topic(topic: dict) -> None:
    p = _topic_path(topic["slug"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(topic, indent=2, ensure_ascii=False), encoding="utf-8")


def _all_topic_slugs() -> list[str]:
    if not _TOPICS_DIR.exists():
        return []
    return [d.name for d in sorted(_TOPICS_DIR.iterdir()) if (d / "thread.json").exists()]


def _all_topics() -> list[dict]:
    topics = []
    for slug in _all_topic_slugs():
        t = _load_topic(slug)
        if t:
            topics.append(t)
    return topics


def _compute_expires(activated_at: str, duration_days: int) -> str:
    """Return YYYY-MM-DD expiry date from ISO activated_at + duration_days."""
    dt = datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
    return (dt + timedelta(days=duration_days)).strftime("%Y-%m-%d")


def _append_state_history(topic: dict, from_state: Optional[str], to_state: str,
                           by: str = "robert", note: str = "") -> None:
    topic.setdefault("state_history", []).append({
        "from": from_state,
        "to":   to_state,
        "at":   datetime.now(timezone.utc).isoformat(),
        "by":   by,
        "note": note,
    })


# ── Topic creation ────────────────────────────────────────────────────────────

def create_topic(
    slug: str,
    motivation: str,
    *,
    tags: Optional[list[str]] = None,
    status: str = "dormant",
    duration_days: int = DEFAULT_DURATION_DAYS,
    note: str = "",
) -> dict:
    """
    Create a new Topic (writes thread.json under threads/{slug}/).
    Raises if topic already exists or slug is invalid.
    status must be dormant or one-shot at creation.
    """
    if status not in {"dormant", "one-shot"}:
        raise ValueError(f"New Topics must start as dormant or one-shot, got {status!r}")
    if _topic_path(slug).exists():
        raise FileExistsError(f"Topic already exists: {slug}")

    now_iso = datetime.now(timezone.utc).isoformat()
    topic: dict = {
        "slug":            slug,
        "topic":           slug,           # legacy field — kept for backward compat
        "status":          status,
        "tags":            list(tags) if tags else [],
        "motivation":      motivation,
        "prior_belief":    "",
        "duration_days":   duration_days,
        "activated_at":    None,
        "expires":         None,
        "paused_at":       None,
        "remaining_days":  None,
        "session_count":   0,
        "links_to":        [],
        "links_from":      [],
        "wrap_up":         None,
        "created_at":      now_iso,
        "state_history":   [],
        "schema_version":  TOPIC_SCHEMA_VERSION,
    }
    _append_state_history(topic, None, status, note=note or "created")
    _save_topic(topic)
    return topic


# ── State transitions ─────────────────────────────────────────────────────────

def _transition(slug: str, to_state: str, by: str = "robert", note: str = "") -> dict:
    """
    Generic state transition with validation. Returns updated Topic.
    Raises ValueError on invalid transition or unknown slug.
    """
    topic = _load_topic(slug)
    if topic is None:
        raise ValueError(f"Topic not found: {slug!r}")

    from_state = topic.get("status")
    if to_state not in VALID_TRANSITIONS.get(from_state, set()):
        raise ValueError(
            f"Invalid transition {from_state!r} → {to_state!r} for topic {slug!r}"
        )

    _append_state_history(topic, from_state, to_state, by=by, note=note)
    topic["status"] = to_state
    _save_topic(topic)
    return topic


def activate_topic(
    slug: str,
    *,
    duration_days: Optional[int] = None,
    note: str = "",
) -> dict:
    """
    Move Topic to active-pull. Sets activated_at, computes expires.
    Works from dormant or paused. For resume from paused, uses remaining_days
    if duration_days not supplied.
    """
    topic = _load_topic(slug)
    if topic is None:
        raise ValueError(f"Topic not found: {slug!r}")

    from_state = topic.get("status")
    if from_state not in {"dormant", "paused"}:
        raise ValueError(f"Can only activate from dormant or paused, not {from_state!r}")

    # Duration: explicit arg > remaining days from pause > topic default > global default
    if duration_days is not None:
        days = duration_days
    elif from_state == "paused" and topic.get("remaining_days"):
        days = topic["remaining_days"]
    else:
        days = topic.get("duration_days") or DEFAULT_DURATION_DAYS

    now_iso = datetime.now(timezone.utc).isoformat()
    topic["activated_at"]   = now_iso
    topic["expires"]        = _compute_expires(now_iso, days)
    topic["duration_days"]  = days
    topic["paused_at"]      = None
    topic["remaining_days"] = None

    _append_state_history(topic, from_state, "active-pull", note=note or f"activated, expires {topic['expires']}")
    topic["status"] = "active-pull"
    _save_topic(topic)
    return topic


def pause_topic(slug: str, *, note: str = "") -> dict:
    """
    Pause an active-pull Topic. Saves remaining_days so resume can restore the gate.
    """
    topic = _load_topic(slug)
    if topic is None:
        raise ValueError(f"Topic not found: {slug!r}")
    if topic.get("status") != "active-pull":
        raise ValueError(f"Can only pause active-pull Topics, not {topic.get('status')!r}")

    now = datetime.now(timezone.utc)
    topic["paused_at"] = now.isoformat()

    # Compute remaining days from expiry
    expires_str = topic.get("expires")
    if expires_str:
        expires_dt = datetime.strptime(expires_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        remaining = max(0, (expires_dt - now).days)
    else:
        remaining = topic.get("duration_days") or DEFAULT_DURATION_DAYS
    topic["remaining_days"] = remaining

    _append_state_history(topic, "active-pull", "paused", note=note or f"{remaining}d remaining")
    topic["status"] = "paused"
    _save_topic(topic)
    return topic


def close_topic(slug: str, *, note: str = "") -> dict:
    """Move a Topic to closed (terminal). Accepts any non-closed state."""
    topic = _load_topic(slug)
    if topic is None:
        raise ValueError(f"Topic not found: {slug!r}")
    if topic.get("status") == "closed":
        return topic   # idempotent

    _append_state_history(topic, topic.get("status"), "closed", note=note or "closed")
    topic["status"] = "closed"
    _save_topic(topic)
    return topic


def downgrade_topic(slug: str, *, note: str = "") -> dict:
    """Move an active-pull Topic back to dormant (manual downgrade, not auto-stop)."""
    return _transition(slug, "dormant", note=note or "manually downgraded to dormant")


# ── Engagement gate ───────────────────────────────────────────────────────────

def auto_stop_check() -> list[dict]:
    """
    Scan all active-pull Topics. Move any with expires < today to dormant.
    Returns list of Topics that were auto-stopped.

    Call this at the start of each curator run (no AI cost — pure date math).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stopped: list[dict] = []

    for slug in _all_topic_slugs():
        topic = _load_topic(slug)
        if not topic:
            continue
        if topic.get("status") != "active-pull":
            continue
        expires = topic.get("expires")
        if expires and expires < today:
            _append_state_history(
                topic, "active-pull", "dormant",
                by="system",
                note=f"auto-stopped: expired {expires}",
            )
            topic["status"] = "dormant"
            _save_topic(topic)
            stopped.append(topic)
            print(f"  ⏹  Auto-stopped Topic: {slug} (expired {expires})")

    return stopped


# ── Queries ───────────────────────────────────────────────────────────────────

def get_topics(
    *,
    status: Optional[str] = None,
    tags: Optional[list[str]] = None,
    resolve: bool = True,
) -> list[dict]:
    """
    Return Topics matching filters (AND logic).
    status — exact match (dormant | active-pull | one-shot | paused | closed)
    tags   — at least one tag matches (alias-resolved if resolve=True)
    """
    topics = _all_topics()

    if status:
        topics = [t for t in topics if t.get("status") == status]

    if tags:
        filter_tags = set(resolve_tags(tags) if resolve else tags)
        topics = [
            t for t in topics
            if filter_tags & set(resolve_tags(t.get("tags", [])) if resolve else t.get("tags", []))
        ]

    return topics


def get_topic(slug: str) -> Optional[dict]:
    """Return a single Topic by slug, or None."""
    return _load_topic(slug)


def update_topic_tags(slug: str, tags: list[str]) -> Optional[dict]:
    """Replace tag list on a Topic. Stored as entered."""
    topic = _load_topic(slug)
    if topic is None:
        return None
    topic["tags"] = list(tags)
    _save_topic(topic)
    return topic


# ── Migration: thread.json v1 → Topic v2 ─────────────────────────────────────

def migrate_threads_to_topics(dry_run: bool = True) -> list[dict]:
    """
    Migrate existing thread.json files (schema_version 1.0) to Topic v2.

    Mapping:
      status "active"  → "active-pull" (engagement gate fields set to None if missing)
      status "closed"  → "closed"
      status anything else → "dormant"
      retired = True   → "closed"

    Adds: slug, tags, activated_at, paused_at, remaining_days, state_history,
          schema_version 2.0. Preserves all existing fields unchanged.

    dry_run=True: prints plan, writes nothing.
    dry_run=False: updates files in place.
    """
    slugs = _all_topic_slugs()
    results: list[dict] = []

    print(f"\n{'DRY RUN — migrate threads' if dry_run else 'Migrating threads':─^60}")
    for slug in slugs:
        topic = _load_topic(slug)
        if not topic:
            continue

        if topic.get("schema_version") == TOPIC_SCHEMA_VERSION:
            print(f"  ⏭  Already v2.0: {slug}")
            continue

        old_status = topic.get("status", "")
        retired = topic.get("retired", False)

        if retired or old_status == "closed":
            new_status = "closed"
        elif old_status == "active":
            new_status = "active-pull"
        else:
            new_status = "dormant"

        print(f"  {'→' if not dry_run else '?'} {slug}: {old_status!r} → {new_status!r}")

        if not dry_run:
            # Preserve all existing fields, add new ones
            topic["slug"]           = slug
            topic["tags"]           = topic.get("tags", [])
            topic["activated_at"]   = topic.get("opened") if new_status == "active-pull" else None
            topic["paused_at"]      = None
            topic["remaining_days"] = None
            topic["state_history"]  = [{
                "from": None,
                "to":   new_status,
                "at":   datetime.now(timezone.utc).isoformat(),
                "by":   "migration",
                "note": f"migrated from thread schema v1 (was: {old_status!r})",
            }]
            topic["status"]         = new_status
            topic["schema_version"] = TOPIC_SCHEMA_VERSION
            _save_topic(topic)

        results.append(topic)

    print("─" * 60)
    return results


# ── Topic summary ─────────────────────────────────────────────────────────────

def topics_summary() -> dict:
    """Return counts by status, plus list of active Topic slugs with expiry."""
    topics = _all_topics()
    by_status: dict[str, int] = {}
    active_list = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for t in topics:
        st = t.get("status", "?")
        by_status[st] = by_status.get(st, 0) + 1
        if st == "active-pull":
            expires = t.get("expires", "no expiry")
            expired = expires != "no expiry" and expires < today
            active_list.append({
                "slug":    t.get("slug", t.get("topic", "?")),
                "expires": expires,
                "expired": expired,
                "tags":    t.get("tags", []),
            })

    return {
        "total":      len(topics),
        "by_status":  by_status,
        "active":     active_list,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Promotion-by-tag flow
# ═══════════════════════════════════════════════════════════════════════════════
"""
Three entry paths into the Source record:

  promote_feed_article()   — daily feed article (curator_latest.json) → Source
  promote_session_find()   — research session find (URL + title) → Source
  promote_manual()         — Robert adds directly (book, paper, link) → Source

All three call add_source() internally and share dedup logic (URL match).
cost_to_act is inferred or supplied:
  free  — web article/post readable without extra cost
  dive  — needs a Scan/Dive session to get value
  book  — physical or paid source, requires conscious acquisition step

suggest_topic_links() — read-only tag-overlap suggestion (no auto-link).
Robert is always the gate; nothing links automatically.
"""

_CURATOR_LATEST  = _REPO_ROOT / "curator_latest.json"
_GROUPS_FILE     = _RESEARCH_DIR / "groups" / "groups.json"
_AGENT_CONFIG    = _REPO_ROOT / "_NewDomains" / "research-intelligence" / "agent" / "config.json"


def _load_feed() -> list[dict]:
    """Load today's scored feed articles. Returns empty list if unavailable."""
    try:
        return json.loads(_CURATOR_LATEST.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _url_already_saved(url: str) -> Optional[dict]:
    """Return existing Source if this URL is already in sources.json, else None."""
    if not url:
        return None
    for s in _load_sources():
        if s.get("url") == url:
            return s
    return None


def _parse_feed_date(published: str) -> tuple[str, str]:
    """
    Parse a feed article's published string to (date, precision).
    Handles ISO strings and stringified datetime objects.
    Returns ('', 'unknown') on failure.
    """
    if not published:
        return ("", "unknown")
    try:
        # Handle 'YYYY-MM-DD HH:MM:SS+HH:MM' and similar
        dt = datetime.fromisoformat(str(published).replace("Z", "+00:00").split(".")[0])
        return (dt.strftime("%Y-%m-%d"), "day")
    except (ValueError, TypeError):
        return ("", "unknown")


# ── Entry path 1: daily feed article ─────────────────────────────────────────

def promote_feed_article(
    hash_id: str,
    tags: list[str],
    *,
    note: Optional[str] = None,
    topics: Optional[list[str]] = None,
) -> dict:
    """
    Promote a daily-feed article to a first-class Source.

    Looks up the article by hash_id in curator_latest.json.
    Deduplicates by URL — returns existing Source if already saved.
    cost_to_act is always 'free' (feed articles are web-readable).
    Robert supplies tags; topics are optional explicit links.

    Raises ValueError if hash_id not found in today's feed.
    """
    feed = _load_feed()
    article = next((a for a in feed if a.get("hash_id") == hash_id), None)
    if article is None:
        raise ValueError(f"hash_id {hash_id!r} not found in curator_latest.json")

    url = article.get("link", "")
    existing = _url_already_saved(url)
    if existing:
        print(f"  ⏭  Already saved: {existing['id']} — {existing['title'][:55]}")
        return existing

    date, precision = _parse_feed_date(article.get("published", ""))

    return add_source(
        type          = "article",
        title         = article.get("title", ""),
        url           = url,
        origin        = "curator-found",
        tags          = tags,
        note          = note,
        topics        = topics or [],
        cost_to_act   = "free",
        date          = date,
        date_precision= precision,
    )


# ── Entry path 2: research session find ──────────────────────────────────────

def promote_session_find(
    url: str,
    title: str,
    tags: list[str],
    *,
    cost_to_act: str = "free",
    note: Optional[str] = None,
    topics: Optional[list[str]] = None,
    origin_session: Optional[str] = None,
    date: Optional[str] = None,
    date_precision: str = "unknown",
    source_type: str = "article",
) -> dict:
    """
    Promote a research-session find (Scan or Dive appendix entry) to a Source.

    cost_to_act:
      'free' — web article/post, directly readable
      'dive' — needs a Scan/Dive session to extract value (e.g. dense academic paper)
      'book' — physical or paid, requires acquisition step

    Deduplicates by URL. Robert supplies tags and topics.
    """
    if cost_to_act not in COST_TO_ACT:
        raise ValueError(f"cost_to_act must be one of {COST_TO_ACT}")

    existing = _url_already_saved(url)
    if existing:
        print(f"  ⏭  Already saved: {existing['id']} — {existing['title'][:55]}")
        return existing

    return add_source(
        type           = source_type,
        title          = title,
        url            = url,
        origin         = "session-find",
        origin_session = origin_session,
        tags           = tags,
        note           = note,
        topics         = topics or [],
        cost_to_act    = cost_to_act,
        date           = date,
        date_precision = date_precision,
    )


# ── Entry path 3: manual add ─────────────────────────────────────────────────

def promote_manual(
    source_type: str,
    title: str,
    tags: list[str],
    *,
    url: Optional[str] = None,
    reference: Optional[str] = None,
    cost_to_act: Optional[str] = None,
    note: Optional[str] = None,
    topics: Optional[list[str]] = None,
    date: Optional[str] = None,
    date_precision: str = "unknown",
) -> dict:
    """
    Robert adds a Source directly — book, paper, article found outside the feed.

    cost_to_act defaults:
      book  → 'book'
      paper → 'dive'
      article/post → 'free'

    url or reference must be provided (not both required; books often have reference only).
    Deduplicates by URL when url is present.
    """
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {SOURCE_TYPES}")

    if url:
        existing = _url_already_saved(url)
        if existing:
            print(f"  ⏭  Already saved: {existing['id']} — {existing['title'][:55]}")
            return existing

    # Infer cost_to_act if not supplied
    if cost_to_act is None:
        cost_to_act = {
            "book":    "book",
            "paper":   "dive",
            "article": "free",
            "post":    "free",
        }.get(source_type, "free")

    return add_source(
        type           = source_type,
        title          = title,
        url            = url,
        reference      = reference,
        origin         = "added-by-robert",
        tags           = tags,
        note           = note,
        topics         = topics or [],
        cost_to_act    = cost_to_act,
        date           = date,
        date_precision = date_precision,
    )


# ── Tag-overlap suggestion (read-only, no auto-link) ─────────────────────────

def suggest_topic_links(source: dict, resolve: bool = True) -> list[dict]:
    """
    Return Topics whose tags overlap with the Source's tags.
    Read-only — does not modify anything. Robert decides whether to link.

    Returns list of {slug, status, overlapping_tags} sorted by overlap count desc.
    """
    source_tags = set(resolve_tags(source.get("tags", [])) if resolve else source.get("tags", []))
    if not source_tags:
        return []

    suggestions = []
    for topic in _all_topics():
        topic_tags = set(resolve_tags(topic.get("tags", [])) if resolve else topic.get("tags", []))
        overlap = source_tags & topic_tags
        if overlap:
            suggestions.append({
                "slug":            topic.get("slug", topic.get("topic", "")),
                "status":          topic.get("status", ""),
                "overlapping_tags": sorted(overlap),
                "overlap_count":   len(overlap),
            })

    return sorted(suggestions, key=lambda x: x["overlap_count"], reverse=True)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Group primitive + pull scoping
# ═══════════════════════════════════════════════════════════════════════════════
"""
Group schema:
  id             — grp_YYYYMMDD_NNN
  name           — optional human label (a Group with no Leaning = what concept calls a Theme)
  member_tags    — list of tag strings (stored as entered, alias-resolved at read time)
  member_topics  — list of Topic slugs (explicit membership)
  leaning        — None here; Leaning attaches in a later PLAN
  note           — optional
  created_at     — ISO timestamp
  schema_version — "1.0"

Storage: _NewDomains/research-intelligence/data/groups/groups.json (flat array)

Pull scoping:
  narrow_pull_context(slug)       — one Topic's queries + tags + motivation
  contextual_pull_context(group_id) — merged across all member Topics
Both return a plain dict the session runner can consume directly.
"""

GROUP_SCHEMA_VERSION = "1.0"


# ── Group I/O ─────────────────────────────────────────────────────────────────

def _load_groups() -> list[dict]:
    try:
        return json.loads(_GROUPS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_groups(groups: list[dict]) -> None:
    _GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GROUPS_FILE.write_text(
        json.dumps(groups, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _group_next_id(groups: list[dict]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"grp_{today}_"
    existing = [
        int(g["id"][len(prefix):])
        for g in groups
        if g.get("id", "").startswith(prefix) and g["id"][len(prefix):].isdigit()
    ]
    return f"{prefix}{max(existing, default=0) + 1:03d}"


def _load_session_searches() -> dict[str, list[str]]:
    """Load session_searches from agent config.json. Returns {} if unavailable."""
    try:
        cfg = json.loads(_AGENT_CONFIG.read_text(encoding="utf-8"))
        return cfg.get("session_searches", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ── Group CRUD ────────────────────────────────────────────────────────────────

def create_group(
    *,
    name: Optional[str] = None,
    member_tags: Optional[list[str]] = None,
    member_topics: Optional[list[str]] = None,
    note: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Create a first-class Group record.
    name is optional — unnamed Groups are valid (neutral Themes).
    member_tags and member_topics can be empty; add them after creation.
    Leaning slot exists as None — Leaning is built in a later PLAN.

    dry_run=True: builds and returns the record but does NOT write to disk.
    """
    groups = _load_groups()
    group: dict = {
        "id":            _group_next_id(groups),
        "name":          name,
        "member_tags":   list(member_tags) if member_tags else [],
        "member_topics": list(member_topics) if member_topics else [],
        "leaning":       None,          # attachment point — not yet built
        "note":          note,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "schema_version": GROUP_SCHEMA_VERSION,
    }
    if not dry_run:
        groups.append(group)
        _save_groups(groups)
    return group


def get_group(group_id: str) -> Optional[dict]:
    """Return a Group by ID, or None."""
    return next((g for g in _load_groups() if g.get("id") == group_id), None)


def get_groups(
    *,
    tags: Optional[list[str]] = None,
    topic_slug: Optional[str] = None,
    resolve: bool = True,
) -> list[dict]:
    """
    Return Groups matching filters (AND logic).
    tags       — Group must contain at least one matching member_tag
    topic_slug — Group must have the topic in member_topics
    """
    groups = _load_groups()

    if tags:
        filter_tags = set(resolve_tags(tags) if resolve else tags)
        groups = [
            g for g in groups
            if filter_tags & set(resolve_tags(g.get("member_tags", [])) if resolve else g.get("member_tags", []))
        ]

    if topic_slug:
        groups = [g for g in groups if topic_slug in g.get("member_topics", [])]

    return groups


def update_group(
    group_id: str,
    *,
    name: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[dict]:
    """Update mutable fields on a Group. Returns updated Group or None if not found."""
    groups = _load_groups()
    for g in groups:
        if g.get("id") == group_id:
            if name is not None:
                g["name"] = name
            if note is not None:
                g["note"] = note
            _save_groups(groups)
            return g
    return None


def add_tag_to_group(group_id: str, tag: str) -> Optional[dict]:
    """Add a tag to a Group's member_tags (idempotent). Stored as entered."""
    groups = _load_groups()
    for g in groups:
        if g.get("id") == group_id:
            tags = g.setdefault("member_tags", [])
            if tag not in tags:
                tags.append(tag)
                _save_groups(groups)
            return g
    return None


def add_topic_to_group(group_id: str, slug: str) -> Optional[dict]:
    """
    Add a Topic to a Group's member_topics (idempotent).
    Also adds the group_id to the Source records linked to that Topic.
    Does NOT auto-link anything — Robert's explicit action only.
    """
    groups = _load_groups()
    for g in groups:
        if g.get("id") == group_id:
            topics = g.setdefault("member_topics", [])
            if slug not in topics:
                topics.append(slug)
                _save_groups(groups)
            return g
    return None


def remove_topic_from_group(group_id: str, slug: str) -> Optional[dict]:
    """Remove a Topic from a Group's member_topics."""
    groups = _load_groups()
    for g in groups:
        if g.get("id") == group_id:
            g["member_topics"] = [t for t in g.get("member_topics", []) if t != slug]
            _save_groups(groups)
            return g
    return None


# ── Pull scoping ──────────────────────────────────────────────────────────────

def narrow_pull_context(slug: str) -> dict:
    """
    Return pull context for a single Topic — queries, tags, motivation.
    This is the dict passed to the session runner for a narrow (one-Topic) pull.

    Keys:
      scope       — "narrow"
      topic_slug  — the slug
      status      — Topic status
      tags        — Topic's tags (alias-resolved)
      queries     — session_searches[slug] from agent config (empty list if none configured)
      motivation  — Topic's motivation text
    """
    topic = _load_topic(slug)
    if topic is None:
        raise ValueError(f"Topic not found: {slug!r}")

    searches = _load_session_searches()
    return {
        "scope":       "narrow",
        "topic_slug":  slug,
        "status":      topic.get("status"),
        "tags":        resolve_tags(topic.get("tags", [])),
        "queries":     searches.get(slug, []),
        "motivation":  topic.get("motivation", ""),
    }


def contextual_pull_context(group_id: str) -> dict:
    """
    Return pull context for a whole Group — merged queries and tags from all
    member Topics, deduped. This is the dict passed to the session runner for
    a contextual (whole-Group) pull.

    Keys:
      scope         — "contextual"
      group_id      — the Group's ID
      group_name    — Group name (may be None)
      member_topics — list of slugs in the Group
      tags          — union of all member Topic tags (alias-resolved, deduped)
      queries       — merged session_searches from all member Topics (deduped, order preserved)
      motivations   — dict of {slug: motivation} for all member Topics
    """
    group = get_group(group_id)
    if group is None:
        raise ValueError(f"Group not found: {group_id!r}")

    searches = _load_session_searches()
    member_slugs = group.get("member_topics", [])

    merged_tags: list[str] = []
    seen_tags: set[str] = set()
    merged_queries: list[str] = []
    seen_queries: set[str] = set()
    motivations: dict[str, str] = {}

    # Also include the Group's own member_tags
    for tag in resolve_tags(group.get("member_tags", [])):
        if tag not in seen_tags:
            seen_tags.add(tag)
            merged_tags.append(tag)

    for slug in member_slugs:
        topic = _load_topic(slug)
        if not topic:
            continue
        # Merge tags
        for tag in resolve_tags(topic.get("tags", [])):
            if tag not in seen_tags:
                seen_tags.add(tag)
                merged_tags.append(tag)
        # Merge queries
        for q in searches.get(slug, []):
            if q not in seen_queries:
                seen_queries.add(q)
                merged_queries.append(q)
        # Collect motivations
        motivations[slug] = topic.get("motivation", "")

    return {
        "scope":         "contextual",
        "group_id":      group_id,
        "group_name":    group.get("name"),
        "member_topics": member_slugs,
        "tags":          merged_tags,
        "queries":       merged_queries,
        "motivations":   motivations,
    }


# ── Groups summary ────────────────────────────────────────────────────────────

def groups_summary() -> dict:
    """Return counts and a compact list of all Groups."""
    groups = _load_groups()
    return {
        "total": len(groups),
        "groups": [
            {
                "id":            g.get("id"),
                "name":          g.get("name"),
                "member_topics": g.get("member_topics", []),
                "member_tags":   g.get("member_tags", []),
                "has_leaning":   g.get("leaning") is not None,
            }
            for g in groups
        ],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
# Run with:  python curator_research.py <command> [args]
# This is the primary interface until Flask routes are added.

def _cli_status(_args) -> None:
    """Print overview: Sources, Topics by state, Groups, tag aliases."""
    sources = _load_sources()
    all_topics = _all_topics()
    groups = _load_groups()
    aliases = _load_tag_aliases()

    state_counts: dict[str, int] = {}
    for t in all_topics:
        s = t.get("status", "unknown")
        state_counts[s] = state_counts.get(s, 0) + 1

    today = datetime.now(timezone.utc).date().isoformat()
    overdue = [
        t for t in all_topics
        if t.get("status") == "active-pull" and t.get("expires") and t["expires"] < today
    ]

    print("=== Curator Research — Status ===")
    print(f"Sources    : {len(sources)}")
    print(f"Topics     : {len(all_topics)}")
    for state in ("active-pull", "dormant", "paused", "one-shot", "closed"):
        if state in state_counts:
            print(f"  {state:<18}: {state_counts[state]}")
    print(f"Groups     : {len(groups)}")
    print(f"Tag aliases: {len(aliases)}")

    if overdue:
        print(f"\n⚠  {len(overdue)} active-pull topic(s) past expiry — run `auto-stop` to close them:")
        for t in overdue:
            print(f"   {t.get('slug', '?')} (expires {t.get('expires')})")


def _cli_topics(args) -> None:
    """List Topics, optionally filtered by status."""
    all_topics = _all_topics()
    if args.status:
        all_topics = [t for t in all_topics if t.get("status") == args.status]
    if not all_topics:
        msg = f"No topics found with status={args.status!r}." if args.status else "No topics found."
        print(msg)
        return
    print(f"{'SLUG':<32} {'STATUS':<16} {'EXPIRES':<12} {'SESS':>4}  TAGS")
    print("─" * 90)
    for t in all_topics:
        slug     = t.get("slug", t.get("topic", "?"))
        status   = t.get("status", "?")
        expires  = t.get("expires") or "—"
        sessions = str(t.get("session_count", 0))
        tags     = ", ".join(t.get("tags", [])) or "—"
        print(f"{slug:<32} {status:<16} {expires:<12} {sessions:>4}  {tags}")


def _cli_activate(args) -> None:
    topic = activate_topic(args.slug, duration_days=args.days, note=args.note or "")
    print(f"Activated : {topic['slug']}")
    print(f"  Status  : {topic['status']}")
    print(f"  Expires : {topic.get('expires')}")
    print(f"  Duration: {topic.get('duration_days')} days")


def _cli_pause(args) -> None:
    topic = pause_topic(args.slug, note=args.note or "")
    remaining = topic.get("remaining_days", "?")
    print(f"Paused: {topic['slug']} — {remaining} day(s) banked for resume")


def _cli_close(args) -> None:
    topic = close_topic(args.slug, note=args.note or "")
    print(f"Closed: {topic['slug']}")


def _cli_auto_stop(_args) -> None:
    stopped = auto_stop_check()
    if stopped:
        for t in stopped:
            print(f"Auto-stopped: {t['slug']} (expired {t.get('expires')}) → dormant")
    else:
        print("No topics past expiry — nothing changed.")


def _cli_groups(_args) -> None:
    summary = groups_summary()
    if not summary["groups"]:
        print("No groups.")
        return
    print(f"{'ID':<20} {'NAME':<24} {'TOPICS'}")
    print("─" * 80)
    for g in summary["groups"]:
        gid    = g["id"]
        name   = g.get("name") or "—"
        topics = ", ".join(g["member_topics"]) if g["member_topics"] else "—"
        tags   = ", ".join(g["member_tags"]) if g["member_tags"] else ""
        leaning = " [Leaning]" if g["has_leaning"] else ""
        extra  = f"  tags: {tags}" if tags else ""
        print(f"{gid:<20} {name:<24} {topics}{leaning}{extra}")


def _cli_create_group(args) -> None:
    member_tags   = [t.strip() for t in args.tags.split(",") if t.strip()]   if args.tags   else []
    member_topics = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else []
    group = create_group(
        name=args.name,
        member_tags=member_tags,
        member_topics=member_topics,
        note=args.note or "",
        dry_run=args.dry_run,
    )
    prefix = "[DRY RUN] Would create" if args.dry_run else "Created"
    print(f"{prefix} group: {group['id']}" + (f" ({group['name']})" if group.get("name") else ""))
    print(f"  Topics: {', '.join(group['member_topics']) or '—'}")
    print(f"  Tags  : {', '.join(group['member_tags']) or '—'}")
    if args.dry_run:
        print("  (nothing written to disk)")


def _cli_sources(args) -> None:
    filter_tags   = [t.strip() for t in args.tags.split(",") if t.strip()]   if args.tags   else None
    filter_topics = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else None
    sources = get_sources(tags=filter_tags, topics=filter_topics, resolve=args.resolve)
    if not sources:
        print("No sources found.")
        return
    print(f"{'ID':<22} {'TYPE':<9} {'DATE':<12} {'COST':<6}  {'TAGS':<30} TITLE")
    print("─" * 105)
    for s in sources:
        sid   = s.get("id", "?")
        stype = s.get("type", "?")
        date  = (s.get("date") or "—")[:10]
        cost  = s.get("cost_to_act", "free")
        tags  = ", ".join(s.get("tags", []))[:28]
        title = (s.get("title") or "")[:46]
        print(f"{sid:<22} {stype:<9} {date:<12} {cost:<6}  {tags:<30} {title}")


def _cli_source_detail(args) -> None:
    """Print full detail for one Source."""
    source = get_source_by_id(args.source_id)
    if not source:
        import sys
        print(f"Source not found: {args.source_id}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(source, indent=2, ensure_ascii=False))


def _cli_promote_feed(args) -> None:
    tags        = [t.strip() for t in args.tags.split(",") if t.strip()]
    topics_list = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else []
    source = promote_feed_article(args.hash_id, tags, note=args.note or "", topics=topics_list)
    print(f"Promoted: {source['id']}")
    print(f"  Title : {source.get('title', '—')}")
    print(f"  Tags  : {', '.join(source.get('tags', []))}")
    print(f"  Topics: {', '.join(source.get('topics', [])) or '—'}")
    links = suggest_topic_links(source)
    if links:
        print(f"  Suggested topic links (tag overlap):")
        for lnk in links:
            print(f"    {lnk['slug']:<28} overlap: {', '.join(lnk['overlap_tags'])}")


def _cli_promote_manual(args) -> None:
    tags        = [t.strip() for t in args.tags.split(",") if t.strip()]
    topics_list = [t.strip() for t in args.topics.split(",") if t.strip()] if args.topics else []
    source = promote_manual(
        source_type=args.type,
        title=args.title,
        tags=tags,
        url=args.url or "",
        reference=args.reference or "",
        cost_to_act=args.cost_to_act or "free",
        note=args.note or "",
        topics=topics_list,
        date=args.date or "",
        date_precision=args.date_precision or "unknown",
    )
    print(f"Added  : {source['id']}")
    print(f"  Title: {source.get('title', '—')}")
    print(f"  Type : {source.get('type')}")
    print(f"  Tags : {', '.join(source.get('tags', []))}")
    links = suggest_topic_links(source)
    if links:
        print(f"  Suggested topic links (tag overlap):")
        for lnk in links:
            print(f"    {lnk['slug']:<28} overlap: {', '.join(lnk['overlap_tags'])}")


def _cli_migrate_signals(args) -> None:
    # migrate_article_signals prints its own formatted output
    migrate_article_signals(dry_run=args.dry_run)


def _cli_migrate_threads(args) -> None:
    results = migrate_threads_to_topics(dry_run=args.dry_run)
    mode    = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode}Migrate thread.json files → Topics schema v2 (idempotent)")
    for r in results:
        print(f"  [{r.get('action', '?')}] {r.get('slug', '?')}")


def _cli_pull(args) -> None:
    """Print pull context for the session runner — JSON output."""
    if args.scope == "narrow":
        ctx = narrow_pull_context(args.target)
    else:
        ctx = contextual_pull_context(args.target)
    print(json.dumps(ctx, indent=2, ensure_ascii=False))


def _cli_suggest_links(args) -> None:
    """Suggest topic links for a Source by ID."""
    import sys
    source = get_source_by_id(args.source_id)
    if not source:
        print(f"Source not found: {args.source_id}", file=sys.stderr)
        raise SystemExit(1)
    links = suggest_topic_links(source, resolve=True)
    if not links:
        print("No topic link suggestions (no tag overlap found).")
        return
    print(f"Suggested topic links for {args.source_id}:")
    for lnk in links:
        print(f"  {lnk['slug']:<32} overlap: {', '.join(lnk['overlap_tags'])}")


def _cli_tag_aliases(_args) -> None:
    """Print all tag aliases."""
    aliases = _load_tag_aliases()
    if not aliases:
        print("No tag aliases defined. Edit: _NewDomains/research-intelligence/data/tag_aliases.json")
        return
    print(f"{'ALIAS':<24} → CANONICAL")
    print("─" * 40)
    for alias, canonical in sorted(aliases.items()):
        print(f"  {alias:<22} → {canonical}")


# ── Leaning CLI handlers ─────────────────────────────────────────────────────

def _cli_leanings(args):
    """List all leanings."""
    sys.path.insert(0, str(_REPO_ROOT / "_NewDomains" / "research-intelligence"))
    from agent.leanings import list_leanings, needs_badge
    leanings = list_leanings()
    if not leanings:
        print("No leanings yet. Use 'create-leaning' to add one.")
        return
    state_label = {"question": "Question", "leaning": "Leaning ", "hold": "Hold    "}
    print(f"{'STATE':<10} {'ID':<10} {'EV':>4} {'UPD':>12}  TITLE")
    print("─" * 72)
    for l in leanings:
        sl    = state_label.get(l["state"], l["state"])
        ev    = len(l.get("evidence", []))
        badge = " [NEW]" if needs_badge(l) else ""
        print(f"  {sl:<10} {l['id']:<10} {ev:>3}  {l.get('updated','?'):>12}  {l['title']}{badge}")


def _cli_create_leaning(args):
    """Create a new leaning."""
    sys.path.insert(0, str(_REPO_ROOT / "_NewDomains" / "research-intelligence"))
    from agent.leanings import create_leaning
    leaning = create_leaning(
        title  = args.title,
        state  = args.state,
        topics = [t.strip() for t in args.topics.split(",")] if args.topics else [],
        notes  = args.notes or "",
    )
    print(f"Created: {leaning['id']}  [{leaning['state']}]  {leaning['title']}")


def _cli_leaning_state(args):
    """Advance a leaning's state."""
    sys.path.insert(0, str(_REPO_ROOT / "_NewDomains" / "research-intelligence"))
    from agent.leanings import update_leaning
    l = update_leaning(args.id, state=args.state)
    if l is None:
        print(f"Error: leaning '{args.id}' not found.", file=sys.stderr)
        sys.exit(1)
    print(f"Updated {l['id']}: state → {l['state']}")


def _cli_add_evidence(args):
    """Add an evidence item to a leaning."""
    sys.path.insert(0, str(_REPO_ROOT / "_NewDomains" / "research-intelligence"))
    from agent.leanings import add_evidence
    ev = add_evidence(
        leaning_id = args.id,
        title      = args.title,
        url        = args.url or "",
        source     = args.source or "",
        stance     = args.stance,
        note       = args.note or "",
    )
    if ev is None:
        print(f"Error: leaning '{args.id}' not found.", file=sys.stderr)
        sys.exit(1)
    sym = {"supports": "▲", "complicates": "▽", "neutral": "○"}.get(ev["stance"], "○")
    print(f"Added evidence {ev['id']}  {sym} {ev['stance']}  {ev['title']}")


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python curator_research.py",
        description=(
            "Curator research data layer — Sources, Topics, Groups.\n"
            "Primary CLI interface until Flask routes are added."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── status ──
    sub.add_parser("status", help="Overview: Sources, Topics by state, Groups, aliases.")

    # ── topics ──
    p_topics = sub.add_parser("topics", help="List Topics.")
    p_topics.add_argument("--status", metavar="STATE",
        help="Filter by state: dormant | active-pull | paused | one-shot | closed")

    # ── activate ──
    p_act = sub.add_parser("activate", help="Activate a Topic (dormant → active-pull).")
    p_act.add_argument("slug")
    p_act.add_argument("--days", type=int, default=None,
        help="Duration in days (default: topic's configured duration_days).")
    p_act.add_argument("--note", help="Optional note recorded in state_history.")

    # ── pause ──
    p_pause = sub.add_parser("pause", help="Pause an active-pull Topic (banks remaining days).")
    p_pause.add_argument("slug")
    p_pause.add_argument("--note")

    # ── close ──
    p_close = sub.add_parser("close", help="Close a Topic (terminal state).")
    p_close.add_argument("slug")
    p_close.add_argument("--note")

    # ── auto-stop ──
    sub.add_parser("auto-stop", help="Move expired active-pull Topics → dormant.")

    # ── groups ──
    sub.add_parser("groups", help="List Groups.")

    # ── create-group ──
    p_cg = sub.add_parser("create-group", help="Create a new Group.")
    p_cg.add_argument("--name", help="Human-readable name.")
    p_cg.add_argument("--topics", metavar="slug,slug",
        help="Comma-separated topic slugs to include as members.")
    p_cg.add_argument("--tags", metavar="tag,tag",
        help="Comma-separated member tags (in addition to topic tags).")
    p_cg.add_argument("--note")
    p_cg.add_argument("--dry-run", action="store_true",
        help="Preview what would be created without writing to disk.",
        dest="dry_run")

    # ── sources ──
    p_src = sub.add_parser("sources", help="List Sources (all, or filtered).")
    p_src.add_argument("--tags", metavar="tag,tag", help="Filter by tag(s) — any match.")
    p_src.add_argument("--topics", metavar="slug,slug", help="Filter by topic slug(s).")
    p_src.add_argument("--resolve", action="store_true",
        help="Resolve tag aliases before filtering (default: off for speed).")

    # ── source ──
    p_sd = sub.add_parser("source", help="Print full detail for one Source.")
    p_sd.add_argument("source_id", help="Source ID, e.g. src_20260531_001")

    # ── promote-feed ──
    p_pf = sub.add_parser("promote-feed",
        help="Save an article from today's briefing as a Source.")
    p_pf.add_argument("hash_id",
        help="Article hash ID from curator_latest.json (shown in briefing).")
    p_pf.add_argument("tags", metavar="tag,tag",
        help="Comma-separated tags to attach.")
    p_pf.add_argument("--note", help="Your note on this source.")
    p_pf.add_argument("--topics", metavar="slug,slug",
        help="Comma-separated topic slugs to link.")

    # ── promote-manual ──
    p_pm = sub.add_parser("promote-manual",
        help="Add a book, paper, or post as a Source manually.")
    p_pm.add_argument("type", choices=sorted(SOURCE_TYPES))
    p_pm.add_argument("title")
    p_pm.add_argument("tags", metavar="tag,tag")
    p_pm.add_argument("--url")
    p_pm.add_argument("--reference", help="Bibliographic reference (books/papers).")
    p_pm.add_argument("--cost-to-act", choices=sorted(COST_TO_ACT), default="free",
        dest="cost_to_act")
    p_pm.add_argument("--note")
    p_pm.add_argument("--topics", metavar="slug,slug")
    p_pm.add_argument("--date", help="Publication date: YYYY, YYYY-MM, or YYYY-MM-DD.")
    p_pm.add_argument("--date-precision",
        choices=sorted(DATE_PRECISIONS), default="unknown", dest="date_precision")

    # ── migrate-signals ──
    p_ms = sub.add_parser("migrate-signals",
        help="Migrate saved articles from article_signals.json → Sources.")
    p_ms.add_argument("--dry-run", action="store_true",
        help="Preview what would be promoted without writing anything.")

    # ── migrate-threads ──
    p_mt = sub.add_parser("migrate-threads",
        help="Upgrade thread.json files to Topics schema v2 (idempotent).")
    p_mt.add_argument("--dry-run", action="store_true")

    # ── pull ──
    p_pull = sub.add_parser("pull",
        help="Print pull context (queries + scope) for the session runner.")
    p_pull.add_argument("scope", choices=["narrow", "contextual"],
        help="narrow = single topic; contextual = whole group.")
    p_pull.add_argument("target",
        help="Topic slug (narrow) or Group ID (contextual).")

    # ── suggest-links ──
    p_sl = sub.add_parser("suggest-links",
        help="Suggest which Topics a Source might link to (tag overlap, read-only).")
    p_sl.add_argument("source_id")

    # ── tag-aliases ──
    sub.add_parser("tag-aliases", help="List all tag aliases.")

    # ── leanings ──
    sub.add_parser("leanings", help="List all Leanings with state and evidence count.")

    # ── create-leaning ──
    p_cl = sub.add_parser("create-leaning", help="Create a new Leaning.")
    p_cl.add_argument("title", help="Statement being leaned toward.")
    p_cl.add_argument("--state", choices=["question", "leaning", "hold"], default="question")
    p_cl.add_argument("--topics", metavar="slug,slug",
        help="Comma-separated Topic slugs to link.")
    p_cl.add_argument("--notes", help="Optional personal note.")

    # ── leaning-state ──
    p_ls = sub.add_parser("leaning-state", help="Advance a Leaning's state.")
    p_ls.add_argument("id", help="Leaning ID (8-char hex).")
    p_ls.add_argument("state", choices=["question", "leaning", "hold"])

    # ── add-evidence ──
    p_ae = sub.add_parser("add-evidence", help="Add an evidence item to a Leaning.")
    p_ae.add_argument("id", help="Leaning ID.")
    p_ae.add_argument("title", help="Title of the source or article.")
    p_ae.add_argument("--url", help="URL (optional).")
    p_ae.add_argument("--source", help="Publication or author.")
    p_ae.add_argument("--stance", choices=["supports", "complicates", "neutral"],
        default="neutral")
    p_ae.add_argument("--note", help="One-line reaction.")

    # ── dispatch ──
    args = parser.parse_args()
    handlers = {
        "status":          _cli_status,
        "topics":          _cli_topics,
        "activate":        _cli_activate,
        "pause":           _cli_pause,
        "close":           _cli_close,
        "auto-stop":       _cli_auto_stop,
        "groups":          _cli_groups,
        "create-group":    _cli_create_group,
        "sources":         _cli_sources,
        "source":          _cli_source_detail,
        "promote-feed":    _cli_promote_feed,
        "promote-manual":  _cli_promote_manual,
        "migrate-signals": _cli_migrate_signals,
        "migrate-threads": _cli_migrate_threads,
        "pull":            _cli_pull,
        "suggest-links":   _cli_suggest_links,
        "tag-aliases":     _cli_tag_aliases,
        "leanings":        _cli_leanings,
        "create-leaning":  _cli_create_leaning,
        "leaning-state":   _cli_leaning_state,
        "add-evidence":    _cli_add_evidence,
    }
    try:
        handlers[args.command](args)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
