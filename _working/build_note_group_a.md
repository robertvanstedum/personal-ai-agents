# Build Note: Group A Extraction — Constants and Pure Functions
**Date:** 2026-05-19
**Branch:** feat/german-domain-extraction
**Commit:** cf592f3
**Spec:** docs/GERMAN_HTML_BUILD_PLAN_v1.0.md — Step 1, Group A

---

## What Group A is

Constants, data structures, and pure functions with no I/O, no async, no Telegram coupling.
The safe first extraction: anything that fails will fail loudly at import time or in a pure
unit test — no side effects, no live system impact.

---

## Step 0 gate (baseline before any extraction)

- 49/49 existing tests passed on `main` before the branch was cut
- Branch created: `feat/german-domain-extraction`
- Baseline commit: `test: confirm 49-test baseline before extraction`

---

## What moved

### Path constants

| Constant | Value |
|---|---|
| `_BASE_DIR` | `Path(__file__).parent` (repo root) |
| `GERMAN_BASE` | `_BASE_DIR / "_NewDomains" / "language-german"` |
| `GERMAN_DIR` | `GERMAN_BASE / "language" / "german"` |
| `VENV_PYTHON` | `_BASE_DIR / "venv" / "bin" / "python3"` |
| `ROBERT_CHAT_ID` | `8379221702` |

### Regex patterns (all compiled at module level)

`_WRITING_RE`, `_SESSION_RE`, `_CONJUGATE_RE`, `_DRILL_RE`, `_DRILL_L2_RE`,
`_DRILL_CTL_RE`, `_DRILL_AGAIN_RE`, `_DRILL_LIST_RE`, `_DRILL_MORE_RE`,
`_SKIP_LESSON_RE`, `_AGAIN_RE`, `_PHRASE_CAPTURE_RE`, `_PHRASE_PRACTICE_VOICE_RE`,
`_PHRASE_LIST_VOICE_RE`, `_PHRASE_LIST_MORE_VOICE_RE`

### Data structures

| Name | Type | Notes |
|---|---|---|
| `_SPOKEN_NUMBERS` | dict | maps spoken word → zero-padded string ("one" → "001") |
| `_LLM_PROVIDERS` | list of dicts | ordered provider config: xai → anthropic → ollama |
| `_DE_CONTRACTIONS` | dict | German contraction expansions (im → in dem, ins → in das, etc.) |
| `_DE_CONTRACTION_RE` | compiled regex | built from `_DE_CONTRACTIONS` keys |
| `_DRILL_PERSONS` | list | six grammatical persons for conjugation drill |
| `_PERSONS_DISPLAY` | list | display forms matching `_DRILL_PERSONS` |
| `_PERSONS_POOL` | list | pool forms for conjugation lookup |

### Pure functions (14 total)

| Function | What it does |
|---|---|
| `_resolve_keyword_intent` | maps user text to (persona, scenario) via keyword_map dict |
| `_parse_spoken_id` | converts spoken number word or digit string → zero-padded ID |
| `_all_verb_entries` | merges core + on_demand verb pools; core takes precedence on duplicates |
| `_lookup_verb` | finds verb entry in pool by name; returns None if not found |
| `_expand_contractions` | replaces German contractions in text using `_DE_CONTRACTIONS` |
| `_normalize_answer` | lowercase, strip punctuation, collapse whitespace, expand contractions |
| `_spell_feedback` | heuristic spell-check hint for a single German word |
| `_item_tag` | assigns drill result tag based on wrong count and hint usage |
| `_drill_prompt` | builds Level 1 (conjugation fill-in) prompt string |
| `_l2_prompt` | builds Level 2 (translation) prompt string |
| `_start_drill_state` | initializes drill state dict for a verb entry |
| `_record_l2_item` | appends a Level 2 result item to drill state |
| `_record_l1_person` | tracks worst Level 1 tag in drill state |
| `_finalize_l1_items` | converts worst L1 tag into one Anki item if friction occurred |

---

## Key design decisions

**New file vs. appending to existing:** `german_domain.py` created as a new file at repo root.
This is where both `telegram_bot.py` and the planned `html_server.py` can import from without
directory coupling.

**Module docstring signals intent:** The docstring at the top of `german_domain.py` explicitly
states that both Telegram bots route to the same entrypoint, and that Group A is the boundary
definition. This is the OpenClaw note #3 from the build plan.

**`_LLM_PROVIDERS` as data, not code:** Provider configuration (model names, types) moved as a
data structure rather than a class or factory. Keeps the config readable and makes it easy to
add providers or reorder fallback without touching function logic.

**Regex compiled at module level:** All patterns compiled once at import time. No construction
cost at call time. Consistent with the existing `telegram_bot.py` pattern.

**`telegram_bot.py` import block is explicit:** All imported names are listed individually (not
`import *`). This makes the dependency surface visible and prevents accidental name shadowing.

---

## Tests added: D01–D15

| # | Function | What it tests |
|---|---|---|
| D01 | `_normalize_answer` | lowercase, strip punctuation, collapse whitespace |
| D02 | `_expand_contractions` | im → in dem, ins → in das, no-op on unknown |
| D03 | `_parse_spoken_id` | spoken words and bare digits → zero-padded strings |
| D04 | `_lookup_verb` | found in pool, not found returns None |
| D05 | `_all_verb_entries` | merges core + on_demand; core precedence on duplicate |
| D06 | `_item_tag` | wrong=0→clean, wrong=1→reinforced, hint→needs-practice |
| D07 | `_drill_prompt` | contains verb, english, person, position |
| D08 | `_l2_prompt` | contains position and "How do you say" |
| D09 | `_start_drill_state` | all required keys present, initial values correct |
| D10 | `_record_l1_person` | clean keeps drill-clean; wrong escalates l1_worst_tag |
| D11 | `_record_l2_item` | appends item, clears hint_used_current |
| D12 | `_finalize_l1_items` | drill-clean → no item; reinforced → one Anki item |
| D13 | `_resolve_keyword_intent` | keyword match, single-word no-fire, no match → None |
| D14 | `_spell_feedback` | returns hint for clear misspelling |
| D15 | Regex smoke | _SESSION_RE, _DRILL_RE, _DRILL_LIST_RE, _AGAIN_RE match key patterns |

All 15 tests use sample data only — no file I/O, no mocking needed.

---

## Gate results

| Check | Result |
|---|---|
| `venv/bin/python3 _NewDomains/language-german/run_tests.py` | 49/49 pass |
| `venv/bin/python3 _NewDomains/language-german/test_german_domain.py` | 15/15 pass |
| `venv/bin/python3 -c "import telegram_bot"` | clean import |
