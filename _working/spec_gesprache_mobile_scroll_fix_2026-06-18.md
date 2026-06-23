# Spec — Gespräche Mobile: Scroll Fix + Button Hierarchy
*Created: 2026-06-18 — Claude.ai*
*Status: Ready for `_working/`*
*Based on: Claude Code measurement at 390px (iPhone 13 / 14 / 15)*
*Folds into: Spec 1 (Prompt kopieren) — build together*

---

## Context

On iPhone 13 (390×844px) the action buttons sit ~85px below the fold
after persona selection. Samsung A36 is ~40px below. Both require a
deliberate scroll to reach the buttons — a real usability problem.

Measurement summary:
- Safari chrome + nav: ~110px
- Page header + tab toggle: ~130px  
- Persona list: ~220px
- Detail panel visible area: ~274px
- Buttons sit ~359px into detail panel → ~85px below fold

Fix: recover ~85px through CSS-only margin tightening and description
truncation. No layout change, no new elements.

---

## Changes

### CSS — mobile only (≤768px)

```css
@media (max-width: 768px) {

  /* 1. Truncate description to 2 lines — saves ~22px */
  .detail-description {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    margin-bottom: 0.75rem;  /* was 1.25rem — saves 8px */
  }

  /* "mehr anzeigen" expand toggle */
  .detail-description.expanded {
    display: block;
    overflow: visible;
  }

  /* 2. Tighten scene list margin — saves 12px */
  .scene-list {
    margin-bottom: 0.75rem;  /* was 1.5rem */
  }

  /* 3. Tighten detail panel top padding — saves 4px */
  .persona-detail {
    padding-top: 4px;  /* was 8px */
  }

  /* 4. Tighten prompt delivery margin — saves 4px */
  .prompt-delivery {
    margin-top: 4px;   /* was 8px */
    margin-bottom: 4px;
  }

}
```

**Total saved: ~50px margins + ~22px truncation = ~72px**
Sufficient to clear the fold on iPhone 13, A36, and iPhone 14/15.

### "mehr anzeigen" toggle

When description is truncated, show a small expand link below:
- Text: "mehr ▾" — amber, small, inline
- Tap: adds `.expanded` class, shows full description
- Tap again: collapses — "weniger ▴"
- CSS selector: `.detail-description` — confirm with Claude Code
  before building (verify this is the correct class name in the
  actual template)

### Button hierarchy on mobile — fold in from Spec 1

Apply the weight swap in the same commit:
- `📋 Prompt kopieren` → amber filled primary on ≤768px
- `▶ Im Browser` → bordered secondary on ≤768px
- Group labels above each: "EXTERN (GROK, CLAUDE…)" / "IM BROWSER"
- Desktop hierarchy unchanged

---

## What does NOT change

- Desktop layout — all changes scoped to `@media (max-width: 768px)`
- Description content — only display is clipped, full text accessible
  via "mehr anzeigen"
- Scene pills layout — margin tightened, pills themselves unchanged
- Any other German page — scoped to Gespräche detail panel only

---

## Definition of Done

- On iPhone 13 (390px): action buttons visible without scrolling
  after persona selection
- Description shows 2 lines with "mehr ▾" expand working correctly
- Full description accessible via expand — no content lost
- All CSS changes scoped to ≤768px — desktop layout identical
- Button hierarchy: Prompt kopieren amber on mobile, Im Browser bordered
- Group labels "EXTERN" / "IM BROWSER" visible on both desktop and mobile
- Verified on: iPhone 13 (or 390px dev tools), Samsung A36, iPhone 14+
- Verified: "mehr anzeigen" expands and collapses correctly
- No regression on desktop Gespräche layout

## Commit

`Gespräche mobile: buttons above fold — description truncation,
margin tightening, button hierarchy swap. iPhone 13 / A36 / 14+.`

---

*Spec · 2026-06-18 · Claude.ai*
*Build with or immediately after Spec 1 (Prompt kopieren)*
