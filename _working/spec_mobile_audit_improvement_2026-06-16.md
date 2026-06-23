# Spec — Mobile Experience Audit & Improvement
*Created: 2026-06-16 — Claude.ai*
*Status: Ready for `_working/` — audit first, fixes follow*
*Scope: minimoi.ai public landing page + app.minimoi.ai portal*

---

## Goal

Make minimoi.ai and app.minimoi.ai genuinely good on mobile — same branding,
same imagery, same design system as desktop, reflowed for a 390px viewport.
Not a stripped-down mobile version. The full experience, vertical.

Robert is using this as a portfolio showpiece in interviews. Someone should be
able to pull it up on a phone and have it land the same way the desktop does.
Curator already proves this is achievable — it's the reference standard for
every other page.

---

## Reference standard — Curator mobile (confirmed working)

From Robert's 11:42 screenshot:
- CURATOR masthead renders full-width, large, correct typeface
- Italic tagline reflows to two lines naturally
- Date renders cleanly
- Daily card: dark header, full-width watercolor image, label, description,
  link — all correct
- Parchment background correct throughout
- Design tokens (colors, typography) intact

**Every other page should reach this standard.** When verifying fixes, load
Curator mobile first as the baseline, then compare.

---

## Known issues going into the audit (from Robert's test, 2026-06-16)

| Page | Issue | Severity |
|------|-------|----------|
| Preview banner (all pages) | Four-column layout breaks at 390px, text wraps mid-word, "Request live access →" unreadable | High — affects every page |
| German (Mein Deutsch) | Completely blank — snapshot missing or mis-pathed | High — nothing renders |
| Guild | Horizontal/landscape layout doesn't reflow for mobile | Medium |
| Curator sub-nav | SCANS & DIVES wraps mid-label on mobile | Low — readable but rough |

---

## Audit scope — pages to walk through

Claude Code should load each page on a 390px viewport (browser dev tools or
physical iPhone) and document findings before writing any fixes.

### minimoi.ai — public landing page

| Section | What to check |
|---------|--------------|
| Hero / masthead | mini-moi wordmark, tagline — reflow correctly? |
| Preview banner | Single-column readable at 390px? (known broken) |
| "What this is" card | Text reflows? Padding correct? |
| "What's running" card | Curator / Mein Deutsch / Guild blurbs readable? Links work? |
| "About" card | Text reflows? LinkedIn link tappable? |
| Three-card layout | Stack vertically on mobile (not side by side)? |
| Footer / nav | Mini-moi, GitHub, LinkedIn, Dashboard → all tappable at 44px+ |

### app.minimoi.ai — Curator

| Section | What to check |
|---------|--------------|
| Global nav (mini-moi / Curator / German / Guild) | Wraps or scrolls horizontally? All tappable? |
| CURATOR masthead | Already good — regression check only |
| Sub-nav (DAILY / READING ROOM / SCANS & DIVES / LEANINGS / ARCHIVE) | Horizontal scroll or wraps? SCANS & DIVES label wraps mid-word |
| Daily card | Image, label, description, link — already good, regression check |
| Article cards | Stack vertically? Images scale correctly? |
| Reading Room | Article list reflows? |

### app.minimoi.ai — German (Mein Deutsch)

| Section | What to check |
|---------|--------------|
| Snapshot content | Currently blank — Fix 1 from previous spec |
| Mein Deutsch masthead | Georgia serif, correct weight — reflows? |
| Tab nav (Lesen / Gespräche / Schreiben / Wörter / Archiv / Admin) | Horizontal scroll or wraps? All tabs tappable? |
| Lesen tab | Article cards stack vertically? Reading list selector reflows? |
| Gespräche tab | Two-column layout — must collapse to single column on mobile. Persona list stacks above detail panel. |
| Schreiben tab | Prompt and textarea reflow? |
| Wörter tab | Drill/flashcard UI reflows? |
| Archiv tab | Session list readable? |

### app.minimoi.ai — Guild

| Section | What to check |
|---------|--------------|
| Snapshot content | Currently landscape/horizontal — reflow |
| Guild masthead | Reflows correctly? |
| Content sections | Stack vertically? |
| Any data tables or multi-column layouts | Collapse to single column |

---

## Fix approach

### Phase 1 — Shared components (fix once, affects all pages)

1. **Preview banner** — `@media (max-width: 768px)`: single column, full
   width, text flows naturally, "Request live access →" tappable at 44px+.
   This is the same broken component on every page — fix it once.

2. **Global nav** — at 390px the full nav string
   ("mini-moi | Curator | German | Guild | Robert | Sign out") likely
   overflows or wraps awkwardly. Options: horizontal scroll with
   `-webkit-overflow-scrolling: touch`, or collapse secondary items.
   Check current behavior before deciding.

3. **Three-card landing layout** — the "What this is / What's running / About"
   cards must stack vertically on mobile. If they're in a CSS grid or flex row,
   add `@media (max-width: 768px) { flex-direction: column }` or
   `grid-template-columns: 1fr`.

### Phase 2 — Per-domain snapshot content

4. **German snapshot** — diagnose and fix blank (see
   `spec_mobile_preview_fixes_2026-06-16.md` Fix 1). Curator snapshot is the
   working model for path and format.

5. **Guild snapshot** — once content renders, apply same mobile reflow as
   other domains.

### Phase 3 — Domain-specific layouts

6. **Gespräche two-column layout** — the persona list + detail panel is
   explicitly a fixed-height two-column layout on desktop. On mobile this must
   collapse: persona list becomes a horizontal scrollable pill row or a
   dropdown selector at the top, detail panel takes full width below.
   This is the most complex mobile fix — spec separately if needed.

7. **Curator sub-nav** — SCANS & DIVES wrapping mid-label. Options: shorten
   label to "DIVES" on mobile, or allow horizontal scroll on the sub-nav bar
   with clear scroll affordance.

8. **Tab navs (Mein Deutsch, Guild)** — if tabs overflow 390px, implement
   horizontal scroll rather than wrapping.

---

## PWA path (after mobile layout is solid)

Once the mobile layout audit and fixes are complete, the next step is a PWA
wrapper — not a native app. This turns minimoi.ai into a home-screen-installable
experience without a separate codebase or App Store submission.

**What it requires:**
- `manifest.json` — app name, icons (192px + 512px), theme color
  (`#2A1F14`), background color, display: `standalone`
- Service worker — cache the landing page and assets for offline load
- `<link rel="manifest">` in `<head>`
- iOS meta tags: `apple-mobile-web-app-capable`, `apple-mobile-web-app-title`,
  `apple-touch-icon`

**Result:** User taps "Add to Home Screen" on iOS/Android → mini-moi icon on
home screen → launches full-screen with no browser chrome → feels like an app.

**Native app (defer):** Only worth building if native capabilities are needed
(push notifications, background audio, camera). Nothing in the current feature
set requires it. Revisit after PWA is live and in use.

---

## Definition of Done

**Audit phase:**
- Every page in scope loaded at 390px and findings documented
- No new code written during audit — findings only

**Fix phase:**
- Preview banner: single column, readable, tappable at 390px across all pages
- German preview: renders content on mobile matching Curator's format
- Guild: reflows to single column on mobile
- Landing page three-card layout: stacks vertically on mobile
- Global nav: no overflow at 390px
- Curator mobile: no regression from current working state
- Gespräche: persona list and detail panel usable on mobile (single column)
- All interactive elements (links, buttons, tabs): minimum 44px touch target

**Verification:**
- Load every page on physical iPhone after fixes
- Robert confirms each page reaches the Curator standard before closing

## Commits

- `Fix preview banner mobile layout — single column at 390px (all pages).`
- `Fix German preview snapshot — match Curator path and format.`
- `Fix Guild mobile layout — reflow from landscape to single column.`
- `Fix landing page three-card layout — stack vertically on mobile.`
- `Fix Gespräche mobile layout — collapse two-column to single column.`
- *(one commit per logical fix — do not bundle unrelated layout changes)*

---

## Note

This spec supersedes the per-fix approach in `spec_mobile_preview_fixes_2026-06-16.md`.
That spec's Fix 1 (German blank) and Fix 2 (banner) are absorbed here with
the same requirements. Do not build both specs — use this one.

---

*Spec · 2026-06-16 · Claude.ai*
