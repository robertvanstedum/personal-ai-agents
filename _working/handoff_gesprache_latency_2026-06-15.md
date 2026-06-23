# Handoff — Gespräche Latency Investigation
*2026-06-15 — end of session*
*Ref: spec_gesprache_testrun_followup_2026-06-15.md Part B*

---

## Where we landed tonight

All consolidated Gespräche improvements are committed (commit `0a2f890`). The latency
investigation ran but the problem is not fully solved.

### Timing data collected (one live session)

| Stage | Value |
|-------|-------|
| Grok chat turn (grok-4.3, typical) | 5–10s |
| Grok chat turn (grok-4.3, first turn) | ~22s observed — likely cold start / xAI load |
| OpenAI gpt-4o-mini (benchmarked, not tested live) | ~1.2s |
| TTS (tts-1, typical) | 2.5–3.5s |
| TTS (one hung request) | 602s — now has 12s timeout |

### What changed in tonight's commit

- `_chat_grok`: `grok-4.3`, `max_tokens=200` (was 500 — lever #3 applied)
- `_chat_openai`: `gpt-4o-mini`, `max_tokens=200` (was gpt-4o/500 — lever #1 applied for OpenAI)
- TTS speak endpoint: `timeout=12.0` on OpenAI client (fixes 10-min hang)
- "Denkt nach…" indicator: already shipped in this commit (lever #2)

### What is NOT done (for morning)

Grok is still slow (~5s per turn, sometimes 22s on cold start). The benchmark shows
`gpt-4o-mini` at ~1.2s per turn — 4–5x faster than Grok for persona conversation turns.

**The core question for morning:** should the Gespräche default model be changed to OpenAI?

Options:
1. **Change the default dropdown from "Grok" to "OpenAI"** — simplest. gpt-4o-mini is 4x
   faster for live turns. Keep Grok as an option but not the default.
2. **Hardwire chat turns to gpt-4o-mini regardless of dropdown** — transparent speedup, but
   users who select "Grok" get OpenAI silently. Spec says "error-naming behavior preserved
   (no silent fallback)" — this violates that.
3. **Add a fourth provider "Schnell" (gpt-4o-mini behind the scenes)** — clean naming,
   signals speed, honest about what it is.
4. **Accept Grok latency, focus on perceived-latency only** — "denkt nach" is already
   shipped. Add a waveform pulse animation so the panel looks "alive" during the 5s wait.

**Recommended starting point for morning:** run one live session with the dropdown set to
"OpenAI" and verify the 1.2s-class response time feels right for persona conversation.
If yes, decide on option 1 or 3 above. File the decision back to OpenClaw for spec update.

### File to change if option 1 (change default)

`domains/german/templates/german_gesprache.html` line ~69:
```html
<option value="grok" selected>Grok</option>
<option value="openai">OpenAI</option>
```
Change to:
```html
<option value="grok">Grok</option>
<option value="openai" selected>OpenAI</option>
```

### File to change if option 3 (add Schnell)

Same file, plus add `_chat_openai_fast()` in `providers/review_router.py` that
uses `gpt-4o-mini` — or reuse `_chat_openai` (it already uses gpt-4o-mini now).
Add `elif model == "schnell":` branch in `run_chat_turn()`.

---

## Other open issue from tonight

One TTS call hung for 602 seconds (no model routing issue — pure OpenAI TTS API hang).
The 12s timeout fix is in the commit. If TTS fails with timeout error, the user sees
nothing (silent failure). Next improvement: surface TTS failure visibly in the session
panel ("Ton nicht verfügbar — bitte versuche es erneut").

---

*End of session. All changes committed. Server restarted and running.*
