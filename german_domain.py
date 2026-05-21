"""
German language domain — pure logic layer.

Both telegram_bot.py and html_server.py import from here.
Both @minimoi_cmd_bot and @minimoi_agent_bot route to the same Python entrypoint.

Group A: constants, data structures, pure functions (no I/O, no async, no Telegram).
Group B: file I/O, subprocess, LLM callers (no async, no Telegram).
Group C: resolver functions — sync, with optional progress_cb for mid-execution messages.
"""

import html
import re
import random
import time
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
    # grok-4-1-fast: proven in production on Curator scoring; better German capability,
    # lower latency for translation, writing correction, and phrase handling.
    {"name": "grok-fast",    "type": "xai",       "model": "grok-4-1-fast"},
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


# ─── Domain I/O — file loaders and savers ────────────────────────────────────

import json
import subprocess

_PHRASEBOOK_FILE       = GERMAN_DIR / "config" / "phrasebook.json"
_DRILL_STATE_FILE      = _BASE_DIR / "_active_drill_state.json"
_DRILL_LIST_STATE_FILE = _BASE_DIR / "_drill_list_state.json"


def _load_keyword_map_bot() -> dict:
    """Load keyword_map.json for bot routing — returns {} if missing."""
    path = GERMAN_DIR / "config" / "keyword_map.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _last_session_persona() -> str | None:
    """Return persona name from the most recent session JSON, or None."""
    sessions_dir = GERMAN_DIR / "sessions"
    if not sessions_dir.exists():
        return None
    sessions = sorted(sessions_dir.glob("*.json"))
    if not sessions:
        return None
    try:
        data = json.loads(sessions[-1].read_text(encoding="utf-8"))
        return data.get("persona")
    except Exception:
        return None


def _run(cmd, timeout=120, **kwargs):
    """Run a subprocess, return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"⏱ Timed out after {timeout}s", 1


def _german_agent_mode() -> str:
    cfg_path = GERMAN_DIR / "config" / "sync_config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text()).get("agent_mode", "direct")
        except Exception:
            pass
    return "direct"


def _phrase_next_id(phrases: list, today: str) -> str:
    """Global sequence — never resets per day, so short IDs (#001, #002...) stay unique."""
    prefix = f"ph_{today.replace('-', '')}_"
    all_ids = [p["id"] for p in phrases if isinstance(p, dict) and p.get("id")]
    if not all_ids:
        return f"{prefix}001"
    maxn = max(int(pid.rsplit("_", 1)[-1]) for pid in all_ids)
    return f"{prefix}{maxn + 1:03d}"


def _load_drill_pool() -> dict:
    pool_path = GERMAN_DIR / "config" / "drill_pool.json"
    if pool_path.exists():
        try:
            return json.loads(pool_path.read_text())
        except Exception:
            pass
    return {}


def _save_drill_pool(pool: dict) -> None:
    pool_path = GERMAN_DIR / "config" / "drill_pool.json"
    pool_path.write_text(json.dumps(pool, indent=2, ensure_ascii=False))


def _load_phrasebook() -> dict:
    if _PHRASEBOOK_FILE.exists():
        try:
            return json.loads(_PHRASEBOOK_FILE.read_text())
        except Exception:
            pass
    return {"phrases": []}


def _save_phrasebook(data: dict) -> None:
    _PHRASEBOOK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _write_drill_anki(state: dict) -> int:
    """Append friction items from completed drill to vienna_deck.csv. Returns card count written."""
    import csv as _csv
    vienna_csv = GERMAN_DIR / "anki" / "vienna_deck.csv"
    items = state.get("items", [])
    friction = [it for it in items if it["result"] != "drill-clean"]
    if not friction:
        return 0

    existing_fronts: set[str] = set()
    if vienna_csv.exists():
        try:
            with open(vienna_csv, newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    existing_fronts.add(row.get("Front", "").strip())
        except Exception:
            pass

    write_header = not vienna_csv.exists()
    written = 0
    try:
        with open(vienna_csv, "a", newline="", encoding="utf-8") as f:
            writer = _csv.writer(f, quoting=_csv.QUOTE_ALL)
            if write_header:
                writer.writerow(["Front", "Back", "Tags"])
            for it in friction:
                if it["front"].strip() in existing_fronts:
                    continue
                writer.writerow([it["front"], it["back"], it["tags"]])
                existing_fronts.add(it["front"].strip())
                written += 1
    except Exception as e:
        print(f"⚠️  Could not write drill Anki cards: {e}")
    return written


def _drill_completion_message(state: dict, reveal_line: str) -> str:
    """Build completion message with Anki summary. Writes cards as side effect."""
    score, total = state["score"], state["total"]
    written = _write_drill_anki(state)
    counts = {"drill-clean": 0, "drill-reinforced": 0, "needs-practice": 0}
    for it in state.get("items", []):
        counts[it["result"]] = counts.get(it["result"], 0) + 1
    lines = [f"{reveal_line}\n\nDrill complete! {score}/{total} correct."]
    if counts["drill-clean"] or counts["drill-reinforced"] or counts["needs-practice"]:
        lines.append(
            f"  ✅ Clean: {counts['drill-clean']}  "
            f"📝 Reinforced: {counts['drill-reinforced']}  "
            f"⚠️ Needs practice: {counts['needs-practice']}"
        )
    if written:
        lines.append(f"  {written} card(s) added to vienna_deck.csv — reimport Anki to sync to phone.")
    lines.append("(say 'again' to repeat)")
    return "\n".join(lines)


# ─── LLM calls ────────────────────────────────────────────────────────────────

def _call_llm(prompt: str, max_tokens: int = 300) -> str | None:
    """Call LLM providers in order, return text response or None if all fail."""
    import keyring as _keyring
    for provider in _LLM_PROVIDERS:
        try:
            if provider["type"] == "xai":
                api_key = _keyring.get_password("xai", "api_key")
                if not api_key:
                    continue
                from openai import OpenAI as _OpenAI
                client = _OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
                resp = client.chat.completions.create(
                    model=provider["model"],
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content.strip()
            elif provider["type"] == "anthropic":
                api_key = _keyring.get_password("anthropic", "api_key")
                if not api_key:
                    continue
                import anthropic as _anthropic
                client = _anthropic.Anthropic(api_key=api_key)
                msg = client.messages.create(
                    model=provider["model"],
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text.strip()
            elif provider["type"] == "ollama":
                from openai import OpenAI as _OpenAI
                client = _OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
                resp = client.chat.completions.create(
                    model=provider["model"],
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"⚠️  LLM provider '{provider['name']}' failed: {e}")
            continue
    return None


def _fetch_conjugations(verb: str) -> dict | None:
    """Fetch present-tense conjugations for any German verb via LLM, with fallback."""
    prompt = (
        f'Give me the present-tense conjugations of the German verb "{verb}" '
        f'for these persons: ich, du, er, wir, ihr, sie. '
        f'Also give a short English translation (infinitive). '
        f'Reply ONLY with a JSON object in this exact format, no extra text:\n'
        f'{{"verb":"{verb}","english":"...","conjugations":{{"ich":"...","du":"...","er":"...","wir":"...","ihr":"...","sie":"..."}}}}'
    )
    raw = _call_llm(prompt, max_tokens=200)
    if not raw:
        return None
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"⚠️  Failed to parse conjugation JSON for '{verb}': {e}\nRaw: {raw[:100]}")
        return None


def _fetch_phrases(verb: str, english: str) -> list:
    """Generate translation phrases for a verb via LLM. Returns list of {english, german} dicts."""
    prompt = (
        f'Generate 6 natural German phrases using the verb "{verb}" ({english}). '
        f'Mix informal (du/ich) and formal (Sie). Vienna-relevant contexts (café, hotel, transport, small talk). '
        f'Reply ONLY with a JSON array, no extra text:\n'
        f'[{{"english":"...","german":"..."}},{{"english":"...","german":"..."}}]'
    )
    raw = _call_llm(prompt, max_tokens=400)
    if not raw:
        return []
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        return [p for p in result if isinstance(p, dict) and "english" in p and "german" in p]
    except Exception as e:
        print(f"⚠️  Failed to parse phrases JSON for '{verb}': {e}\nRaw: {raw[:100]}")
        return []


# ─── Async-free resolvers (Group C) ───────────────────────────────────────────

def _resolve_verb(verb_lower: str, progress_cb=None) -> dict | None:
    """Find verb in pool or fetch via LLM and cache. Calls progress_cb(msg) for progress."""
    pool = _load_drill_pool()
    entry = _lookup_verb(pool, verb_lower)
    if entry:
        return entry
    if progress_cb:
        progress_cb(f"Looking up '{verb_lower}'…")
    entry = _fetch_conjugations(verb_lower)
    if not entry:
        if progress_cb:
            progress_cb(f"Couldn't look up '{verb_lower}' — check spelling.")
        return None
    on_demand_verbs = pool.setdefault("on_demand", {}).setdefault("verbs", [])
    on_demand_verbs.append(entry)
    _save_drill_pool(pool)
    return entry


def _resolve_phrases(entry: dict, progress_cb=None) -> list:
    """Return cached phrases for a verb entry, or fetch+cache via LLM."""
    if entry.get("phrases"):
        return entry["phrases"]
    if progress_cb:
        progress_cb(f"Generating phrases for {entry['verb']}…")
    phrases = _fetch_phrases(entry["verb"], entry.get("english", ""))
    if not phrases:
        if progress_cb:
            progress_cb(f"Couldn't generate phrases for '{entry['verb']}'.")
        return []
    pool = _load_drill_pool()
    for section in (pool.get("core", {}).get("verbs", []), pool.get("on_demand", {}).get("verbs", [])):
        for v in section:
            if isinstance(v, dict) and v.get("verb", "").lower() == entry["verb"].lower():
                v["phrases"] = phrases
                break
    _save_drill_pool(pool)
    entry["phrases"] = phrases
    return phrases


def _resolve_drill_verb(target_lower: str, progress_cb=None) -> dict | None:
    """Extract verb from trigger text, look up or fetch. Returns entry or None."""
    pool = _load_drill_pool()
    all_verbs = _all_verb_entries(pool)
    entry = next((v for v in all_verbs if v["verb"].lower() in target_lower), None)
    if entry:
        return entry
    stop_words = {"drill", "german", "mode", "start", "verb", "my", "errors", "mistakes",
                  "level", "translate", "l2", "phrase", "phrases"}
    words = [w for w in target_lower.split() if w not in stop_words and len(w) > 3 and not w.isdigit()]
    if words:
        word = words[0]
        all_scenes = {s for v in all_verbs for s in v.get("scenes", [])}
        if word in all_scenes:
            scene_verbs = [v for v in all_verbs if word in v.get("scenes", [])]
            if scene_verbs:
                chosen = random.choice(scene_verbs)
                if progress_cb:
                    progress_cb(f"'{word}' is a scene, not a verb — picking {chosen['verb']} ({chosen.get('english', '')}) from that scene.")
                return chosen
        return _resolve_verb(word, progress_cb=progress_cb)
    return random.choice(all_verbs) if all_verbs else None


# ─── Lesen — article pool (Group D: HTML interface) ──────────────────────────

import feedparser
import datetime
from bs4 import BeautifulSoup

_LESEN_ARTICLES_FILE = GERMAN_DIR / "config" / "lesen_articles.json"
_LESEN_FEEDBACK_FILE = GERMAN_DIR / "config" / "lesen_feedback.json"
_LESEN_SOURCES_FILE  = GERMAN_DIR / "config" / "lesen_sources.json"
_LESEN_FILTERS_FILE  = GERMAN_DIR / "config" / "lesen_filters.json"

# Sources are config-driven — edit lesen_sources.json to add/remove/disable.
# All sources must be non-paywalled. Verify on addition. Remove immediately if paywall detected.
def _load_rss_sources() -> list:
    try:
        data = json.loads(_LESEN_SOURCES_FILE.read_text())
        return [s for s in data.get("sources", []) if s.get("active", True)]
    except Exception:
        return []


def _load_lesen_blocked_keywords() -> list:
    try:
        data = json.loads(_LESEN_FILTERS_FILE.read_text())
        return [kw.lower() for kw in data.get("blocked_keywords", [])]
    except Exception:
        return []


def _load_lesen_articles() -> dict:
    if _LESEN_ARTICLES_FILE.exists():
        try:
            return json.loads(_LESEN_ARTICLES_FILE.read_text())
        except Exception:
            pass
    return {"articles": [], "last_fetched": None}


def _save_lesen_articles(data: dict) -> None:
    _LESEN_ARTICLES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _strip_html(raw: str) -> str:
    return BeautifulSoup(raw or "", "html.parser").get_text(separator=" ").strip()


def get_lesen_pool() -> list:
    """Return active + pinned articles, pinned first. Blocked keywords filtered at display time."""
    data = _load_lesen_articles()
    blocked = _load_lesen_blocked_keywords()
    pool = [
        a for a in data["articles"]
        if a["status"] in ("active", "pinned")
        and not any(kw in a["title"].lower() for kw in blocked)
    ]
    return sorted(pool, key=lambda a: 0 if a["status"] == "pinned" else 1)


def fetch_lesen_articles() -> list:
    """Fetch all RSS sources, deduplicate against existing pool. Returns new article dicts."""
    data = _load_lesen_articles()
    existing_urls = {a["url"] for a in data["articles"]}
    blocked = _load_lesen_blocked_keywords()
    new_articles = []
    today = datetime.date.today().isoformat()

    for source in _load_rss_sources():
        try:
            feed = feedparser.parse(source["url"])
            for i, entry in enumerate(feed.entries[:6]):
                url = entry.get("link", "")
                if not url or url in existing_urls:
                    continue
                title_lower = entry.get("title", "").lower()
                if any(kw in title_lower for kw in blocked):
                    continue
                raw_summary = ""
                if entry.get("content"):
                    raw_summary = entry.content[0].value
                elif entry.get("summary"):
                    raw_summary = entry.summary
                summary = _strip_html(raw_summary)[:800]
                if not summary:
                    summary = _strip_html(entry.get("title", ""))
                art_id = f"art_{today.replace('-','')}_{source['name'][:3].lower()}_{i:02d}"
                new_articles.append({
                    "id": art_id,
                    "title": html.unescape(entry.get("title", "").strip()),
                    "url": url,
                    "source": source["name"],
                    "date_fetched": today,
                    "summary": summary,
                    "status": "active",
                    "feedback": None,
                })
                existing_urls.add(url)
        except Exception as e:
            print(f"⚠️  Lesen RSS fetch failed for {source['name']}: {e}")

    return new_articles


def refresh_lesen_feed() -> dict:
    """Fetch new articles, append to pool, save. Returns summary."""
    data = _load_lesen_articles()
    new_articles = fetch_lesen_articles()
    data["articles"].extend(new_articles)
    data["last_fetched"] = datetime.datetime.now().isoformat()
    _save_lesen_articles(data)
    pool_size = len([a for a in data["articles"] if a["status"] in ("active", "pinned")])
    return {"added": len(new_articles), "pool_size": pool_size}


def lesen_action(article_id: str, action: str) -> None:
    """Record article action: pos/neg/pin/unpin. Updates pool and feedback log."""
    data = _load_lesen_articles()
    status_map = {"pos": "dismissed_pos", "neg": "dismissed_neg", "pin": "pinned", "unpin": "active"}
    for article in data["articles"]:
        if article["id"] == article_id:
            new_status = status_map.get(action)
            if new_status:
                article["status"] = new_status
                article["feedback"] = action
            break
    _save_lesen_articles(data)

    feedback_data = {"entries": []}
    if _LESEN_FEEDBACK_FILE.exists():
        try:
            feedback_data = json.loads(_LESEN_FEEDBACK_FILE.read_text())
        except Exception:
            pass
    feedback_data["entries"].append({
        "article_id": article_id,
        "action": action,
        "timestamp": datetime.datetime.now().isoformat(),
    })
    _LESEN_FEEDBACK_FILE.write_text(json.dumps(feedback_data, indent=2, ensure_ascii=False))


def translate_phrase(phrase: str) -> tuple[str, bool, dict]:
    """Translate a German word/phrase to English. Checks phrasebook cache first.
    Returns (translation, cached, timing) where timing = {total_ms, llm_ms}."""
    t0 = time.perf_counter()
    phrase_lower = phrase.lower().strip()
    phrasebook = _load_phrasebook()
    for entry in phrasebook.get("phrases", []):
        if entry.get("german", "").lower().strip() == phrase_lower:
            total_ms = round((time.perf_counter() - t0) * 1000)
            return entry.get("english", ""), True, {"total_ms": total_ms, "llm_ms": 0}

    prompt = (
        f"Translate this German word or phrase to English. "
        f"Reply with only the English translation, nothing else.\n"
        f"German: {phrase}"
    )
    t_llm = time.perf_counter()
    result = _call_llm(prompt, max_tokens=60)
    llm_ms = round((time.perf_counter() - t_llm) * 1000)
    total_ms = round((time.perf_counter() - t0) * 1000)
    return (result.strip() if result else ""), False, {"total_ms": total_ms, "llm_ms": llm_ms}


def save_lesen_phrase(german: str, english: str, context_sentence: str, article_title: str) -> dict:
    """Save a captured phrase to phrasebook with lesen provenance."""
    data = _load_phrasebook()
    today = datetime.date.today().isoformat()
    new_entry = {
        "id": _phrase_next_id(data.get("phrases", []), today),
        "german": german,
        "english": english,
        "scene": "lesen",
        "added": today,
        "status": "library",
        "verb_hint": "",
        "practice_count": 0,
        "last_practiced": None,
        "source_sentence": context_sentence,
        "source_article": article_title,
    }
    data.setdefault("phrases", []).append(new_entry)
    _save_phrasebook(data)
    return new_entry


# ─── Schreiben — writing sessions (Group D: HTML interface) ──────────────────

_WRITING_SESSIONS_FILE = GERMAN_DIR / "config" / "writing_sessions.json"
_NOTES_FILE            = GERMAN_DIR / "config" / "notes.json"

_TAGEBUCH_PROMPTS = [
    "Was hast du heute in Wien gesehen?",
    "Beschreib deinen Morgen auf Deutsch.",
    "Was war heute interessant oder überraschend?",
    "Beschreib ein Gespräch, das du heute geführt hast.",
    "Was hast du gegessen oder getrunken? Wie hat es geschmeckt?",
    "Was planst du für morgen?",
    "Beschreib das Wetter und wie es dich gestimmt hat.",
    "Was hast du heute auf Deutsch gelesen oder gehört?",
]


def get_tagebuch_prompts() -> list:
    return list(_TAGEBUCH_PROMPTS)


def correct_writing(text: str, context: str = "") -> dict:
    """Submit German text for LLM correction. Returns {corrected, notes}."""
    context_block = f"Context (the article the learner was reading):\n{context}\n" if context else ""
    prompt = (
        "You are a German language tutor. Correct the following German text written by "
        "an intermediate learner.\n"
        "Return ONLY valid JSON with exactly these fields:\n"
        '- "corrected": the corrected text (string)\n'
        '- "notes": array of short strings, each explaining one correction '
        "(gender, case, word order, vocabulary). Maximum 5 notes.\n\n"
        f"{context_block}"
        f"Text to correct:\n{text}"
    )
    raw = _call_llm(prompt, max_tokens=400)
    if not raw:
        return {"corrected": text, "notes": ["(Correction unavailable)"]}
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        return {
            "corrected": result.get("corrected", text),
            "notes": result.get("notes", []),
        }
    except Exception:
        return {"corrected": text, "notes": ["(Correction unavailable)"]}


def save_note(article_id: str, article_title: str, original: str,
              corrected: str, rewritten: str) -> dict:
    """Save a Notizen entry (write→correct→rewrite) linked to a Lesen article."""
    import uuid
    data = {"notes": []}
    if _NOTES_FILE.exists():
        try:
            data = json.loads(_NOTES_FILE.read_text())
        except Exception:
            pass
    note = {
        "note_id": str(uuid.uuid4()),
        "article_id": article_id,
        "article_title": article_title,
        "date": datetime.date.today().isoformat(),
        "original": original,
        "corrected": corrected,
        "rewritten": rewritten,
        "saved": True,
    }
    data.setdefault("notes", []).append(note)
    _NOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return note


def save_writing_entry(mode: str, text_original: str, text_corrected: str = "",
                       notes: list = None, context_title: str = "") -> dict:
    """Append a writing session entry. Trims to last 50. Returns new entry."""
    data = {"entries": []}
    if _WRITING_SESSIONS_FILE.exists():
        try:
            data = json.loads(_WRITING_SESSIONS_FILE.read_text())
        except Exception:
            pass
    entry = {
        "id": f"ws_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "mode": mode,
        "text_original": text_original,
        "text_corrected": text_corrected,
        "notes": notes or [],
        "timestamp": datetime.datetime.now().isoformat(),
        "context_title": context_title,
    }
    data.setdefault("entries", []).append(entry)
    data["entries"] = data["entries"][-50:]
    _WRITING_SESSIONS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return entry
