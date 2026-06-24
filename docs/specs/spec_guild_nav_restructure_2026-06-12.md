# Spec — Guild Navigation Restructure
*mini-moi · Guild*
*Created: 2026-06-12 12:49 CDT — Claude.ai*
*For: Claude Code*

---

## What's wrong now

Guild has accumulated **three stacked full-width nav rows**:

```
mini-moi | Curator | German | Guild
CAREER FOCUS · BUILD
POSITIONS · ACTIVE PIPELINE        (or BUILD LOG · QUEUE)
```

And on top of that, the two pages within each section are **rendering
together on one page** instead of as separate views — Positions table +
Active Pipeline kanban both show on `/guild/career#pipeline`; Build Log
table + Queue kanban both show on `/guild/build?status=done#log`. The
secondary nav exists as labels, but clicking between them doesn't swap
content — both are always present. The `#pipeline` / `#log` fragments in
those URLs are workarounds for this.

---

## Target structure — 2 rows, always

**Row 1 — unchanged:** `mini-moi | Curator | German | Guild`

**Row 2 — context-sensitive, one of two states:**

- **At `/guild` (new page, see below):**
  `CAREER FOCUS · BUILD` — two links to the sections' default pages.

- **Inside a section (any of the 4 pages below):**
  `← Guild    PAGE_A · PAGE_B` — back-link plus the *current section's* two
  pages, with the active one styled as current. The *other* Guild section
  isn't shown at all here — to reach it, go `← Guild` then pick it.

  - In Career Focus: `← Guild    POSITIONS · ACTIVE PIPELINE`
  - In Build: `← Guild    BUILD LOG · QUEUE`

This is the same shared nav partial across all pages — it just needs to know
two things: which Guild section it's in, and which of that section's two
pages is current.

---

## The five pages

1. **`/guild`** — new. Row 1 + Row 2 (`CAREER FOCUS · BUILD`), plus a
   minimal body: just two links/cards, "Career Focus →" and "Build →",
   pointing at `/guild/career` and `/guild/build`. **Keep this minimal —
   it's v0 of the Daily Briefing design in `docs/GUILD.md`, which will
   *enhance* this page later, not replace it.** Don't build briefing
   features here now.

2. **`/guild/career`** (Positions) — renders **only** the Positions table
   (the job-listing rows with star/Employment/status/date columns). Remove
   the Active Pipeline kanban section from this page if present.

3. **`/guild/career/active`** (Active Pipeline) — renders **only** the
   kanban (Starred Applied / Reviewing / Interview / Recently Closed). Remove
   the Positions table from this page if present.

4. **`/guild/build`** (Build Log) — renders **only** the Build Log table
   with status filters. Remove the Queue kanban section if present.

5. **`/guild/build/queue`** (Queue) — renders **only** the Queue kanban
   (Spec Ready / In Build / Blocked / Recently Done). Remove the Build Log
   table if present.

Each is a genuinely separate route/view — not a client-side tab toggle over
one template. The `#pipeline` / `#log` URL fragments and `?status=done`
workaround can be removed once each page stands on its own.

---

## Definition of Done

- [ ] `/guild` exists: Row 1 + Row 2 (`CAREER FOCUS · BUILD` as links) +
      minimal two-link body. Nothing more.
- [ ] Shared nav partial updated: context-aware, renders
      `← Guild   PAGE_A · PAGE_B` on all four section pages, current page
      indicated
- [ ] `/guild/career` shows Positions table only
- [ ] `/guild/career/active` shows Active Pipeline kanban only
- [ ] `/guild/build` shows Build Log table only
- [ ] `/guild/build/queue` shows Queue kanban only
- [ ] Old standalone `CAREER FOCUS · BUILD` row removed from the four
      section pages (appears only at `/guild`)
- [ ] Old standalone `POSITIONS · ACTIVE PIPELINE` / `BUILD LOG · QUEUE` row
      removed as its own row — folded into Row 2 alongside `← Guild`
- [ ] Two rows total on every Guild page, no exceptions
- [ ] Visual sign-off: Robert clicks through all five pages, confirms
      correct 2-row nav, correct single-view content on each, and that
      `← Guild` returns to `/guild` from any of the four section pages

---

## Commit

```bash
git add domains/guild/templates/
git commit -m "fix: Guild nav restructure — 2 rows, separate pages, /guild index

- New /guild minimal index (v0 of Daily Briefing)
- Shared nav: Row 2 is either 'CAREER FOCUS · BUILD' (at /guild) or
  '← Guild  PAGE_A · PAGE_B' (inside a section)
- Split Positions/Active Pipeline and Build Log/Queue back into separate
  views — each route renders only its own content
- Removes #pipeline / #log / ?status=done workarounds"
git push origin main
```

---

*Spec · Guild · 2026-06-12*
