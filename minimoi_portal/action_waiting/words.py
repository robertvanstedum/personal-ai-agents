"""The fixed words of every notice and of MC's lines (spec v0.2 §3.6).

A notice is plain text (no ``parse_mode``) built only from an item's kind, code, app, times, agent, branch, session
prefix and links. Guest names and emails never reach an item, so they cannot reach a notice. Agent and decision words
go to Telegram only with D8 on, already scrubbed when stored, cut so the message stays within MESSAGE_MAX with the
header and links whole.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .settings import MESSAGE_MAX
from .sources import agent_name, epoch

CUT = "… (cut — full words in MC)"
KIND_WORDS = {"guest_access": "Guest access request", "password_reset": "Password reset request",
              "decision": "Decision", "agent_ask": "Agent ask"}
OPERATE = {"label": "Operate", "path": "/guild/operate"}
GUESTS_ADMIN = {"label": "Guests admin", "path": "/admin/guests"}

try:
    from zoneinfo import ZoneInfo
    _ZONE, _ZONE_LABEL = ZoneInfo("America/Chicago"), "CT"
except Exception:                                      # no zone database in the image: say UTC rather than guess
    _ZONE, _ZONE_LABEL = timezone.utc, "UTC"


def when(t, *, day=True) -> str:
    t = epoch(t)
    if t is None:
        return "unknown time"
    dt = datetime.fromtimestamp(t, timezone.utc).astimezone(_ZONE)
    text = f"{dt:%H:%M} {_ZONE_LABEL}"
    return f"{text}, {dt.day} {dt:%b}" if day else text


def acts_on(item) -> list:
    """The pages that act, as same-origin paths: Operate for guild refs and resets, /admin/guests for pending refs
    (review N8: never /guild-next/guild/operate, whose page lists neither store)."""
    kind = item.get("kind")
    if kind == "password_reset":
        return [OPERATE]
    if kind == "guest_access":
        heads = {str(r).split(":", 1)[0] for r in item.get("refs", [])}
        out = ([OPERATE] if "guild" in heads else []) + ([GUESTS_ADMIN] if "pending" in heads else [])
        return out or [OPERATE]
    return []


def headline(item) -> str:
    kind, facts = item.get("kind"), item.get("facts") or {}
    if kind == "guest_access":
        return f"Guest access request · {item.get('app') or 'app unknown'}"
    if kind == "password_reset":
        return "Password reset request" + (" · account expired: renew first" if facts.get("account_expired") else "")
    if kind == "decision":
        code = facts.get("decision") or "D-?"
        return f"Decision {code} from {agent_name(facts.get('agent'))}"
    if kind == "agent_ask":
        text = f"{agent_name(facts.get('agent'))} is blocked on you"
        if facts.get("branch"):
            text += f" · branch {facts['branch']}"
        if facts.get("session"):
            text += f" · session {facts['session'][:8]}"
        return text
    return "Action waiting"


def detail_line(item) -> str:
    """The second line: what, and since when (and until when for a reset)."""
    kind, facts = item.get("kind"), item.get("facts") or {}
    since = facts.get("requested_at") or facts.get("created_at") or item.get("opened_at")
    if kind == "guest_access":
        return f"{headline(item)} · since {when(since)}"
    if kind == "password_reset":
        text = f"Password reset request · since {when(since)} · expires {when(facts.get('expires_at'))}"
        return text + (" · account expired: renew first" if facts.get("account_expired") else "")
    if kind == "decision":
        code = facts.get("decision") or "D-?"
        return f"{headline(item)} · answer in MC: approve {code} / no {code}"
    return headline(item)


def answer(item) -> str:
    """Where Robert answers, for MC and the API. MC never claims to hand words to a session (Codex S2)."""
    kind, facts = item.get("kind"), item.get("facts") or {}
    if item.get("source_lost"):
        pages = " or ".join(f"{p['label']} ({p['path']})" for p in acts_on(item))
        return f"its source record was lost: check {pages}, or say \"dismiss {item['code']}\" here"
    if kind in ("guest_access", "password_reset"):
        return " or ".join(f"{p['label']} ({p['path']})" for p in acts_on(item))
    if kind == "decision":
        code = facts.get("decision") or "D-?"
        return f"\"approve {code}\" or \"no {code}\" here, or the approvals bot reply"
    if kind == "agent_ask":                          # the session is named in the headline
        return f"in {agent_name(facts.get('agent'))}'s own session, or \"dismiss {item['code']}\" here"
    return "in MiniMoi"


def _link(settings, focus) -> str:
    return f"{settings.base_url}/guild-next/guild/build?waiting={focus}"


def _acts_line(settings, item) -> str:
    pages = acts_on(item)
    return ("Acts on: " + " or ".join(settings.base_url + p["path"] for p in pages)) if pages else ""


def _fit(head_lines, words, tail_lines=()) -> str:
    """Header and links whole; only the quoted words are cut to keep the message within MESSAGE_MAX."""
    head = "\n".join(line for line in head_lines if line)
    tail = "\n".join(line for line in tail_lines if line)
    if not words:
        return "\n".join(x for x in (head, tail) if x)[:MESSAGE_MAX]
    room = MESSAGE_MAX - len(head) - len(tail) - 6
    quoted = words if len(words) <= room else words[:max(0, room - len(CUT))].rstrip() + CUT
    return "\n".join(x for x in (head, f"“{quoted}”", tail) if x)


def single(settings, item, *, reminder=False) -> str:
    first = (f"{settings.label}MiniMoi · still waiting ({int(settings.remind_hours)} h) · {item['code']}" if reminder
             else f"{settings.label}MiniMoi · action waiting · {item['code']}")
    lines = [first, detail_line(item), f"Open in MC: {_link(settings, item['code'])}", _acts_line(settings, item)]
    words = item.get("words") if settings.words_on_telegram and item.get("kind") in ("agent_ask", "decision") else ""
    return _fit(lines, words)


def group(settings, items, *, reminder=False) -> str:
    n = len(items)
    first = (f"{settings.label}MiniMoi · {n} actions still waiting ({int(settings.remind_hours)} h)" if reminder
             else f"{settings.label}MiniMoi · {n} actions waiting")
    lines = [first]
    for item in items:
        since = (item.get("facts") or {}).get("requested_at") or item.get("opened_at")
        line = f"{item['code']} · {headline(item)}"
        if item.get("kind") in ("guest_access", "password_reset"):
            line += f" · since {when(since, day=False)}"
        lines.append(line[:300])
    lines.append(f"Open in MC: {_link(settings, 'all')}")
    return "\n".join(lines)[:MESSAGE_MAX]


def direct(settings, item, *, recorded: bool) -> str:
    """The safe direct notice, sent on the legacy bot when the notices service can't (spec §3.5, D11)."""
    if recorded:
        first = f"{settings.label}MiniMoi · action waiting · {item['code']} (sent directly: notices service not healthy)"
        link = f"Open in MC: {_link(settings, item['code'])}"
    else:
        first = f"{settings.label}MiniMoi · action waiting (NOT RECORDED in MiniMoi)"
        link = ""
    return "\n".join(x for x in (first, detail_line(item), link, _acts_line(settings, item)) if x)[:MESSAGE_MAX]
