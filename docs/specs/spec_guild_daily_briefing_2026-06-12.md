# Spec — Guild Daily Briefing (`/guild` Phase 1: the page)
*mini-moi · Guild*
*Created: 2026-06-12 22:25 CDT — Claude.ai*
*For: Claude Code*
*Design reference: `docs/GUILD.md` — Daily Briefing section (four-part
mockup: Systems/Career/Build/Ahead, Telegram timing 7:00/7:30/8:30)*

---

## What this is

`/guild` currently exists as the v0 page from the nav restructure — Row 1 +
Row 2 (`CAREER FOCUS · BUILD`) + two placeholder links. This spec replaces
the placeholder body with the four-section briefing already designed in
`docs/GUILD.md`. **This is the page becoming what it was always meant to
be**, not a new build.

What's changed since `GUILD.md` was written: `guild.design_log` and
`pipeline.items` — the data sources that design anticipated — now exist and
are live. This spec is primarily an integration job: wire the designed
layout to real data.

**This is the unifying view** — right now Career Focus and Build are
separate worlds, one "← Guild" click apart. The briefing is where both
show up together: blocked builds, active interviews, system health, what's
coming next — one glance.

---

## Scope — this spec is the page only

- **In scope:** the four sections rendered on `/guild` with live data,
  replacing the current placeholder body.
- **Out of scope, separate follow-up:** the Telegram sends (7:00/7:30/8:30).
  Note that 7:30 (CoS Loop F, build-discipline staleness check) already
  exists independently — the open question for a later spec is just 7:00
  and 8:30, and whether their content overlaps with what this page shows.
- **Unchanged:** Row 1, Row 2 (`CAREER FOCUS · BUILD` stays — it's the
  "jump to a section" nav, still correct on a homepage).

---

## What to build

Pull each section's exact layout, fields, and copy from `docs/GUILD.md`'s
Daily Briefing mockup — that's the source of truth for *what it should say*.
This spec covers *where the data comes from*:

- **Systems** — current health of the three domains (Curator, German,
  Guild) and the background loops (A/B/C/D). Check how the Operations
  agent (port 8768) currently exposes status — `/status` endpoint, shared
  file, or DB table — and use whatever's actually there.

- **Career** — query `pipeline.items` (context = career): counts by status
  (Suggested / Reviewing / Interview / etc.), with anything in Interview or
  Recently Closed surfaced specifically, matching the mockup's level of
  detail.

- **Build** — query `guild.design_log`: counts by status. Any `blocked`
  item surfaced with its `blocked_reason` — this is the "needs Robert"
  signal, make it visually distinct (matches Queue's red Blocked column
  treatment).

- **Ahead** — "what's coming." At minimum: next scheduled fire times for
  Loops A/B/C/D, and any known key dates (e.g. contract end Aug 3, Loop
  B/C/D first-fire Sunday 6/15 if still upcoming). If `docs/GUILD.md`'s
  mockup also calls for roadmap items, the top line or two from
  `docs/ROADMAP.md`'s "Verify first" and "Next" sections is a natural fit —
  but follow the mockup's actual design over this suggestion if they differ.

If anything in `GUILD.md`'s mockup assumes a data source that *doesn't*
exist yet (i.e., something beyond `design_log`/`pipeline.items`/Operations
status), flag it rather than building a placeholder — better to know what's
still missing than ship a section with fake data.

---

## Definition of Done

- [ ] `/guild` renders all four sections (Systems/Career/Build/Ahead) per
      `docs/GUILD.md`'s mockup, with live data — no static/placeholder
      content
- [ ] Career section reflects actual `pipeline.items` state
- [ ] Build section reflects actual `design_log` state; `blocked` items
      visually distinct with their reason shown
- [ ] Systems section reflects actual current system status
- [ ] Ahead section shows real upcoming items (loop schedule + key dates,
      possibly roadmap)
- [ ] Row 1 + Row 2 unchanged — `CAREER FOCUS · BUILD` links still work
      from the briefing
- [ ] Any mockup element requiring a data source that doesn't exist yet is
      flagged, not faked
- [ ] Visual sign-off: Robert confirms `/guild` reads as a real homepage —
      a useful "what's the state of everything" glance

---

## Commit

```bash
git add domains/guild/templates/ domains/guild/routes/ domains/guild/services/
git commit -m "feat: Guild Daily Briefing — /guild becomes the four-section
homepage (Systems/Career/Build/Ahead), live data from design_log and
pipeline.items per docs/GUILD.md design"
git push origin main
```

---

*Spec · Guild · 2026-06-12*
