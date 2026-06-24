# Spec — Gespräche Test-Run Follow-Up
*Created: 2026-06-15 — Claude.ai*
*Source: Robert's first full KI-Sitzung test run (Maria, café bill role-play)*
*Relationship: amends `spec_gesprache_consolidated_2026-06-15.md` (Part A) + new investigation (Part B)*

---

## Summary

Robert ran the full KI-Sitzung flow end-to-end (Maria, café bill). Two findings:

1. **Layout** — the center/right panel sits too low and carries unneeded vertical
   space in both the resting state and the analysis state. Tighten it so the
   working area fits more comfortably without scrolling past empty bands.
2. **Performance** — the session was **very slow to respond**, slow enough that
   Robert nearly restarted it mid-session. Needs measurement, then a fix.

Sequencing note: most of `spec_gesprache_consolidated_2026-06-15.md` is **not yet
built** (FEEDBACK box still dark gray, analysis sections not collapsed by default,
two-mode layout not active — only the tab rename has shipped). Part A below should
be **folded into that consolidated layout pass**, not built as a separate commit
afterward.

---

## Part A — Tighten center column spacing

**Fold into:** `spec_gesprache_consolidated_2026-06-15.md`, items #5 (two-mode
layout) and #6/#7 (styling). Build as part of that pass.

### Observed (from test-run screenshots)

- **Resting state:** large empty band between the "Mit KI-Persona / Mit echtem
  Mensch" toggle and the top of the two-column card. The card starts well below
  where it needs to.
- **Analysis state ("✓ analysiert"):** empty band above the `✓ analysiert /
  bearbeiten` row, and again between that row and the `FEEDBACK` block. The
  content is pushed unnecessarily far down the right panel.

### Change

Pull the center/right working panel up and remove the dead vertical space in
both states. The persona-list column (left) and the working panel (right) should
start at roughly the same top edge.

### Acceptance criteria (Definition of Done)

- Resting state: top of the persona detail card aligns within ~8px of the top of
  the persona list; the gap below the toggle pills is tightened to a single
  consistent spacing step (match the design system's standard block gap, not a
  custom large margin).
- **Persona name top padding:** reduce the space above the persona name inside the
  detail card (e.g. above "Maria" / "Frau Berger") so the name sits closer to the
  card's top edge. Slight reduction — keep it balanced with the card's other
  internal padding, don't crowd it against the edge.
- Analysis state: the `✓ analysiert` row and the `FEEDBACK` block start near the
  top of the right panel — no empty band above either. Feedback content visible
  without scrolling past whitespace on a standard laptop viewport.
- No horizontal layout regression; left column width and the two-column split are
  unchanged.
- Spacing values come from existing design-system tokens where they exist; do not
  introduce one-off pixel margins if a token already covers it.
- Verified visually in both states (resting + post-analysis) before commit.

### Commit

- If folded into the consolidated build: include in that commit, note in the
  commit body: "tighten center column spacing (resting + analysis states) per
  test-run feedback."
- If built standalone (only if consolidated is delayed): `Tighten Gespräche
  center column spacing — remove dead vertical space in resting and analysis
  states.`

---

## Part B — Investigate live-session response latency

**Standalone investigation. Measure first, then decide the fix.** Do not change
the model or pipeline before there are numbers.

### Symptom

Per-turn response was very slow during a live KI-Sitzung. Robert nearly restarted
mid-session because there was no sign the system was working.

### Step 1 — Instrument the turn pipeline (do this first)

Add per-stage timing to a single conversation turn and log it (console + a line
in the session record / admin view if cheap):

- `transcribe_ms` — `/api/transcribe` (Whisper) round trip
- `ai_turn_ms` — `/api/gesprache/ai-turn` (`run_chat_turn()` → provider) round trip
- `tts_ms` — `/api/gesprache/speak` (OpenAI `tts-1`) round trip
- `total_turn_ms` — mic stop → audio playback start

Capture one real Maria session's numbers and record them here before changing
anything. This tells us which stage owns the latency rather than guessing.

### Step 2 — Candidate levers (ranked, gate on Step 1 data)

1. **Task-tiered model routing (most likely win, lowest risk).**
   `review_router.py` already separates `run_chat_turn()` from `run_review()`.
   Live conversation turns do not need deep reasoning — route `run_chat_turn()`
   to a fast/low-latency model and keep the stronger model for `run_review()`
   (Analysieren). If `ai_turn_ms` dominates, this is the fix.
2. **Perceived-latency indicator.** The pipeline is fully serial
   (Whisper → full completion → full TTS file → play), so some wait is inherent.
   A visible "denkt nach…" state on the persona during the AI turn — plus the
   already-queued audio cue on session start (consolidated #4) — directly
   addresses the "is it even working?" problem that nearly caused a restart.
   Cheap, ship regardless of Step 1.
3. **Reduce live-turn output length.** Cap `max_tokens` on `run_chat_turn()` —
   persona replies are short by nature; a high cap costs generation time.
4. **Streaming TTS** — meaningful latency improvement but more involved; already
   a roadmap item. Only pursue if 1–3 don't get the turn under a comfortable
   threshold.

### Acceptance criteria (Definition of Done)

- Per-stage timings captured for one real session and written into this spec (or
  the session record).
- A decision recorded on which lever(s) to apply, justified by the numbers.
- If lever #2 (thinking indicator) is shipped: persona shows a visible working
  state during the AI turn; verified in a live session.
- If lever #1 (model routing) is applied: `run_chat_turn()` default model is
  documented; `run_review()` model unchanged; error-naming behavior preserved
  (no silent fallback).

### Commit

- Instrumentation: `Add per-stage timing to Gespräche turn pipeline (transcribe /
  ai-turn / tts / total).`
- Indicator: `Add persona thinking indicator during AI turn (perceived latency).`
- Routing (if applied): `Route run_chat_turn() to fast model; keep run_review()
  on stronger model.`

---

## Part C — Exit path from analysis back to resting state (functional dead-end)

**Highest priority of the three findings.** Fold into
`spec_gesprache_consolidated_2026-06-15.md` #5 (two-mode layout).

### Problem

After clicking **Analysieren**, the panel stays in `.session-active` and shows the
analysis (`✓ analysiert` / `bearbeiten`) with **no way to return to the start**.
The only escape is a page reload. Consolidated #5 defines how the session *enters*
`.session-active` and what happens at session end, but never defines how it
*leaves*. Entry with no exit — a user (or interview demo) gets stranded.

### Change

Add a clear control in the post-analysis state — label **"Neue Sitzung"** — that
returns the panel to the resting state.

### Behavior

- "Neue Sitzung" removes `.session-active` and restores the resting layout:
  re-expands persona description, action buttons, MODELL selector, Heutige
  Vorbereitung, and Nach der Sitzung.
- Clears the session area: transcript and analysis are reset, not carried into the
  next session.
- Keeps the **current persona selected** so the user can start another round
  immediately. Scrolls the panel back to the top.
- Safety net: selecting a **different persona** from the left list while in
  session or analysis mode also exits to that persona's resting state. The user
  is never trapped.

### Placement

Alongside the `✓ analysiert / bearbeiten` row (the natural home), or as a clear
button at the end of the analysis block. Match the design system — parchment /
amber, not a dark button.

### Acceptance criteria (Definition of Done)

- From the post-analysis state, a single visible control returns the panel to the
  resting state for the current persona.
- Resting state fully restored — all collapsed sections re-expanded, session area
  cleared, no orphaned transcript or analysis bleeding into the next session.
- Current persona stays selected; user can start a new session without a reload.
- Selecting a different persona while in session/analysis mode returns to resting
  state for the newly selected persona.
- Verified by running a full session → Analysieren → Neue Sitzung → second
  session, with no page reload.

### Commit

`Add Neue Sitzung control — exit session-active back to resting state after
Analysieren (fixes post-analysis dead-end).`

---

## Not in this spec (observations for confirmation)

- Persona order in the live build still shows **Frau Berger first**, not Maria —
  the reorder requested this session hasn't landed. Confirm current branch state.
- "Meine Frau" rendered correctly in the test transcript (not "Mein Frau") — the
  persistent error did not recur this run. Worth noting; no action.

---

*Spec · 2026-06-15 · Claude.ai*
