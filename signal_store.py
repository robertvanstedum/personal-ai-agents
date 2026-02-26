#!/usr/bin/env python3
"""
Signal Store - Append-only event log for the learning system

All events are written here: article scoring, feedback, source changes, priorities.
Format: One JSON object per line (JSONL). Self-contained, grep-friendly.
Any model can read a slice without context.

Key design decisions:
- article_id is the shared key across event types (correlation without foreign keys)
- session_id (UUID) on every event for debugging and session slicing
- Timestamps in ISO 8601 UTC
- No deletions, only appends
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

# Signal Store location (same directory as scripts)
SIGNAL_STORE_PATH = Path(__file__).parent / "signal_store.jsonl"

# Session ID: generated once per script run, shared across all events in that run
# This allows slicing the store by session to see exactly what one run did
_SESSION_ID: Optional[str] = None


def get_session_id() -> str:
    """
    Get or create session ID for this run.
    Call once at script startup, reuse for all events.
    """
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = str(uuid.uuid4())
    return _SESSION_ID


def append_event(event_type: str, data: Dict[str, Any]) -> None:
    """
    Append an event to the Signal Store.
    
    Args:
        event_type: Event type (article_scored, feedback, source_change, priority_added, etc.)
        data: Event-specific data (article_id, score, action, etc.)
    
    All events get timestamp and session_id added automatically.
    """
    event = {
        "event": event_type,
        "session_id": get_session_id(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data
    }
    
    # Append to store (create if doesn't exist)
    with open(SIGNAL_STORE_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")


def log_article_scored(
    article_id: str,
    title: str,
    source: str,
    category: str,
    score: float,
    model: str,
    url: Optional[str] = None,
    rank: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log an article scoring event.
    
    Called by curator_rss_v2.py after scoring articles.
    """
    data = {
        "article_id": article_id,
        "title": title,
        "source": source,
        "category": category,
        "score": score,
        "model": model,
    }
    
    if url:
        data["url"] = url
    if rank is not None:
        data["rank"] = rank
    if metadata:
        data["metadata"] = metadata
    
    append_event("article_scored", data)


def log_feedback(
    article_id: str,
    action: str,
    channel: str,
    title: Optional[str] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
    rank: Optional[int] = None,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a feedback event (like/dislike/save).
    
    Called by curator_feedback.py when user provides feedback.
    
    Args:
        article_id: Article identifier
        action: like, dislike, or save
        channel: telegram, web_ui, cli
        title: Article title (optional, for readability)
        source: Article source (optional)
        category: Article category (optional)
        rank: Article rank in briefing (optional)
        reason: User's explanation (optional)
        metadata: Additional context (optional)
    """
    data = {
        "article_id": article_id,
        "action": action,
        "channel": channel,
    }
    
    if title:
        data["title"] = title
    if source:
        data["source"] = source
    if category:
        data["category"] = category
    if rank is not None:
        data["rank"] = rank
    if reason:
        data["reason"] = reason
    if metadata:
        data["metadata"] = metadata
    
    append_event("feedback", data)


def log_source_change(
    source_id: str,
    old_status: str,
    new_status: str,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a source status change (staging → seed, seed → muted, etc.).
    
    Args:
        source_id: Source identifier (feed name)
        old_status: Previous status (seed, staging, muted, rejected)
        new_status: New status
        reason: Explanation for change (optional)
        metadata: Additional context (optional)
    """
    data = {
        "source_id": source_id,
        "old_status": old_status,
        "new_status": new_status,
    }
    
    if reason:
        data["reason"] = reason
    if metadata:
        data["metadata"] = metadata
    
    append_event("source_change", data)


def log_priority_added(
    concern: str,
    boost: float,
    expires: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a priority injection event.
    
    Args:
        concern: Priority text (e.g., "US tariff escalation")
        boost: Score boost value
        expires: Expiry date (YYYY-MM-DD) or None for no expiry
        metadata: Additional context (optional)
    """
    data = {
        "concern": concern,
        "boost": boost,
    }
    
    if expires:
        data["expires"] = expires
    if metadata:
        data["metadata"] = metadata
    
    append_event("priority_added", data)


def log_priority_expired(
    concern: str,
    expired_date: str,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log a priority expiration event.
    
    Args:
        concern: Priority text that expired
        expired_date: Date it expired (YYYY-MM-DD)
        metadata: Additional context (optional)
    """
    data = {
        "concern": concern,
        "expired_date": expired_date,
    }
    
    if metadata:
        data["metadata"] = metadata
    
    append_event("priority_expired", data)


def log_priority_match(
    priority_id: str,
    priority_label: str,
    article_id: str,
    article_title: str,
    boost: float,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log when an article matches a priority and receives a boost.
    
    Args:
        priority_id: Priority identifier (e.g., "p_001")
        priority_label: Human-readable priority label
        article_id: Article hash ID
        article_title: Article title
        boost: Boost applied to article
        metadata: Additional context (optional)
    """
    data = {
        "priority_id": priority_id,
        "priority_label": priority_label,
        "article_id": article_id,
        "article_title": article_title,
        "boost": boost,
    }
    
    if metadata:
        data["metadata"] = metadata
    
    append_event("priority_match", data)


def read_events(
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Read events from the Signal Store.
    
    Args:
        event_type: Filter by event type (article_scored, feedback, etc.)
        since: Only return events after this ISO timestamp
        limit: Maximum number of events to return (most recent first)
    
    Returns:
        List of event dicts
    """
    if not SIGNAL_STORE_PATH.exists():
        return []
    
    events = []
    
    with open(SIGNAL_STORE_PATH, "r") as f:
        for line in f:
            try:
                event = json.loads(line.strip())
                
                # Filter by event type
                if event_type and event.get("event") != event_type:
                    continue
                
                # Filter by timestamp
                if since and event.get("timestamp", "") < since:
                    continue
                
                events.append(event)
                
            except json.JSONDecodeError:
                # Skip malformed lines
                continue
    
    # Apply limit (most recent first)
    if limit:
        events = events[-limit:]
    
    return events


def get_article_history(article_id: str) -> List[Dict[str, Any]]:
    """
    Get all events related to a specific article.
    
    Returns list of events (scoring, feedback, etc.) in chronological order.
    """
    if not SIGNAL_STORE_PATH.exists():
        return []
    
    events = []
    
    with open(SIGNAL_STORE_PATH, "r") as f:
        for line in f:
            try:
                event = json.loads(line.strip())
                if event.get("article_id") == article_id:
                    events.append(event)
            except json.JSONDecodeError:
                continue
    
    return events


if __name__ == "__main__":
    # Self-test: append a test event and read it back
    print("Signal Store Self-Test")
    print(f"Store location: {SIGNAL_STORE_PATH}")
    print(f"Session ID: {get_session_id()}")
    print()
    
    # Test article scored event
    log_article_scored(
        article_id="test_123",
        title="Test Article",
        source="Test Source",
        category="geo_major",
        score=8.5,
        model="grok-3-mini",
        url="https://example.com/test",
        rank=1
    )
    print("✅ Logged article_scored event")
    
    # Test feedback event
    log_feedback(
        article_id="test_123",
        action="like",
        channel="cli",
        title="Test Article",
        source="Test Source",
        reason="Testing Signal Store"
    )
    print("✅ Logged feedback event")
    
    # Read back events
    events = read_events(limit=2)
    print(f"\n📖 Read {len(events)} events:")
    for event in events:
        print(f"   {event['event']}: {event.get('article_id', event.get('source_id', 'N/A'))}")
    
    # Test article history
    history = get_article_history("test_123")
    print(f"\n📚 Article history for test_123: {len(history)} events")
    
    print("\n✅ Signal Store is operational")
