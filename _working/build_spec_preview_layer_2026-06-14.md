# Build Spec — minimoi.ai Preview Layer + Mein Deutsch Animation
*mini-moi · Guild*
*Created: 2026-06-14 — Claude.ai*
*Design input: Grok (animation), Claude Code (technical investigation)*
*Status: spec_ready*
*For: Claude Code*
*Depends on: design_preview_guest_access_2026-06-13.md (design rationale)*

---

## Overview

Build the always-on preview layer for minimoi.ai in three workstreams,
sequenced below. This is what a recruiter or interviewer sees when they
click through from LinkedIn or GitHub — the site needs to be up, real, and
impressive without requiring Robert to be online.

**Workstream order:**
1. Snapshot capture + `/preview` routing + admin modal (core — always-on)
2. Mein Deutsch animated walkthrough (the standout demo piece)
3. Guest access audit + entry point (verify existing plumbing works)

---

## Workstream 1 — Snapshot Capture + `/preview` Routing

### Decision: `/preview` path, not subdomain

Serve preview at `minimoi.ai/preview/...` from Flask's static directory.
No DNS change, no separate host. Flask serves the static files whether or
not the backend is running (confirm this is true with the current setup —
if Flask must be up to serve static files, Cloudflare Pages for `/preview`
is the fallback, but the `/preview` path stays the same either way).

### Capture script: `tools/capture_snapshot.py`

Use **Playwright** (headless Chromium) not simple HTTP requests. Rationale:
several pages (Guild Daily Briefing, Build Queue) render state via JS after
page load — simple requests would capture incomplete HTML. Playwright
captures the fully-rendered DOM.

Script behavior:
1. Authenticates as owner (reads credentials from `.env`, never hardcoded)
2. Visits each page in the manifest (see below), waits for JS render to
   settle (`networkidle`)
3. Saves rendered HTML + inlines CSS to `static/preview/<domain>/<page>.html`
4. Injects snapshot banner into each saved page (see banner spec below)
5. Replaces all admin-tab `<a>` href links with `data-admin-blocked="true"`
   attribute (triggers modal — see admin modal spec below)
6. Replaces all form POSTs and write-action buttons with `disabled` + a
   `data-preview-disabled="true"` attribute
7. For Career Focus: applies Option A — excludes pipeline table, shows only
   aggregate counts ("12 opportunities, 1 interview") — see rationale in
   design doc
8. Writes `static/preview/manifest.json`:
   ```json
   {
     "captured_at": "2026-06-14T07:30:00Z",
     "pages": [...],
     "failures": [...]
   }
   ```
9. Prints summary to stdout

**Run command:**
```bash
python tools/capture_snapshot.py
```

**Pages to capture (manifest):**

| Domain | Page | URL |
|--------|------|-----|
| Curator | Daily Briefing | `/curator` |
| Curator | Deeper Dive (pick most recent) | `/curator/dive/<id>` |
| Curator | Archive | `/curator/archive` |
| Mein Deutsch | Landing/Lesen | `/german` |
| Mein Deutsch | Gespräche | `/german/gesprache` |
| Mein Deutsch | Wörter | `/german/worter` |
| Mein Deutsch | Schreiben | `/german/schreiben` |
| Mein Deutsch | Archiv | `/german/archiv` |
| Guild | Daily Briefing | `/guild` |
| Guild | Build Log | `/guild/build` |
| Guild | Queue | `/guild/build/queue` |
| Guild | Roadmap | `/guild/build/roadmap` |

**Career Focus** (`/guild/career`) — captured but pipeline table replaced
with aggregate view before save (see Option A above).

### Snapshot banner

Injected at top of `<body>` on every preview page. Non-dismissable.

```html
<div class="preview-banner">
  <span>Preview snapshot — </span>
  <span class="preview-date">Captured June 2026</span>
  <span>. Data is real but frozen. </span>
  <a href="/contact" class="preview-request-link">Request guest access →</a>
</div>
```

CSS: thin bar, `background: #2A1F14`, `color: #F5F0E8`, amber accent on
the link (`#C68A5E`). `position: sticky; top: 0; z-index: 999`. Does not
obscure nav (nav sits below it).

The date ("June 2026") is written dynamically by the capture script from
`captured_at` in the manifest.

### Admin tab modal

Any element with `data-admin-blocked="true"` triggers this modal on click:

```
┌─────────────────────────────────────────────┐
│  Admin — not available in preview           │
│                                             │
│  This section is for authenticated users.  │
│  Request guest access to see it live.      │
│                                             │
│  [Request access →]          [Close]        │
└─────────────────────────────────────────────┘
```

"Request access →" links to `/contact` (or Robert's chosen entry point —
see Workstream 3). "Close" dismisses. Dark overlay behind modal.
CSS only — no framework dependency.

### Preview navigation

Each preview page keeps the real site nav but with:
- All links pointing to other preview pages (`/preview/...`) not live pages
- "Dashboard →" button in the nav replaced with:
  `<a href="/contact" class="nav-dashboard-preview">Request Access →</a>`
- Preview banner always visible above nav

The capture script handles link rewriting — replace all internal hrefs
with their `/preview/...` equivalent, leave external links (GitHub,
LinkedIn) untouched.

### Refresh workflow

Robert runs `python tools/capture_snapshot.py` monthly or after a major
feature ships. The script overwrites `static/preview/` in place. Git-commit
the updated static files and push — Cloudflare tunnel picks them up on
next request.

A note in `docs/ROADMAP.md` under Operations: "Monthly preview refresh —
run `capture_snapshot.py`, commit + push."

---

## Workstream 2 — Mein Deutsch Animated Walkthrough

*Design: Grok + Claude.ai. Implemented as CSS + JS, no video files,
fully static.*

### Placement

Embedded in the Gespräche tab of the Mein Deutsch preview section — appears
above the static snapshot of the Gespräche page. The animation is a
self-contained `<div class="md-walkthrough">` block; the static snapshot
sits below it as supporting context.

### Trigger

**Auto-play on scroll into view** using Intersection Observer API. Fires
once when the `.md-walkthrough` div enters the viewport. Does not re-fire
on scroll. "↺ Replay" button appears on completion and resets + replays
from Step 1 on click.

A subtle "▶ Watch demo" button sits above the animation container — visible
before the animation starts, disappears once it fires. This acts as a
fallback for desktop visitors where the section may already be in view on
load (Intersection Observer may not fire for content visible on initial
render in all browsers).

A subtle "▶ Watch it work" label pulses gently (amber, 1s fade cycle)
before the animation starts, so visitors who scroll fast know something
is about to play.

### Total runtime: 40–50 seconds (updated — 6 steps reflecting real feature)

### Animation steps (updated — reflects real feature as shipped)

**Step 1 — Persona selection (6–7s)**
- Persona list visible, Maria card lifts + amber glow
- Suggestion pills appear: Zahlen, bitte. / Was empfehlen Sie? / Ich hätte gerne...
- MODELL selector shows "Grok"

**Step 2 — Session start (3–4s)**
- [▶ Sitzung starten] button pulses amber, clicked
- Immediate: "Sitzung beginnt." audio cue (show sound wave animation)
- Pre-session content collapses, session area expands
- Status: 🟢 Sitzung läuft · Maria · grok

**Step 3 — Maria speaks (8–10s)**
- "Maria:" label appears, text types in as if TTS is playing
- Small speaker/audio wave icon pulses next to Maria's name while
  her line appears: "Guten Morgen! Was darf ich Ihnen bringen?"
- Status changes to "Zuhört..." after Maria finishes

**Step 4 — Robert speaks (8–10s)**
- Mic indicator pulses (VAD listening)
- "Sie:" label, text types in: "Ich hätte gerne einen kleinen Brauner."
- Brief "Verarbeitet..." flash, then Maria responds:
  "Einen kleinen Brauner — sehr gerne. Sonst noch etwas?"
- Note: show the ⚠ mistake moment — "kleinen" correct here,
  but show the Anki card connection in Step 6

**Step 5 — Session end (4–5s)**
- [■ Beenden] clicked
- Session panel collapses
- Transcript auto-expands below: full speaker-labelled exchange visible
- Status: "Sitzung beendet ✓ — Transkript bereit"
- [Analysieren] button pulses

**Step 6 — Analysis result (10–12s)**
- [Analysieren] clicked, "Analysiere..." brief pause
- FEEDBACK section fades in (parchment card, amber left border):
  "Natural café register, appropriate use of formal Sie..."
- SCHWÄCHEN expands: one error item highlighted
- 3 Anki cards fan in:
  [einen kleinen Brauner | a small Brauner (coffee)]
  [Zahlen, bitte | The bill, please]
  [Was empfehlen Sie? | What do you recommend?]
- Final frame holds 2s, "↺ Replay" appears

### CSS/JS constraints

- No external libraries (pure CSS transitions + vanilla JS)
- No video files, no canvas, no WebGL
- Self-contained in one `<script>` block + one `<style>` block in the page
- Works without a running backend (static HTML)
- Respects `prefers-reduced-motion` — if set, skip typing animation, show
  steps as instant fades instead

### Sample conversation

The 5-turn exchange above uses a real mistake ("ein Kaffee") and real
vocabulary from Mein Deutsch sessions. If a better real-session excerpt
exists in the transcripts, Claude Code can substitute — criteria: 4-5
turns, at least one natural mistake, one scene-setting question from Maria,
ends on a complete-feeling exchange.

---

## Workstream 3 — Guest Access Audit + Entry Point

### Audit checklist (investigate + fix if broken)

- [ ] `/register` form works end-to-end
- [ ] Telegram alert fires to Robert on signup
- [ ] What happens after Robert receives the alert? Is there a `/approve`
      route, or does Robert manually update the DB? Document the current
      mechanism; if it's manual DB, add a simple `/admin/approve/<user_id>`
      route
- [ ] What does a guest session actually see? `/guest/briefing` — is this
      real Curator content? Is it read-only enforced at the route level, or
      just by convention?
- [ ] Guest session revocation — is there a clean way to remove access?
      If not, add `is_active` flag to guest accounts + admin toggle

### Entry point

The preview snapshot banner contains:
```
Request live access →
```

This links to `/contact` — a simple contact page (or anchor on the landing
page) with:
- Robert's LinkedIn DM link
- A note that guest access is available on request

**Do not link to `/register` directly.** Keep guest access on-request-only.
No public self-service signup.

Optional (low priority): a lightweight request form on `/contact`
(name, email, "why you're interested") that sends a Telegram message to
Robert without granting access. Robert responds personally. Flag as optional
scope — implement only if Robert confirms he wants it.

---

## Definition of Done

**Workstream 1:**
- [ ] `tools/capture_snapshot.py` runs cleanly, captures all pages in
      manifest, writes `static/preview/` and `manifest.json`
- [ ] `/preview/...` routes serve static files correctly — accessible
      without backend running (or via Cloudflare Pages if Flask static
      serving doesn't meet this requirement)
- [ ] Snapshot banner appears on every preview page, date accurate, link
      functional
- [ ] Admin tabs trigger modal on click, not navigation
- [ ] All write-action buttons/forms disabled in preview
- [ ] Career Focus shows aggregate only (Option A)
- [ ] Internal links point to `/preview/...` equivalents, external links
      (GitHub, LinkedIn) untouched
- [ ] "Dashboard →" replaced with "Request Access →" in preview nav
- [ ] Robert runs script, commits output, confirms live at minimoi.ai/preview

**Workstream 2:**
- [ ] Animation embedded in Mein Deutsch Gespräche preview page
- [ ] Auto-plays on scroll into view (Intersection Observer)
- [ ] All 5 steps animate correctly at correct timing
- [ ] "Send to Telegram" button visible but not animated
- [ ] "↺ Replay" appears on completion, replays correctly
- [ ] `prefers-reduced-motion` respected
- [ ] Works in latest Chrome, Firefox, Safari (static HTML, no backend)
- [ ] Robert watches it and approves

**Workstream 3:**
- [ ] Audit findings documented (what works, what needs fixing)
- [ ] Any broken guest access flows fixed
- [ ] `/contact` page or anchor exists, linked from preview banner
- [ ] Robert confirms guest access works end-to-end with a test account

---

## Commit

```bash
# Workstream 1
git add tools/capture_snapshot.py static/preview/ docs/ROADMAP.md
git commit -m "feat: minimoi.ai always-on preview layer

Snapshot capture script (Playwright), /preview routing, snapshot banner,
admin modal, write-action disabling, Career Focus Option A (aggregate only).
Internal links rewritten to /preview/..., external links preserved.
Mein Deutsch animation placeholder in Gespräche tab."

# Workstream 2
git add static/preview/german/gesprache.html
git commit -m "feat: Mein Deutsch animated walkthrough in preview

CSS/JS animation, 45-55s, 5 steps: persona selection, scene selection,
voice conversation (5 turns, real mistake visible), submission, AI review
+ 3 Anki cards. Auto-play on scroll, Replay button, prefers-reduced-motion
respected. Send to Telegram visible but not animated (Phase 1)."

# Workstream 3
git add <guest access files>
git commit -m "fix/feat: guest access audit + /contact entry point

[describe findings and fixes]"
```

---

## Notes for Grok (Phase 2+ deferred items)

- Animate the Telegram submission path — Phase 3, optional, not in scope
- "Send to Telegram" visible but static in Phase 1 (per Grok spec)
- If Robert wants a video version of the walkthrough as an alternative
  (e.g., embedded on LinkedIn), the animation above can be screen-recorded
  from the live preview page — no separate production needed

---

*Build Spec · Preview Layer + Guest Access · 2026-06-14*
