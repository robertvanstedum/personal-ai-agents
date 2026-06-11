# Handoff — Curator IA Phase 4 Polish + Phase 5 Archive
*mini-moi · personal-ai-agents · Curator domain*

- **Authored:** 2026-06-07 06:22 CDT (11:22 UTC) — Claude.ai, design session
- **Status:** READY TO BUILD — approved by Robert
- **Branch:** `feat/curator-ia` (same as Phase 1–4)
- **Prerequisite:** Phase 4 complete and signed off (Desk renamed, portal sidebar removed, threads grouped, /research redirected). Baseline screenshots at `docs/screenshots/curator/current/` (2026-06-07 07:58).
- **Scope:** Two parts. Phase 4 Polish — four targeted consistency fixes plus a Reading Room queue bar. Phase 5 — wire the Archive page, replacing the placeholder.

---

## PART A — Phase 4 Polish

Four targeted fixes to align text colors, background colors, and action styling across pages.

### Fix 1 — Reading Room background

**Problem:** The Reading Room content area renders with a near-white background, noticeably lighter than the parchment (`#F5F0E8`) the other pages use.

**Fix:** Find whatever CSS rule or template container is overriding the background on the Reading Room page and remove or correct it. The content area should use the site-wide parchment background, matching Leanings, Scans & Dives, and the Desk.

**Test:** Open Reading Room and Leanings side by side. Background should be visually identical.

---

### Fix 2 — Leanings card and form backgrounds

**Problem:** The three leaning item boxes and the NEW LEANING form box render with a white or near-white background. They should use the card background color (`#EDE7DC`), matching the landing page cards.

**Fix:** In the Leanings template, set `background: #EDE7DC` (or the CSS variable equivalent) on:
- The NEW LEANING form container
- Each leaning item row/box

**Test:** On the Leanings page, the form and item boxes should read as warm card-coloured, not white.

---

### Fix 3 — Desk active thread names

**Problem:** Thread names in the ACTIVE section are rendering in the accent colour (`#C68A5E`). Accent should be reserved for action links only. Thread names are the primary content label, not a call-to-action.

**Fix:** In the Desk template, change the thread name text colour to primary dark (`#2A1F14`). The action links on the right (`Generate Deeper Dive →`, `View Deeper Dive →`) stay in accent. Not-yet-run thread names should remain in muted secondary (`#8A7060`) as they are.

**Test:** On the Desk, active thread names are dark, actions are accent-coloured, never-run names are muted.

---

### Fix 4 — Reading Room table action links

**Problem:** Reading Room actions (Read, Start Dive, View Scan) use styled pill buttons. Every other page that has action links (Desk, Scans & Dives) uses plain text links in accent colour with `→` arrows. The pill buttons are heavier and visually inconsistent.

**Fix:** Replace the styled pill buttons in the Reading Room table with plain text links in accent colour, matching the text-link pattern used elsewhere:
- `Read` → `Read →`
- `Start Dive` → `Start dive →`
- `View Scan` → `View scan →`

Keep the same actions; change only the visual treatment. Existing click handlers and routes stay unchanged.

**Test:** Reading Room action column shows text links in accent colour, no pill buttons. Actions still work.

---

### Feature — Reading Room queue bar

Add a **reading queue bar** between the meta-line ("20 articles saved · date range") and the filter row. This gives a sequential reading affordance so the user can work through their saved articles without returning to the table between each one.

**Component structure:**

```
[Reading queue]  [Article title — truncated]        [1 of N]  [← prev]  [Read →]  [next →]
                 [Source · Date]
```

**Behaviour:**
- On page load, queue position defaults to 1 (most recent article in the current filtered list).
- `Read →` opens the article external URL in a new tab. Position does not advance automatically — user controls pacing.
- `← prev` and `next →` move through the filtered list by one. When at position 1, `← prev` is disabled. When at the last item, `next →` is disabled.
- The queue tracks the **current filter state** — if the user filters to "Liked", the queue moves through liked articles only. On filter change, position resets to 1.
- Persist queue position in a URL param (`?q=N`) so refresh holds position and the link is shareable. Fall back to 1 if param is absent or out of range.

**Styling:**
- Outer container: same border/border-radius/padding as the NEW LEANING form on Leanings.
- "Reading queue" label: mono, 10px, uppercase, muted (`#A89880`), left-aligned.
- Article title: 13px, font-weight 500, truncated with ellipsis.
- Source/date: 11px, muted, below the title.
- Counter ("1 of N"): 12px, muted, right of the title block.
- `← prev` and `next →`: unstyled/outline buttons (same secondary button style used elsewhere).
- `Read →`: primary button (same accent-filled button style as ADD → on Leanings).

**Test:**
- Queue bar appears on the Reading Room page above the filter row.
- `Read →` opens the correct article URL in a new tab.
- `next →` advances position and updates the displayed title/source/counter.
- Filter change resets position to 1 and updates the title shown.
- URL param `?q=3` on page load starts at position 3.
- At position 1, `← prev` is disabled. At position N, `next →` is disabled.

---

## PART B — Phase 5 Archive

Replace the current Archive placeholder with a live page. Remove the "COMING — PHASE 5" kicker and the "← BACK TO CURATOR" link.

### Page structure

**Heading:** `Archive` (Georgia serif, 26px, weight 400 — matching other page headings)
**Subtitle:** `Everything kept, and searchable over time.`
**Search bar:** Full-width text input immediately below the subtitle. Placeholder: `Search across all archives…`

**Five sections below the search bar, in this order:**

| Section | Data source | Default rows shown |
|---|---|---|
| Daily editions | Historical briefing files | 3 |
| Sources | Saved articles JSON (same as Reading Room) | 3 |
| Scans | Scans JSON (same as Scans & Dives) | 3 |
| Dives | Dives/threads JSON (same as Scans & Dives) | 3 |
| Observations | Observations JSON | 3 |

Each section follows this pattern:
```
SECTION NAME   N                        show all →
────────────────────────────────────────────────────
[row 1]
[row 2]
[row 3]
+ N more [section name]
```

- Section kicker: mono, 10px, uppercase, muted. Count is dimmer (opacity 0.6) beside the name.
- `show all →`: accent text link, right-aligned on the same line as the kicker.
- Row border: hairline bottom (`0.5px solid #C4B49A`), row padding `10px 0`.
- `+ N more [section name]`: 12px, muted, in the final row — clicking this expands the section in-place (no new route needed in Phase 5).

### Row formats

**Daily editions:**
```
Mon, Jun 7   20 articles · 5 topics active   View →
```
- Date left (11px, muted, fixed 100px), summary text middle (13px), `View →` link right (12px, accent).
- `View →` links to the briefing HTML for that date (e.g. `/daily/2026-06-07`).
- **Conditional:** Check whether historical editions are archived as dated files (e.g. `curator_briefing_YYYY-MM-DD.html`). If yes, list all available. If only the current static `curator_briefing.html` exists, show only today's edition and add a `TODO` comment: `# FUTURE: archive daily editions as dated files`.

**Sources** (read-only — no action buttons):
```
Jun 3, 2026   China's AI Governance Offensive…   War on the Rocks
```
- Date (11px, muted, 100px), title bold (13px, weight 500, truncated), source (11px, muted, right).
- No Like / Save / Dive / Scan buttons — this is the permanent record.

**Scans:**
```
Jun 3, 2026   Does Israel have nukes?…   Al Jazeera
```
- Same row pattern as Sources (date | title | source).

**Dives:**
```
[THREAD]   Hellscape Taiwan Porcupine   2 sessions · $0.18 · Mar 29
```
- THREAD badge (same as Scans & Dives page), title (13px, weight 500), meta right (11px, muted).

**Observations:**
```
May 31, 2026   5 responses saved   View →
Jun 7, 2026    Weekly connections updated   View →
```
- Date (11px, muted, 100px), summary text (13px, muted), `View →` link right (12px, accent).
- `View →` links to `/leanings?date=YYYY-MM-DD` to open the Leanings page at that date's observations.

### Search behaviour

Client-side filter, JavaScript on keyup. No server-side query needed at current data volume.

When the search input contains text:
- Hide all five sections.
- Show a unified results list below the search bar. Header: `Results for "[query]"` (12px, muted).
- Each result row: `TYPE` badge (SOURCE / SCAN / DIVE / OBSERVATION / EDITION — mono, 9px, uppercase, bordered) + title (13px) + date and source (11px, muted, right).
- Ordered by date descending.
- If no results: `No results for "[query]".` (13px, muted, centred).

When search is cleared (input empty):
- Hide results list. Sections reappear.

The filter is a simple case-insensitive substring match against title, source name, and date string. Extend later if needed.

### Template changes

- Remove the `COMING — PHASE 5` kicker block.
- Remove the `← BACK TO CURATOR` link (top nav handles navigation).
- Heading changes from all-caps styled `ARCHIVE` to standard page heading style (matching `The Desk`, `Leanings`, `Scans & Dives`).
- Reading Room's `View full archive →` footer link already points to `/archive` — confirm it still works after the template change.

---

## Definition of done

**Phase 4 Polish:**
- [ ] Reading Room background is parchment, visually matching Leanings and Desk.
- [ ] Leanings leaning item boxes and NEW LEANING form have `#EDE7DC` background (warm card, not white).
- [ ] Desk active thread names are dark primary text; accent reserved for action links only.
- [ ] Reading Room table actions are text links in accent (`Read →`, `Start dive →`, `View scan →`), no pill buttons.
- [ ] Reading Room queue bar appears above filter row; shows current article title, source, date, and `1 of N` counter.
- [ ] Queue bar `Read →` opens the correct article in a new tab.
- [ ] Queue `next →` and `← prev` navigate the filtered list; disable at boundaries.
- [ ] Filter change resets queue to position 1.
- [ ] URL param `?q=N` sets queue position on load.

**Phase 5 Archive:**
- [ ] Archive page loads without placeholder ("COMING — PHASE 5" and "← BACK TO CURATOR" removed).
- [ ] All five sections render with live data.
- [ ] Daily editions: if dated files exist, lists them; if not, shows current edition with a TODO comment.
- [ ] Sources render read-only (no action buttons).
- [ ] `show all →` links expand their section in-place.
- [ ] Search bar filters across all five sections on keyup; unified results list appears; results clear on empty input.
- [ ] `View →` links on Observations rows navigate to `/leanings?date=YYYY-MM-DD`.
- [ ] `View full archive →` on Reading Room still resolves correctly to `/archive`.
- [ ] `python3 tools/health_check.py` passes.
- [ ] Re-run `python3 tools/capture_screenshots.py` — Archive screenshot shows live sections, not placeholder.
- [ ] **Robert visual sign-off before push to main.**

If any step cannot be tested, say so explicitly — do not commit and claim done.

---

## Guardrails

- `main` is production — stays stable. Work on `feat/curator-ia`.
- JSON is source of truth. No DB dependency for any of this.
- Daily briefing pipeline and Telegram delivery must be unaffected.
- Do Phase 4 Polish fixes first (low risk, targeted) → test → then Phase 5 (new page, higher surface area) → test.

---

## Deferred (not this phase)

- Phase 6: Hard cleanup — delete `/observations`, `/priorities`, `/research` template and redirect stubs.
- Archive section-specific routes (`/archive/scans`, `/archive/daily` etc.) — add when individual sections grow enough to warrant their own browse page.
- Historical daily edition archiving — if only the current static `curator_briefing.html` exists, the Daily editions section ships with a single row and a TODO. Archiving past editions is a separate pipeline task.
- Reading Room: "Mark as done" / processed state — a future improvement to track which articles have been actioned.
- Server-side search — upgrade from client-side filter when data volume exceeds a few hundred items per section.

---

## Suggested commit sequence

```
# Phase 4 Polish fixes
git add templates/...
git commit -m "phase4: fix Reading Room background (parchment throughout)"

git add templates/...
git commit -m "phase4: fix Leanings card and form backgrounds (#EDE7DC)"

git add templates/...
git commit -m "phase4: fix Desk thread name colors (dark text, accent actions only)"

git add templates/...
git commit -m "phase4: convert Reading Room action buttons to text links"

git add templates/... static/...
git commit -m "phase4: add reading queue bar to Reading Room"

# Phase 5 Archive
git add templates/... routes/... static/...
git commit -m "phase5: wire Archive page — sections + client-side search, remove placeholder"

# After screenshots re-run and Robert sign-off
git add docs/screenshots/...
git commit -m "phase4+5: update screenshot baseline (polish applied, Archive live)"
```

---

*Curator IA Phase 4 Polish + Phase 5 · feat/curator-ia · ready to build · 2026-06-07 06:22 CDT*
