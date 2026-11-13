"""A work runtime's answer made fit to be spoken (#561).

Robert, 13 Nov 2026, voice on his phone: MC's answer to "did the deploy finish?" was right, but it was read out with "That
is not a live refresh, and it is not whole-platform acceptance", doubled full stops and raw timestamps. The runtime is asked
for a voice-length answer (minimoi_portal/guild_ui/mc_voice_transcripts.SPOKEN_ANSWER); this is the safety net for what
still comes back: timestamps said as words in the owner's time ("about eleven twenty-five last night"), the deploy record's
disclaimer sentences dropped, and sentences joined with one full stop. Pure text, no network or model call.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Chicago")
_ONES = ("", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty")
# The deploy record's and the awareness cache's own disclaimers (release_context.block, github_awareness LIMITATION):
# right in writing, noise when spoken. A sentence carrying one is left out of speech; the written reply keeps it.
_CHROME = re.compile(r"not (?:a )?live refresh|whole[- ]platform|owner acceptance|acceptance (?:is )?unverified|"
                     r"not (?:feature|login|user[- ]journey) acceptance|not proof of (?:the )?latest", re.I)
_ISO = re.compile(r"(?:\bat\s+)?\b(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00)(?![\w:])")
_UTC = re.compile(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s*(?:UTC|Z)\b")


def _number(n: int) -> str:
    return _ONES[n] if n < 20 else _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")


def clock_words(local: datetime) -> str:
    """"eleven twenty-five", "nine o'clock", "seven oh five"."""
    hour = local.hour % 12 or 12
    if local.minute == 0:
        return f"{_number(hour)} o'clock"
    return f"{_number(hour)} {'oh ' + _number(local.minute) if local.minute < 10 else _number(local.minute)}"


def _day(local: datetime, now: datetime) -> str:
    days = (now.date() - local.date()).days
    part = "morning" if local.hour < 12 else "afternoon" if local.hour < 18 else "evening"
    if days == 0:
        return "early this morning" if local.hour < 5 else f"this {part}"
    if days == 1:
        return "last night" if local.hour >= 18 else f"yesterday {part}"
    if 1 < days < 7:
        return "on " + local.strftime("%A")
    return f"on {local.day} {local.strftime('%B')}"


def time_words(when: datetime, now: datetime | None = None) -> str:
    """A moment as he would say it, in Chicago time: "about eleven twenty-five last night"."""
    now = (now or datetime.now(timezone.utc)).astimezone(ZONE)
    local = when.astimezone(ZONE)
    return f"about {clock_words(local)} {_day(local, now)}"


def _utc_clock(hour: int, minute: int, now: datetime) -> datetime:
    """A bare "05:25 UTC": its most recent occurrence at or before now (a few minutes ahead allowed for clock skew)."""
    now_utc = now.astimezone(timezone.utc)
    at = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at - timedelta(days=1) if at > now_utc + timedelta(minutes=5) else at


def for_speech(text: str, now: datetime | None = None) -> str:
    """``text`` as it should be spoken: timestamps as words, disclaimer sentences out, single full stops."""
    now = now or datetime.now(timezone.utc)
    t = _ISO.sub(lambda m: time_words(datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3]}:00+00:00"), now), text or "")
    t = _UTC.sub(lambda m: time_words(_utc_clock(int(m[1]), int(m[2]), now), now), t)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", t.strip()) if s]
    kept = [s for s in sentences if not _CHROME.search(s)] or sentences[:1]
    return single_stops(" ".join(kept))


def single_stops(text: str) -> str:
    """".." (two sentences joined, each with its own stop) becomes ".", leaving a real ellipsis ("...") alone."""
    t = re.sub(r"(?<!\.)\.\s*\.(?!\.)", ".", text)
    t = re.sub(r"([!?])\s*\.(?!\.)", r"\1", t)
    return re.sub(r"\s+([.!?,;:])", r"\1", t).strip()
