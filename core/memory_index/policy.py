"""What the index may hold (v0.5 C6/E4, amendment R3/R5, capture-revision handoff 2026-10-04).

Three gates, all decided from the record's own front matter and turn frames, never from what the text says:

1. **Record gate** (``record_gate``): the record contract holds, the provider is known, and the record's current
   edition was read by a normalizer at or above the provider's **retrieval minimum**. Editions written before the
   capture revision carry known misattribution (Claude Code owner turns stored as system; Codex approval-review
   traffic stored as human/assistant; claude.ai extracted attachment text stored as dialogue). Those records are
   **held** with ``awaiting_reprocess`` until re-mediation gives them a new current edition. History stays on the
   shelf; it simply never reaches retrieval. If ``core.memory_shelf`` publishes its own minimum
   (``sessions.RETRIEVAL_MIN_NORMALIZER``), that value wins over the table here.
2. **Turn gate** (``retrievable_turns``): only the owner's dialogue: ``human`` and ``assistant`` turns that are not
   typed as approval review, a subagent thread or an agent handoff. ``system`` and ``coordination`` turns stay on
   the shelf as attributed evidence and are not indexed. Uses ``render.dialogue_turns`` when the shelf provides it.
3. **Caller gate** (``allowed``): tier and scope, checked from the record's front matter **before** any content is
   read for a caller. Tier is derived at read time: ``curated`` when the record says so or carries a
   ``designated-curated`` event, else ``raw``. Canaries are seen only when asked for by name.

``POLICY_VERSION`` is stored in the index; changing any gate means a ``moi rebuild``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.memory_shelf import record, render, sessions

POLICY_VERSION = 1

# Lowest normalizer version whose output is fit for retrieval, per provider. Unknown providers are held (fail closed).
MIN_NORMALIZER = {"claude-code": 2, "codex": 3, "claude-ai": 2, "grok": 1, "chatgpt": 1, "paste": 1, "rooms": 1, "canary": 1,
                  "minimoi-mc": 1}
NON_DIALOGUE_CLASSES = frozenset({"approval_review", "subagent_task", "subagent", "handoff"})
DIALOGUE_SPEAKERS = ("human", "assistant")
TIERS = {"curated": ("curated",), "raw": ("raw",), "all": ("curated", "raw")}

# Reason codes for a held record (fixed vocabulary; never text from the record).
INVALID = "invalid_record"
UNKNOWN_PROVIDER = "unknown_provider"
AWAITING_REPROCESS = "awaiting_reprocess"
NOT_SESSION = "unsupported_kind"
PARSE_MISMATCH = "parse_mismatch"
UNREADABLE = "unreadable"
NO_DIALOGUE = "no_dialogue"


def min_normalizer(provider: str) -> int | None:
    shared = getattr(sessions, "RETRIEVAL_MIN_NORMALIZER", None)
    if isinstance(shared, dict) and provider in shared:
        return int(shared[provider])
    return MIN_NORMALIZER.get(provider)


def provider_of(meta: dict) -> str:
    return str(meta.get("source", "")).split(":", 1)[0]


def effective_tier(meta: dict) -> str:
    if meta.get("tier") == "curated" or any(e.get("kind") == "designated-curated" for e in meta.get("events") or []):
        return "curated"
    return "raw"


def is_canary(meta: dict) -> bool:
    return meta.get("kind") == "canary" or provider_of(meta) == "canary"


def record_gate(meta: dict) -> str | None:
    """``None`` when the record may be indexed, else a reason code."""
    try:
        record.check(meta)
    except record.InvalidRecord:
        return INVALID
    if meta.get("kind") not in ("session", "canary"):
        return NOT_SESSION
    floor = min_normalizer(provider_of(meta))
    if floor is None:
        return UNKNOWN_PROVIDER
    if int((meta.get("normalized") or {}).get("normalizer", sessions.DEFAULT_NORMALIZER)) < floor:
        return AWAITING_REPROCESS
    return None


def retrievable_turns(turns) -> list:
    shared = getattr(render, "dialogue_turns", None)
    if callable(shared):
        return list(shared(turns))
    return [t for t in turns if t.speaker in DIALOGUE_SPEAKERS
            and (getattr(t, "attrs", None) or {}).get("class") not in NON_DIALOGUE_CLASSES]


_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class Caller:
    """Who is searching. Agent-neutral: the same procedure serves Robert's CLI, CoS and Master Craftsman (owner
    direction 2026-10-04); ``agent`` and ``entry_point`` only identify the caller (M4 logs them), they never change
    what is allowed. ``tier``: curated (default, E4), raw or all. ``scopes``: ``None`` = every scope the record
    contract allows (``robert`` and ``mandate:<id>``; never a guest), else an explicit allow-list."""
    tier: str = "curated"
    scopes: tuple[str, ...] | None = None
    canary: bool = False
    agent: str = "robert"          # robert | cos | mc | ... (a short machine token)
    entry_point: str = "cli"       # cli | chat | brief | ...

    def __post_init__(self):
        if self.tier not in TIERS:
            raise ValueError("tier must be curated, raw or all")
        if not _TOKEN.match(self.agent) or not _TOKEN.match(self.entry_point):
            raise ValueError("agent and entry_point are short lowercase tokens")

    def label(self) -> dict:
        return {"agent": self.agent, "entry_point": self.entry_point}

    @property
    def tiers(self) -> tuple[str, ...]:
        return TIERS[self.tier]


def allowed(meta: dict, caller: Caller) -> str | None:
    """``None`` when ``caller`` may see this record, else a reason code. Front matter only; no content."""
    if is_canary(meta) != caller.canary:
        return "canary_gate"
    if effective_tier(meta) not in caller.tiers:
        return "tier_gate"
    scope = meta.get("scope")
    if caller.scopes is not None and scope not in caller.scopes:
        return "scope_gate"
    if not (scope == "robert" or str(scope).startswith("mandate:")):
        return "scope_gate"
    return None


__all__ = ["POLICY_VERSION", "MIN_NORMALIZER", "Caller", "record_gate", "retrievable_turns", "allowed",
           "effective_tier", "is_canary", "provider_of", "min_normalizer"]
