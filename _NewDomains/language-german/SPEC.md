# Language Learning Domain – German POC
## Design Specification v1.0
**Status:** _NewDomains candidate  
**Author:** Robert van Stedum  
**Date:** 2026-04-19  
**Reviewed by:** Claude, Grok, Claude Code  
**Target:** Vienna trip, ~3 weeks  
**Graduation target:** After Vienna trip validation

---

## 1. Vision & Goals

Build spoken German confidence for a Vienna trip through daily mobile voice practice, automated session review, and Anki-ready vocabulary output. This domain is the first non-geopolitics/finance domain in mini-moi and serves as a **showpiece of the system's extensibility** — demonstrating that the research intelligence architecture generalizes to any learning or practice domain.

**Primary goal:** Output confidence (speaking), not passive comprehension.  
**Secondary goal:** Validate that the mini-moi orchestration pattern works for real-time personal practice loops, not just long-horizon research.

---

## 2. Architecture Overview

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

## 3. Folder Structure

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
  parse_transcript.py
  reviewer.py
  status.py
```

**.gitignore additions required:**
```
_NewDomains/language-german/language/german/sessions/
_NewDomains/language-german/language/german/anki/
_NewDomains/language-german/language/german/lessons/
_NewDomains/language-german/language/german/progress.json
```

---

## 4. Handoff Mechanism: Telegram Bridge

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

**What Robert sends** (Grok output + manual header prepended if Grok doesn't produce it):

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

## 5. JSON Transcript Schema

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

## 6. Personas (Vienna Travel Focus)

All personas are optimized for voice — natural spoken register, forgiving but realistic, occasional gentle in-character correction.

### Persona Definitions

**1. Frau Berger — Bakery Owner**  
Warm, patient Viennese woman in her 50s. Runs a traditional Bäckerei near the Naschmarkt. Loves chatting about bread and pastries. Will gently rephrase things Robert says incorrectly as part of her natural reply ("Ach, Sie meinen...").  
*Scenarios:* `bakery_order`, `neighborhood_chat`  
*Difficulty:* Beginner-friendly

**2. Herr Fischer — Hotel Receptionist**  
Professional, efficient, polite. Works at a mid-range hotel near the Ringstraße. Speaks clearly and somewhat formally — good for practicing standard Hochdeutsch before encountering Viennese dialect.  
*Scenarios:* `hotel_checkin`, `hotel_checkout`, `room_problem`, `directions_from_hotel`  
*Difficulty:* Beginner-friendly

**3. Maria — Café Waitress**  
Young, slightly hurried, uses a bit of Viennese slang. Works at a classic Kaffeehaus. Tests whether Robert can keep up with natural pace and informal register.  
*Scenarios:* `cafe_order`, `cafe_bill`, `cafe_small_meal`  
*Difficulty:* Intermediate

**4. Dr. Huber — Museum Guide**  
Enthusiastic, knowledgeable, speaks in complete sentences. Works at the Kunsthistorisches Museum. Good for practicing listening comprehension of longer explanations and asking follow-up questions.  
*Scenarios:* `museum_exhibit`, `museum_navigation`, `museum_recommendation`  
*Difficulty:* Intermediate

**5. Stefan — U-Bahn Stranger**  
Relaxed local, early 30s. Willing to help a tourist but speaks at normal speed. Tests directional vocabulary and city navigation under mild time pressure.  
*Scenarios:* `ubahn_directions`, `ubahn_which_line`, `ubahn_confirm_stop`  
*Difficulty:* Intermediate

**6. Frau Novak — Pharmacist**  
Calm, precise, professional. Good for medical and practical vocabulary that travelers might genuinely need.  
*Scenarios:* `pharmacy_medicine`, `pharmacy_symptom`, `pharmacy_instructions`  
*Difficulty:* Beginner-friendly

**7. Klaus — Upscale Restaurant Waiter**  
Formal, proud of the menu, expects proper dining etiquette. Slight Austrian formality ("Bitte sehr, der Herr..."). Good for practicing elevated register and full meal interactions.  
*Scenarios:* `restaurant_reservation`, `restaurant_full_meal`, `restaurant_wine`, `restaurant_bill`  
*Difficulty:* Advanced

**8. Anna — Airbnb Host**  
Friendly, practical, explains things clearly. A good onboarding persona for early sessions — patient and encouraging.  
*Scenarios:* `apartment_handoff`, `appliance_question`, `neighborhood_recommendations`, `small_problem`  
*Difficulty:* Beginner-friendly

---

## 7. Reviewer Output Schema

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
Track errors by type across sessions to surface patterns over the 3-week period:

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

## 8. Anki CSV Format

Standard Anki import with tags and example sentences:

```
Front,Back,Tags
"das Kipferl","crescent roll (Austrian) — Example: Ich hätte gerne zwei Kipferln, bitte.","german vienna food bakery"
"Ich hätte gerne...","I would like... (polite order form) — Example: Ich hätte gerne einen Kaffee, bitte.","german vienna polite_forms"
```

**Deduplication:** Check each vocabulary highlight's `german` field against `progress["vocabulary_seen"]` before writing. Skip if already present. Add new words to `vocabulary_seen` after writing.

**File path:** `anki/YYYY-MM-DD_anki.csv`. If file already exists for the date, append new rows — handles multiple sessions per day.

---

## 9. Daily Lesson Plan Schema

```json
{
  "lesson_date": "2026-04-21",
  "lesson_number": 3,
  "persona": "Maria",
  "scenario": "cafe_order",
  "warm_up": "Review: das Brot, die Semmel, das Kipferl. Gender drill.",
  "focus": "Ordering coffee in a Kaffeehaus. Practice Melange, Verlängerter, Einspänner.",
  "speaking_prompt": "You walk into Café Central. Maria greets you. Order a Melange and a piece of Apfelstrudel. Ask if they have a newspaper.",
  "writing_exercise": "Write 3–4 sentences describing what you ordered yesterday and how the conversation went. Focus on past tense (hatte, war, habe bestellt).",
  "vocabulary_targets": ["Melange", "Verlängerter", "Einspänner", "die Rechnung", "Zahlen bitte"],
  "carry_forward_errors": ["das/die/der Brot correction from session 2026-04-20"]
}
```

---

## 10. Progress Tracking

`progress.json` accumulates across all sessions:

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

## 11. OpenClaw Orchestration Commands

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

## 12. Build Sequence & Timeline

### Step 0 — Pre-build validation (before any code)
- [ ] Telegram bridge: send test message, confirm OpenClaw receives and parses
- [ ] `python -c "import anthropic"` works in the venv

Both must pass before build starts. If either fails, resolve before proceeding.

### Today (Sunday April 19) — Documents to GitHub
1. OpenClaw creates files in `_NewDomains/language-german/`
2. Claude Code commits and pushes to GitHub
3. Confirm live on GitHub before any implementation begins

### Monday (April 20) — Build
1. Scaffold folder structure + config files
2. `parse_transcript.py` — tested with sample fixture
3. `reviewer.py` — tested with session JSON from step 2
4. Anki CSV and lesson plan generators
5. `status.py`
6. `.gitignore` update
7. First live test session

### Tuesday (April 21) — Use & Fix
- Morning: first real voice session (Frau Berger, bakery)
- Fix reviewer output quality issues
- Import first Anki deck

### Wednesday onward — Steady State
- Daily sessions, minor tweaks only

---

## 13. Graduation Criteria (Post-Vienna)

This domain graduates from `_NewDomains/` to main repo if, after the Vienna trip:
- Telegram bridge was stable across 5+ sessions
- Reviewer output quality was genuinely useful
- Anki cards were imported and used
- Progress tracking showed meaningful pattern detection

If any criterion fails, domain is archived with a post-mortem note.

---

## 14. Out of Scope (v1.0)

- Real-time voice error correction during session
- Automated Anki import (manual import is fine)
- Full mobile review UI
- Pronunciation scoring
- Audio hints in Anki cards (post-graduation candidate)
- Grammar explanation depth beyond practical corrections
- Any language other than German
