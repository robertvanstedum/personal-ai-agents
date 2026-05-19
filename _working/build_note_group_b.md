# Build Note: Group B Extraction — Domain I/O and LLM Functions
**Date:** 2026-05-19
**Branch:** feat/german-domain-extraction
**Commit:** 559188d
**Spec:** docs/GERMAN_HTML_BUILD_PLAN_v1.0.md — Step 1, Group B

---

## What Group B is

Functions that have file I/O, subprocess calls, or external LLM API calls — but no async, no
Telegram coupling. After Group B, `german_domain.py` contains all non-async domain logic.
Group C (async + callback pattern) follows.

---

## Path constants moved

These are used exclusively by Group B functions and moved with them:

| Constant | Value |
|---|---|
| `_PHRASEBOOK_FILE` | `GERMAN_DIR / "config" / "phrasebook.json"` |
| `_DRILL_STATE_FILE` | `_BASE_DIR / "_active_drill_state.json"` |
| `_DRILL_LIST_STATE_FILE` | `_BASE_DIR / "_drill_list_state.json"` |

**Not moved:** `_active_drills`, `_last_drills`, `_drill_list_state` runtime dicts — these are
Telegram session state, not domain logic. They stay in `telegram_bot.py`.

---

## Functions extracted (14 total)

| Function | Dependencies | Notes |
|---|---|---|
| `_load_keyword_map_bot` | `json`, `GERMAN_DIR` | reads keyword_map.json |
| `_last_session_persona` | `json`, `GERMAN_DIR` | reads latest session JSON, returns persona name |
| `_run` | `subprocess` | general subprocess wrapper, returns (stdout, stderr, returncode) |
| `_german_agent_mode` | `json`, `GERMAN_DIR` | reads sync_config.json, returns "direct" or mode string |
| `_phrase_next_id` | none | pure — missed in Group A, moved here; global sequence, never resets per day |
| `_load_drill_pool` | `json`, `GERMAN_DIR` | reads drill_pool.json; returns {} on missing |
| `_save_drill_pool` | `json`, `GERMAN_DIR` | writes drill_pool.json |
| `_load_phrasebook` | `json`, `_PHRASEBOOK_FILE` | reads phrasebook.json; returns {"phrases": []} on missing |
| `_save_phrasebook` | `json`, `_PHRASEBOOK_FILE` | writes phrasebook.json |
| `_call_llm` | `keyring`, `openai`, `anthropic` | tries xai → anthropic → ollama in order; returns None on all fail |
| `_fetch_conjugations` | `_call_llm`, `json` | LLM → JSON parse; returns None on bad JSON or None from LLM |
| `_fetch_phrases` | `_call_llm`, `json` | LLM → JSON array; returns [] on bad JSON or None from LLM |
| `_write_drill_anki` | `csv`, `GERMAN_DIR` | appends friction items to vienna_deck.csv; skips clean items and duplicates |
| `_drill_completion_message` | `_write_drill_anki` | builds completion string with score/counts; calls _write_drill_anki as side effect |

**Not in Group B** (these mutate Telegram session state dicts):
- `_save_drill_list_state` / `_load_drill_list_state` — mutate `_drill_list_state`
- `_save_drill_state` / `_load_drill_state` — mutate `_active_drills`

---

## Key design decisions

**keyring inside the function body:** `keyring` is imported as `import keyring as _keyring` inside
`_call_llm`, not at module level. Reason: `german_domain.py` must be importable in environments
where keyring is not installed (e.g. system Python during testing). Moving the import inside the
function means the module loads cleanly; keyring is only required when `_call_llm` is actually
called.

**_phrase_next_id classification:** This is a pure function (no I/O) that was missed in Group A.
It was pulled in Group B rather than opening a new micro-commit. The global-sequence design (IDs
never reset per day) was already in production from a previous bug fix (2ae4abd).

**Runtime state dicts stay in telegram_bot.py:** `_active_drills`, `_last_drills`, `_drill_list_state`
are Telegram polling-session state — they track live user interactions and are not domain logic.
Only the FILE PATH constants (`_DRILL_STATE_FILE`, `_DRILL_LIST_STATE_FILE`) move, since the
save/load functions that use them are moving.

---

## Tests added: D16–D27

| # | Function | What it tests | Approach |
|---|---|---|---|
| D16 | `_phrase_next_id` | global max sequence, first ID, date prefix | pure — no mocking |
| D17 | `_run` | returns (stdout, stderr, returncode) tuple | calls `echo hello` |
| D18 | `_load_drill_pool` | returns `{}` when file missing; loads dict when present | tempdir |
| D19 | `_save_drill_pool` + `_load_drill_pool` | round-trip preserves data | tempdir |
| D20 | `_load_phrasebook` | returns `{"phrases": []}` when missing; loads correctly | tempdir |
| D21 | `_save_phrasebook` + `_load_phrasebook` | round-trip preserves data | tempdir |
| D22 | `_load_keyword_map_bot` | returns `{}` when missing; loads dict when present | tempdir |
| D23 | `_call_llm` empty provider list | returns `None` when no providers configured | patches `_LLM_PROVIDERS` to `[]` |
| D24 | `_fetch_conjugations` | parses valid JSON; returns None on bad JSON or LLM None | patches `_call_llm` |
| D25 | `_fetch_phrases` | parses valid JSON array; returns `[]` on bad JSON or LLM None | patches `_call_llm` |
| D26 | `_write_drill_anki` | friction written to CSV; clean items skipped | tempdir + patches `GERMAN_DIR` |
| D27 | `_drill_completion_message` | returns string with score and counts | patches `_write_drill_anki` |

**Mocking pattern:** stdlib `unittest.mock` only — no new test dependencies.
- File I/O: `tempfile.TemporaryDirectory()` + monkeypatch `german_domain.GERMAN_DIR`
- LLM: `mock.patch("german_domain._call_llm", return_value=...)`
- D23 note: originally patched `keyring.get_password`, but Ollama (running locally) bypasses keyring.
  Switched to patching `_LLM_PROVIDERS` to `[]` — tests the exhaustion path directly.

**Test runner:** `venv/bin/python3` required (not bare `python3`) — keyring not in system Python.

---

## Gate results

| Check | Result |
|---|---|
| `venv/bin/python3 _NewDomains/language-german/run_tests.py` | 49/49 pass |
| `venv/bin/python3 _NewDomains/language-german/test_german_domain.py` | 27/27 pass |
| `venv/bin/python3 -c "import telegram_bot"` | clean import |
| `telegram_bot._call_llm is german_domain._call_llm` | True |
