# Build Note: Group C Extraction — Async-Free Resolvers with Callback Pattern
**Date:** 2026-05-19
**Branch:** feat/german-domain-extraction
**Spec:** docs/GERMAN_HTML_BUILD_PLAN_v1.0.md — Step 1, Group C

---

## What Group C is

Async functions that make mid-execution `reply_text` Telegram calls during processing.
The extraction converts them to plain (non-async) functions and replaces the Telegram
call with an optional `progress_cb` parameter — keeping `german_domain.py` free of async
and Telegram coupling.

---

## Scope boundary

Exploration found ~25 async handlers in telegram_bot.py that use `reply_text` mid-execution
(drill loop, phrase library, session handlers). Group C extracts only three, per spec:
- `_resolve_verb`
- `_resolve_phrases`
- `_resolve_drill_verb`

The remaining handlers are not domain logic — they are Telegram-specific response flows.
Future refactor if needed.

---

## Callback signature

```python
progress_cb: callable | None = None
```

The domain functions are plain `def` (not async). The callback is called synchronously:
```python
if progress_cb:
    progress_cb("message text")
```

In telegram_bot.py, the adapter wraps it as an async function:
```python
async def _cb(msg): await update.message.reply_text(msg)
entry = _resolve_verb(verb_lower, progress_cb=_cb)  # NOT awaited
```

This design keeps `german_domain.py` completely free of async. The `_cb` definition
lives in the Telegram handler — one per handler that calls a Group C function.

---

## Functions extracted (3 total)

### `_resolve_verb(verb_lower: str, progress_cb=None) -> dict | None`
- Looks up verb in drill pool; if missing, calls `_fetch_conjugations` and caches result
- progress_cb called: "Looking up '{verb}'…" before LLM call; error message on failure
- Internal call from `_resolve_drill_verb` threads the callback through

### `_resolve_phrases(entry: dict, progress_cb=None) -> list`
- Returns cached phrases from entry; if missing, calls `_fetch_phrases` and caches in drill_pool
- progress_cb called: "Generating phrases for {verb}…" before LLM call; error on empty result

### `_resolve_drill_verb(target_lower: str, progress_cb=None) -> dict | None`
- Extracts verb from drill trigger text (direct match, scene keyword, or LLM lookup)
- progress_cb called when a scene keyword resolves to a specific verb
- Falls back to `_resolve_verb(word, progress_cb=progress_cb)` — callback threaded through

---

## Call sites updated in telegram_bot.py (4 total)

| Handler | Old | New |
|---|---|---|
| `_handle_conjugate` | `await _resolve_verb(update, verb.lower())` | `_resolve_verb(verb.lower(), progress_cb=_cb)` |
| `_handle_drill_l1_start` | `await _resolve_drill_verb(update, target_lower)` | `_resolve_drill_verb(target_lower, progress_cb=_cb)` |
| `_handle_drill_l2_start` | `await _resolve_drill_verb(update, target_lower)` | `_resolve_drill_verb(target_lower, progress_cb=_cb)` |
| `_handle_drill_l2_start` | `await _resolve_phrases(update, entry)` | `_resolve_phrases(entry, progress_cb=_cb)` |

Each handler defines `async def _cb(msg): await update.message.reply_text(msg)` once and
reuses it for all Group C calls within that handler.

---

## Also fixed: shadowing local redefinitions

During Group B, `_DRILL_STATE_FILE` and `_DRILL_LIST_STATE_FILE` were added to the import
block but the old local definitions at telegram_bot.py lines 941–942 were not removed.
They shadowed the imports with identical values (same path). Removed in this commit —
now the imported values from german_domain are authoritative.

---

## Tests added: D28–D32

| # | Function | What it tests | Approach |
|---|---|---|---|
| D28 | `_resolve_verb` | returns entry from pool; progress_cb=None doesn't raise | patches `_load_drill_pool` |
| D29 | `_resolve_verb` | progress_cb called with "Looking up" when verb not in pool | mock callback + patches |
| D30 | `_resolve_phrases` | returns cached phrases; progress_cb=None doesn't raise | pure call |
| D31 | `_resolve_drill_verb` | returns entry for known verb; progress_cb=None | patches `_load_drill_pool` |
| D32 | `_resolve_drill_verb` | callback fired when scene keyword resolves to verb | patches `_load_drill_pool` |

No asyncio.run() needed — functions are plain def.

---

## Gate results

| Check | Result |
|---|---|
| `venv/bin/python3 _NewDomains/language-german/run_tests.py` | 49/49 pass |
| `venv/bin/python3 _NewDomains/language-german/test_german_domain.py` | 32/32 pass |
| `venv/bin/python3 -c "import telegram_bot"` | clean import |
| `telegram_bot._resolve_verb is german_domain._resolve_verb` | True |
| `telegram_bot._resolve_phrases is german_domain._resolve_phrases` | True |
| `telegram_bot._resolve_drill_verb is german_domain._resolve_drill_verb` | True |
