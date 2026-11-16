"""The chat-list name of a finished voice call, the same words for MC and CoS (#620).

A call that began on a new chat is named from the clock in Robert's zone and the
first thing he said. A call inside a chat that already has words keeps that chat's name.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

OWNER_ZONE = ZoneInfo("America/Chicago")
TITLE_MAX = 80
SNIPPET = 40


def voice_call_title(when: datetime, words: str) -> str:
    """``Voice call · 2:29 PM · decision log…``. ``when`` is the call's end, or its start."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    clock = when.astimezone(OWNER_ZONE).strftime("%I:%M %p").lstrip("0")
    clean = " ".join(str(words or "").split())
    if len(clean) > SNIPPET:
        cut = clean[:SNIPPET].rsplit(" ", 1)[0]
        snippet = (cut if len(cut) > 12 else clean[:SNIPPET]).rstrip(" ,.;:") + "…"
    else:
        snippet = clean or "call"
    return f"Voice call · {clock} · {snippet}"[:TITLE_MAX]


def done_line(started: str, saved: str, turns: int) -> str:
    """``Done · 4 min · 12 turns``. A clock that cannot be read is zero seconds, never a guess of the words."""
    seconds = 0
    try:
        a = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(saved).replace("Z", "+00:00"))
        seconds = max(0, int((b - a).total_seconds()))
    except (TypeError, ValueError):
        seconds = 0
    span = f"{seconds}s" if seconds < 60 else f"{max(1, round(seconds / 60))} min"
    n = max(0, int(turns))
    return f"Done · {span} · {n} {'turn' if n == 1 else 'turns'}"
