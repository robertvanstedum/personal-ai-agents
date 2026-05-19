# Build Note: Group D — Wire Adapters (Adapter Boundary Verification)
**Date:** 2026-05-19
**Branch:** feat/german-domain-extraction
**Spec:** docs/GERMAN_HTML_BUILD_PLAN_v1.0.md — Step 1, Group D

---

## What Group D is

Verification that the adapter boundary is clean after Groups A–C. No new extraction —
Group D confirms that telegram_bot.py now contains only:
- Message routing (handler dispatch)
- Whisper voice transcription
- Telegram formatting helpers
- Callback delivery (reply_text, send_message)
- Session state dicts (Telegram polling state, not domain logic)

And that `german_domain.py` contains all domain logic.

---

## Import block — final state

```python
from german_domain import (
    # Group A — constants and pure functions
    ROBERT_CHAT_ID, GERMAN_BASE, GERMAN_DIR, VENV_PYTHON,
    _WRITING_RE, _SESSION_RE, _CONJUGATE_RE,
    _DRILL_RE, _DRILL_L2_RE, _DRILL_CTL_RE, _DRILL_AGAIN_RE,
    _DRILL_LIST_RE, _DRILL_MORE_RE, _SKIP_LESSON_RE, _AGAIN_RE,
    _PHRASE_CAPTURE_RE, _PHRASE_PRACTICE_VOICE_RE,
    _PHRASE_LIST_VOICE_RE, _PHRASE_LIST_MORE_VOICE_RE,
    _SPOKEN_NUMBERS, _LLM_PROVIDERS,
    _DE_CONTRACTIONS, _DE_CONTRACTION_RE,
    _DRILL_PERSONS, _PERSONS_DISPLAY, _PERSONS_POOL,
    _resolve_keyword_intent, _parse_spoken_id,
    _all_verb_entries, _lookup_verb,
    _expand_contractions, _normalize_answer, _spell_feedback,
    _item_tag, _drill_prompt, _l2_prompt,
    _start_drill_state, _record_l2_item, _record_l1_person, _finalize_l1_items,
    # Group B — I/O and LLM functions
    _PHRASEBOOK_FILE, _DRILL_STATE_FILE, _DRILL_LIST_STATE_FILE,
    _load_keyword_map_bot, _last_session_persona, _run, _german_agent_mode,
    _phrase_next_id, _load_drill_pool, _save_drill_pool,
    _load_phrasebook, _save_phrasebook,
    _call_llm, _fetch_conjugations, _fetch_phrases,
    _write_drill_anki, _drill_completion_message,
    # Group C — async-free resolvers with callback pattern
    _resolve_verb, _resolve_phrases, _resolve_drill_verb,
)
```

---

## Adapter boundary analysis

### Stays in telegram_bot.py (correct)

| Item | Reason |
|---|---|
| `_arun` | Async wrapper for `_run` — uses `loop.run_in_executor` to keep event loop free. Telegram adapter layer. |
| `_active_drills`, `_last_drills`, `_drill_list_state` | Polling session state dicts — track live user interactions, not domain logic |
| `_save_drill_state`, `_load_drill_state` | Mutate `_active_drills` (Telegram session dict) |
| `_save_drill_list_state`, `_load_drill_list_state` | Mutate `_drill_list_state` (Telegram session dict) |
| All `async def _handle_*` functions | Telegram message handlers — routing + reply_text delivery |
| `transcribe_voice`, `classify_voice` | Whisper transcription, Telegram-specific audio pipeline |
| `send_message`, `send_briefing`, `send_article` | Telegram formatting + delivery |
| `button_callback` | Telegram inline keyboard callback handling |

### What's NOT in scope for future extraction

~25 async handlers (drill loop, phrase library, session flow) remain in telegram_bot.py.
They use `reply_text` mid-execution but are orchestration logic tied to Telegram state
(active_drills, phrase_practice, etc.). Extracting them would require either:
- Moving all the session state dicts too, or
- A much larger async context + callback design

Deferred. Not a v1.0 requirement.

---

## Gate results

| Check | Result |
|---|---|
| `venv/bin/python3 _NewDomains/language-german/run_tests.py` | 49/49 pass |
| `venv/bin/python3 _NewDomains/language-german/test_german_domain.py` | 32/32 pass |
| `venv/bin/python3 -c "import telegram_bot"` | clean import |
| All Group C identity checks | True |
| No `await _resolve_verb/phrases/drill_verb` remaining | Confirmed (grep clean) |
| No shadowing local path constant redefinitions | Confirmed — removed in Group C commit |
