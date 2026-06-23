# Spec — Gespräche: KI-Sitzung Mobile Tuning
*Created: 2026-06-18 — Claude.ai*
*Status: Ready for `_working/`*
*Priority: High — makes Path B (in-page API session) usable on mobile*

---

## Context

The KI-Sitzung (in-page API conversation) works on desktop but the
mobile layout hasn't been tuned. On a 390px viewport the session area,
suggestion pills, transcript, and controls need to be rethought for
one-handed use. Voice is the primary input but text must be available
as a fallback — sometimes voice isn't practical.

Path B (in-page session) is the no-switch alternative to Path A
(external model). Both should feel equally comfortable on mobile.

---

## Changes

### 2.1 — Resting state layout on mobile (before session starts)

**Problem:** On mobile the prompt text (SESSION INSTRUCTIONS block)
dominates the panel, pushing the action buttons below the fold.
Users can't find the Sitzung starten button without scrolling.

**Fix — collapse prompt into `<details>` block:**

Layout order in the detail panel on mobile (≤768px):
1. Persona name + role (always visible)
2. Persona description (short — 2-3 lines max, truncate with "mehr" if longer)
3. Scene pills (Szene wählen) — tap to select
4. **Action buttons immediately here** — ▶ Sitzung starten, Prompt kopieren,
   Telegram, Als .txt
5. `<details>` collapsed block below: "Prompt anzeigen ▾"
   — expands to show full SESSION INSTRUCTIONS text
   — collapsed by default on mobile
   — label makes clear it's a copy artifact, not UI to read

The prompt is a Path A tool. It's rarely read on repeat visits.
Collapsing it puts the buttons where they need to be — immediately
reachable after picking a scene.

### 2.2 — Session area layout on mobile (during session)

When `.session-active` is active on mobile (≤768px):

- Persona description, prompt block, MODELL selector, Heutige
  Vorbereitung all collapse
- Session area takes full width of screen
- Transcript box: full width, `min-height: 200px`, scrollable,
  `font-size: 16px` (no Safari auto-zoom)
- Timer and status line (● Aufnehmen / 00:00) pinned at top of
  session area — always visible while scrolling transcript

### 2.3 — Suggestion pills on mobile

Currently pills are small and hard to tap on mobile. In session-active
mode on mobile:

- Pills reflow to full-width rows (not horizontal scroll)
- Each pill: full width, `min-height: 44px`, amber border, parchment bg
- Tapping a pill sends it as a conversation turn (already specced) —
  confirm this works on mobile touch
- Maximum 3 pills visible — "Mehr anzeigen" expands if more exist
  (prevents pills from dominating the screen)

### 2.4 — Text input fallback

When voice is not practical (quiet zone, public transport, preference):

- A "Tippen" toggle appears below the mic button during a session
- Tap "Tippen" → text input appears, mic hides
- Text input: full width, `font-size: 16px`, amber border on focus,
  "Senden →" button below
- Tap "Sprechen" to return to voice mode
- Both modes produce the same turn — routes through same
  `sendTurn()` function, no backend change

### 2.5 — Mic and end-session controls

On mobile the mic pulse and Beenden button must be large enough to tap
confidently during a live session:

- Mic indicator: `min-width: 48px`, `min-height: 48px`
- ■ Beenden button: full width on mobile, `min-height: 52px`,
  amber fill — the primary action, deserves the most space
- "Denkt nach…" indicator visible during AI turn — already specced,
  confirm it renders correctly at mobile width

### 2.6 — Transcript readability during session

- Each turn clearly labelled: "Maria:" / "Sie:" with amber/muted
  color distinction
- Font size: `16px` minimum
- Auto-scrolls to latest turn
- Transcript does not jump or reflow when new turns arrive

---

## Definition of Done

- Prompt block collapsed by default on mobile — "Prompt anzeigen ▾" expands it
- Action buttons visible immediately after scene selection on mobile
  (no scrolling past prompt required)
- Session area takes full width on mobile when `.session-active`
- Suggestion pills are full-width rows, 44px+ touch targets
- "Tippen" toggle available during session — text input works,
  sends turn, same output as voice
- Beenden button is full-width, 52px+ height on mobile
- Transcript auto-scrolls, font ≥ 16px, turns clearly labelled
- "Denkt nach…" indicator visible during AI turn on mobile
- No regression on desktop layout
- Verified on: iPhone Safari, iPhone Chrome, Samsung A36 Chrome
- Full loop tested on mobile: voice turn → AI response → text turn →
  AI response → Beenden

## Commit

`Gespräche: KI-Sitzung mobile tuning — collapse prompt block,
buttons above fold, full-width session area, pill reflow,
text input fallback, larger touch targets.`

---

*Spec 2 of 3 · 2026-06-18 · Claude.ai*
