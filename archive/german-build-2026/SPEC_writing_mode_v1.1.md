# Writing Mode — German Practice Domain
**Version:** 1.1
**Date:** 2026-04-24
**Author:** Robert van Stedum + Claude + Grok (polish review)
**Status:** Polish pass — apply after v1.0 commit, re-run acceptance tests
**Parent spec:** `_NewDomains/language-german/SPEC.md`

---

## Purpose

Add a writing mode to the German practice domain. Same personas, same
scenarios, same pipeline — but practiced via typed text instead of voice.
Mitigates Grok unavailability. Enables voice vs writing error comparison
over time.

---

## Design

Minimal change. One new header field drives everything.

### 1. Transcript header — add `Mode` field

```
---SESSION---
Date: 2026-04-24
Persona: Maria
Scenario: cafe_order
Duration: 12
Mode: writing
---END---
```

`Mode` is optional. Valid values: `voice` (default if absent), `writing`.
Place after `Duration:` for visual consistency. Parser order does not matter.

### 2. `parse_transcript.py`

Add `Mode` to header field parsing. Store as `mode` in session JSON:

```json
{
  "session_id": "2026-04-24_002",
  "mode": "writing",
  ...
}
```

One extra `elif` in the header parser. No other changes.

### 3. `progress.json` — split session counts

Add two new fields alongside `total_sessions`:

```json
{
  "total_sessions": 12,
  "voice_sessions": 9,
  "writing_sessions": 3,
  ...
}
```

`reviewer.py` increments the correct counter based on `mode`.
`total_sessions` continues to increment regardless of mode.

### 4. `status.py` output — show mode split when both exist

```
Sessions: 12 (voice: 9 · writing: 3) | Minutes: 118 | Anki cards: 44
```

Falls back to current single-line format if only one mode has been used.

### 5. Persona prompts — no change needed

The in-character correction behavior ("gently rephrase things Robert says
incorrectly as part of your natural reply") already works in text mode.
This is the same correction style used in Claude.ai text conversations.

### 6. Writing session prompt file — one line addition

Add to the top of the prompt file when `mode: writing`:

```
⌨️  WRITING SESSION — Turn off auto-correct.
Type slowly and in full sentences. I will gently correct you naturally.
```

OpenClaw adds these two lines when generating a writing-mode prompt file.
No change to the persona prompt itself.

### 7. OpenClaw command for writing sessions

Add `writing [scenario] [persona]` as a named OpenClaw command,
consistent with the existing `drill [scenario] [persona] [N]` syntax.

Examples:
```
writing café Maria
writing hotel Herr Fischer
next writing session
```

When used, OpenClaw:
1. Reads today's lesson plan (or uses active persona from `domain.json`)
2. Adds `⌨️ WRITING SESSION` header to the prompt file
3. Adds `Mode: writing` to the transcript template inside the prompt file
4. Writes timestamped prompt file to `prompts/` as normal

---

## How to run a writing session today (before pipeline support)

No build needed for immediate use. Run a writing session directly in
Claude.ai or Grok text mode:

1. Open Claude.ai
2. Paste the persona prompt from
   `config/prompts/[persona].txt`
3. Say: "Start today's writing session"
4. Type back and forth — no voice required
5. End with the standard transcript request
6. Add `Mode: writing` manually to the header before submitting
   to the pipeline

Pipeline will handle it correctly once `parse_transcript.py` is updated.

---

## Build instructions for Claude Code

**Scope:** Small — 3 files touched, no new files created.

1. `parse_transcript.py` — parse `Mode:` header field, default to `voice`
2. `reviewer.py` — increment `voice_sessions` or `writing_sessions` in
   `_update_progress()` based on session `mode`
3. `progress.json` — add `voice_sessions: 0` and `writing_sessions: 0`
   to initial state template
4. `status.py` — show mode split in sessions line when both > 0

**Commit message:**
```
feat(german): writing mode polish — header order, OpenClaw command, prompt wording, future section
```

**Do not change:**
- Persona prompts
- Anki CSV format
- Lesson plan format
- Error taxonomy
- Telegram workflow

---

## Acceptance Tests (run before every commit — standard for all German domain specs)

Implementation is complete when all tests pass. Report results to Robert
before committing. Do not commit on partial pass.

### Test 1 — Writing mode parsed correctly
```
Input: sample transcript with Mode: writing in header
Run:   python parse_transcript.py --input test_writing.txt --base-dir language/german/
Pass:  session JSON contains "mode": "writing"
```

### Test 2 — Voice mode default (backward compatibility)
```
Input: sample transcript with no Mode field (existing format)
Run:   python parse_transcript.py --input test_voice.txt --base-dir language/german/
Pass:  session JSON contains "mode": "voice"
       existing sessions unaffected — no change to their JSON
```

### Test 3 — progress.json increments correctly
```
Input: writing mode session JSON from Test 1
Run:   python reviewer.py --latest --base-dir language/german/
Pass:  progress.json writing_sessions incremented by 1
       total_sessions incremented by 1
       voice_sessions unchanged
```

### Test 4 — status.py shows mode split
```
Input: progress.json with voice_sessions > 0 AND writing_sessions > 0
Run:   python status.py --base-dir language/german/
Pass:  output line contains "(voice: N · writing: M)"
```

### Test 5 — status.py single mode (no regression)
```
Input: progress.json with writing_sessions = 0 (voice only)
Run:   python status.py --base-dir language/german/
Pass:  output line shows original format with no mode split
       no crash, no empty parentheses
```

### Reporting format
Claude Code reports results as:
```
Test 1 — Writing mode parsed:     PASS
Test 2 — Voice default:           PASS
Test 3 — Progress increments:     PASS
Test 4 — Status split:            PASS
Test 5 — Status no regression:    PASS

Ready to commit.
```

Any FAIL: fix before reporting. Do not report partial results as ready.

---

## Future (post-Vienna, not now)

- `errors_by_mode` in `progress.json` — store error type counts split by
  voice and writing to reveal whether you make more mistakes speaking vs
  typing (e.g. gender errors higher in voice, case errors higher in writing)
- Compare error patterns: voice vs writing per error type in `status.py`
- Separate Anki tags: `voice-session` vs `writing-session`
- `!german progress --mode voice` filter command
- Writing-specific warm-up variants (slower pace, longer sentences)
