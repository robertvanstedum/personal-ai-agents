# Language Learning Domain — DOMAIN_SPEC.md
**Status:** Design phase — nothing built yet  
**Visibility:** Private until feedback loop working  
**Created:** 2026-03-07

---

## Vision

A personal AI language tutor that learns from your actual conversations and errors — not from a fixed curriculum. The same feedback loop architecture as the geopolitics curator, applied to language acquisition.

**Not:** a course, an app, a flashcard system  
**Yes:** a dynamic system that gets smarter the more you use it

---

## Current State (What Already Exists)

- German voice conversations via Grok (ongoing)
- Two personas configured: tech guy + girlfriend
- German articles already appearing in geopolitics daily briefing
- No capture, analysis, or feedback loop yet

---

## Target Outcome

**12-month goal:** German B1 business level  
**Method:** Learning by doing, feedback loop, not structured course  
**Future languages:** French (next), Spanish/Portuguese maintenance (already fluent)

---

## Core Pattern

```
INPUT                    ANALYSIS              LEARNED PROFILE
─────                    ────────              ───────────────
Grok transcripts    →    gap detection    →    learner_profile.json
Written exercises   →    error patterns   →    (per language)
German briefing     →    vocabulary hits  →
        │                                            │
        └────────────────────────────────────────────┘
                          ↓
               PERSONALIZED OUTPUT
               - Written exercises from real errors
               - Vocabulary from your conversations
               - Session prep for next conversation
               - Level progress tracking
                          ↓
                   USER FEEDBACK
               (correct / still struggling / mastered)
                          ↓
                   PROFILE UPDATES
```

---

## Language-Agnostic Design (Critical)

This domain is built for any language from day one. German is the first implementation.

```
domains/language_learning/
├── languages/
│   ├── german/
│   │   ├── learner_profile.json   ← gaps, strengths, vocabulary
│   │   ├── sessions/              ← transcripts (gitignored)
│   │   ├── exercises/             ← generated written work
│   │   └── vocabulary/            ← personal word bank
│   └── french/                    ← ready when needed
├── tools/
│   ├── transcript_parser.py       ← ingests conversation transcripts
│   ├── analyzer.py                ← AI identifies gaps and patterns
│   ├── exercise_generator.py      ← builds written work from real errors
│   └── profile_updater.py         ← updates learner_profile.json
├── learner_config.yaml            ← language, level, goals, persona config
└── DOMAIN_SPEC.md                 ← this file
```

---

## Persona System

Conversations are structured around personas. Each persona has:

```yaml
personas:
  - name: Marcus
    relationship: friend
    background: software engineer, Berlin startup
    topics: [tech, work, city life, food]
    style: casual, some English tech terms
    difficulty: intermediate
    
  - name: Julia
    relationship: Marcus's girlfriend
    background: marketing, Munich originally
    topics: [culture, travel, relationships, weekend plans]
    style: casual, faster speech
    difficulty: intermediate-advanced
```

Over time the system tracks which persona exposes which gaps. Marcus may surface technical vocabulary holes. Julia may surface gender agreement errors. That's useful signal.

---

## Learner Profile Schema

```json
{
  "language": "german",
  "level_target": "B1",
  "level_current": "A2-B1",
  "updated_at": "2026-03-07",
  
  "recurring_gaps": [
    {"pattern": "accusative case endings", "frequency": "high", "last_seen": "2026-03-05"},
    {"pattern": "separable verbs word order", "frequency": "medium", "last_seen": "2026-03-04"}
  ],
  
  "vocabulary": {
    "holes": ["der Vorstand", "die Niederlassung"],
    "mastered": ["das Unternehmen", "arbeiten", "wohnen"],
    "in_progress": ["trotzdem", "obwohl"]
  },
  
  "strong_areas": ["present tense conjugation", "basic word order"],
  "avoid_drilling": ["numbers", "basic greetings"],
  
  "recent_focus": {
    "topic": "accusative case",
    "started": "2026-03-04",
    "expires": "2026-03-14"
  }
}
```

---

## Output Types

**After each conversation session:**
- Error summary: what you got wrong, patterns
- 5-10 targeted written exercises from real mistakes
- 3-5 new vocabulary items from that conversation
- One grammar focus for next session

**Weekly:**
- Progress summary
- Level assessment
- Recommended reading from German briefing articles matched to current level

**Session prep (before next conversation):**
- Recap of last session gaps
- 3 sentences to practice before starting
- Vocabulary refresh

---

## Integration with Geopolitics Domain

German articles already appear in the daily briefing. With a small addition:
- Tag German articles with estimated reading level (A2/B1/B2)
- Surface articles matched to current learner level
- Track which articles were read (signal for profile)

This is a synergy — not a dependency. Geopolitics domain does not require language learning domain.

---

## Privacy Rules

| Content | Treatment |
|---------|-----------|
| Conversation transcripts | Gitignored always |
| Written exercises | Gitignored always |
| Learner profile | Gitignored (contains personal performance data) |
| Tools and code | Private repo until milestone |
| Config schema | Can go public (no personal data) |

---

## Build Sequence

### Phase 1 — Capture (blocker: resolve transcript export)
- Determine Grok transcript export method
- If exportable: build transcript_parser.py
- If not: design manual paste workflow as fallback
- Store transcripts in sessions/ (gitignored)

### Phase 2 — Analysis
- analyzer.py: send transcript to Haiku/Sonnet for gap detection
- Output: structured error report
- Update learner_profile.json

### Phase 3 — Exercise Generation
- exercise_generator.py: generate written exercises from real errors
- Not generic drills — always from your actual output
- Store in exercises/ (gitignored)

### Phase 4 — Feedback Loop
- User marks exercises: correct / still struggling / mastered
- profile_updater.py adjusts weights
- Loop closes: next session prep informed by feedback

### Phase 5 — Go Public
- Anonymize/remove personal data from repo
- Write clean README for language-agnostic system
- Add to public GitHub and resume

---

## Key Blocker

**Grok transcript export:** Everything depends on being able to ingest conversation transcripts. Robert to check whether Grok saves voice conversation history and whether it's exportable. If not, design a manual paste workflow as fallback.

---

## Notes for Future

- System should work with any AI conversation partner, not just Grok
- Could eventually generate new persona configs for specific scenarios (job interview, cocktail party, travel)
- B1 certification prep: mock exam exercises when approaching milestone
- French domain: same tools, different language config — should be trivial to add
