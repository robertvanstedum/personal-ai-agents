# SPEC v1.3 — Implementation Addendum
**Date:** 2026-04-24
**Author:** Claude (from Robert's decisions post plan-mode review)
**Status:** Approved — commit alongside SPEC_icloud_bridge_v1.3.md
**Parent spec:** `_NewDomains/language-german/SPEC_icloud_bridge_v1.3.md`

---

## Instructions for Claude Code

Commit this file and `SPEC_icloud_bridge_v1.3.md` to
`_NewDomains/language-german/` with commit message:

```
docs: add iCloud bridge spec v1.3 and implementation addendum
```

**Do not start any implementation yet.**
Implementation begins this weekend after OpenClaw files GitHub issues.

---

## Blocker Resolutions

### Blocker 1 — Dropbox path
Dropbox setup is Robert's pre-build dependency. Do not start the watcher
build until Robert confirms the local Dropbox path and updates
`sync_config.json`.

> ⚠️ Pre-build check: Robert confirms `~/Dropbox` (or alternate path)
> exists and syncs before build starts. Do not hardcode path anywhere —
> read from `sync_config.json` only.

### Blocker 2 — drill_mode signaling
Use header fields in the transcript. `parse_transcript.py` already parses
header fields — add two optional lines to the parser. Transcript header
format becomes:

```
---SESSION---
Date: 2026-04-24
Persona: Maria
Scenario: cafe_order
Duration: 12
Drill: true
Drill-Session: 3 of 10
---END---
```

`Drill` and `Drill-Session` are optional. If absent, parser treats session
as normal. `reviewer.py` reads `drill_mode` and `drill_session` from the
parsed session JSON.

### Blocker 3 — Telegram credentials in watcher
Import `get_token()` and `get_chat_id()` from `telegram_bot.py`.
The stdlib-only constraint applies to the polling mechanism only, not the
entire file. Consistent with existing codebase pattern.

---

## Medium Issue Resolutions

### Issue 4 — pipeline_base_dir resolution
Watcher anchors relative paths to `Path(__file__).parent.parent`
(the project root). Do not use absolute paths in `sync_config.json` —
portability to Mac Mini or VPS requires relative paths only.

> OpenClaw to confirm this anchor resolves correctly from
> `_NewDomains/language-german/watch_transcripts.py` before build starts.

### Issue 5 — File write race condition
Replace fixed 2-second sleep with file size stability check:

```python
def _wait_for_stable(path, interval=1.0, checks=3):
    prev = -1
    for _ in range(checks):
        size = path.stat().st_size
        if size == prev:
            return
        prev = size
        time.sleep(interval)
```

Proceed when size is stable across two consecutive checks.

### Issue 6 — warm_up_variants vs dynamic warm-up
Normal sessions keep current behavior (`reviewer.py` line 267 — dynamic
warm-up from top error type). Drill sessions cycle through
`warm_up_variants` from `personas.json`. No change to normal session
behavior.

---

## Low Issue Resolutions

### Issue 7 — parse_transcript.py call in watcher
Use `--input <path>` when calling `parse_transcript.py` from the watcher,
not `--stdin`. Simpler and already supported.

### Issue 9 — Error types: 6 → 9
Fix pre-existing debt in `reviewer.py` in the same build pass. Add
`case`, `preposition`, `idiomatic` to `ERROR_TYPES` and update the
system prompt. Minimal lift.

### Issue 10 — warm_up_variants for all 8 personas
Claude Code generates plausible Vienna-scenario warm-up variants for all
8 personas. Robert reviews before merge. Variants must match each
persona's style and difficulty level.

---

## Updated Build Sequence (replaces section 12 of SPEC v1.3)

```
0. ⚠️  PRE-BUILD: Robert confirms Dropbox path → updates sync_config.json
1. Create sync_config.json
2. Update parse_transcript.py — add Drill + Drill-Session header parsing
3. Build watch_transcripts.py
       - stdlib polling + telegram_bot.py import
       - file size stability check (not fixed sleep)
       - Path(__file__).parent.parent anchor for project root
       - creates folder structure on first run
4. Add warm_up_variants to all 8 personas in personas.json
5. Update reviewer.py
       - drill_mode flag behavior (skip lesson rotation on non-final sessions)
       - fix error types: 6 → 9 (add case, preposition, idiomatic)
6. OpenClaw: prompt file writing + drill command (OpenClaw's work, not Claude Code)
7. Update GERMAN_USER_GUIDE.md
       - add Dropbox workflow section
       - add rapid-drill instructions
       - Telegram workflow section unchanged
8. Test sequence:
       - 3 manual transcripts dropped in inbox → confirm pipeline fires
       - drill café Maria 3 → confirm 3 prompt files + drill summary
```

Steps 1–5 and 7 are Claude Code.
Step 6 is OpenClaw.
Step 0 is Robert (pre-build dependency).

---

## Notes from Grok Review (v1.1) — for Claude Code awareness

1. **Section 7 heading mismatch in SPEC v1.3:** The heading says "Prompt
   File Format" but opens with the transcript block. The actual prompt
   file format starts at the 📚 block. Context makes this clear but note
   it when reading section 7.

2. **parse_transcript.py flags:** Confirm `--input` and `--base-dir`
   flags are implemented before wiring the watcher. Quick check before
   starting step 3.
