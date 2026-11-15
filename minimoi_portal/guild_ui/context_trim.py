"""What is attached to each of Robert's messages to MC, trimmed (#592, step 8 of #567 v0.2).

The portal sends Robert's words and the attached platform blocks to OpenClaw as one user message, and OpenClaw keeps that
message in its session transcript, so every later call carries it again. Step 0 measured 20-30k characters attached per
message. With MC's tools on, each fact a tool returns is no longer attached; standing rules live in the persona and its
cards; live state comes as one line, or in full only when Robert's words ask for it.

``MINIMOI_MC_CONTEXT_TRIM=0`` restores the full attachments (a per-process setting: restart to change it). The trim
never applies where MC's tools are off, because the dropped facts are reached through those tools.
"""
from __future__ import annotations

import re

SWITCH = "MINIMOI_MC_CONTEXT_TRIM"

# Robert asking how things are, about builds, agents or status: the words that bring the full live detail. Broad on
# purpose; a false match costs a few hundred characters, a miss costs an answer.
STATUS_QUESTION = re.compile(
    r"\bhow(?:'s| is| are)\s+(?:things|it going|we doing|everything|the build|the platform|minimoi)\b"
    r"|\bhow\s+(?:goes|are we)\b"
    r"|\bwhat(?:'s| is)\s+(?:going on|happening|up|new|running|waiting|broken|wrong)\b"
    r"|\banything\s+(?:new|wrong|broken|failing|waiting|running|on fire)\b"
    r"|\b(?:status|health|healthy|jobs?|watchdog|check-?ins?|supervision|progress|update)\b"
    r"|\b(?:build(?:s|ing)?|agents?|sessions?|claude(?: code)?|codex|grok|apprentice|helpers?|pull requests?|prs?)\b"
    r"|\b(?:running|working on|stuck|blocked)\b"
    r"|\bD-?\d+\b"
    r"|\bwie\s+(?:läuft|laeuft|steht|geht)\b|\bwas\s+(?:gibt'?s|läuft|laeuft)\b",
    re.I)


def active(environ, tools_own_actions: bool) -> bool:
    """Whether this turn's attachments are trimmed: MC's tools own its actions here, and the switch is not off."""
    return bool(tools_own_actions) and (environ or {}).get(SWITCH) != "0"


def asks_status(owner_text) -> bool:
    return isinstance(owner_text, str) and bool(STATUS_QUESTION.search(owner_text))
