"""Admission-time depth selection shared by MC and CoS.

Only a host catalogue opts a conversation role in. No prompt, attachment or model
output may name a route. The owner's unaugmented words can choose one of the two
host-approved routes; the conversation's stored selection is never rewritten.
Runtime continuation is separate from this admission policy. A host that sets
``"phrase_only": true`` in the pair turns it off: only the owner's words choose
deep, and an ordinary turn stays quick (#586).

A host may also name a ``"light"`` route in the pair (#593): the same model at a lower
effort, for short everyday turns (a greeting, a direct question). It is chosen here,
by a cheap and conservative test of the owner's own words; a turn with files, an
explicit depth phrase, or any sign of lookup or action stays on the quick route.
"""
from __future__ import annotations

import re

from . import RouteRefused, check_selectable, selection_of, snapshot

# Deliberately anchored: quoted documents and earlier conversation are not routing
# instructions. Unrecognized important questions belong to the lead's judgement.
# "Look into this" is not here (#586): on the quick route MC says its scope in a line,
# then looks; only "think hard" or "go deep" pays for the deep call.
_QUICK = re.compile(r"^\s*(?:please\s+)?quick answer\b", re.I)
_DEEP = re.compile(
    r"^\s*(?:please\s+)?(?:think hard(?:er)?(?: about this)?|this is important|go deep)\b", re.I
)
_KINDS = {"quick", "deep"}
_OPTIONAL = {"light", "phrase_only"}

# ── #593: short turns ─────────────────────────────────────────────────────────────────────────
# A light turn is one of two shapes, and nothing else: a social line on its own, or a short
# general question. Anything that may need a tool, a record, or the conversation so far stays
# quick. The light route keeps the same tools; it only reasons less, so a wrong "light" costs
# some depth on one turn, never an action.
_LIGHT_MAX_CHARS = 80
_LIGHT_MAX_WORDS = 12
_SOCIAL = re.compile(
    r"^(?:(?:hi|hello|hey|hiya|howdy|yo|morning|evening|good (?:morning|afternoon|evening|night)"
    r"|thanks|thank you|thanks a lot|many thanks|cheers|bye|goodbye|good ?night|see you|see ya|later"
    r"|how are you|how are you doing|how(?:'|’)?s it going|how(?:'|’)?s your day|what(?:'|’)?s up|nice one|great|perfect)"
    r"(?:,?\s+(?:mc|master craftsman|there|again|so much|very much|doing|today|mate))*[\s!.?,]*)+$",
    re.I,
)
_QUESTION_START = re.compile(
    r"^(?:what|what(?:'|’)s|why|how|who|is|are|can|could|should|would|do|does|define|explain)\b", re.I)
# Words that point at something MC would have to look up, recall, or act on, or at the turns
# before this one. One hit keeps the turn quick.
_LOOKUP = re.compile(r"\b(?:" + "|".join([
    # shared context and recall
    "we", "we(?:'|’)ve", "we(?:'|’)re", "our", "us", "i", "i(?:'|’)m", "i(?:'|’)ve", "my", "me", "mine", "you", "your",
    "it", "its", "this", "that", "these", "those", "they", "them", "he", "she", "his", "her",
    "did", "decide", "decided", "remember", "recall", "earlier", "before", "last", "yesterday", "today", "tonight",
    "tomorrow", "now", "currently", "current", "latest", "recent", "recently", "still", "yet", "again", "status",
    # things in MiniMoi
    "prod", "production", "dev", "staging", "deploy", "deployed", "release", "server", "host", "container",
    "repo", "branch", "commit", "pr", "issue", "ticket", "log", "logs", "error", "test", "tests", "build",
    "file", "files", "doc", "docs", "document", "page", "link", "url", "vault", "record", "records", "note", "notes",
    "plan", "board", "job", "jobs", "mc", "cos", "grok", "gateway", "key", "budget", "cost", "spend",
    # actions and lookups
    "check", "look", "find", "search", "fetch", "show", "open", "read", "run", "list", "send", "post", "write",
    "draft", "fix", "make", "create", "add", "delete", "remove", "change", "update", "schedule", "remind",
    "approve", "price", "weather", "news", "score",
    # a choice among things said earlier
    "which", "better", "best", "worse", "worst", "option", "options", "alternative", "alternatives", "risk", "risks",
    "next", "else", "other", "others", "instead", "here", "there",
]) + r")\b", re.I)
_NOT_PLAIN = re.compile(r"[\n`>#@/\\|{}\[\]<=_*~]|\d|https?:|www\.|\w\.\w")

# Third shape (15 Nov production, MC and CoS): a short everyday German line, as Robert writes when he practises
# ("Die Sonne scheint heute.", "Danke, dass du mich korrigierst."). Both agents answered every such line on the strong
# route, 5–14 s each. It counts only when it is plainly German and carries no request: no German word for looking
# something up, recalling it, or acting, and none of MiniMoi's own nouns. A wrong "light" costs some depth on one turn.
_LIGHT_GERMAN_MAX_CHARS = 160
_LIGHT_GERMAN_MAX_WORDS = 24
_GERMAN = re.compile(r"\b(?:" + "|".join([
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich", "mir", "dir", "uns", "euch", "ihm", "ihn",
    "und", "oder", "aber", "dass", "daß", "weil", "wenn", "auch", "nicht", "kein", "keine", "noch", "schon", "sehr",
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "mit", "mein", "meine", "meiner",
    "meinem", "dein", "deine", "ist", "bin", "bist", "sind", "war", "hat", "habe", "hast", "haben", "wird", "würde",
    "möchte", "will", "kann", "danke", "bitte", "heute", "gestern", "morgen", "gut", "schön", "ja", "nein", "doch",
    "guten", "tag", "sonntag", "für", "auf", "zu", "im", "am", "vom", "zum", "zur", "fürs",
]) + r")\b", re.I)
_GERMAN_LOOKUP = re.compile(r"\b(?:" + "|".join([
    # looking up, recalling, acting
    "such\\w*", "find\\w*", "prüf\\w*", "zeig\\w*", "lies", "lese\\w*", "schreib\\w*", "erstell\\w*", "schick\\w*",
    "send\\w*", "speicher\\w*", "lösch\\w*", "änder\\w*", "öffne\\w*", "erinner\\w*", "notier\\w*", "plan\\w*",
    "termin\\w*", "status", "stand", "neu\\w*", "letzte\\w*", "was\\s+steht", "wie\\s+weit", "erklär\\w*",
    "warum", "wieso", "weshalb", "welche\\w*", "kannst\\s+du", "könntest\\s+du", "würdest\\s+du", "sag\\s+mir",
    # MiniMoi's own nouns
    "datei\\w*", "dokument\\w*", "notiz\\w*", "ticket\\w*", "issue\\w*", "vault", "board", "release", "deploy\\w*",
    "produktion", "server", "fehler\\w*", "kosten", "budget", "mc", "cos", "grok", "github", "repo\\w*",
]) + r")\b", re.I)


_EN_HOMOGRAPHS = {"die", "will", "war", "am", "tag", "bin", "es", "er", "an", "in", "so", "hat", "den"}


def _short_german_line(text: str) -> bool:
    if len(text) > _LIGHT_GERMAN_MAX_CHARS or _NOT_PLAIN.search(text) or explicit_depth(text):
        return False
    words = re.findall(r"[A-Za-zÄÖÜäöüß]+", text)
    if not 2 <= len(words) <= _LIGHT_GERMAN_MAX_WORDS:
        return False
    hits = [w.lower() for w in words if _GERMAN.fullmatch(w)]
    german = len(hits)
    umlaut = re.search(r"[äöüßÄÖÜ]", text) is not None
    plainly = umlaut or any(w not in _EN_HOMOGRAPHS for w in hits)   # "The war will end" is English
    return (plainly and (german >= 2 or (german >= 1 and umlaut)) and german * 4 >= len(words)
            and _GERMAN_LOOKUP.search(text) is None and _LOOKUP.search(text) is None)


def explicit_depth(owner_text: str) -> str | None:
    if _QUICK.match(owner_text):
        return "quick"
    if _DEEP.match(owner_text):
        return "deep"
    return None


def short_turn(owner_text: str) -> bool:
    """True only for a short social line, or a short general question needing nothing looked up (#593)."""
    if not isinstance(owner_text, str):
        return False
    text = owner_text.strip()
    if text and _short_german_line(text):
        return True
    if not text or len(text) > _LIGHT_MAX_CHARS or _NOT_PLAIN.search(text) or explicit_depth(text):
        return False
    words = text.split()
    if len(words) > _LIGHT_MAX_WORDS:
        return False
    if _SOCIAL.match(text):
        return True
    # "What are the risks?": a very short question about "the" something is about the thread, not the world.
    if len(words) <= 4 and re.search(r"\bthe\b", text, re.I):
        return False
    return (text.endswith("?") and text.count("?") == 1 and len(words) >= 3
            and _QUESTION_START.match(text) is not None and _LOOKUP.search(text) is None)


def validate_policy(catalogue: dict, role_name: str) -> dict | None:
    role = catalogue["roles"][role_name]
    policy = role.get("depth_routes")
    if policy is None:
        return None
    if (role_name not in ("mc-conversation", "cos-conversation") or not role.get("selectable")
            or not isinstance(policy, dict) or not _KINDS <= set(policy) <= _KINDS | _OPTIONAL
            or not all(isinstance(policy[k], str) for k in _KINDS | ({"light"} & set(policy)))
            or not isinstance(policy.get("phrase_only", False), bool)):
        raise ValueError("invalid conversation depth routes")
    kinds = _KINDS | ({"light"} & set(policy))
    routes = {kind: check_selectable(catalogue, role["agent"], policy[kind]) for kind in kinds}
    if any(r.get("runtime") != "openclaw" or not r.get("runtime_override", True) for r in routes.values()):
        raise ValueError("conversation depth requires explicit OpenClaw model routes")
    # Effort metadata is not transport. Until effort transport is proven, each
    # depth must have its own runtime model reference (including gateway alias).
    if len({(r["provider"], r["model"]) for r in routes.values()}) != len(kinds):
        raise ValueError("conversation depth routes must have distinct runtime models")
    if role["route"] not in (policy["quick"], policy["deep"]):
        raise ValueError("conversation default must belong to its depth routes")
    return policy


def for_owner_turn(catalogue: dict, role_name: str, conv: dict | None, owner_text: str, *,
                   attached: bool | None = None) -> dict:
    """Pin the selected route and deep continuation candidate at admission.

    An explicit conversation model outside the host's pair retains its existing
    semantics. The pair is not enabled by default in any shipped catalogue.
    ``attached`` says whether the turn carries files; only ``False`` (known: none) lets
    a short turn take the host's light route.
    """
    original = snapshot(catalogue, role_name, conv)
    policy = validate_policy(catalogue, role_name)
    if policy is None or original["route_id"] not in (policy["quick"], policy["deep"]):
        return original
    if not isinstance(owner_text, str):
        raise RouteRefused("depth_text_invalid", "The original message is unavailable. Nothing was sent.")
    requested = explicit_depth(owner_text)
    kind = requested or ("deep" if original["route_id"] == policy["deep"] else "quick")
    selection = selection_of(conv or {}, original["agent"])

    def pin(which):
        return snapshot(catalogue, role_name, {"routes": {original["agent"]: {
            **selection, "route_id": policy[which],
        }}})

    if (kind == "quick" and requested is None and "light" in policy and attached is False
            and original["route_id"] == policy["quick"] and short_turn(owner_text)):
        kind = "light"
    selected = pin(kind)
    selected["depth"] = {"kind": kind, "explicit": requested is not None,
                         "allow_continuation": kind == "quick" and requested is None
                         and not policy.get("phrase_only", False)}
    if selected["depth"]["allow_continuation"]:
        selected["depth"]["continuation_route"] = pin("deep")
    return selected
