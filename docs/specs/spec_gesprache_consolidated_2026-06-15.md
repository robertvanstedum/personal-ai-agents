# Spec — Gespräche: All Current Improvements (Consolidated)
*mini-moi · Guild*
*Created: 2026-06-15 — Claude.ai*
*Status: spec_ready*
*For: Claude Code*
*Consolidates: spec_gesprache_twmode_layout_2026-06-15.md + all small
fixes discussed in session. Build everything here unless specifically
marked DEFER.*

---

## Overview

Seven changes, roughly in implementation order. All are small-to-medium.
Nothing deferred except streaming TTS and cross-session patterns (too
complex for now, in ROADMAP).

| # | Change | Effort |
|---|--------|--------|
| 1 | Tab rename (KI-Personas / Konversation labels) | Tiny |
| 2 | VAD silence window → 2s | Tiny |
| 3 | Suppress transcribing text flash | Tiny |
| 4 | Audio cue on session start | Small |
| 5 | Two-mode layout (session collapses pre-session) | Medium |
| 6 | Feedback box styling (match design system) | Small |
| 7 | Collapsible analysis sections (Stärken/Schwächen/Anki) | Small |

---

## Change 1 — Tab rename

```
KI-Personas   →  Mit KI-Persona
Konversation  →  Mit echtem Mensch
```

Just the pill labels. No structural change. One line each in the template.

---

## Change 2 — VAD silence window

Current: 1.5s silence triggers end-of-turn.
New: **2.0s**.

German sentences run long; mid-thought pauses are normal. 2s catches
sentence ends more reliably without meaningfully increasing turn latency.
Also reduces Whisper accuracy issues from sentences cut off early.

Find the silence detection threshold in the VAD loop and change the
constant. One line.

---

## Change 3 — Suppress transcribing text flash

During an active session the Whisper output is briefly written to a
visible DOM element before being sent as a turn. This should be suppressed
entirely — the transcript builds silently in `sessionHistory` /
`sessionTranscript` arrays. The DOM element should not be updated during
an active session.

Find the line that writes Whisper output to the visible field. Wrap it
in `if (!sessionActive)` or remove the DOM write entirely during session.

---

## Change 4 — Audio cue on session start

**Problem:** 3-5s silent gap between clicking [▶ Sitzung starten] and
Maria's first words (AI fetch + TTS are sequential).

**Fix:** Play a short preloaded audio cue immediately on button click,
before any API call fires. This gives instant feedback that something
is happening.

**Implementation:** preload a short MP3 or use Web Speech API synthesis
for a brief German phrase. Options in order of simplicity:

Option A — Web Speech API (no file needed):
```javascript
startBtn.addEventListener('click', async () => {
  // Immediate audio feedback — no network call needed
  const utter = new SpeechSynthesisUtterance('Sitzung beginnt.');
  utter.lang = 'de-DE';
  utter.rate = 1.1;
  speechSynthesis.speak(utter);
  // Then fire the actual session start
  await startSession();
});
```

Option B — Preloaded MP3 at `/static/audio/session_start.mp3` (a short
neutral chime or "Sitzung beginnt" TTS pre-rendered). Play synchronously
on click.

**Recommend Option A** — no file to manage, works immediately, sounds
natural enough for a brief cue. If it sounds robotic, switch to Option B.

---

## Change 5 — Two-mode layout

Full detail in `spec_gesprache_twmode_layout_2026-06-15.md`. Summary:

**Session start:** add `.session-active` class to `.persona-detail`.
CSS hides: persona description, action buttons, MODELL selector,
Heutige Vorbereitung, Nach der Sitzung.
CSS shows: compact header (name + role, one line), status bar, suggestion
pills (repositioned into session area), text input.

**Suggestion pills during session:** clickable — sends pill text as
Robert's turn (same as typing + Enter). Small pills, horizontal row,
above the text input.

**Session end:** remove `.session-active`. Right column shows:
- Compact persona header
- Transkript section (auto-expanded)
- MODELL selector + [Analysieren]
- Analysis result box (auto-scrolled to)
- Heutige Vorbereitung (collapsed, available for reference)

Key CSS:
```css
.persona-detail.session-active .persona-description  { display: none; }
.persona-detail.session-active .action-buttons        { display: none; }
.persona-detail.session-active .modell-selector       { display: none; }
.persona-detail.session-active .heutige-vorbereitung  { display: none; }
.persona-detail.session-active .nach-der-sitzung      { display: none; }
.persona-detail.session-active .session-panel         { display: block; }
.persona-detail.session-active .session-pills         { display: flex; }
```

**Nothing touches:** left persona list, top nav, tab bar, session logic.

---

## Change 6 — Feedback box styling

**Current:** dark gray box on parchment. Reads like an error card or
terminal output — disconnected from the design system.

**New:** match the design system. Parchment background, amber left border,
no heavy dark background.

```css
.analysis-feedback-box {
  background: var(--color-parchment, #F5F0E8);
  border-left: 3px solid var(--color-accent, #C68A5E);
  border-radius: 6px;
  padding: 1rem 1.25rem;
  color: var(--color-text-dark, #2A1F14);
}
```

Section labels (FEEDBACK, STÄRKEN, SCHWÄCHEN) stay as small-caps headers
in amber — same pattern used elsewhere on the site.

---

## Change 7 — Collapsible analysis sections

The full analysis output (Feedback summary → STÄRKEN → SCHWÄCHEN →
Anki cards) is tall. Make each section independently collapsible.

```html
<div class="analysis-section">
  <button class="analysis-toggle" data-target="section-staerken">
    STÄRKEN <span class="toggle-icon">▼</span>
  </button>
  <div id="section-staerken" class="analysis-section-body">
    <!-- content -->
  </div>
</div>
```

Default state:
- **FEEDBACK** (summary paragraph): expanded — always visible first
- **STÄRKEN**: collapsed — tap to expand
- **SCHWÄCHEN**: expanded — errors/corrections are the most actionable
- **Anki cards**: collapsed — expand when ready to review

Toggle is a simple `display: none` / `display: block` on click.
No animation needed — instant is fine.

---

## Deferred to ROADMAP

- **Streaming TTS** — show AI text as it arrives rather than waiting for
  full response. Meaningful latency improvement but requires streaming API
  integration + browser audio queuing. Not trivial.
- **Cross-session pattern detection** — errors that persist across both
  KI and real sessions. Needs DB schema work. See design doc.
- **Persona from real conversation** — create new KI-Persona from a
  Mit echtem Mensch session. See design doc.

---

## Definition of Done

- [ ] Tab pills read "Mit KI-Persona" / "Mit echtem Mensch"
- [ ] VAD silence window is 2.0s
- [ ] No text flash during active session (Whisper output DOM-silent)
- [ ] Audio cue fires immediately on [Sitzung starten] click
- [ ] Session mode: pre-session content hidden, session area takes column
- [ ] Session end: transcript auto-expanded, analysis renders below
- [ ] Suggestion pills clickable as turn-senders during session
- [ ] Feedback box: parchment bg, amber left border (no dark gray)
- [ ] FEEDBACK expanded, STÄRKEN collapsed, SCHWÄCHEN expanded, Anki collapsed
      by default
- [ ] Robert completes full session (start → talk → end → analyse) without
      scrolling and without visible processing flashes
- [ ] Layout checked at full width and narrower (existing breakpoints)

---

## Commit

```bash
git add domains/german/templates/german_gesprache.html \
        domains/german/static/german.css \
        domains/german/html_server.py
git commit -m "feat/ux: Gespräche consolidated improvements

Tab rename: Mit KI-Persona / Mit echtem Mensch. VAD silence 1.5s → 2s.
Whisper output DOM-silent during session. Audio cue on session start
(Web Speech API, immediate). Two-mode layout: session-active class
collapses pre-session content, session area takes full right column,
suggestion pills clickable as turn-senders. Post-session: transcript
auto-expanded, analysis renders below. Feedback box: parchment bg /
amber left border (replaces dark gray). Analysis sections collapsible:
FEEDBACK expanded, STÄRKEN collapsed, SCHWÄCHEN expanded, Anki collapsed."
git push origin main
```

---

*Spec · Gespräche Consolidated Improvements · 2026-06-15*
