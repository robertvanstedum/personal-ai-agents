# Mobile UI — Work Done + Enhancement Spec
**Date:** 2026-06-16  
**Status:** Work complete (fixes shipped). Enhancement ideas need design + prioritization.  
**Audience:** Claude.ai / OpenClaw — please review, suggest additions, and produce a prioritized issue list.

---

## Part 1 — What We Fixed (Already Shipped)

This session was a full mobile audit of `minimoi.ai` (preview static pages) and `app.minimoi.ai` (live portal) at 390px viewport. Robert tested on iPhone after each round.

### Live Portal Fixes

**1. German pages blank on mobile** *(HIGH — all 5 German pages were completely invisible)*  
Root cause: `proxy.py` injected `nav{top:38px!important;}` as a global override. German CSS has a `@media (max-width:768px)` rule that makes the nav a bottom tab bar (`position:fixed; bottom:0; top:auto`). The `!important` override blocked `top:auto`, so combined with `bottom:0` the nav was `height:806px` covering the entire viewport.  
Fix: scoped the nav offset to desktop only — `@media (min-width:769px){nav{top:38px!important;}}` in `proxy.py` and in the `portal-offset-css` style tag injected into each page.  
Files: `minimoi_portal/proxy.py`, `tools/capture_snapshot.py`

**2. Guild Briefing — 2-column grid didn't collapse on mobile**  
The CoS briefing panel used `display:grid; grid-template-columns:1fr 1fr`. Added `@media (max-width:768px)` to make it single-column.  
Files: `minimoi_portal/templates/guild/guild_landing.html`

**3. German Archiv — rows overflowed right edge**  
Source and mode-badge columns have fixed min-widths that push past 390px. Added mobile rule to hide `.archiv-source` and `.archiv-mode-badge`, relax min-widths on date and persona columns.  
Files: `domains/german/static/german.css`, `static/public/preview/assets/german.css`

**4. German Lesen — 3rd category card clipped**  
Category row used `display:flex; flex-wrap:nowrap; min-width:120px` per card. 3 cards = 360px + gaps = overflow. Switched to `display:grid; grid-template-columns:repeat(2,1fr)` at mobile — 2×2 layout.  
Files: `domains/german/static/german.css`, `static/public/preview/assets/german.css`

**5. German Admin — action buttons clipped right**  
Multi-button rows in admin list view didn't wrap. Added `flex-wrap:wrap` to `.admin-list-item` and `.admin-item-actions`.  
Files: `domains/german/static/german.css`, `static/public/preview/assets/german.css`

**6. Gespräche — buttons missing after session ends**  
Root cause: pressing ■ Beenden calls `transcriptEl.scrollIntoView()`, scrolling `.persona-detail` (max-height:600px, overflow-y:auto) down to the transcript area. When you then tap a different persona, `selectPersona()` didn't reset `detail.scrollTop`, so the 4 action buttons were scrolled above the visible area within the panel. They existed — just not visible.  
Fix: `detail.scrollTop = 0` added unconditionally at top of `selectPersona()`.  
File: `domains/german/templates/german_gesprache.html`

**7. Gespräche — detail panel not visible after tapping persona on mobile**  
On mobile, `.persona-detail` stacks below the persona list. Tapping a persona filled in the detail content but the page didn't scroll. User had to manually scroll down to see the detail panel and buttons.  
Fix: added `scrollIntoView({ behavior: 'smooth', block: 'start' })` on `#persona-detail` at the end of `selectPersona()`, gated on `window.innerWidth <= 768`.  
File: `domains/german/templates/german_gesprache.html`

### Preview Static Page Fixes

Preview pages at `minimoi.ai` are Playwright-captured snapshots of the live portal served via Cloudflare Pages. All 17 pages were patched in-place.

**8. Preview banner broken on mobile**  
Banner had 3 separate `<span>` elements in a `display:flex` row with no wrapping. On 390px each span was a flex item — layout broke badly.  
Fix: consolidated to one `.preview-banner-text` span + button, added `flex-wrap:wrap` and `@media (max-width:768px)` padding/font rules.  
Files: `tools/capture_snapshot.py` (template), all 17 `static/public/preview/**/*.html` files patched.

**9. German preview pages blank on mobile** *(same root cause as #1)*  
Preview HTML files had the `portal-offset-css` style tag with the unscoped `nav{top:38px!important;}`. Patched to `@media (min-width:769px){nav{top:38px!important;}}` in all 5 German preview files.  
Files: `static/public/preview/german/*.html`

**10. Guild Queue — 5-column kanban board clipped**  
At 390px, 5 columns were each ~78px wide — content unreadable. Added single-column rule for mobile.  
File: `static/public/preview/guild/queue.html`

**11. Guild Briefing preview — same as live (#2)**  
File: `static/public/preview/guild/briefing.html`

---

## Part 2 — Open UX Issues Noted (Not Fixed Yet)

**A. Schreiben — no confirmation on save**  
When a writing entry is saved, the count below increases but there's no toast or visual confirmation. Robert noted it and accepted it — UX polish for later.

**B. Schreiben — history rows not clickable**  
Clicking a past entry in the history list below does nothing. Robert said "maybe is ok" — leave as-is unless there's a plan for what clicking should do.

**C. Gespräche — no mobile shortcut to start a session from the persona list**  
On mobile the persona list shows name + role but no direct tap-to-session affordance. User taps persona → scrolls to detail → selects scene → taps "Sitzung starten." Multiple steps. Consider a long-press or swipe action on mobile.

---

## Part 3 — New Feature Request: Transcript Paste Flow

### Problem
After a KI-Sitzung, the session transcript auto-populates in the "Nach der Sitzung" textarea for analysis. But there are two adjacent workflows that have no clean path:

1. **External transcript → app**: Robert did sessions in Grok externally (copy prompt → paste into Grok on phone → conversation happens → want to bring the Grok output back for analysis). Currently there's no way to paste a raw Grok/Claude/external conversation into the analysis flow from mobile. The textarea in "Nach der Sitzung" requires scrolling to find and then paste into on a small screen.

2. **KI-Sitzung transcript → external model**: After a voice session, the transcript is in the textarea. The "Telegram öffnen" button copies the prompt and opens Telegram. But what Robert actually did was send the completed session transcript via Telegram to get it into the inbox for review. That flow works but isn't designed — it's a workaround.

### Proposed Feature: Transcript Paste Panel (Mobile-First)

Add a dedicated input path at the top of the "Nach der Sitzung" section that is clearly labeled and touch-friendly on mobile.

**Option A — Paste-first panel (recommended)**  
A single large textarea labeled "Transkript einfügen" that is:
- Full-width, 6-8 lines tall (easy to tap and paste into on mobile)
- Shown immediately when "Nach der Sitzung" is opened, before the existing analysis area
- Pre-populated with the KI-Sitzung transcript if one just ended
- Has a clear "Analysieren →" button below it (same as existing btn-analysieren)
- "Transkript löschen" to clear and start fresh

This makes the paste-to-analyze flow obvious from mobile without hunting for the textarea.

**Option B — Modal paste overlay**  
A "Transkript einfügen" button that opens a full-screen overlay with a large textarea on mobile. Good for pasting long text from clipboard. Less discoverable.

**Clipboard paste button (both options)**  
A "📋 Aus Zwischenablage einfügen" button that calls `navigator.clipboard.readText()` and fills the textarea in one tap. Requires HTTPS (app.minimoi.ai) — works on portal, not on direct localhost.

---

## Part 4 — Enhancement Ideas for Claude.ai Review

Please evaluate these and suggest which to build, which to defer, and what's missing. For each one you think is worth building, propose the simplest implementation approach.

### Gespräche Enhancements

**E1. Post-session summary card**  
After a session ends, show a compact card above "Nach der Sitzung" with: turns taken, personas, duration, and 1-line auto-summary. No LLM call needed — just from session state (sessionTurns, sessionTranscript.length, elapsed time).

**E2. Session history on mobile**  
"Letzte Sitzungen" section shows the last 5 sessions. On mobile after the detail panel stacks, this section is far down the page. Consider surfacing the last session as a "Continue" card at the top of the detail panel for the same persona.

**E3. Grok-in-browser on mobile**  
The "Sitzung starten" button launches a voice/text session against xAI Grok within the browser. On mobile this works but the voice recognition (Web Speech API) is less reliable. Consider: fall back to text-only mode automatically if `SpeechRecognition` is not available or fails, with a clear "Texteingabe" mode that makes the text input prominent.

**E4. Share session (iOS Share Sheet)**  
After a session, add a "Teilen" button that invokes the native iOS share sheet with the transcript as text. Uses `navigator.share({ text: transcript })` — supported on iOS Safari. More natural than "Telegram öffnen" on mobile.

**E5. Persona card preview on hover/tap**  
On desktop, persona list items show name + round indicator. On mobile they're list items you tap to select. Consider showing a brief "What will we practice?" tooltip or small expandable before committing to the full detail panel swap.

### German App General

**E6. Bottom nav active state visibility on mobile**  
The German bottom tab bar (fixed, bottom:0) shows the current page. On some pages (e.g., Gespräche after interaction) the page content sits close to the nav bar. Consider a bottom safe-area padding for devices with home indicator (iPhone 14+): `padding-bottom: env(safe-area-inset-bottom)`.

**E7. Swipe between German pages**  
On mobile, the nav is a bottom tab bar. Could support left/right swipe to move between tabs. Useful for Schreiben → Wörter → Gespräche flow without reaching for the nav.

**E8. Schreiben — save confirmation toast**  
Small 1.5s toast ("Gespeichert ✓") after `POST /api/write-save` succeeds. Robert noted the lack of feedback.

### Preview Pages

**E9. Preview pages → live access CTA prominence on mobile**  
The "Request live access →" button in the banner is the only CTA on preview pages. On mobile it can wrap to a second line and look small. Consider a sticky bottom CTA strip on mobile (fixed, above safe area) that stays visible while scrolling.

**E10. Preview page capture as part of deploy**  
Currently preview recapture is a manual step (`python3 tools/capture_snapshot.py`). Consider adding a note in the deploy checklist that mobile viewport should be spot-checked after each capture run, since several fixes in this session were caught post-deploy.

---

## Questions for Claude.ai

1. Are there any other standard mobile UX patterns we should be auditing for (e.g., touch target size 44×44px minimum, iOS safe areas, font size ≥16px to prevent Safari auto-zoom)?
2. For the transcript paste feature, which option (A or B) fits better with the current German app design language?
3. Is E4 (navigator.share) reliable enough on iOS Safari to ship without fallback?
4. What's the right order to build E1–E10 given German learning is Robert's daily use case and Gespräche is the most-used feature on mobile?
5. Should mobile testing be formalized as a checklist step before any Gespräche commit, or is the current test-on-device approach sufficient?
