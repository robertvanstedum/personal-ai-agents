# Spec — Gespräche: Deferred Analysis
*Created: 2026-06-18 — Claude.ai*
*Status: Ready for `_working/`*
*Priority: High — enables multiple sessions before analysing*

---

## Context

Currently tapping Beenden ends the session but does not save the
transcript automatically. The user must then manually tap Analysieren
to get feedback. This forces a decision after every session and
prevents doing multiple sessions in a row without stopping to review.

Robert's workflow: do 2-3 sessions (Path A or Path B), then analyse
at the end when convenient. Analysis should be available but not forced.

---

## Change

### On tap Beenden

1. Session ends — VAD stops, TTS stops
2. **Transcript auto-saves to session state** — immediately, no tap needed
3. A compact session card appears above Nach der Sitzung:

```
┌─────────────────────────────────────────┐
│ ✓ Maria · Runde 1 · 8 turns · 3m 20s   │
│                          [Analysieren →] │
└─────────────────────────────────────────┘
```

4. **Analysieren is available but not required** — tap it when ready,
   skip it to start another session
5. **Neue Sitzung is immediately available** — one tap, same persona,
   fresh session. No need to analyse first.

### Multiple sessions

If Robert does 3 sessions before analysing:
- Three session cards stack above Nach der Sitzung
- Each has its own [Analysieren →] button
- Analyse in any order, at any time
- Cards collapse to a summary row once analysed:
  "✓ Analysiert — Maria · 3m 20s · [anzeigen]"

### Nach der Sitzung — two paths, no contradiction

**Problem:** After a KI session, the paste panel ("Transkript einfügen
oder Sitzung beenden") remains visible below the session card. The two
coexist and read as contradictory — the user has already ended the
session and the card is the transcript.

**Fix — context-aware visibility:**

**After KI session (Path B):**
- Session card appears (auto-saved transcript)
- Paste panel is hidden — the session card is the transcript
- No paste needed, no visual noise

**For Path A (external model):**
- Paste panel is renamed "Transkript einfügen ▾"
- Collapsed by default — clear expand affordance
- Always accessible but not prominent unless needed
- Tapping expands the full paste panel with clipboard button

**The combined flow:**
```
KI session → Beenden → session card appears → paste panel hidden
Path A → tap "Transkript einfügen ▾" → expands → paste → Analysieren
```

Two distinct surfaces, two distinct purposes, no contradiction.
Session cards appear first (most recent at top).
"Transkript einfügen ▾" always available below for Path A.

### Session card design

```
✓ Maria · Runde 1 · 8 turns · 3m 20s
[Transkript ▾]  [✕]  [Analysieren →]
```

- Parchment background, amber left border
- Single line: ✓ [Persona] · [Runde X] · [N turns] · [duration]

**Three independent controls — no forced sequence:**

**[Transkript ▾]** — expands to show full transcript inline below the
card. Tap again to collapse. Optional — read it, skip it, or read it
after analysing. Does not trigger analysis.

**[✕] delete** — muted, smaller than the other controls
- Tap: confirm dialog "Diese Sitzung löschen?" [Löschen] / [Abbrechen]
- On confirm: card removed, transcript discarded, not analysed
- Use case: bad take, false start, session not worth keeping

**[Analysieren →]** — amber, 44px touch target, right-aligned
- Reads from the auto-saved transcript — user does not need to view
  the transcript first
- Triggers analysis and renders feedback below the card
- Available immediately after Beenden, before or after reading transcript,
  or never

**All three are independent. No step requires another:**
- Analyse without reading transcript: fine
- Read transcript without analysing: fine
- Start Neue Sitzung without doing either: fine
- Come back later and analyse: fine — card persists

**After analysis:**
- Card collapses to summary row: "✓ Analysiert — Maria · 3m 20s"
- [anzeigen] expands feedback inline
- [Transkript ▾] still available
- Card persists until page reload

### What does NOT change

- Analysieren API route — unchanged, same output format
- Feedback display (FEEDBACK / FEHLER / STÄRKEN / SCHWÄCHEN) — unchanged
- The transcript paste panel — unchanged
- Letzte Sitzungen section — unchanged

---

## Definition of Done

- Beenden auto-saves transcript to session state — no tap needed
- Session card appears immediately after Beenden with correct
  metadata (persona, turns, duration)
- [Analysieren →] on session card triggers analysis and renders
  feedback correctly
- Neue Sitzung available immediately after Beenden without requiring
  analysis first
- Multiple session cards stack correctly (test with 3 sessions)
- Session cards and paste panel coexist without layout conflict
- After KI session Beenden: paste panel hidden, session card prominent
- "Transkript einfügen ▾" collapsed by default, expands for Path A
- Analysieren button on card: 44px minimum touch target on mobile
- Verified: complete 2 sessions back-to-back without analysing,
  then analyse both — feedback correct for each
- [Transkript ▾] expands/collapses transcript inline — works independently
- [Analysieren →] works without user having viewed transcript first
- [✕] delete: confirm dialog, card removed on confirm
- Neue Sitzung available immediately after Beenden — no analysis required
- Delete does not affect other session cards
- Verified: complete session → Analysieren without viewing transcript →
  feedback correct
- Verified: complete 2 sessions → Neue Sitzung between them → analyse
  both afterward
- No regression on existing Analysieren flow

## Commit

`Gespräche: deferred analysis — auto-save on Beenden, session card,
hide paste panel after KI session, rename Path A entry point,
Neue Sitzung without wait.`

---

*Spec 3 of 3 · 2026-06-18 · Claude.ai*
