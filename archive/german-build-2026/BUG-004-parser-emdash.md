# BUG-004: Parser fails on em-dashes (—) from iPhone

**Status:** Open  
**Owner:** Claude Code (after OpenClaw files bug)  
**Date:** 2026-04-25

## Problem
When Robert pastes transcripts from Grok Voice on iPhone, the `---SESSION---` and `---END---` markers are often converted to em-dashes (`—SESSION—` and `—END—`).

The current parser only handles the em-dash version in `START_TRIGGER_ALT` / `END_TRIGGER_ALT` for the initial trigger detection, but the body splitting and line parsing still fail in some cases, resulting in empty `raw_transcript` list and the reviewer receiving no actual dialogue.

This caused today's session (`raw_2026-04-25_08-56.txt`) to be parsed with zero turns.

## Reproduction
- Paste transcript containing `—SESSION—` and `—END—` (common on iOS).
- Run `parse_transcript.py`.
- Result: `raw_transcript: []`, reviewer gets no content.

## Proposed Fix
Update `_parse_turns()` and body extraction to be more robust:
- Normalize all dashes/em-dashes to standard `-` at the beginning.
- Make speaker detection more flexible (handle missing colon, extra spaces, etc.).
- Improve fallback when header is malformed.

This is a high-priority reliability bug for the German daily practice pipeline.

**Next step:** Claude Code to implement robust parsing + add test case with real iPhone transcript.