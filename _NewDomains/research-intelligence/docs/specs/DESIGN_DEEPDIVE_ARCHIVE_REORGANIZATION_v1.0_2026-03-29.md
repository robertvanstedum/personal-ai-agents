# DESIGN_DEEPDIVE_ARCHIVE_REORGANIZATION_v1.0_2026-03-29.md
*Date: March 29, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code*
*File location: _NewDomains/research-intelligence/docs/specs/*

---

## Problem

The Deep Dive archive (`/curator/deep-dives`) is a flat chronological list mixing two fundamentally different artifact types:

- **Deep Dives** — single article analyses, generated from one saved article + interest statement
- **Deeper Dives** — multi-session research thread closings, sustained inquiry over days, two-agent synthesis + challenge essays

These are different in origin, depth, and value. A flat list treats them identically. As Deeper Dives accumulate, this becomes incoherent.

Additionally, the Deeper Dive generated from `strait-of-hormuz` is not currently visible in the archive index — it landed in the file system but isn't surfaced in the UI.

---

## Intent

Reorganize the archive into two clearly distinguished sections on the same page. Deeper Dives appear first and prominently — they represent the highest-value output in the system. Deep Dives follow as the existing archive.

The page should immediately communicate: *"Here is what sustained inquiry produced. Here is what individual articles produced."*

---

## Layout

### Page Header
- Title: "Deep Dive Archive" — keep as-is
- Subtitle: remove or replace with "Single articles and research threads, analyzed"
- Count: split into "X deeper dives · Y deep dives" rather than one combined count

---

### Section 1: Deeper Dives (top)

Only appears if at least one Deeper Dive exists. If none, section is hidden.

**Section header:** "Deeper Dives" — left-aligned, same typographic weight as existing headers

**Row anatomy (one per Deeper Dive):**
- Left: Topic name — styled as clickable link, normal weight
- Center-left: `3 sessions · 3 sources · 5 days` — small muted stats inline
- Center: Short excerpt from the revised framing section (1 sentence, truncated) — gives a sense of what the inquiry concluded
- Right: Date generated + "Read →" button
- Badge: Small "Deeper Dive" label in parchment/amber tone, consistent with the "Ready to close" badge on Research landing page

**Row style:**
- Slightly more prominent than Deep Dive rows — subtle left border in amber/parchment tone
- Not a card — still a row, just with slightly more visual weight

---

### Divider

A hairline rule with a subtle label: "Deep Dives" — separating the two sections clearly.

---

### Section 2: Deep Dives (below)

Existing archive table, unchanged. Date · Source · Title · Read Analysis →

No changes to this section except the count in the header reflects only Deep Dives, not total.

---

## What Changes in the Index

The archive index (`interests/2026/deep-dives/index.html`) currently renders one flat list. Changes needed:

1. **Detect artifact type** — Deeper Dives are identified by filename pattern `{topic}-deeper-dive-{NNN}.md` or by a metadata field in the file header. Parse accordingly.

2. **Split into two arrays** — `deeperDives[]` and `deepDives[]` — render separately.

3. **Deeper Dive row** — pull the following from the file:
   - Topic name (from filename or header)
   - Session count, source count, cost (from header metadata line)
   - Generated date (from header)
   - Revised framing excerpt (from Section 4 of the Challenger output — first sentence)

4. **Deep Dive row** — existing rendering, unchanged.

---

## Deeper Dive Detail Page

When user clicks "Read →" on a Deeper Dive row, they should reach a rendered version of the essay. This may already exist at `/research/deeper-dive-result/{topic}-deeper-dive-{NNN}` — if so, link there. If not, render the markdown file directly, same as existing Deep Dive detail pages.

**The detail page should display:**
- Full essay (Synthesizer + Challenger sections)
- Header metadata (sessions, sources, cost, date, original motivation)
- Bibliography
- A "Back to Archive" link

---

## Visual Style

- Maintain parchment palette throughout
- Deeper Dive badge: same amber/warm tone as "Ready to close" badge on Research dashboard — visual consistency across the system
- No cards — rows only, consistent with the redesigned Research landing page
- Deeper Dive rows slightly more prominent than Deep Dive rows but not dramatically so — this is an archive, not a dashboard

---

## Acceptance Criteria

- [ ] Page shows "Deeper Dives" section at top when at least one exists
- [ ] `strait-of-hormuz` Deeper Dive appears in the Deeper Dives section
- [ ] Deeper Dive rows show: topic, stats, date, excerpt, Read → link
- [ ] Deeper Dive badge visible on each row
- [ ] Hairline divider separates Deeper Dives from Deep Dives
- [ ] Deep Dives section unchanged except for count in header
- [ ] Page count reflects split: "X deeper dives · Y deep dives"
- [ ] Clicking Read → on a Deeper Dive reaches a readable detail page
- [ ] No Deeper Dives appear in the Deep Dives section

---

## Out of Scope

- Search or filtering within the archive
- Tagging or categorizing Deep Dives by topic
- Any changes to Deep Dive generation flow
- Mobile layout optimization
