# Gespräche — Forward Spec & Mobile Showcase Vision
*Created: 2026-06-17 — Claude.ai*
*Status: Roadmap item — top of German domain*
*Location: docs/GESPRACHE_FORWARD_SPEC.md*
*Design session: pending for voice commands and automatic handoff tiers*

---

## What's built as of 2026-06-17

All near-term specs from the June 15/16 session are confirmed shipped:

| Feature | Status | Notes |
|---------|--------|-------|
| Tab rename (Mit KI-Persona / Mit echtem Mensch) | ✓ done | |
| VAD silence window 2.0s | ✓ done | |
| Audio cue on session start | ✓ done | |
| Two-mode layout (.session-active) | ✓ done | |
| Feedback box parchment + amber border | ✓ done | |
| Collapsible analysis sections | ✓ done | |
| Dead-end fix (Neue Sitzung button) | ✓ done | |
| Transcript paste panel (clipboard button) | ✓ done | |
| Latency instrumentation | ✓ done | |
| Mobile fixes (persona tap scrolls, buttons visible) | ✓ done | |
| All 5 German pages render on mobile | ✓ done | |

---

## The mobile showcase vision

Gespräche on mobile is the primary portfolio demo surface. The phone is
the natural device for language practice — it's where you'd actually be
in a Viennese café. The full loop on mobile should be:

```
Select persona → tap Sitzung starten →
speak naturally (VAD handles turns) →
Maria responds in character via TTS →
tap Beenden → transcript auto-populates →
tap Analysieren → feedback renders →
tap Neue Sitzung → repeat
```

This loop currently works. The showcase goal is making it feel native —
fast, responsive, no friction, no dead ends.

---

## Next tier — ready to spec

These are agreed, need specs before Claude Code builds:

### T2-A — iOS Share Sheet
After a session, "Teilen" button invokes native iOS share sheet with
transcript as text. `navigator.share({ text: transcript })`.
Gate with `if (navigator.share)`.
**Note:** Different from "Telegram öffnen" — that copies the prompt for
the bot. This shares the completed transcript outward to any app.

### T2-B — Schreiben Save Confirmation Toast
1.5s "Gespeichert ✓" toast after POST /api/write-save succeeds.
Robert noted missing feedback. Small, self-contained.

### T2-C — Post-Session Summary Card
Compact card above Nach der Sitzung: turns taken, persona, duration,
1-line summary. No LLM call — from session state only.

### T1-B — Safe Area + Font Size Audit
`padding-bottom: env(safe-area-inset-bottom)` on German bottom tab bar.
All inputs ≥ 16px to prevent Safari auto-zoom. One pass, all German pages.

---

## Next tier — needs design session first

These need a design conversation before speccing:

### Automatic transcript handoff
Remove manual Analysieren tap. Transcript posts automatically at session
end, review runs in background, results waiting when Nach der Sitzung
opens. Small backend change, meaningful UX shift.
**Gate:** Two weeks of stable daily use with current flow first.

### Voice commands to backend agents
Intent classifier before `run_chat_turn()`. Lightweight keyword/pattern
match routes commands vs. conversation turns.
Starting vocabulary: "Wiederhole," "Neue Persona erstellen,"
"Schwerpunkt ändern," "Speichere das."
**Design question:** What commands are actually needed from daily use?
Build from observed need, not anticipated need.
**Gate:** 2-4 weeks of daily mobile use to surface real command needs.

### Repeat and modify
After Maria corrects an error, "Wiederhole" replays the same scene with
that correction active. Builds a backlog of flagged moments the review
agent pulls patterns from across sessions.
**Design question:** How does the correction carry into the next round?
Does the persona explicitly watch for it, or does it surface in the brief?

---

## Open questions from Grok review (unresolved)

These surfaced in parallel review and haven't been formally decided:

**Q1 — Text-only fallback for voice (E3)**
If SpeechRecognition unavailable or fails on mobile, automatically
switch to text input mode. Web Speech API on iOS Safari — is it
reliable enough to defer this, or have we seen it fail?
**Action needed:** Test on iPhone Safari in today's session. Note if
voice fails or is unreliable.

**Q2 — Persona card preview before committing (E5)**
Brief expandable on persona list item before full detail panel swap.
"What will we practice?" tooltip.
**Current state:** The scroll-to-detail fix shipped. Does this still
feel needed after the mobile fix, or does the current flow work?
**Action needed:** Test on mobile today and note.

**Q3 — Session history "Continue" card (E2)**
For same persona, surface last session as "Continue" card at top of
detail panel on mobile. Useful for Runde 2/5 sessions.
**Action needed:** Does the current Letzte Sitzungen section feel
sufficient, or is this worth building?

**Q4 — Latency numbers**
The pipeline was instrumented last session. What are the actual numbers
for transcribe_ms / ai_turn_ms / tts_ms / total_turn_ms?
**Action needed:** Run a session and check the logs. Numbers determine
which latency lever to pull.

---

## Known failure modes (from this session's DR)

- **Global CSS overrides in proxy.py** block domain mobile rules.
  Any future shared CSS must be scoped to `@media (min-width:769px)`.
- **Two-mode layouts must define exit as well as entry.**
  The dead-end was caused by defining `.session-active` entry without
  defining how to leave it. Always spec both directions.
- **Latency without a visible indicator** causes users to think the
  system stopped. "Denkt nach…" indicator ships regardless of which
  latency lever the numbers point to.

---

## Learning system connection

Every Gespräche session generates:
- Transcript (conversation history)
- Error/correction pairs (from Analysieren)
- Session metadata (persona, duration, turns)

This is the primary signal for:
- **RAG (Phase 1):** "What mistakes does Robert make most often
  with Maria?" answered from actual session history
- **LoRA (Phase 2):** Error/correction pairs as training data for
  a German-specific adapter that corrects Robert's German the way
  his personas do

The more sessions run, the better the eventual local model. Daily use
now is Phase 0 signal accumulation.

---

## Testing checklist — use today

Three things to verify in today's session:

- [ ] Full loop without page reload: select persona → session →
      Beenden → Analysieren → Neue Sitzung → second session
- [ ] Clipboard button in Nach der Sitzung works on iPhone Safari
- [ ] Persona tap scrolls correctly to detail panel on mobile

Note anything that feels slow, broken, or missing. That becomes the
next spec priority.

---

## Roadmap status

| Item | Status |
|------|--------|
| Near-term fixes (10 items) | ✓ done |
| T1-B Safe area + font audit | target — spec next |
| T2-A iOS share sheet | target — spec next |
| T2-B Schreiben toast | target — spec next |
| T2-C Post-session summary card | target — spec next |
| Automatic transcript handoff | target — design session first |
| Voice commands | target — design session first |
| Repeat and modify | target — design session first |
| LoRA German error adapter | Phase 2 — accumulating signal now |

---

*Gespräche Forward Spec · 2026-06-17 · Claude.ai*
*Commit to: docs/GESPRACHE_FORWARD_SPEC.md*
*Update this document as items ship and new questions surface*
