# Plan: Language Learning Domain — German POC

## Context
Build a spoken German practice loop before a Vienna trip (~3 weeks). Voice sessions happen on iPhone via Grok, transcripts flow through the Telegram bridge to OpenClaw, then a Python reviewer generates feedback, Anki flashcards, and a next-day lesson plan. This is the first non-geopolitics domain in mini-moi — designed to validate the system's extensibility.

Spec: `_NewDomains/language-german/SPEC.md`  
Delta (pre-build additions): `_NewDomains/language-german/PLAN_DELTA.md`

---

## 0. Pre-Build Checks (Must Pass Before Any Code)

- [ ] Telegram bridge: send a test message to OpenClaw bot, confirm receipt and parse
- [ ] `python -c "import anthropic"` works in the venv — if not, `pip install anthropic`

Do not proceed to step 1 until both pass.

---

## 1. Folder Structure

All files live under `_NewDomains/language-german/` until post-Vienna graduation.

```
_NewDomains/language-german/
  SPEC.md
  PLAN.md
  PLAN_DELTA.md
  language/
    german/
      config/
        domain.json
        personas.json
      sessions/
        inbox/
      anki/
      lessons/
      progress.json
      README.md
  parse_transcript.py
  reviewer.py
  status.py
```

`.gitignore` additions:
```
_NewDomains/language-german/language/german/sessions/
_NewDomains/language-german/language/german/anki/
_NewDomains/language-german/language/german/lessons/
_NewDomains/language-german/language/german/progress.json
```

---

## 2. Config Files

### `config/domain.json`
```json
{
  "domain": "german",
  "active": true,
  "current_lesson_number": 1,
  "active_persona": "Frau Berger",
  "level": "A2-B1",
  "target": "B1 spoken confidence for Vienna trip",
  "trip_date": "2026-05-10",
  "reviewer_model": "claude-sonnet-4-6",
  "sessions_dir": "sessions",
  "anki_dir": "anki",
  "lessons_dir": "lessons"
}
```

### `config/personas.json`
Array of all 8 personas from SPEC.md section 6. Each entry:
```json
{
  "name": "Frau Berger",
  "role": "Bakery Owner",
  "description": "Warm, patient Viennese woman in her 50s...",
  "scenarios": ["bakery_order", "neighborhood_chat"],
  "style": "casual, forgiving, gentle in-character corrections",
  "difficulty": "beginner-friendly"
}
```

### `progress.json` (initial state)
```json
{
  "total_sessions": 0,
  "total_minutes": 0,
  "personas_practiced": [],
  "scenarios_covered": [],
  "cumulative_error_counts": {
    "gender": 0, "word_order": 0, "missing_article": 0,
    "verb_conjugation": 0, "vocabulary": 0, "register": 0,
    "case": 0, "preposition": 0, "idiomatic": 0
  },
  "anki_cards_generated": 0,
  "vocabulary_seen": [],
  "strengths_noted": [],
  "last_updated": null
}
```

---

## 3. `parse_transcript.py`

**Purpose:** Convert raw Telegram message text into a session JSON file.

**Function signature:**
```python
def parse_transcript(raw_text: str, sessions_dir: Path) -> Path:
    """Parse raw transcript text → write session JSON → return path."""
```

**Session ID collision logic:**
```python
def _next_session_id(date_str: str, sessions_dir: Path) -> str:
    existing = sorted(sessions_dir.glob(f"{date_str}_*.json"))
    if not existing:
        return f"{date_str}_001"
    last = existing[-1].stem
    n = int(last.split("_")[-1]) + 1
    return f"{date_str}_{n:03d}"
```

**Header parsing:** Detect `GERMAN_SESSION_TRANSCRIPT` on first line. Extract `Date:`, `Persona:`, `Scenario:`, `Duration:` from header lines. Body is everything after the first blank line.

**Transcript body parsing:** Split on newlines. Detect `Robert:`, `You:`, or `User:` as valid Robert prefixes. Detect persona name as the other speaker. Unknown prefixes: append to prior turn rather than drop.

**Output:** Writes `sessions/YYYY-MM-DD_NNN.json` with `reviewer_output: null`, `reviewer_raw_output: null`, `anki_generated: false`, `next_lesson_generated: false`.

**Test fixture:** Create `sessions/inbox/test_transcript.txt` with a sample Frau Berger bakery session before running parse tests.

**CLI:**
```
python parse_transcript.py --input transcript.txt --base-dir language/german/
python parse_transcript.py --stdin --base-dir language/german/
```

---

## 4. `reviewer.py`

**Purpose:** Take a session JSON, call Claude Sonnet, populate `reviewer_output`, generate Anki CSV and lesson plan.

**Client init pattern** (matches existing codebase):
```python
def _get_anthropic_client():
    try:
        import keyring
        api_key = keyring.get_password('anthropic', 'api_key') or os.getenv('ANTHROPIC_API_KEY')
    except Exception:
        api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("Anthropic API key not found in keychain or environment.")
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)
```

**System prompt** (set once, returns pure JSON):
```
You are a German language tutor reviewing a voice practice session.
Analyze the transcript and return a single JSON object — no markdown,
no explanation, just the JSON.

Required schema:
{
  "overall_summary": "string",
  "errors": [
    {
      "type": "gender|word_order|missing_article|verb_conjugation|vocabulary|register|case|preposition|idiomatic",
      "original": "string",
      "correction": "string",
      "explanation": "string — one sentence",
      "context": "string — full sentence from transcript"
    }
  ],
  "error_type_counts": {
    "gender": 0, "word_order": 0, "missing_article": 0,
    "verb_conjugation": 0, "vocabulary": 0, "register": 0,
    "case": 0, "preposition": 0, "idiomatic": 0
  },
  "vocabulary_highlights": [
    {
      "german": "string",
      "english": "string",
      "example_sentence": "string",
      "tags": ["string"],
      "note": "string"
    }
  ],
  "strengths": ["string"],
  "next_focus": "string"
}
```

**Malformed response handling:**
```python
def _parse_llm_response(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    import re
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    print(f"⚠️  Reviewer response parse failed. Raw:\n{text[:500]}")
    return {
        "overall_summary": "Parse failed — check reviewer_raw_output field.",
        "errors": [],
        "error_type_counts": {k: 0 for k in ERROR_TYPES},
        "vocabulary_highlights": [],
        "strengths": [],
        "next_focus": ""
    }
```

Store raw LLM text in `session["reviewer_raw_output"]` whenever parse fails.

**After review:** Call `_generate_anki_csv()`, `_generate_lesson_plan()`, then `_update_progress()` in sequence.

**`_update_progress()` must update all fields:**
- `total_sessions` += 1
- `total_minutes` += session duration
- `personas_practiced` — append if not already present
- `scenarios_covered` — append if not already present
- `cumulative_error_counts` — add all error type counts from this session
- `anki_cards_generated` += count of new cards written
- `vocabulary_seen` — append new words (deduplication source)
- `strengths_noted` — append new strengths not already listed
- `last_updated` — set to current ISO timestamp

**CLI:**
```
python reviewer.py --session sessions/2026-04-20_001.json --base-dir language/german/
python reviewer.py --latest --base-dir language/german/
python reviewer.py --today --base-dir language/german/
```

---

## 5. Anki CSV Generator

**Function:** `_generate_anki_csv(reviewer_output, session_date, scenario, base_dir, progress)`

**Output format** (with tags and example sentences):
```
Front,Back,Tags
"das Kipferl","crescent roll (Austrian) — Example: Ich hätte gerne zwei Kipferln, bitte. — bakery_order 2026-04-20","german vienna food bakery"
```

**Deduplication:** Check `german` field against `progress["vocabulary_seen"]`. Skip if present. Add new words to `vocabulary_seen` after writing.

**File path:** `anki/YYYY-MM-DD_anki.csv`. Append if file already exists (multiple sessions per day).

---

## 6. Lesson Plan Generator

**Function:** `_generate_lesson_plan(reviewer_output, session, domain_cfg, personas, progress, base_dir)`

**Persona rotation:**
```python
def _pick_next_persona(personas, progress):
    recent = progress["personas_practiced"][-2:]
    candidates = [p for p in personas if p["name"] not in recent]
    if not candidates:
        candidates = personas
    not_yet = [p for p in candidates if p["name"] not in progress["personas_practiced"]]
    pool = not_yet if not_yet else candidates
    return pool[0]
```

**Scenario selection:** First scenario from selected persona's list not in `progress["scenarios_covered"]`. If all covered, cycle from start.

**Carry-forward errors:** Up to 2 errors from highest `cumulative_error_counts` bucket. Format: `"das/die/der Brot correction from session 2026-04-20"`.

**Lesson number:** Read `domain.json["current_lesson_number"]`, increment, write back.

**Output:** `lessons/YYYY-MM-DD_lesson.json` per SPEC.md section 9.

---

## 7. `status.py`

**Reads:** `domain.json`, `progress.json`, most recent session JSON, most recent lesson JSON.

**Output:**
```
── German Practice Status ─────────────────────────
Sessions: 3 | Minutes: 38 | Anki cards: 22

Last session (2026-04-20, Frau Berger — bakery_order):
  Overall: Good session. Article genders were the main challenge.
  Errors: gender×2, case×1
  Next focus: das/die/das for food nouns

Tomorrow's lesson (Lesson 4):
  Persona: Maria (Café Waitress)
  Scenario: cafe_order
  Warm-up: Review das Brot, die Semmel, das Kipferl
  Carry forward: das/die/der Brot correction from 2026-04-20

Top error pattern: gender (8 total)
────────────────────────────────────────────────────
```

**CLI:**
```
python status.py --base-dir language/german/
```

---

## 8. Dependencies

- `anthropic` — verify in venv before build. Not in requirements.txt; install if missing.
- Everything else: stdlib only (json, pathlib, csv, re, os, sys, argparse, datetime).
- No genanki, no pyyaml, no rich.

---

## 9. Risks

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | Telegram bridge stability | 🔴 Blocker | Validate before any build work |
| 2 | Grok transcript format variance | 🟡 Medium | Accept Robert:/You:/User: prefixes; forgiving parser |
| 3 | `anthropic` not in venv | 🟡 Medium | Check first; fail fast with clear error |
| 4 | LLM JSON malformation | 🟡 Medium | 3-stage parse handler |
| 5 | Multiple sessions per day | 🟢 Low | `_NNN` suffix + CSV append |
| 6 | `progress.json` missing on first run | 🟢 Low | reviewer.py creates with initial state if missing |

---

## 10. Build Order

1. Scaffold folder structure + config files + `progress.json`
2. `parse_transcript.py` — test with sample fixture
3. `reviewer.py` — test with session JSON from step 2
4. Anki CSV and lesson plan generators (called from reviewer.py)
5. `status.py`
6. `.gitignore` update
7. First live Telegram bridge test

**Do not start step 1 until pre-build checks in section 0 pass.**
