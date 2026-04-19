# Language Learning Domain – German POC
## Design Specification v1.1
**Status:** _NewDomains candidate  
**Author:** Robert van Stedum  
**Date:** 2026-04-19  
**Reviewed by:** Claude, Grok, Claude Code  
**Target:** Vienna trip, ~3 weeks  
**Graduation target:** After Vienna trip validation  
**Changed in v1.1:** Added section 2 — Multi-Language Architecture Vision

---

## 1. Vision & Goals

Build spoken German confidence for a Vienna trip through daily mobile voice practice, automated session review, and Anki-ready vocabulary output. This domain is the first non-geopolitics/finance domain in mini-moi and serves as a **showpiece of the system's extensibility** — demonstrating that the research intelligence architecture generalizes to any learning or practice domain.

**Primary goal:** Output confidence (speaking), not passive comprehension.  
**Secondary goal:** Validate that the mini-moi orchestration pattern works for real-time personal practice loops, not just long-horizon research.

---

## 2. Multi-Language Architecture Vision

### One Domain Per Language

This domain is intentionally named `language-german`, not `language`. The design decision is **one domain per language**, each living independently under `_NewDomains/` until it graduates:

```
_NewDomains/language-german/     ← current build
_NewDomains/language-french/     ← future, when needed
_NewDomains/language-portuguese/ ← future (lower priority given existing fluency)
_NewDomains/language-spanish/    ← future
```

**Why one domain per language, not subfolders:**
- Error taxonomies are language-specific. German errors (case, gender, V2 word order) are structurally different from French errors (subjunctive, liaison, register agreement). A shared schema would require language-aware branching throughout.
- Persona libraries are language-and-culture specific. Frau Berger is not reusable for Paris.
- Progress tracking is meaningless across languages — cumulative error counts only make sense within one language.
- Graduation criteria apply per language independently.

### Shared Utility Library (Post-Graduation Refactor)

The implementation code — `parse_transcript.py`, `reviewer.py`, `status.py` — will be **written first for German** with reuse in mind, then refactored into a shared library once a second language domain proves the pattern. This follows the mini-moi principle: prove on real data before abstracting.

Target structure after first graduation:

```
language_core/
  parse_transcript.py    ← language-agnostic transcript parser
  reviewer.py            ← language-agnostic reviewer (language passed as param)
  status.py              ← language-agnostic status reader
  base_progress.json     ← template for new language domains

language-german/
  config/
    domain.json          ← german-specific: personas, error types, level
    personas.json
  sessions/
  anki/
  lessons/
  progress.json

language-french/
  config/
    domain.json          ← french-specific config
    personas.json
  sessions/
  ...
```

**Design principle for v1.0 German build:** Write every utility function to accept `language` and `base_dir` as parameters rather than hardcoding German-specific values. This costs almost nothing now and makes the refactor trivial later.

### Language-Specific Reviewer Prompts

The Claude reviewer prompt will be stored per-language in `config/domain.json` or a dedicated `config/reviewer_prompt.txt`. It is **not** shared — French grammar instruction requires a completely different system prompt from German grammar instruction. The reviewer infrastructure (API call, JSON parsing, error handling) is shared; the prompt content is not.

### Graduation Independence

Each language domain graduates on its own merits after a trip or sustained practice period. `language-german` graduating does not trigger `language-french` graduation. The shared utility library (`language_core/`) graduates when two or more language domains have proven stable.

---

## 3. Architecture Overview

```
iPhone (Grok Voice)
    │
    │  Voice session → "End session. Give me a clean transcript."
    │  Copy transcript → Telegram message to OpenClaw bot
    ▼
Telegram Bridge
    │
    ▼
OpenClaw (MacBook)
    │  Receives transcript via Telegram
    │  Saves raw text → sessions/inbox/
    │  Calls parse_transcript.py
    │  Calls reviewer.py
    │  Sends Telegram summary back to Robert
    ▼
Python Reviewer
    │  Parses transcript
    │  Calls Claude Sonnet to generate feedback + error analysis
    │  Generates: Anki CSV, next-day lesson plan
    │  Updates progress.json
    ▼
Output files → anki/, lessons/, progress.json
```

**Agent division of labor (binding):**
- **Claude.ai** — design, spec, persona definitions, reviewer logic design
- **Claude Code** — implementation, commits, pushes to GitHub
- **OpenClaw** — orchestration, Telegram bridge, file system writes, subprocess calls
- **Robert** — decision point between all agents; approves spec before build begins

**Git responsibility (binding):**
- **Claude Code owns all git operations.** `git add`, `git commit`, `git push` — Claude Code only.
- **OpenClaw owns file system operations.** Creates and writes files, never runs git commands.
- Workflow: OpenClaw writes files → Robert tells Claude Code to commit and push → Claude Code confirms.

---

## 4. Folder Structure

Lives inside the main mini-moi repo under `_NewDomains/` until post-Vienna graduation.

```
_NewDomains/language-german/
  SPEC.md                    ← this document
  PLAN.md                    ← Claude Code implementation plan
  PLAN_DELTA.md              ← targeted additions from pre-build review
  language/
    german/
      config/
        domain.json          ← domain settings, active persona, lesson counter
        personas.json        ← all 8 persona definitions
      sessions/              ← gitignored; YYYY-MM-DD_NNN.json per session
        inbox/               ← raw transcript drop folder (Telegram fallback)
      anki/                  ← gitignored; YYYY-MM-DD_anki.csv
      lessons/               ← gitignored; YYYY-MM-DD_lesson.json
      progress.json          ← gitignored; cumulative stats
      README.md
  parse_transcript.py        ← written for German, parameterized for reuse
  reviewer.py                ← written for German, parameterized for reuse
  status.py                  ← written for German, parameterized for reuse
```

**.gitignore additions required:**
```
_NewDomains/language-german/language/german/sessions/
_NewDomains/language-german/language/german/anki/
_NewDomains/language-german/language/german/lessons/
_NewDomains/language-german/language/german/progress.json
```

---

## 5. Handoff Mechanism: Telegram Bridge

### Why Telegram
- OpenClaw already runs on Telegram — no new infrastructure
- Grok iOS has share/copy functionality; 2–3 taps to forward transcript
- OpenClaw can parse, save, and trigger the review pipeline automatically upon receipt

### Known Risk: Telegram Instability
> ⚠️ **Blocker risk.** Recent Telegram issues with OpenClaw have been observed. Before any build work proceeds, the Telegram receive-and-parse flow must be validated end-to-end with a test transcript. This is **step 0** of the build sequence.

### Mobile Workflow (iPhone)
1. Open Grok iOS → German Practice chat
2. Tell Grok: *"Start today's [scenario] with [persona name]"*
3. Conduct voice session (10–15 min)
4. Say: *"End session. Give me a clean transcript."*
5. Copy Grok's output → paste into OpenClaw Telegram bot
6. Done. MacBook handles everything from here.

### Telegram Message Format Contract

**What Robert sends** (Grok output + manual header prepended if needed):

```
GERMAN_SESSION_TRANSCRIPT
Date: 2026-04-20
Persona: Frau Berger
Scenario: bakery_order
Duration: 12

Robert: Guten Morgen. Ich hätte gerne zwei Kipferln, bitte.
Frau Berger: Natürlich! Sonst noch etwas?
Robert: Nein danke. Was kostet das?
Frau Berger: Das macht 2,40 Euro, bitte.
```

- `Duration` is an estimate in minutes. If omitted, parser records 0 — does not crash.
- Speaker prefixes: `Robert:`, `You:`, and `User:` are all valid for Robert's turns. Unknown prefixes are appended to the prior turn rather than dropped.

**What OpenClaw does on receipt:**

1. Detects `GERMAN_SESSION_TRANSCRIPT` as the first line of any incoming Telegram message
2. Writes full message body to `sessions/inbox/raw_YYYY-MM-DD_HH-MM.txt`
3. Calls `python parse_transcript.py --stdin --base-dir language/german/`
4. On success: calls `python reviewer.py --latest --base-dir language/german/`
5. On completion: sends Robert a Telegram summary:

```
✅ Session reviewed — Frau Berger / bakery_order
Errors: gender×2, case×1
New Anki cards: 6
Tomorrow: Maria — café_order (Lesson 4)
Next focus: Article genders for food nouns
```

6. On any failure: sends Robert a Telegram message with the error and raw file path for manual debugging.

### Fallback (if Telegram is unavailable)
Drop the transcript text as a `.txt` file into `sessions/inbox/` via iCloud Drive. OpenClaw folder-watches this path as a secondary trigger. iOS Shortcut to be built if needed.

---

## 6. JSON Transcript Schema

Saved by OpenClaw to `sessions/YYYY-MM-DD_NNN.json`:

```json
{
  "session_id": "2026-04-20_001",
  "date": "2026-04-20",
  "persona": "Frau Berger",
  "scenario": "bakery_order",
  "duration_estimate_min": 12,
  "source": "telegram",
  "raw_transcript": [
    { "speaker": "Robert", "text": "Guten Morgen. Ich hätte gerne zwei Kipferln, bitte." },
    { "speaker": "Frau Berger", "text": "Natürlich! Sonst noch etwas?" }
  ],
  "reviewer_output": null,
  "reviewer_raw_output": null,
  "anki_generated": false,
  "next_lesson_generated": false
}
```

`reviewer_raw_output` stores the raw LLM response whenever JSON parsing fails, enabling manual debugging.

---

## 7. Personas (Vienna Travel Focus)

All personas are optimized for voice — natural spoken register, forgiving but realistic, occasional gentle in-character correction.

**1. Frau Berger — Bakery Owner**  
Warm, patient Viennese woman in her 50s. Runs a traditional Bäckerei near the Naschmarkt. Will gently rephrase things Robert says incorrectly as part of her natural reply ("Ach, Sie meinen...").  
*Scenarios:* `bakery_order`, `neighborhood_chat`  
*Difficulty:* Beginner-friendly

**2. Herr Fischer — Hotel Receptionist**  
Professional, efficient, polite. Works at a mid-range hotel near the Ringstraße. Speaks clearly and somewhat formally.  
*Scenarios:* `hotel_checkin`, `hotel_checkout`, `room_problem`, `directions_from_hotel`  
*Difficulty:* Beginner-friendly

**3. Maria — Café Waitress**  
Young, slightly hurried, uses a bit of Viennese slang. Works at a classic Kaffeehaus. Tests natural pace and informal register.  
*Scenarios:* `cafe_order`, `cafe_bill`, `cafe_small_meal`  
*Difficulty:* Intermediate

**4. Dr. Huber — Museum Guide**  
Enthusiastic, knowledgeable, speaks in complete sentences. Works at the Kunsthistorisches Museum.  
*Scenarios:* `museum_exhibit`, `museum_navigation`, `museum_recommendation`  
*Difficulty:* Intermediate

**5. Stefan — U-Bahn Stranger**  
Relaxed local, early 30s. Willing to help a tourist but speaks at normal speed.  
*Scenarios:* `ubahn_directions`, `ubahn_which_line`, `ubahn_confirm_stop`  
*Difficulty:* Intermediate

**6. Frau Novak — Pharmacist**  
Calm, precise, professional. Good for medical and practical vocabulary.  
*Scenarios:* `pharmacy_medicine`, `pharmacy_symptom`, `pharmacy_instructions`  
*Difficulty:* Beginner-friendly

**7. Klaus — Upscale Restaurant Waiter**  
Formal, proud of the menu. Slight Austrian formality ("Bitte sehr, der Herr...").  
*Scenarios:* `restaurant_reservation`, `restaurant_full_meal`, `restaurant_wine`, `restaurant_bill`  
*Difficulty:* Advanced

**8. Anna — Airbnb Host**  
Friendly, practical, patient and encouraging. Good onboarding persona for early sessions.  
*Scenarios:* `apartment_handoff`, `appliance_question`, `neighborhood_recommendations`, `small_problem`  
*Difficulty:* Beginner-friendly

---

## 8. Reviewer Output Schema

```json
"reviewer_output": {
  "overall_summary": "string",
  "errors": [
    {
      "type": "gender|word_order|missing_article|verb_conjugation|vocabulary|register|case|preposition|idiomatic",
      "original": "string — what Robert said",
      "correction": "string — correct form",
      "explanation": "string — one sentence why",
      "context": "string — full sentence from transcript"
    }
  ],
  "error_type_counts": {
    "gender": 0,
    "word_order": 0,
    "missing_article": 0,
    "verb_conjugation": 0,
    "vocabulary": 0,
    "register": 0,
    "case": 0,
    "preposition": 0,
    "idiomatic": 0
  },
  "vocabulary_highlights": [
    {
      "german": "string",
      "english": "string",
      "example_sentence": "string — example in context",
      "tags": ["food", "polite_forms"],
      "note": "string"
    }
  ],
  "strengths": ["string"],
  "next_focus": "string — one concrete grammar or vocabulary focus for next session"
}
```

### Error Taxonomy

| Type | Description |
|---|---|
| `gender` | Wrong article (der/die/das) |
| `word_order` | V2 rule violation, verb not final in subordinate clause |
| `missing_article` | Article omitted entirely |
| `verb_conjugation` | Wrong person, tense, or mood |
| `vocabulary` | Wrong word choice or missing word |
| `register` | Too informal or too formal for context |
| `case` | Wrong case (nominative/accusative/dative/genitive) |
| `preposition` | Wrong preposition or wrong case after preposition |
| `idiomatic` | Non-Austrian or unnatural phrasing where a natural idiom exists |

---

## 9. Anki CSV Format

```
Front,Back,Tags
"das Kipferl","crescent roll (Austrian) — Example: Ich hätte gerne zwei Kipferln, bitte. — bakery_order 2026-04-20","german vienna food bakery"
```

**Deduplication:** Check `german` field against `progress["vocabulary_seen"]` before writing. Skip if present.  
**File path:** `anki/YYYY-MM-DD_anki.csv`. Append if file already exists.

---

## 10. Daily Lesson Plan Schema

```json
{
  "lesson_date": "2026-04-21",
  "lesson_number": 3,
  "persona": "Maria",
  "scenario": "cafe_order",
  "warm_up": "Review: das Brot, die Semmel, das Kipferl. Gender drill.",
  "focus": "Ordering coffee in a Kaffeehaus. Practice Melange, Verlängerter, Einspänner.",
  "speaking_prompt": "You walk into Café Central. Maria greets you. Order a Melange and a piece of Apfelstrudel. Ask if they have a newspaper.",
  "writing_exercise": "Write 3–4 sentences describing what you ordered yesterday. Focus on past tense.",
  "vocabulary_targets": ["Melange", "Verlängerter", "Einspänner", "die Rechnung", "Zahlen bitte"],
  "carry_forward_errors": ["das/die/der Brot correction from session 2026-04-20"]
}
```

---

## 11. Progress Tracking

```json
{
  "total_sessions": 0,
  "total_minutes": 0,
  "personas_practiced": [],
  "scenarios_covered": [],
  "cumulative_error_counts": {
    "gender": 0,
    "word_order": 0,
    "missing_article": 0,
    "verb_conjugation": 0,
    "vocabulary": 0,
    "register": 0,
    "case": 0,
    "preposition": 0,
    "idiomatic": 0
  },
  "anki_cards_generated": 0,
  "vocabulary_seen": [],
  "strengths_noted": [],
  "last_updated": null
}
```

---

## 12. OpenClaw Orchestration Commands

| Command | Action |
|---|---|
| `!german status` | Report today's lesson plan + last session summary |
| `!german next` | Generate tomorrow's lesson plan manually |
| `!german anki` | Re-export today's Anki CSV |
| `!german progress` | Print cumulative progress summary |
| `!german persona [name]` | Override tomorrow's assigned persona |
| `!german debug` | Dump last received transcript + parse result |
| `!german today` | Run reviewer on today's session manually |

---

## 13. Build Sequence & Timeline

### Step 0 — Pre-build validation (before any code)
- [ ] Telegram bridge: send test message, confirm OpenClaw receives and parses
- [ ] `python -c "import anthropic"` works in the venv

Both must pass before build starts.

### Today (Sunday April 19) — Documents to GitHub
1. OpenClaw replaces SPEC.md in `_NewDomains/language-german/` with this v1.1
2. Claude Code commits and pushes to GitHub
3. Confirm live on GitHub before any implementation begins

### Monday (April 20) — Build
1. Scaffold folder structure + config files
2. `parse_transcript.py` with test fixture
3. `reviewer.py` with Anki and lesson generators
4. `status.py`
5. `.gitignore` update
6. First live Telegram bridge test

### Tuesday (April 21) — Use & Fix
- First real voice session (Frau Berger, bakery)
- Fix reviewer output quality issues
- Import first Anki deck

### Wednesday onward — Steady State
- Daily sessions, minor tweaks only

---

## 14. Graduation Criteria (Post-Vienna)

This domain graduates from `_NewDomains/` to main repo if:
- Telegram bridge was stable across 5+ sessions
- Reviewer output quality was genuinely useful
- Anki cards were imported and used
- Progress tracking showed meaningful pattern detection

On graduation, evaluate whether `parse_transcript.py`, `reviewer.py`, and `status.py` warrant refactoring into a shared `language_core/` library. This refactor is only justified if a second language domain is actively planned.

If any criterion fails, domain is archived with a post-mortem note and learnings applied to the next language domain attempt.

---

## 15. Out of Scope (v1.0)

- Real-time voice error correction during session
- Automated Anki import (manual is fine)
- Full mobile review UI
- Pronunciation scoring
- Audio hints in Anki cards (post-graduation candidate)
- Grammar explanation depth beyond practical corrections
- Any language other than German
- `language_core/` shared library refactor (post-graduation, only if second language warranted)
