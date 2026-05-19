"""
German language domain — pure logic layer.

Both telegram_bot.py and html_server.py import from here.
Both @minimoi_cmd_bot and @minimoi_agent_bot route to the same Python entrypoint.

Group A: constants, data structures, pure functions (no I/O, no async, no Telegram).
"""

import re
import random
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).parent
GERMAN_BASE = _BASE_DIR / "_NewDomains" / "language-german"
GERMAN_DIR  = GERMAN_BASE / "language" / "german"
VENV_PYTHON = _BASE_DIR / "venv" / "bin" / "python3"
ROBERT_CHAT_ID = 8379221702

# ─── Session / intent patterns ────────────────────────────────────────────────

_WRITING_RE = re.compile(
    r"(writing session|written session|"
    r"session.{0,20}writing|writing.{0,20}session|"
    r"next.{0,20}german.{0,20}writing|german.{0,20}writing)",
    re.I,
)
_SESSION_RE = re.compile(
    r"(pull today.?s german session|what.?s my german session|"
    r"give me today.?s german prompt|german session please|"
    r"german session today|today.?s german session|"
    r"german session|session german|next session|next lesson|"
    r"start.{0,30}german|start.{0,30}session.{0,30}german|"
    r"let.{0,10}s.{0,10}german)",
    re.I,
)
_CONJUGATE_RE = re.compile(r'\bconjugate\s+(\w+)\b', re.I)
_DRILL_RE = re.compile(
    r'(german\s+drill|drill\s+german|drill\s+mode|start\s+drill|drill\s+(?:level\s*2|l2|translate|verb|noun|word|vocab|my\s+mistakes|errors?|\d+\s+[a-zäöüß]{3,}|[a-zäöüß]{3,}))',
    re.I,
)
_DRILL_L2_RE = re.compile(r'\b(?:level\s*2|l2|translate|phrases?|2)\b', re.I)
_DRILL_CTL_RE = re.compile(r'\b(?:end(?:\s+drill)?|done|stop|quit|enough)\b', re.I)
_DRILL_AGAIN_RE = re.compile(r'\b(?:again|repeat|once more|one more)\b', re.I)
_DRILL_LIST_RE = re.compile(r'\b(?:drill\s+(?:\w+\s+)?list|list\s+(?:drills?|verbs?)|verbs?\s+list|show\s+verbs?|what\s+verbs?)\b', re.I)
_DRILL_MORE_RE = re.compile(r'\b(?:more|next)\b', re.I)
_SKIP_LESSON_RE = re.compile(
    r'\b(?:skip\s+(?:lesson|session|this\s+one)|next\s+one|different\s+scene)\b',
    re.I,
)
_AGAIN_RE = re.compile(
    r"\b(again|one more|repeat|same (session|persona|scenario)|do it again)\b",
    re.I,
)

# ─── Phrase patterns ──────────────────────────────────────────────────────────

_PHRASE_CAPTURE_RE = re.compile(
    r'\b(?:save\s+(?:a\s+)?phrase|capture\s+(?:this|a\s+phrase|phrase)|'
    r'add?\s+(?:a\s+)?phrase|phrase\s+(?:add|capture|merken|speichern)|'
    r'new\s+phrase|start\s+phrase\s+capture|neue\s+phrase|das\s+merken)\b',
    re.I
)
_PHRASE_PRACTICE_VOICE_RE = re.compile(
    r'\b(?:phra?se\s+practice|practice\s+(?:a\s+)?phra?se|phrase\s+üben)\b', re.I
)
_PHRASE_LIST_VOICE_RE = re.compile(
    r'\b(?:phrase\s+list|list\s+(?:my\s+)?phrases?|show\s+(?:my\s+)?phrases?|my\s+phrases?)\b', re.I
)
_PHRASE_LIST_MORE_VOICE_RE = re.compile(
    r'\b(?:phrase\s+more|more\s+phrases?|next\s+phrases?)\b', re.I
)

_SPOKEN_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# ─── LLM provider list ────────────────────────────────────────────────────────

_LLM_PROVIDERS = [
    {"name": "grok-mini",    "type": "xai",       "model": "grok-3-mini"},
    {"name": "claude-haiku", "type": "anthropic",  "model": "claude-haiku-4-5-20251001"},
    {"name": "ollama-gemma", "type": "ollama",     "model": "gemma3:1b"},
]

# ─── German contraction expansion ────────────────────────────────────────────

_DE_CONTRACTIONS = {
    "beim": "bei dem",
    "im":   "in dem",
    "am":   "an dem",
    "vom":  "von dem",
    "zum":  "zu dem",
    "zur":  "zu der",
    "ans":  "an das",
    "ins":  "in das",
    "aufs": "auf das",
    "ums":  "um das",
}
_DE_CONTRACTION_RE = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in _DE_CONTRACTIONS) + r')\b', re.IGNORECASE
)

# ─── Drill constants ──────────────────────────────────────────────────────────

_DRILL_PERSONS   = ["ich", "du", "er", "wir", "ihr", "sie"]
_PERSONS_DISPLAY = ["ich", "du", "er/sie/es", "wir", "ihr", "sie/Sie"]
_PERSONS_POOL    = ["ich", "du", "er",         "wir", "ihr", "sie"]

# ─── Pure functions ───────────────────────────────────────────────────────────

def _resolve_keyword_intent(text: str, keyword_map: dict) -> tuple[str, str] | None:
    """
    Match trigger words from keyword_map against text.
    Returns (persona_name, scenario) or None.

    Safety rule: a lone trigger word with no surrounding context does not fire.
    The message must have >=2 words OR contain 'german'/'session' alongside the keyword.
    """
    if not keyword_map:
        return None
    words = text.lower().split()
    if len(words) < 2:
        return None
    for persona_name, data in keyword_map.items():
        for trigger in data.get("trigger_words", []):
            if trigger.lower() in text.lower():
                return (persona_name, data.get("default_scenario", ""))
    return None


def _parse_spoken_id(text: str) -> str:
    """Convert spoken phrase id to zero-padded string: 'one' → '001', '3' → '003'."""
    t = re.sub(r'^number\s+', '', text.strip().strip(".,!? "), flags=re.I).strip()
    m = re.search(r'\d+', t)
    if m:
        return f"{int(m.group()):03d}"
    word = t.lower().strip(".,")
    return f"{_SPOKEN_NUMBERS[word]:03d}" if word in _SPOKEN_NUMBERS else t


def _all_verb_entries(pool: dict) -> list:
    """Return all verb entries from core + on_demand, core takes precedence."""
    core = [v for v in pool.get("core", {}).get("verbs", []) if isinstance(v, dict) and "verb" in v]
    on_demand = [v for v in pool.get("on_demand", {}).get("verbs", []) if isinstance(v, dict) and "verb" in v]
    core_names = {v["verb"].lower() for v in core}
    return core + [v for v in on_demand if v["verb"].lower() not in core_names]


def _lookup_verb(pool: dict, verb_lower: str) -> dict | None:
    return next((v for v in _all_verb_entries(pool) if v["verb"].lower() == verb_lower), None)


def _expand_contractions(text: str) -> str:
    """Expand German preposition+article contractions to canonical long form."""
    return _DE_CONTRACTION_RE.sub(lambda m: _DE_CONTRACTIONS[m.group(0).lower()], text)


def _normalize_answer(text: str) -> str:
    """Lowercase, strip punctuation, expand contractions, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = _expand_contractions(text)
    return " ".join(text.split())


def _spell_feedback(normalized_answer: str, expected: str = "") -> str | None:
    """Return feedback for misspelled German words. Input must already be normalized (no punctuation, lowercase).
    If expected is given, only suggest corrections that appear in the expected answer."""
    try:
        from spellchecker import SpellChecker
        from difflib import SequenceMatcher
        checker = SpellChecker(language="de")
        expected_words = set(expected.split()) if expected else set()
        words = [w for w in normalized_answer.split() if len(w) >= 3]
        unknown = checker.unknown(words)
        if not unknown:
            return None
        parts = []
        for w in unknown:
            candidates = checker.candidates(w) or set()
            best = max(candidates, key=lambda c: SequenceMatcher(None, w, c).ratio(), default=None)
            if not best or SequenceMatcher(None, w, best).ratio() < 0.80:
                continue
            if expected_words and best not in expected_words:
                continue
            parts.append(f"'{w}' → '{best}'?")
        return ("Check spelling: " + ", ".join(parts)) if parts else None
    except Exception:
        return None


def _item_tag(wrong_count: int, hint_used: bool, auto_revealed: bool) -> str:
    if auto_revealed or hint_used or wrong_count >= 2:
        return "needs-practice"
    if wrong_count == 1:
        return "drill-reinforced"
    return "drill-clean"


def _drill_prompt(state: dict) -> str:
    """Level 1 prompt: person fill-in."""
    person = state["current"]
    verb = state["verb"]
    english = state["english"]
    total = len(state["queue"])
    return f"{verb} ({english}) — {state['pos']+1}/{total}\n\n{person} ___?"


def _l2_prompt(state: dict) -> str:
    """Level 2 prompt: translate this phrase."""
    idx = state["queue"][state["pos"]]
    phrase = state["phrases"][idx]
    total = len(state["queue"])
    return f"{state['pos']+1}/{total}\n\nHow do you say:\n\"{phrase['english']}\""


def _start_drill_state(entry: dict) -> dict:
    queue = random.sample(_DRILL_PERSONS, len(_DRILL_PERSONS))
    return {
        "verb":             entry["verb"],
        "english":          entry.get("english", ""),
        "conjugations":     entry.get("conjugations", {}),
        "queue":            queue,
        "pos":              0,
        "current":          queue[0],
        "score":            0,
        "total":            0,
        "retry":            False,
        "items":            [],
        "hint_used_current": False,
        "l1_worst_tag":     "drill-clean",
    }


def _record_l2_item(state: dict, phrase: dict, wrong_count: int, auto_revealed: bool) -> None:
    tag = _item_tag(wrong_count, state.get("hint_used_current", False), auto_revealed)
    state["items"].append({
        "front":  phrase["english"],
        "back":   phrase["german"],
        "result": tag,
        "tags":   f"{tag} Vienna phrase {state['verb']}",
    })
    state["hint_used_current"] = False


def _record_l1_person(state: dict, wrong_count: int, auto_revealed: bool) -> None:
    tag = _item_tag(wrong_count, state.get("hint_used_current", False), auto_revealed)
    priority = {"drill-clean": 0, "drill-reinforced": 1, "needs-practice": 2}
    if priority.get(tag, 0) > priority.get(state.get("l1_worst_tag", "drill-clean"), 0):
        state["l1_worst_tag"] = tag
    state["hint_used_current"] = False


def _finalize_l1_items(state: dict) -> None:
    """Convert l1_worst_tag into one Anki item if any friction occurred."""
    tag = state.get("l1_worst_tag", "drill-clean")
    if tag == "drill-clean":
        return
    conj = state.get("conjugations", {})
    table = " / ".join(
        f"{dp} {conj.get(pp, '?')}"
        for dp, pp in zip(_PERSONS_DISPLAY, _PERSONS_POOL)
    )
    state["items"].append({
        "front":  f"{state['verb']} — {state.get('english', '')}",
        "back":   table,
        "result": tag,
        "tags":   f"{tag} Vienna conjugation",
    })
