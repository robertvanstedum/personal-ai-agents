# Persona Memory + Learner Profile + Tutor System
*mini-moi · language-german domain · v1.1 feature*
*Spec version 2 — 2026-05-23*
*See also: _working/LEARNING_LOOP_VISION.md*

---

## Intent

This feature closes the learning loop. Every session leaves a trace.
Every trace informs the next session. Over time the system knows Robert
as a learner — not just as a user.

This is not optional polish. It is the reason the system exists.

---

## Architecture

```
Layer 1 — Persona Memory     (per relationship, per user)
Layer 2 — Learner Profile    (cross-persona, cross-tab pattern layer)
Layer 3 — Tutor              (surfaces suggestions, backs off on improvement)
Layer 4 — Profile Access     (placeholder: ACTIVE_USER env var)
```

---

## Safe Write Helper (prerequisite — build first)

**Problem:** Telegram and HTML both write to the same JSON files concurrently.
Without locking, writes can corrupt data. This must ship before Phase 1.

**File:** `german_domain.py` (or new `storage.py` helper module)

```python
import fcntl
import json
from pathlib import Path
from typing import Any

def safe_read_json(path: Path) -> dict:
    """Read JSON file with shared lock. Returns {} if file absent."""
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def safe_write_json(path: Path, data: dict) -> None:
    """Write JSON file with exclusive lock. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2, ensure_ascii=False)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
```

**All JSON reads/writes in the memory system must use these helpers.**
Retrofit existing `_load_phrasebook()` / `_save_phrasebook()` to use
them as well — they have the same race condition risk.

---

## Layer 1 — Persona Memory

### Round Model

A **round** is one ongoing memory thread between a user and a persona.
Accumulates across sessions. Closes when complete. Archives as a summary.

```
Round 1: Robert + Frau Berger (5 sessions) → ARCHIVED
Round 2: Robert + Frau Berger (5 sessions) → ARCHIVED
Round 3: Robert + Frau Berger (2 sessions) → ACTIVE ● ready_to_archive: false
```

**Default round lengths by persona type:**

| Type | Default | Reset trigger |
|---|---|---|
| Casual (café, hotel, pharmacy) | 5 sessions | Auto-flag, manual confirm |
| Social (U-Bahn stranger, local) | 4 sessions | Auto-flag, manual confirm |
| Friend (long-term persona) | Open-ended | Manual only |

Admin can override defaults per persona. User can extend any active round.

### Data Model — `config/persona_memory.json`

```json
{
  "robert_frau_berger": {
    "user": "robert",
    "persona": "frau_berger",
    "persona_type": "casual",
    "current_round": 3,
    "round_default": 5,
    "round_extended": false,
    "ready_to_archive": false,
    "active_memory": {
      "round_number": 3,
      "sessions_this_round": 2,
      "last_seen": "2026-05-23",
      "topics_discussed": ["Brot", "Naschmarkt", "Wiener Küche"],
      "errors_noted": ["Dativ prepositions", "word order in subordinate clauses"],
      "strengths": ["polite register", "Konjunktiv II"],
      "vocabulary_introduced": ["das Gebäck", "die Semmel", "ausverkauft"],
      "notes": "Robert ist entspannter geworden. Macht weniger Pausen.",
      "rapport_level": "warm"
    },
    "archived_rounds": [
      {
        "round_number": 1,
        "sessions": 5,
        "date_start": "2026-04-15",
        "date_end": "2026-04-28",
        "summary": "Erste Begegnungen. Robert übte Bestellungen und Smalltalk. Häufigste Fehler: Akkusativ nach 'für'. Stärken: höflicher Register.",
        "key_errors": ["Akkusativ nach 'für'", "kein → keinen"],
        "key_strengths": ["bitte/danke", "Entschuldigung"],
        "summary_generated": "llm"
      }
    ]
  }
}
```

**`summary_generated`:** `"llm"` | `"auto"` (fallback if LLM call fails —
generated from raw fields without LLM).

### Domain Functions

```python
def get_persona_memory(user: str, persona_slug: str) -> dict:
    """
    Returns memory block for user+persona pair.
    Creates empty default on first access.
    Uses safe_read_json / safe_write_json.
    """

def update_persona_memory(user: str, persona_slug: str,
                          updates: dict) -> dict:
    """
    Merges updates into active_memory.
    Lists: append + deduplicate + cap at 20.
    Scalars: overwrite.
    Increments sessions_this_round.
    If sessions_this_round >= round_default:
        sets ready_to_archive = True
        does NOT auto-close — user confirms
    Returns updated memory dict.
    """

def close_round(user: str, persona_slug: str,
                summary: str = None) -> None:
    """
    Archives active_memory as completed round.
    If summary is None: generates via LLM (see prompt below).
    If LLM fails: auto-generates from raw fields (fallback).
    Resets active_memory to empty new round.
    Increments current_round.
    Sets ready_to_archive = False.
    """

def extend_round(user: str, persona_slug: str,
                 extra_sessions: int = 3) -> None:
    """
    Adds extra_sessions to round_default for current round.
    Sets round_extended = True.
    Sets ready_to_archive = False.
    """

def build_persona_prompt(persona: dict, memory: dict,
                         tutor_focus: str = None) -> str:
    """
    Injects active memory into persona system prompt.
    Round 1, session 1: base prompt only.
    Subsequent: includes memory block in German.
    If tutor_focus provided: adds focus instruction.
    """
```

### Round Summary Prompt

```
Generate a 2-sentence summary in German of this language learning round.
Focus on: main errors observed, main strengths, overall progress.
Be specific and encouraging. Do not use filler phrases.

Sessions completed: {N}
Main errors: {errors}
Main strengths: {strengths}
Topics covered: {topics}
Vocabulary introduced: {vocabulary}
```

**Fallback (if LLM fails):**
```python
summary = (
    f"Runde {round_num} abgeschlossen ({sessions} Sitzungen). "
    f"Themen: {', '.join(topics[:3])}. "
    f"Häufigste Fehler: {', '.join(errors[:2])}."
)
summary_generated = "auto"
```

### Round-Ready UI Indicator

When `ready_to_archive: true`, show in Gespräche persona list:

```
🥐 Frau Berger   Runde 3/5 ●   ← dot signals ready
   Bäckerei
```

Clicking the dot or the persona row shows:
```
Runde 3 abgeschlossen (5 Sitzungen).
[ Neue Runde starten ] [ Verlängern +3 ] [ Jetzt nicht ]
```

This keeps the decision in the workflow, not buried in Admin.
Admin can also manage rounds — but the first prompt should be in context.

### Integration Points

**After `analyse_session()`:**
```python
memory = update_persona_memory(
    user=ACTIVE_USER,
    persona_slug=session_persona_slug,
    updates={
        "last_seen": today_iso,
        "topics_discussed": feedback.get("topics", []),
        "errors_noted": [e["explanation"] for e in feedback.get("errors", [])],
        "strengths": feedback.get("strengths", []),
        "vocabulary_introduced": feedback.get("vocabulary", []),
        "notes": feedback.get("overall_summary", ""),
    }
)
# Then update learner profile (Layer 2)
update_learner_profile(
    user=ACTIVE_USER,
    error_list=[e["explanation"] for e in feedback.get("errors", [])],
    strength_list=feedback.get("strengths", []),
    source="gesprache"
)
```

**After `correct_writing()`:**
```python
update_learner_profile(
    user=ACTIVE_USER,
    error_list=correction_notes,
    strength_list=[],
    source="schreiben"
)
```

**Telegram sessions:** same `update_persona_memory()` and
`update_learner_profile()` calls — unified store, unified learning loop.

---

## Layer 2 — Learner Profile

### Data Model — `config/learner_profile.json`

```json
{
  "robert": {
    "user": "robert",
    "display_name": "Robert",
    "language": "de",
    "created": "2026-04-01",
    "tutor_settings": {
      "observation_window_days": 14,
      "observation_window_sessions": 5,
      "error_threshold_count": 3,
      "error_threshold_sessions": 2,
      "improvement_threshold_sessions": 2
    },
    "active_focus": {
      "topic": "Dativ prepositions",
      "set_date": "2026-05-20",
      "source": "accepted",
      "sessions_since_set": 3,
      "improvement_detected": false
    },
    "error_trends": [
      {
        "error": "Dativ prepositions",
        "count": 8,
        "sessions_seen": 4,
        "first_seen": "2026-04-20",
        "last_seen": "2026-05-22",
        "improving": false,
        "sources": ["gesprache", "schreiben"]
      }
    ],
    "strengths": ["polite register", "Konjunktiv II"],
    "dismissed_suggestions": [
      {
        "topic": "subordinate clause word order",
        "dismissed": "2026-05-15",
        "resurface_after": "2026-06-15"
      }
    ],
    "total_sessions": 18,
    "total_writing_entries": 12,
    "last_updated": "2026-05-23"
  }
}
```

### Tutor Suggestion Model — `config/tutor_suggestions.json`

```json
{
  "suggestions": [
    {
      "suggestion_id": "sug_20260523_001",
      "user": "robert",
      "topic": "Dativ prepositions",
      "evidence": "6 Fehler in 3 Sitzungen (15.–22. Mai)",
      "sources": ["gesprache", "schreiben"],
      "suggested_at": "2026-05-23",
      "status": "pending",
      "message": "Dativ-Präpositionen waren in den letzten 3 Sitzungen ein wiederkehrendes Thema. Sollen wir das betonen?"
    }
  ]
}
```

### Domain Functions

```python
def update_learner_profile(user: str, error_list: list,
                           strength_list: list, source: str) -> None:
    """
    Updates error_trends: increment count, update last_seen, add source.
    Updates strengths list.
    Checks threshold: if error count >= 3 AND sessions >= 2
        within observation window → creates pending suggestion.
    Checks active_focus improvement: if focus error drops off for
        improvement_threshold_sessions → sets improvement_detected = True.
    """

def get_pending_tutor_suggestions(user: str) -> list:
    """Returns pending suggestions for nav badge + Admin display."""

def respond_to_suggestion(user: str, suggestion_id: str,
                          response: str, variant: str = None) -> None:
    """
    response options:
      'accept'  → sets active_focus in learner profile
      'dismiss' → sets resurface_after (+30 days)
      'variant' → stores variant text as active_focus topic
      'defer'   → sets resurface_after (+14 days)
    Updates suggestion status.
    """

def check_focus_improvement(user: str) -> dict | None:
    """
    Called after each session.
    If active_focus.improvement_detected == True:
        returns positive notification dict
        clears active_focus
    Returns None if no improvement yet.
    """
```

### Tutor Focus Injection

When `active_focus` is set, `build_persona_prompt()` appends:
```
Aktueller Lernfokus: {active_focus.topic}
Achte besonders darauf und korrigiere sanft wenn nötig.
```

When `improvement_detected` becomes True, next session prompt:
```
Hinweis: {active_focus.topic} zeigt deutliche Verbesserung.
Kein expliziter Fokus nötig — weiter so.
```

---

## Layer 3 — Tutor UI

### Nav Badge

```html
<!-- german_base.html — shows only when pending suggestions exist -->
{% if tutor_pending_count > 0 %}
<a href="/admin#tutor" class="tutor-badge">
    🎓 {{ tutor_pending_count }}
</a>
{% endif %}
```

Small, unobtrusive. Links directly to Admin Tutor section.

### Admin — Tutor Section

```
TUTOR-EMPFEHLUNGEN
──────────────────────────────────────────────────────
Dativ-Präpositionen
6 Fehler in 3 Sitzungen · Gespräche + Schreiben
Zuletzt: 22. Mai 2026

[ ✓ Fokus setzen ] [ ✗ Nein ] [ ~ Variante ] [ ⏸ Später ]

──────────────────────────────────────────────────────
AKTUELLER FOKUS
Dativ-Präpositionen (seit 20. Mai · 3 Sitzungen)
[ Fokus aufheben ]

──────────────────────────────────────────────────────
FEHLERTRENDS  (letzte 14 Tage)
Dativ-Präpositionen      ████████░░  8×  ↑ steigend
Nebensatz-Wortstellung   ████░░░░░░  4×  ↓ besser
Genus-Fehler             ██░░░░░░░░  2×  → neu
                    Zuletzt aktualisiert: 23. Mai, 14:32

──────────────────────────────────────────────────────
STÄRKEN
✓ Höflicher Register  ✓ Konjunktiv II  ✓ Natürliches Tempo

──────────────────────────────────────────────────────
EINSTELLUNGEN
Beobachtungsfenster:  [ 14 ] Tage  oder  [ 5 ] Sitzungen
Schwellenwert:        [ 3 ] Fehler in [ 2 ] Sitzungen
```

Note: "Zuletzt aktualisiert" timestamp on error trends — Grok's suggestion,
good for knowing how fresh the data is.

---

## Layer 4 — Profile Access Placeholder

```python
# german_domain.py — near top with other constants
ACTIVE_USER = os.environ.get("GERMAN_USER", "robert")
```

All new functions default to `ACTIVE_USER`. When Vera wants her own
profile: set `GERMAN_USER=vera` in her environment. All her data is
separate. No code changes needed.

**Future access levels (not built now):**
- Individual: robert, vera (each sees own data)
- Family admin: sees all profiles, manages round defaults
- System admin: personas, sources, config

---

## GitHub / Repo Placement

**New files to commit:**

| File | Location | Purpose |
|---|---|---|
| `LEARNING_LOOP_VISION.md` | `_working/` | Vision doc — explains the why |
| `persona_memory_tutor_spec.md` | `_working/` | This spec |
| `persona_memory.json` | `config/` | Auto-created on first use |
| `learner_profile.json` | `config/` | Auto-created on first use |
| `tutor_suggestions.json` | `config/` | Auto-created on first suggestion |

**Commit message:**
```
feat(memory): persona memory + learner profile + tutor system spec

Adds the learning loop architecture to _working/.
Three-layer system: persona memory (per relationship),
learner profile (cross-session patterns), tutor (suggestions + focus).
Safe write helper prerequisite. v1.1 feature — spec only, not yet built.

See _working/LEARNING_LOOP_VISION.md for intent.
```

**Branch:** commit to `feat/german-html-interface` or open a new
`feat/learning-loop` branch — recommend new branch given the scope.

---

## Build Order

### Prerequisite — Safe Write Helper
Wire `safe_read_json()` / `safe_write_json()` throughout.
Retrofit phrasebook reads/writes. Must ship before any memory writes.

### Phase 1 — Persona Memory
1. Domain functions: `get_persona_memory`, `update_persona_memory`,
   `close_round`, `extend_round`, `build_persona_prompt`
2. Wire into `analyse_session()` — HTML path
3. Wire into Telegram reviewer path
4. Round-ready indicator in Gespräche persona list
5. Round management in Admin (extend, close, new round)

### Phase 2 — Learner Profile + Tutor
1. Domain functions: `update_learner_profile`,
   `get_pending_tutor_suggestions`, `respond_to_suggestion`,
   `check_focus_improvement`
2. Wire `update_learner_profile` into `analyse_session()` +
   `correct_writing()`
3. Tutor badge in nav (fed by Flask context processor)
4. Tutor section in Admin

### Phase 3 — Archiv Integration
1. Archived rounds per persona visible in Archiv
2. Error trend history chart (Chart.js, Phase 3)
3. Round summaries readable

---

## Files to Touch

| File | Change |
|---|---|
| `german_domain.py` | Safe write helper + all Layer 1 + 2 functions |
| `html_server.py` | Routes: round manage, tutor respond, context processor for badge |
| `templates/german_base.html` | Tutor badge in nav |
| `templates/german_gesprache.html` | Round-ready indicator on persona list |
| `templates/german_admin.html` | Tutor section + persona round management |
| `templates/german_archiv.html` | Archived rounds per persona |
| `config/persona_memory.json` | Auto-created |
| `config/learner_profile.json` | Auto-created |
| `config/tutor_suggestions.json` | Auto-created |

## Files NOT to Touch
- `reviewer.py` — Telegram flow unchanged
- `parse_transcript.py` — unchanged
- All other templates

---

## Definition of Done — Prerequisite

- [ ] `safe_read_json()` + `safe_write_json()` implemented
- [ ] Phrasebook reads/writes use safe helpers
- [ ] Concurrent Telegram + HTML write test passes without corruption

## Definition of Done — Phase 1

- [ ] Persona memory created on first Gespräche session
- [ ] Memory updates after every `analyse_session()` (HTML + Telegram)
- [ ] `build_persona_prompt()` injects memory into session
- [ ] Round counter increments correctly
- [ ] `ready_to_archive` flag set at threshold
- [ ] Round-ready dot visible on persona list in Gespräche
- [ ] Close/extend/defer UI works in Gespräche + Admin
- [ ] `close_round()` archives with LLM summary (auto fallback if LLM fails)
- [ ] New round starts with blank active_memory
- [ ] Different users have separate memory per persona

## Definition of Done — Phase 2

- [ ] Error trends tracked across Gespräche + Schreiben + Wörter
- [ ] Tutor suggestion generated when threshold crossed
- [ ] Tutor badge in nav shows pending count
- [ ] Accept/dismiss/variant/defer all work
- [ ] Active focus injected into next session prompt
- [ ] Improvement detection clears active focus
- [ ] Positive notification shown on improvement
- [ ] "Zuletzt aktualisiert" timestamp on error trends in Admin
- [ ] Observation window settings configurable in Admin
