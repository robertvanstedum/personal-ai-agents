# Spec — Gespräche Transcript Paste Panel
*Created: 2026-06-16 — Claude.ai*
*Status: Ready for `_working/`*
*Feature: T1-A from mobile forward plan*

---

## Background — what was built today

This spec is self-contained. Here is the context needed to build it.

### Mobile audit session (2026-06-16)

Robert tested minimoi.ai and app.minimoi.ai on iPhone this morning. Claude Code
ran a full mobile audit at 390px and shipped 11 fixes across the live portal
and preview static pages. The most significant fix was the German pages being
completely blank on mobile — root cause was `proxy.py` injecting
`nav{top:38px!important;}` as a global override, which blocked the German CSS
`@media (max-width:768px)` rule that sets the nav as a bottom tab bar
(`position:fixed; bottom:0; top:auto`). Combined, the nav was 806px tall and
covered the entire viewport. Fix: scoped the override to desktop only
(`@media (min-width:769px)`).

Other fixes shipped today: Guild briefing grid collapsed to single column,
German Archiv overflow fixed, German Lesen category cards switched to 2×2
grid, German Admin buttons wrapped, Gespräche `selectPersona()` resets
`scrollTop` so action buttons are always visible, Gespräche auto-scrolls to
detail panel on persona tap on mobile, preview banner consolidated to single
column at 390px across all 17 static pages.

### Gespräche — how it works today

Gespräche is the conversation practice tab in Mein Deutsch. The flow:

1. Robert selects a KI-Persona (Maria, Frau Berger, Dr. Huber, Stefan, etc.)
2. Taps "Sitzung starten" — voice session begins
3. VAD (voice activity detection) handles turn-taking — Robert speaks, the
   persona responds via TTS, back and forth
4. Taps "Beenden" — session ends, transcript auto-populates in "Nach der
   Sitzung" textarea
5. Taps "Analysieren" — transcript sent to `run_review()` via
   `providers/review_router.py`, structured feedback returned (FEEDBACK,
   FEHLER, STÄRKEN, SCHWÄCHEN, Anki cards)

### The external Grok flow (what this spec solves)

Robert also practices by copying the Gespräche session prompt and pasting it
into Grok externally on his phone — having the conversation there, then wanting
to bring the transcript back into Mein Deutsch for analysis through the same
feedback pipeline.

Currently there is no clean path to do this. The "Nach der Sitzung" textarea
exists but on mobile it requires scrolling to find and then tapping a small
area to paste into. There is no clipboard shortcut. The flow has too many steps
to be practical on a phone.

The goal of this spec: make pasting an external transcript into the analysis
flow a first-class mobile action — two taps maximum from the detail panel.

### Design system reference

- Dark nav: `#2A1F14`
- Parchment background: `#F5F0E8`
- Accent / amber: `#C68A5E`
- Georgia serif headings
- Buttons follow existing `btn-analysieren` style (amber fill) and
  `btn-secondary` style (parchment bg, amber border)
- All new UI must match the existing Gespräche parchment aesthetic — no dark
  gray boxes, no off-palette colors

---

## What this spec builds

A dedicated transcript paste panel inside the "Nach der Sitzung" section of
Gespräche. It makes pasting an external transcript — from Grok, Claude, or
any external session — as easy as tapping a clipboard button. The same panel
is also pre-populated from the KI-Sitzung transcript when a voice session
just ended, so it serves both internal and external flows from one UI.

---

## Design — Option A (selected)

### Layout

Inside the existing "Nach der Sitzung" collapsible section, replace or
restructure the current textarea with:

```
┌─────────────────────────────────────────┐
│ TRANSKRIPT                              │
│                                         │
│ ┌─────────────────────────────────────┐ │
│ │                                     │ │
│ │  [textarea — 6–8 lines, full width] │ │
│ │                                     │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ [📋 Aus Zwischenablage]  [Löschen]      │
│                                         │
│ [        Analysieren →               ]  │
└─────────────────────────────────────────┘
```

### Elements

**Textarea — "Transkript einfügen"**
- Placeholder text: `Transkript hier einfügen oder Sitzung beenden…`
- Full width, `min-height: 160px` (approximately 6–8 lines on mobile)
- Font size: `16px` minimum — prevents Safari auto-zoom on focus
- `inputmode="text"` and `autocapitalize="off"` — correct for German
  transcripts on mobile keyboards
- Parchment background (`#F5F0E8`), amber border on focus (`#C68A5E`)
- Reuse existing design system textarea class — no new CSS rules

**Status line** (below textarea, above buttons)
- Single line, small text
- States and colors:
  - "Aus letzter KI-Sitzung" — muted amber, subtle badge when pre-populated
    from a KI-Sitzung transcript so the source is clear
  - "Eingefügt ✓" — visually distinct positive color, not the same muted
    amber used for other states; soft green tint (`#5a8a5a` or similar) if
    it doesn't conflict with the parchment palette, otherwise a brighter amber.
    Shown for 2s then clears.
  - "Bitte manuell einfügen" — muted amber, on Android clipboard permission
    denial
  - "Manuelles Einfügen" — muted, static note when `navigator.clipboard`
    unavailable
  - Empty otherwise

**"📋 Aus Zwischenablage einfügen" button**
- Calls `navigator.clipboard.readText()` on tap
- Fills textarea with clipboard content in one tap
- After successful paste: auto-focuses textarea, scrolls into view, status
  line shows "Eingefügt ✓" for 2s then clears
- On Android Chrome: browser prompts for clipboard permission on first use —
  if user denies, status line shows "Bitte manuell einfügen", textarea
  focused so they can paste manually
- On iOS Safari/Chrome: clipboard read requires user gesture (tap satisfies)
- Style: reuse existing `btn-secondary` class — no new CSS rules
- If `navigator.clipboard` unavailable (HTTP, old browser): hide button,
  status line shows "Manuelles Einfügen" as static note. No broken control.

**"Löschen" button**
- Clears textarea and status line
- Style: text link — amber color, no border, no background
- Hover/active state: `opacity: 0.65` — simple, no new CSS class needed
- Positioned inline with the clipboard button (right-aligned or after it)
- No confirm dialog — transcript remains in session history

**"Analysieren →" button**
- Full width on mobile
- Reuse existing `btn-analysieren` class (amber fill, white text)
- Visually distinct disabled state when textarea is empty — not just grayed;
  add helper text below: "Transkript zum Analysieren einfügen"
- Helper text disappears once textarea has content
- On tap: same behavior as existing Analysieren — sends transcript to
  `run_review()` via existing API route, output unchanged

### Pre-population behavior

- If a KI-Sitzung just ended (transcript exists in session state): textarea
  pre-populated when "Nach der Sitzung" is opened. Status line shows
  "Aus letzter KI-Sitzung" badge.
- If no session has run (external paste flow): textarea empty, placeholder
  shown, status line empty.
- If user taps "Löschen" after a pre-populated session: textarea and status
  line clear. Transcript remains in session history.

---

## Mobile behavior

- On mobile (`max-width: 768px`): textarea and buttons are full-width, large
  touch targets (min 44px height on buttons)
- "Analysieren →" button is full-width on mobile for easy tap
- "📋 Aus Zwischenablage" and "Löschen" sit on the same row, each with
  adequate padding
- When textarea is tapped on mobile: page does not jump or auto-zoom (ensured
  by font-size ≥ 16px)
- After tapping "📋 Aus Zwischenablage": textarea scrolls into view if it
  isn't already visible

---

## Files to change

- `domains/german/templates/german_gesprache.html` — restructure Nach der
  Sitzung section, add clipboard JS
- `domains/german/static/german.css` — textarea and button styles if not
  already covered by design system
- No backend changes — this feature is entirely frontend. The existing
  Analysieren API route (`POST /api/gesprache/analyse` or equivalent) is
  called unchanged.

---

## What does not change

- The Analysieren API route — no backend changes
- The existing session flow (VAD, TTS, turn-taking) — untouched
- The FEEDBACK/FEHLER/STÄRKEN/SCHWÄCHEN/Anki card output format — untouched
- The "Telegram öffnen" button — stays, different flow (copies prompt +
  opens Telegram for sending to bot, not for sharing transcript)
- The "Mit echtem Mensch" tab — separate transcript flow, not in scope here

---

## Definition of Done

- "Nach der Sitzung" section contains the new paste panel as described
- Textarea pre-populates from KI-Sitzung transcript when a session just ended
- Textarea is empty with placeholder when no session has run
- "📋 Aus Zwischenablage einfügen" fills the textarea in one tap on mobile
- Clipboard permission denial (Android) shows "Bitte manuell einfügen" and
  focuses the textarea
- "Analysieren →" button is disabled when textarea is empty, enabled when
  it has content
- "Analysieren →" calls the existing analysis route — output unchanged
- "Löschen" clears the textarea
- Font size on textarea ≥ 16px (no Safari auto-zoom)
- All buttons ≥ 44px touch target height
- No design system regressions — parchment palette, amber accent throughout
- `navigator.clipboard` unavailable: clipboard button hidden, status line
  shows "Manuelles Einfügen", user can still paste manually into textarea
  and Analysieren works normally — verify this fallback path explicitly
- Verified on:
  - [ ] Desktop (Chrome dev tools, 390px)
  - [ ] iPhone — Safari
  - [ ] iPhone — Chrome
  - [ ] Samsung A36 — Chrome
- Full flow tested: external Grok session → copy transcript → open Nach der
  Sitzung → tap clipboard button → transcript fills → tap Analysieren →
  feedback renders correctly

## Commit

`Add transcript paste panel to Gespräche Nach der Sitzung — clipboard
button, pre-population from KI-Sitzung, mobile-first layout.`

---

*Spec · 2026-06-16 · Claude.ai*
