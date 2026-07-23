# Plan Delta — Pre-Build Additions
## Language Learning Domain — German POC

**Purpose:** This document records the targeted additions made after Claude Code's initial plan review, incorporating feedback from Grok and final design decisions from Claude. These changes are already folded into SPEC.md v1.0. This document exists as an audit trail of what changed and why.

---

## Change 1: Expanded Error Taxonomy

**Why:** The original 6 error types missed two of the most common German learner errors — case and preposition — and ignored the Austrian-specific idiomatic layer that makes Vienna German distinct from textbook German.

**What changed:** Three new error types added to the taxonomy, reviewer prompt, `error_type_counts` schema, and `progress.json` initial state:

| New Type | Description |
|---|---|
| `case` | Wrong case (nominative/accusative/dative/genitive) |
| `preposition` | Wrong preposition or wrong case after preposition |
| `idiomatic` | Non-Austrian or unnatural phrasing where a natural idiom exists |

**Affected files to update:**
- `reviewer.py` system prompt schema
- `progress.json` initial state
- `SPEC.md` sections 7 and 10 (already updated in v1.0)

---

## Change 2: Anki Card Quality Upgrade

**Why:** Simple two-column CSV (Front/Back) produces low-quality cards with no context. Example sentences and tags make cards dramatically more useful for active recall and Anki deck organization.

**What changed:** Anki CSV format upgraded to three columns with richer Back field:

```
Front,Back,Tags
"das Kipferl","crescent roll (Austrian) — Example: Ich hätte gerne zwei Kipferln, bitte. — bakery_order 2026-04-20","german vienna food bakery"
```

- `Back` now includes: English definition + example sentence in context + session date/scenario
- `Tags` column added: space-separated Anki tags derived from vocabulary highlight tags in reviewer output
- `vocabulary_highlights` schema in reviewer prompt updated to include `example_sentence` and `tags` fields

**Affected files:**
- `reviewer.py` system prompt (vocabulary_highlights schema)
- `_generate_anki_csv()` function
- `SPEC.md` section 8 (already updated in v1.0)

---

## Change 3: Telegram Format Contract Locked

**Why:** The original plan flagged the Telegram bridge as a blocker but did not specify the exact message format OpenClaw should expect or the exact actions it should take. This was a real gap — an underspecified contract between Robert's mobile action and OpenClaw's parse logic.

**What changed:** Full bidirectional contract added:

**Inbound (Robert → OpenClaw):**
```
GERMAN_SESSION_TRANSCRIPT
Date: 2026-04-20
Persona: Frau Berger
Scenario: bakery_order
Duration: 12

Robert: [turn text]
Frau Berger: [turn text]
```

- Trigger keyword: `GERMAN_SESSION_TRANSCRIPT` on first line
- `Duration` is optional — missing value recorded as 0, does not crash
- Robert prefixes accepted: `Robert:`, `You:`, `User:`
- Unknown prefixes: append to prior turn, do not drop

**Outbound (OpenClaw → Robert after successful review):**
```
✅ Session reviewed — Frau Berger / bakery_order
Errors: gender×2, case×1
New Anki cards: 6
Tomorrow: Maria — café_order (Lesson 4)
Next focus: Article genders for food nouns
```

**On failure:** OpenClaw sends error message + raw file path for manual debugging.

**Affected files:**
- OpenClaw Telegram handler configuration
- `parse_transcript.py` header detection logic
- `SPEC.md` section 4 (already updated in v1.0)

---

## Change 4: Additional CLI Flags

**Why:** Quality-of-life additions flagged during review.

| Flag | File | Purpose |
|---|---|---|
| `--today` | `reviewer.py` | Process all unreviewed sessions from today |
| `!german today` | OpenClaw commands | Manual trigger for today's review via Telegram |

---

## Change 5: `_update_progress()` Made Explicit

**Why:** The original plan described reading `progress.json` in multiple places but did not name the write-back function or enumerate all fields it must update. This risked silent data loss if the function was implemented incompletely.

**What changed:** `_update_progress()` is now a named, required function in `reviewer.py` with all fields explicitly listed. See PLAN.md section 4 for the complete field list.

---

## What Did NOT Change

These Grok suggestions were reviewed and deliberately deferred:

- **Audio hints in Anki cards** — adds API complexity and a new dependency. Deferred to post-graduation if the domain proves useful.
- **Full plan rewrite** — the plan was 85-90% correct. Surgical additions (this document) are cleaner than a full rewrite and preserve Claude Code's prior review.
