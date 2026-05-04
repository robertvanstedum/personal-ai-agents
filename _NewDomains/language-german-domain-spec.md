# Language Learning Domain – German POC
## Design Specification v0.9
**Status:** _NewDomains candidate  
**Author:** Robert van Stedum  
**Date:** 2026-04-19  
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
    │  Voice session → "End session. Clean transcript."
    │  Copy transcript → Telegram message to OpenClaw bot
    ▼
Telegram Bridge
    │
    ▼
OpenClaw (MacBook)
    │  Receives transcript via Telegram
    │  Saves as JSON → language/german/sessions/
    │  Triggers Python reviewer
    ▼
Python Reviewer
    │  Parses transcript
    │  Generates: feedback, vocabulary highlights, Anki CSV, next-day prompts
    ▼
Output files → language/german/anki/, language/german/lessons/
    │
    ▼
OpenClaw updates progress.json + sends summary back via Telegram
```

**Agent division of labor (binding):**
- **Claude.ai** — design, spec, persona definitions, reviewer logic design
- **Claude Code** — implementation, commits, file scaffolding
- **OpenClaw** — orchestration, Telegram bridge, folder watcher, memory
- **Robert** — decision point between all agents; approves spec before build begins

---

## 3. Folder Structure

Lives inside the main mini-moi repo under `language/`:

```
language/
  german/
    config/
      domain.json            # Domain config: active persona, lesson number, settings
      personas.json          # All persona definitions
    sessions/
      YYYY-MM-DD_NNN.json    # Raw transcript + metadata per session
    anki/
      YYYY-MM-DD_anki.csv    # Daily Anki import file
    lessons/
      YYYY-MM-DD_lesson.json # Next-day lesson plan generated after each session
    progress.json            # Cumulative stats, error patterns, vocabulary seen
    README.md                # Domain overview
```

**`_NewDomains/` placement:** Spec lives at `_NewDomains/language-german/SPEC.md` until graduation. Domain folder at `_NewDomains/language-german/language/german/`.

---

## 4. Handoff Mechanism: Telegram Bridge

### Why Telegram
- OpenClaw already runs on Telegram — no new infrastructure
- Grok iOS has share/copy functionality; 2–3 taps to forward transcript
- OpenClaw can parse, save, and trigger the review pipeline automatically upon receipt

### Known Risk: Telegram Instability
> ⚠️ **Blocker risk noted.** Recent Telegram issues with OpenClaw have been observed. Before this domain goes live, the Telegram receive-and-parse flow must be validated end-to-end with a test transcript. This is **the first thing to test** before any other build work proceeds.

### Mobile Workflow (iPhone)
1. Open Grok iOS → German Practice chat
2. Tell Grok: *"Start today's [scenario] with [persona name]"*
3. Conduct voice session (10–15 min)
4. Say: *"End session. Give me a clean transcript."*
5. Copy Grok's output → paste into OpenClaw Telegram bot
6. Done. MacBook handles everything from here.

### Telegram Message Format Contract
OpenClaw must detect incoming transcripts by a **trigger header** in the message:

```
GERMAN_SESSION_TRANSCRIPT
Date: YYYY-MM-DD
Persona: [persona name]
Scenario: [scenario label]

[transcript body — alternating Robert: / Persona: lines]
```

If Grok does not produce this exact format natively, Robert will prepend the header manually before sending. The trigger keyword `GERMAN_SESSION_TRANSCRIPT` is the parse signal.

### Fallback (if Telegram is unavailable)
Drop the transcript text as a `.txt` file into `language/german/sessions/inbox/` via iCloud Drive. OpenClaw folder-watches this path as a secondary trigger. This path requires an iOS Shortcut (to be built if needed).

---

## 5. JSON Transcript Schema

Saved by OpenClaw to `sessions/YYYY-MM-DD_NNN.json`:

```json
{
  "session_id": "2026-04-20_001",
  "date": "2026-04-20",
  "persona": "Frau Berger",
  "scenario": "bakery",
  "duration_estimate_min": 12,
  "source": "telegram",
  "raw_transcript": [
    { "speaker": "Robert", "text": "Guten Morgen. Ich hätte gerne zwei Kipferln, bitte." },
    { "speaker": "Frau Berger", "text": "Natürlich! Sonst noch etwas?" }
  ],
  "reviewer_output": null,
  "anki_generated": false,
  "next_lesson_generated": false
}
```

`reviewer_output` and downstream fields are populated by the Python reviewer after processing.

---

## 6. Personas (Vienna Travel Focus)

All personas are optimized for voice — natural spoken register, forgiving but realistic, occasional gentle correction in-character.

### Persona Definitions

**1. Frau Berger — Bakery Owner**  
Warm, patient Viennese woman in her 50s. Runs a traditional Bäckerei near the Naschmarkt. Loves chatting about bread and pastries. Will gently rephrase things Robert says incorrectly as part of her natural reply ("Ach, Sie meinen...").  
*Scenarios:* Ordering bread/pastries, asking about specials, small talk about the neighborhood.

**2. Herr Fischer — Hotel Receptionist**  
Professional, efficient, polite. Works at a mid-range hotel near the Ringstraße. Speaks clearly and a bit formally — good for practicing standard Hochdeutsch before encountering Viennese dialect.  
*Scenarios:* Check-in/check-out, asking about amenities, reporting a problem with the room, asking for directions.

**3. Maria — Café Waitress**  
Young, slightly hurried, uses a bit of Viennese slang. Works at a classic Kaffeehaus. Tests whether Robert can keep up with natural pace and informal register.  
*Scenarios:* Ordering coffee (Melange, Verlängerter, etc.), asking for the bill, ordering a small meal.

**4. Dr. Huber — Museum Guide**  
Enthusiastic, knowledgeable, speaks in complete sentences. Works at the Kunsthistorisches Museum. Good for practicing listening comprehension of longer explanations and asking follow-up questions.  
*Scenarios:* Asking about an exhibit, requesting a recommendation, navigating the museum.

**5. Stefan — U-Bahn Stranger**  
Relaxed local, early 30s. Willing to help a tourist but speaks at normal speed. Tests directional vocabulary and city navigation.  
*Scenarios:* Asking for directions, figuring out which line to take, confirming a stop.

**6. Frau Novak — Pharmacist**  
Calm, precise, professional. Good for medical/practical vocabulary that travelers might genuinely need.  
*Scenarios:* Asking for medicine, describing a mild symptom, understanding instructions.

**7. Klaus — Restaurant Waiter (Upscale)**  
Formal, proud of the menu, expects proper dining etiquette. Slight Austrian formality ("Bitte sehr, der Herr..."). Good for practicing more elevated register.  
*Scenarios:* Making a reservation by phone, ordering a full meal, asking about wine, paying the bill.

**8. Anna — Airbnb Host**  
Friendly, practical, explains things clearly. A good "onboarding" persona for early sessions — patient and encouraging.  
*Scenarios:* Apartment handoff, asking about appliances, neighborhood recommendations, reporting a small problem.

---

## 7. Python Reviewer Architecture

**Input:** `sessions/YYYY-MM-DD_NNN.json`  
**Outputs:**
- Updated session JSON with `reviewer_output` populated
- `anki/YYYY-MM-DD_anki.csv`
- `lessons/YYYY-MM-DD_lesson.json`

### Reviewer Output Schema (inside session JSON)

```json
"reviewer_output": {
  "overall_summary": "Good session. Strong use of polite forms. Article genders were the main challenge.",
  "errors": [
    {
      "type": "gender",
      "original": "der Brot",
      "correction": "das Brot",
      "explanation": "Brot is neuter in German.",
      "context": "Ich hätte gerne der Brot."
    }
  ],
  "error_type_counts": {
    "gender": 2,
    "word_order": 1,
    "missing_article": 0,
    "verb_conjugation": 1,
    "vocabulary": 0
  },
  "vocabulary_highlights": [
    { "german": "Kipferl", "english": "crescent roll (Austrian term)", "note": "Used correctly" },
    { "german": "Melange", "english": "Viennese coffee with steamed milk", "note": "New word this session" }
  ],
  "strengths": ["Polite register", "Correct use of hätte gerne", "Good recovery after hesitation"],
  "next_focus": "Article genders for food items. Practice: das Brot, die Semmel, das Kipferl."
}
```

### Error Taxonomy
Track errors by type across sessions to surface patterns over the 3-week period:
- `gender` — wrong article (der/die/das)
- `word_order` — V2 rule, verb-final in subordinate clause
- `missing_article` — article omitted entirely
- `verb_conjugation` — wrong person/tense form
- `vocabulary` — wrong word choice or missing word
- `register` — too informal / too formal for context

### Anki CSV Format
Standard Anki 2-column import:

```
Front,Back
"das Kipferl","crescent roll (Austrian) — used in bakery session 2026-04-20"
"Ich hätte gerne...","I would like... (polite order form)"
```

---

## 8. Daily Lesson Plan Schema

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
  "carry_forward_errors": ["das/die/der Brot correction from session 2"]
}
```

---

## 9. Progress Tracking

`progress.json` accumulates across all sessions:

```json
{
  "total_sessions": 5,
  "total_minutes": 63,
  "personas_practiced": ["Frau Berger", "Maria", "Herr Fischer"],
  "scenarios_covered": ["bakery", "cafe_order", "hotel_checkin"],
  "cumulative_error_counts": {
    "gender": 8,
    "word_order": 3,
    "missing_article": 2,
    "verb_conjugation": 4,
    "vocabulary": 1,
    "register": 0
  },
  "anki_cards_generated": 34,
  "vocabulary_seen": ["Kipferl", "Melange", "Verlängerter", "Semmel"],
  "strengths_noted": ["Polite register", "hätte gerne construction"],
  "last_updated": "2026-04-20T21:15:00"
}
```

---

## 10. OpenClaw Orchestration Commands

Standard commands Robert can issue via Telegram:

| Command | Action |
|---|---|
| `!german status` | Report today's lesson plan + last session summary |
| `!german next` | Generate tomorrow's lesson plan manually |
| `!german anki` | Re-export today's Anki CSV |
| `!german progress` | Print cumulative progress summary |
| `!german persona [name]` | Override tomorrow's assigned persona |
| `!german debug` | Dump last received transcript + parse result (for debugging) |

---

## 11. Build Sequence & Timeline

### Today (Sunday April 20) — Build
1. ✅ Spec complete (this document)
2. OpenClaw registers domain in `_NewDomains/`
3. **Claude Code (plan mode):** Review spec, flag implementation questions
4. Validate Telegram bridge end-to-end with test transcript ← **critical path**
5. Scaffold folder structure + config files
6. Implement Telegram receiver + transcript parser in OpenClaw
7. Build Python reviewer skeleton (transcript → feedback + error tagging)
8. Build Anki CSV generator
9. Build lesson plan generator
10. Wire OpenClaw orchestration commands
11. First live test session

### Monday (April 21) — Use & Fix
- Morning: first real voice session with Frau Berger (bakery scenario)
- Review output quality, fix reviewer bugs
- Tune Anki format if needed
- Import first Anki deck

### Tuesday (April 22) — Steady State
- Second session with Maria (café)
- Minor tweaks only
- Domain considered live

---

## 12. Graduation Criteria (Post-Vienna)

This domain graduates from `_NewDomains/` to main repo if:
- Telegram bridge is stable across 5+ sessions
- Reviewer output quality is genuinely useful (not just noise)
- Anki cards are being imported and used
- Progress tracking shows meaningful pattern detection

If any of these fail, the domain is archived with a post-mortem note and learnings applied to the next language domain attempt.

---

## 13. Out of Scope (v0.9)

- Real-time voice error correction during session
- Automated Anki import (manual import is fine)
- Full mobile review UI
- Pronunciation scoring
- Grammar explanation depth beyond practical corrections
- Any language other than German
