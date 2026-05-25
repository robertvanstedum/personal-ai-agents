"""
German language domain — pure logic layer.

Both telegram_bot.py and html_server.py import from here.
Both @minimoi_cmd_bot and @minimoi_agent_bot route to the same Python entrypoint.

Group A: constants, data structures, pure functions (no I/O, no async, no Telegram).
Group B: file I/O, subprocess, LLM callers (no async, no Telegram).
Group C: resolver functions — sync, with optional progress_cb for mid-execution messages.
"""

import fcntl
import html
import os
import re
import tempfile
import random
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).parent
GERMAN_BASE = _BASE_DIR / "_NewDomains" / "language-german"
GERMAN_DIR  = GERMAN_BASE / "language" / "german"
VENV_PYTHON = _BASE_DIR / "venv" / "bin" / "python3"
ROBERT_CHAT_ID = 8379221702
DEFAULT_USER = os.environ.get("GERMAN_USER", "robert")

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
    r"let.{0,10}s.{0,10}german|"
    r"let.?s\s+practice|\bready\b|\bbereit\b)",
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
    r"\b(again|one more|once more|repeat|same (session|persona|scenario|one)|do it again)\b",
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
    r'\b(?:phra?se\s+practice|phase\s+practice|practice\s+(?:a\s+)?phra?se|practice\s+phase|phrase\s+üben)\b', re.I
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


def safe_read_json(path: Path) -> dict:
    """Read JSON with shared lock. Returns {} if file absent, corrupt, or unreadable."""
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (OSError, PermissionError) as e:
        print(f"⚠️  safe_read_json failed for {path}: {e}")
        return {}


def safe_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename. Exclusive lock. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, prefix='.tmp_', suffix='.json'
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except (OSError, PermissionError) as e:
        print(f"⚠️  safe_write_json failed for {path}: {e}")
        raise


_PHRASEBOOK_FILE       = GERMAN_DIR / "config" / "phrasebook.json"
_DRILL_STATE_FILE      = _BASE_DIR / "_active_drill_state.json"
_DRILL_LIST_STATE_FILE = _BASE_DIR / "_drill_list_state.json"
_PERSONA_MEMORY_FILE   = GERMAN_DIR / "config" / "persona_memory.json"
_PROGRESS_FILE         = GERMAN_DIR / "progress.json"
_KEYWORD_MAP_FILE      = GERMAN_DIR / "config" / "keyword_map.json"
_PROMPTS_DIR           = GERMAN_DIR / "config" / "prompts"


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
    data = safe_read_json(_PHRASEBOOK_FILE)
    return data if data else {"phrases": []}


def _save_phrasebook(data: dict) -> None:
    safe_write_json(_PHRASEBOOK_FILE, data)


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
_LESEN_ARCHIV_FILE   = GERMAN_DIR / "config" / "lesen_archiv.json"

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


def get_lesen_pool(category: str | None = None) -> list:
    """Return active articles from today and yesterday only. Older articles belong in Archiv.

    Args:
        category: Optional filter — 'alltag' | 'kultur' | 'politik' | 'wien'.
                  None returns all categories.
    """
    data = _load_lesen_articles()
    blocked = _load_lesen_blocked_keywords()
    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    # Source→category map for articles that pre-date category tagging
    sources = _load_rss_sources()
    source_category_map = {s["name"]: s.get("category", "wien") for s in sources}
    pool = []
    for a in data["articles"]:
        if a["status"] != "active":
            continue
        if a.get("date_fetched") not in (today, yesterday):
            continue
        if any(kw in a["title"].lower() for kw in blocked):
            continue
        # Derive category on the fly for articles that don't have one yet
        if "category" not in a:
            a["category"] = source_category_map.get(a.get("source", ""), "wien")
        pool.append(a)
    if category:
        pool = [a for a in pool if a.get("category") == category]
    return pool


def categorize_article(title: str, summary: str,
                       source_category: str = "wien") -> str:
    """LLM-based categorization of a German news article.

    Uses the source-based category as a hint and fallback.
    Returns: 'alltag' | 'kultur' | 'politik' | 'wien'
    """
    prompt = (
        "Categorize this German news article into exactly one category.\n"
        "Return only the category name, nothing else.\n\n"
        "Note: articles are from Austrian publications.\n"
        "Use Austrian German register and context to judge.\n\n"
        "Categories:\n"
        "- alltag: domestic life, health, consumer topics, sport,\n"
        "  morning-show register, everyday practical content\n"
        "- kultur: arts, music, film, theatre, festivals, exhibitions,\n"
        "  pop culture\n"
        "- politik: politics, economics, government, law,\n"
        "  international news, business\n"
        "- wien: Vienna/Austrian local news, neighborhoods, parks,\n"
        "  transport, city life, Austrian geography and culture\n\n"
        f"Source category hint (may be correct or too broad): {source_category}\n"
        f"Article title: {title}\n"
        f"Summary: {summary[:200]}\n\n"
        "Category:"
    )
    result = _call_llm(prompt, max_tokens=10)
    if not result:
        print(f"⚠️  categorize_article: LLM returned None for '{title[:60]}'")
        return source_category
    category = result.strip().lower()
    valid = ("alltag", "kultur", "politik", "wien")
    if category not in valid:
        print(f"⚠️  categorize_article: unexpected result '{category}' for '{title[:60]}'")
        return source_category
    return category


def fetch_lesen_articles() -> list:
    """Fetch all RSS sources, deduplicate against existing pool. Returns new article dicts."""
    data = _load_lesen_articles()
    existing_urls = {a["url"] for a in data["articles"]}
    blocked = _load_lesen_blocked_keywords()
    new_articles = []
    today = datetime.date.today().isoformat()
    all_sources = _load_rss_sources()
    source_category_map = {s["name"]: s.get("category", "wien") for s in all_sources}

    for source in all_sources:
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
                title_clean = html.unescape(entry.get("title", "").strip())
                source_cat  = source_category_map.get(source["name"], "wien")
                category    = categorize_article(title_clean, summary,
                                                 source_category=source_cat)
                new_articles.append({
                    "id": art_id,
                    "title": title_clean,
                    "url": url,
                    "source": source["name"],
                    "category": category,
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
    pool_size = len(get_lesen_pool())
    return {"added": len(new_articles), "pool_size": pool_size}


def lesen_action(article_id: str, action: str) -> None:
    """Record article action: pos/neg/pin/unpin. Updates pool and feedback log."""
    data = _load_lesen_articles()
    status_map = {"pos": "dismissed_pos", "neg": "dismissed_neg", "pin": "archived", "unpin": "active"}
    archived_article = None
    for article in data["articles"]:
        if article["id"] == article_id:
            new_status = status_map.get(action)
            if new_status:
                article["status"] = new_status
                article["feedback"] = action
            if action == "pin":
                archived_article = article
            break
    _save_lesen_articles(data)

    if action == "pin" and archived_article:
        archiv_data = {"archived": []}
        if _LESEN_ARCHIV_FILE.exists():
            try:
                archiv_data = json.loads(_LESEN_ARCHIV_FILE.read_text())
            except Exception:
                pass
        archiv_data["archived"].append({
            **{k: archived_article[k] for k in ("id", "title", "url", "source", "date_fetched", "summary") if k in archived_article},
            "archived_at": datetime.datetime.now().isoformat(),
            "via": "merken",
        })
        _LESEN_ARCHIV_FILE.write_text(json.dumps(archiv_data, indent=2, ensure_ascii=False))

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


_deepl_client: object | None = None  # cached — avoids HTTP session overhead per call


def _get_deepl_client():
    """Return a cached DeepL Translator, initializing on first call."""
    global _deepl_client
    if _deepl_client is not None:
        return _deepl_client
    try:
        import deepl as _deepl
        import keyring as _kr
        api_key = _kr.get_password("deepl", "api_key")
        if not api_key:
            return None
        _deepl_client = _deepl.Translator(api_key)
        return _deepl_client
    except Exception:
        return None


def _translate_with_deepl(phrase: str) -> str | None:
    """Translate German phrase to English via DeepL. Returns None on any failure."""
    try:
        translator = _get_deepl_client()
        if not translator:
            return None
        result = translator.translate_text(
            phrase,
            source_lang="DE",
            target_lang="EN-US",
        )
        return result.text
    except Exception:
        global _deepl_client
        _deepl_client = None  # reset on error so next call retries init
        return None


def translate_phrase(phrase: str) -> tuple[str, bool, dict]:
    """Translate a German word/phrase to English.
    Hierarchy: phrasebook cache → DeepL → LLM fallback.
    Returns (translation, cached, timing) where timing = {total_ms, deepl_ms, llm_ms}."""
    t0 = time.perf_counter()
    phrase_lower = phrase.lower().strip()

    # Layer 1: phrasebook cache
    phrasebook = _load_phrasebook()
    for entry in phrasebook.get("phrases", []):
        if entry.get("german", "").lower().strip() == phrase_lower:
            total_ms = round((time.perf_counter() - t0) * 1000)
            return entry.get("english", ""), True, {"total_ms": total_ms, "deepl_ms": 0, "llm_ms": 0}

    # Layer 2: DeepL
    t_deepl = time.perf_counter()
    deepl_result = _translate_with_deepl(phrase)
    deepl_ms = round((time.perf_counter() - t_deepl) * 1000)
    if deepl_result:
        total_ms = round((time.perf_counter() - t0) * 1000)
        return deepl_result, False, {"total_ms": total_ms, "deepl_ms": deepl_ms, "llm_ms": 0}

    # Layer 3: LLM fallback
    prompt = (
        f"Translate this German word or phrase to English. "
        f"Reply with only the English translation, nothing else.\n"
        f"German: {phrase}"
    )
    t_llm = time.perf_counter()
    result = _call_llm(prompt, max_tokens=60)
    llm_ms = round((time.perf_counter() - t_llm) * 1000)
    total_ms = round((time.perf_counter() - t0) * 1000)
    return (result.strip() if result else ""), False, {"total_ms": total_ms, "deepl_ms": 0, "llm_ms": llm_ms}


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


# ─── Wörter / Phrasebook ──────────────────────────────────────────────────────

def get_phrasebook_entries(source: str = None, status: str = None,
                           user: str = DEFAULT_USER) -> list:
    """
    Returns phrasebook entries sorted by date desc.
    source: scene tag filter — 'lesen', 'telegram', 'manual', or None for all.
    status: 'library', 'practice', 'review_ready', 'mastered', or None for all.
    """
    data = _load_phrasebook()
    entries = [e for e in data.get("phrases", []) if e.get("user") == user]
    if source:
        entries = [e for e in entries if e.get("scene", "") == source]
    if status:
        entries = [e for e in entries if e.get("status", "") == status]
    return sorted(entries, key=lambda e: e.get("added", ""), reverse=True)


def get_personas() -> list:
    """Returns all personas from personas.json. Shared config — not user-scoped."""
    path = GERMAN_DIR / "config" / "personas.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def get_drill_pool(user: str = DEFAULT_USER) -> list:
    """Phrasebook entries with status == 'practice' for this user."""
    return get_phrasebook_entries(status="practice", user=user)


def save_drill_result(phrase_id: str, result: str, user: str = DEFAULT_USER) -> dict:
    """
    Records drill result. result = "correct" | "wrong" | "skip"
    Updates practice_count, last_practiced.
    Amendment 4: at 5 correct, sets status='review_ready' — never auto-promotes to mastered.
    Returns updated entry dict, or {} if not found.
    """
    data = _load_phrasebook()
    for entry in data.get("phrases", []):
        if entry.get("id") == phrase_id and entry.get("user") == user:
            if result == "correct":
                entry["practice_count"] = entry.get("practice_count", 0) + 1
            entry["last_practiced"] = datetime.datetime.now().isoformat()
            if result == "correct" and entry.get("practice_count", 0) >= 5:
                entry["status"] = "review_ready"
            _save_phrasebook(data)
            return entry
    return {}


def update_phrase_status(phrase_id: str, new_status: str,
                         user: str = DEFAULT_USER) -> bool:
    """
    Updates status on a phrase. Returns True if found and updated.
    Valid statuses: library, practice, review_ready, mastered.
    """
    valid = {"library", "practice", "review_ready", "mastered"}
    if new_status not in valid:
        return False
    data = _load_phrasebook()
    for entry in data.get("phrases", []):
        if entry.get("id") == phrase_id and entry.get("user") == user:
            entry["status"] = new_status
            _save_phrasebook(data)
            return True
    return False


# ─── Persona Memory (Layer 1) ────────────────────────────────────────────────

_ROUND_DEFAULTS = {
    "casual": 5,
    "social": 4,
    "friend": 999,
}


def persona_to_slug(persona_name: str) -> str:
    """Convert persona display name to storage key slug. 'Frau Berger' → 'frau_berger'."""
    return re.sub(r'\s+', '_', persona_name.strip().lower())


def _default_active_memory(round_number: int = 1) -> dict:
    return {
        "round_number": round_number,
        "sessions_this_round": 0,
        "last_seen": None,
        "topics_discussed": [],
        "errors_noted": [],
        "strengths": [],
        "vocabulary_introduced": [],
        "notes": "",
        "rapport_level": "neutral",
    }


def get_persona_memory(user: str, persona_slug: str) -> dict:
    """Return memory block for user+persona pair. Creates default on first access."""
    all_data = safe_read_json(_PERSONA_MEMORY_FILE)
    key = f"{user}_{persona_slug}"
    if key not in all_data:
        persona_type = "casual"
        for p in get_personas():
            if persona_to_slug(p["name"]) == persona_slug:
                persona_type = p.get("type", "casual")
                break
        round_default = _ROUND_DEFAULTS.get(persona_type, 5)
        all_data[key] = {
            "user": user,
            "persona": persona_slug,
            "persona_type": persona_type,
            "current_round": 1,
            "round_default": round_default,
            "round_extended": False,
            "ready_to_archive": False,
            "active_memory": _default_active_memory(round_number=1),
            "archived_rounds": [],
        }
        safe_write_json(_PERSONA_MEMORY_FILE, all_data)
    return all_data[key]


def update_persona_memory(user: str, persona_slug: str, updates: dict) -> dict:
    """
    Merge updates into active_memory.
    Lists: append + deduplicate + cap at 20. Scalars: overwrite.
    Increments sessions_this_round. Sets ready_to_archive when threshold is reached.
    """
    all_data = safe_read_json(_PERSONA_MEMORY_FILE)
    key = f"{user}_{persona_slug}"
    if key not in all_data:
        get_persona_memory(user, persona_slug)
        all_data = safe_read_json(_PERSONA_MEMORY_FILE)

    entry = all_data[key]
    mem = entry["active_memory"]

    for list_key in ("topics_discussed", "errors_noted", "strengths", "vocabulary_introduced"):
        if updates.get(list_key):
            existing = mem.get(list_key, [])
            new_items = [item for item in updates[list_key] if item and item not in existing]
            mem[list_key] = (existing + new_items)[:20]

    for scalar in ("last_seen", "notes", "rapport_level"):
        if updates.get(scalar):
            mem[scalar] = updates[scalar]

    mem["sessions_this_round"] = mem.get("sessions_this_round", 0) + 1

    if mem["sessions_this_round"] >= entry.get("round_default", 5) and not entry["ready_to_archive"]:
        entry["ready_to_archive"] = True

    all_data[key] = entry
    safe_write_json(_PERSONA_MEMORY_FILE, all_data)
    return entry


def close_round(user: str, persona_slug: str, summary: str = None) -> None:
    """Archive active_memory as completed round, reset to new round."""
    all_data = safe_read_json(_PERSONA_MEMORY_FILE)
    key = f"{user}_{persona_slug}"
    if key not in all_data:
        return

    entry = all_data[key]
    mem = entry["active_memory"]
    round_num = entry["current_round"]

    if summary is None:
        summary = _generate_round_summary(
            round_num=round_num,
            sessions=mem.get("sessions_this_round", 0),
            topics=mem.get("topics_discussed", []),
            errors=mem.get("errors_noted", []),
            strengths=mem.get("strengths", []),
            vocabulary=mem.get("vocabulary_introduced", []),
        )

    archived = {
        "round_number": round_num,
        "sessions": mem.get("sessions_this_round", 0),
        "date_end": datetime.date.today().isoformat(),
        "summary": summary,
        "key_errors": mem.get("errors_noted", [])[:5],
        "key_strengths": mem.get("strengths", [])[:5],
        "summary_generated": "auto" if summary.startswith(f"Runde {round_num}") else "llm",
    }

    entry["archived_rounds"] = entry.get("archived_rounds", []) + [archived]
    entry["current_round"] = round_num + 1
    entry["ready_to_archive"] = False
    entry["round_extended"] = False
    entry["round_default"] = _ROUND_DEFAULTS.get(entry.get("persona_type", "casual"), 5)
    entry["active_memory"] = _default_active_memory(round_number=round_num + 1)

    all_data[key] = entry
    safe_write_json(_PERSONA_MEMORY_FILE, all_data)


def extend_round(user: str, persona_slug: str, extra_sessions: int = 3) -> None:
    """Add extra_sessions to current round threshold and clear ready_to_archive."""
    all_data = safe_read_json(_PERSONA_MEMORY_FILE)
    key = f"{user}_{persona_slug}"
    if key not in all_data:
        return
    entry = all_data[key]
    entry["round_default"] = entry.get("round_default", 5) + extra_sessions
    entry["round_extended"] = True
    entry["ready_to_archive"] = False
    all_data[key] = entry
    safe_write_json(_PERSONA_MEMORY_FILE, all_data)


def _generate_round_summary(round_num: int, sessions: int, topics: list,
                             errors: list, strengths: list, vocabulary: list) -> str:
    prompt = (
        "Generate a 2-sentence summary in German of this language learning round.\n"
        "Focus on: main errors observed, main strengths, overall progress.\n"
        "Be specific and encouraging. Do not use filler phrases.\n\n"
        f"Sessions completed: {sessions}\n"
        f"Main errors: {', '.join(errors[:5]) or 'none noted'}\n"
        f"Main strengths: {', '.join(strengths[:5]) or 'none noted'}\n"
        f"Topics covered: {', '.join(topics[:5]) or 'various'}\n"
        f"Vocabulary introduced: {', '.join(vocabulary[:5]) or 'various'}"
    )
    result = _call_llm(prompt, max_tokens=150)
    if result:
        return result
    return (
        f"Runde {round_num} abgeschlossen ({sessions} Sitzungen). "
        f"Themen: {', '.join(topics[:3]) or 'verschiedene'}. "
        f"Häufigste Fehler: {', '.join(errors[:2]) or 'keine notiert'}."
    )


def build_persona_prompt(persona: dict, memory: dict, tutor_focus: str = None) -> str:
    """Inject active persona memory into system prompt. Returns enriched prompt string."""
    base = persona.get("description", "")
    if not memory:
        return base

    mem = memory.get("active_memory", {})
    round_num = memory.get("current_round", 1)
    sessions = mem.get("sessions_this_round", 0)

    if round_num == 1 and sessions == 0:
        return base

    lines = [base, "", "## Erinnerungen aus vergangenen Sitzungen", ""]
    if mem.get("topics_discussed"):
        lines.append(f"Besprochene Themen: {', '.join(mem['topics_discussed'][:5])}")
    if mem.get("errors_noted"):
        lines.append(f"Häufige Fehler des Lernenden: {', '.join(mem['errors_noted'][:3])}")
    if mem.get("strengths"):
        lines.append(f"Stärken: {', '.join(mem['strengths'][:3])}")
    if mem.get("vocabulary_introduced"):
        lines.append(f"Eingeführtes Vokabular: {', '.join(mem['vocabulary_introduced'][:5])}")
    if mem.get("rapport_level") and mem["rapport_level"] != "neutral":
        lines.append(f"Beziehung: {mem['rapport_level']}")
    if mem.get("notes"):
        lines.append(f"Notiz: {mem['notes'][:200]}")

    if tutor_focus:
        lines.extend([
            "",
            f"Aktueller Lernfokus: {tutor_focus}",
            "Achte besonders darauf und korrigiere sanft wenn nötig.",
        ])

    return "\n".join(lines)


# ─── Gespräche — session prompt assembly (HTML interface) ────────────────────

# Mirrors constants in get_german_session.py — both paths must produce identical prompts.
_UNIVERSAL_HEADER = """\
=== SESSION INSTRUCTIONS — READ BEFORE STARTING ===

You are playing a character in a German language practice session. These rules override everything else. Follow them exactly.

0. VOICE AND GENDER: Play the character exactly as described below — including gender. Never switch. Non-negotiable.

1. SCENARIO AND MEDIUM: Follow the scenario setup exactly. If it says "phone call", you answer the phone. If it says I walk in, greet me in person. Never change the setting mid-session.

2. NO NAME PREFIX: Do not announce your name before each turn.
   Wrong: "Klaus: Guten Abend!"
   Correct: "Guten Abend!"

3. LANGUAGE: Always respond in German. Never switch to English unless I say "English please."

4. CORRECTIONS: If I make a grammatical error, gently use the correct form naturally. Do not break character.

5. START TRIGGER: Do not begin until I say "Start today's session", "Start session", or "Let's start." Wait in silence — do not acknowledge or ask.

6. STAY IN CHARACTER: Do not comment on the exercise or your role. You are the character.

=== CHARACTER AND SCENARIO BELOW ===""".strip()

_UNIVERSAL_FOOTER = """\
=== HOW TO END THIS SESSION ===

Switch to TEXT MODE, then type "End session. Give me the transcript." Do NOT end while in voice mode.

Output ONLY this block — nothing before or after, no commentary:

---SESSION---
Date: [today's date as YYYY-MM-DD]
Persona: [character name]
Scenario: [scenario_label]
Duration: [number only — e.g. 12]
Mode: voice

[Character name]: [their exact words]
Robert: [your exact words]
[continue alternating turns in order...]
---END---

Every turn in order, no skips. Use --- not em-dashes. Duration is a number only. Nothing before ---SESSION---. Nothing after ---END---.""".strip()


def _find_persona_prompt_file(persona_name: str) -> Path | None:
    slug = persona_name.lower().replace(" ", "_")
    matches = list(_PROMPTS_DIR.glob(f"{slug}*.txt"))
    return matches[0] if matches else None


def assemble_session_prompt(persona: dict, scene: str, memory: dict) -> str:
    """Full Grok Voice system prompt: UNIVERSAL_HEADER + persona .txt + UNIVERSAL_FOOTER."""
    persona_name = persona.get("name", "")
    prompt_file = _find_persona_prompt_file(persona_name)
    if prompt_file and prompt_file.exists():
        persona_txt = prompt_file.read_text(encoding="utf-8").strip()
    else:
        persona_txt = persona.get("description", f"You are {persona_name}.")
    return "\n\n".join([_UNIVERSAL_HEADER, persona_txt, _UNIVERSAL_FOOTER])


def build_session_brief(persona: dict, scene: str, memory: dict) -> str:
    """Layer 3 session brief for UI display — not pasted into Grok."""
    persona_name = persona.get("name", "")

    progress = safe_read_json(_PROGRESS_FILE)
    lesson_number = progress.get("total_sessions", 0) + 1

    km = safe_read_json(_KEYWORD_MAP_FILE)
    km_entry = km.get(persona_name, {})
    register = km_entry.get("register", "")
    scaffold_phrases = km_entry.get("scaffold_phrases", [])
    recovery_phrase = km_entry.get("recovery_phrase", "")

    speaking_prompts = persona.get("speaking_prompts", {})
    scene_description = speaking_prompts.get(scene, scene.replace("_", " ").title())

    mem = memory.get("active_memory", {}) if memory else {}
    errors_noted = mem.get("errors_noted", [])
    vocab = mem.get("vocabulary_introduced", [])

    if vocab:
        carry = vocab[-1]
    elif errors_noted:
        carry = f"Fehler wiederholen: {errors_noted[-1]}"
    else:
        carry = "Erste Sitzung — kein Carry-forward"

    register_label = f" [use {register}]" if register else ""
    scene_label = scene.replace("_", " ").title()

    lines = [
        f"📚 Sitzung {lesson_number} — {persona_name} / {scene_label}{register_label}",
        f"Carry forward: {carry}",
    ]
    if errors_noted:
        lines.append(f"Warm-up: Wiederhole häufigen Fehler aus letzter Sitzung: {errors_noted[0]}")
    lines.append(f"Ziel: {scene_description}")

    if scaffold_phrases:
        lines.append("")
        lines.append("🧱 Heutige Vorbereitung:")
        for phrase in scaffold_phrases[:4]:
            lines.append(f"   • {phrase['de']}")
        if recovery_phrase:
            lines.append(f"   🆘 {recovery_phrase}")

    return "\n".join(lines)


# ─── Gespräche — transcript analysis (HTML interface) ────────────────────────

_SESSIONS_DIR = GERMAN_DIR / "sessions"

_REVIEW_SYSTEM_PROMPT = """\
You are a German language tutor reviewing a voice practice session between a student (Robert) and an AI persona.
Analyze the transcript and return a single JSON object — no markdown, no explanation, just the JSON.

Required schema:
{
  "overall_summary": "2-3 sentence summary of the session quality and main takeaway",
  "errors": [
    {
      "type": "gender|word_order|missing_article|verb_conjugation|vocabulary|register",
      "original": "what Robert said",
      "correction": "correct form",
      "explanation": "one sentence why",
      "context": "full sentence from transcript"
    }
  ],
  "strengths": ["specific things Robert did well"],
  "next_focus": "one concrete grammar or vocabulary focus for the next session",
  "topics": ["main topics or themes discussed (e.g. food, weather, directions)"],
  "vocabulary": ["notable German words or phrases used or introduced in the session"]
}"""


def _parse_transcript_turns(text: str) -> list:
    """Parse raw transcript into list of {speaker, text} dicts."""
    turns = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' in line:
            speaker, _, body = line.partition(':')
            speaker = speaker.strip()
            body = body.strip()
            if speaker and body and len(speaker) < 40:
                turns.append({"speaker": speaker, "text": body})
                continue
        if turns:
            turns[-1]["text"] += " " + line
        else:
            turns.append({"speaker": "Transcript", "text": line})
    return turns or [{"speaker": "Transcript", "text": text.strip()}]


def _next_session_filename(date_str: str) -> str:
    """Return next available session filename for date (matching parse_transcript.py convention)."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(_SESSIONS_DIR.glob(f"{date_str}_*.json"))
    if not existing:
        return f"{date_str}_001"
    last = existing[-1].stem
    try:
        n = int(last.split("_")[-1]) + 1
    except ValueError:
        n = len(existing) + 1
    return f"{date_str}_{n:03d}"


def analyse_session(transcript: str, persona_name: str, scene: str) -> dict:
    """Analyse a pasted Grok Voice transcript. Returns {session_id, feedback}."""
    import keyring as _keyring
    import anthropic as _anthropic

    ts = datetime.datetime.now()
    date_str = ts.strftime("%Y-%m-%d")
    file_stem = _next_session_filename(date_str)
    session_id = f"session_{file_stem}"

    turns = _parse_transcript_turns(transcript)

    prompt_lines = [
        f"Persona: {persona_name}",
        f"Scenario: {scene}",
        f"Date: {date_str}",
        "",
        "Transcript:",
    ] + [f"{t['speaker']}: {t['text']}" for t in turns]
    user_prompt = "\n".join(prompt_lines)

    feedback = {"overall_summary": "", "errors": [], "strengths": [], "next_focus": ""}
    raw_text = None
    try:
        api_key = _keyring.get_password("anthropic", "api_key")
        if api_key:
            client = _anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=_REVIEW_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = resp.content[0].text.strip()
    except Exception:
        pass

    if raw_text:
        try:
            text = raw_text
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            feedback = {
                "overall_summary": parsed.get("overall_summary", ""),
                "errors": parsed.get("errors", []),
                "strengths": parsed.get("strengths", []),
                "next_focus": parsed.get("next_focus", ""),
                "topics": parsed.get("topics", []),
                "vocabulary": parsed.get("vocabulary", []),
            }
        except Exception:
            feedback["overall_summary"] = raw_text[:500]

    session = {
        "session_id": session_id,
        "date": date_str,
        "persona": persona_name,
        "scenario": scene,
        "duration_estimate_min": max(1, len(transcript) // 300),
        "source": "html",
        "raw_transcript": turns,
        "reviewer_output": feedback,
        "anki_generated": False,
        "next_lesson_generated": False,
    }
    session_path = _SESSIONS_DIR / f"{file_stem}.json"
    session_path.write_text(json.dumps(session, indent=2, ensure_ascii=False))

    update_persona_memory(
        user=DEFAULT_USER,
        persona_slug=persona_to_slug(persona_name),
        updates={
            "last_seen": date_str,
            "topics_discussed": feedback.get("topics", []),
            "errors_noted": [e.get("explanation", "") for e in feedback.get("errors", []) if e.get("explanation")],
            "strengths": feedback.get("strengths", []),
            "vocabulary_introduced": feedback.get("vocabulary", []),
            "notes": feedback.get("overall_summary", ""),
        }
    )

    return {"session_id": session_id, "feedback": feedback}


def get_gesprache_sessions(limit: int = 5) -> list:
    """Return last N sessions (newest first) with summary fields."""
    if not _SESSIONS_DIR.exists():
        return []
    paths = sorted(_SESSIONS_DIR.glob("*.json"), reverse=True)
    result = []
    for path in paths:
        try:
            data = json.loads(path.read_text())
            result.append({
                "session_id": data.get("session_id", path.stem),
                "date": data.get("date", ""),
                "persona": data.get("persona", ""),
                "scenario": data.get("scenario", ""),
                "source": data.get("source", ""),
            })
        except Exception:
            continue
        if len(result) >= limit:
            break
    return result
