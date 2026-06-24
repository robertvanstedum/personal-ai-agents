# Spec — Guild Type Scale (Revision): Variables, Not Per-Element Tweaks
*mini-moi · Guild*
*Created: 2026-06-13 06:46 CDT — Claude.ai*
*For: Claude Code*
*Supersedes the H2/section-header portion of
`spec_guild_typography_hierarchy_2026-06-13.md`*

---

## What went wrong last time

That spec said "section headers need ~2x size, bold, dark" but didn't
constrain *font family* or set an *upper bound relative to H1*. Result:
`SPEC READY` / `IN BUILD` / `BLOCKED` / `RECENTLY DONE` are now in a
monospace font at a size that rivals the "Build Queue" page title — a new
inconsistency instead of the old one.

**Goal for this pass: get the whole scale right in one shot, defined as
variables so any later nudge is a one-line change, not a re-edit of every
instance.**

---

## The scale

| Tier | Examples | Font | Size (rel. to H1) | Weight | Color |
|---|---|---|---|---|---|
| **H1 — page title** | "Build Queue", "Build Log", "Build Daily Briefing" | existing serif — unchanged, the anchor | 100% | as-is | as-is |
| **H2 — section/column headers** | `SYSTEMS · CAREER FOCUS · BUILD · AHEAD`; `SPEC READY · IN BUILD · BLOCKED · RECENTLY DONE`; `SPEC · STATUS · AGE · ISSUE` table header | **same sans-serif as card titles** (e.g. "Surface Incomplete Reason...") — not monospace | ~50-55% of H1 | bold | dark, matching body text |
| **Nav — both rows** | `mini-moi · Curator · German · Guild`; `← Guild · Build Log · Queue · Roadmap` | current nav font, sized up | ~13-14px | regular (inactive) / bold (active) | muted (inactive) / accent+dark (active) — plus existing underline on active, so weight+color+underline together make active unmistakable |
| **Caption/label** | `Status:`, counts `(5)`, `today`, filenames | unchanged | unchanged | unchanged | unchanged |

---

## Implementation — variables + classes, applied everywhere

Define once (CSS custom properties or equivalent):
- `--guild-h2-size`, `--guild-h2-font`, `--guild-h2-weight`, `--guild-h2-color`
- `--guild-nav-size`, plus active/inactive weight+color rules

Create `.guild-h2` and nav classes using these variables. **Apply to every
instance below — this list should be exhaustive:**

- `/guild`: `SYSTEMS`, `CAREER FOCUS`, `BUILD`, `AHEAD`
- `/guild/build/queue`: `SPEC READY`, `IN BUILD`, `BLOCKED`, `RECENTLY DONE`
- `/guild/build`: `SPEC`, `STATUS`, `AGE`, `ISSUE` (table header row)
- `/guild/build/roadmap`, `/guild/career`, `/guild/career/active`: any
  equivalent section/column headers, if present
- Both nav rows, on all six Guild pages

H1 and Caption tiers: **leave untouched.**

---

## If it needs a nudge after sign-off

That's a single variable value change (e.g. `--guild-h2-size: 55% → 50%`),
applied once, visible everywhere immediately. No new spec, no per-element
hunting. If the implementation *can't* do this — if sizes ended up
hardcoded per element — that itself is the thing to fix first.

---

## Definition of Done

- [ ] H2 and Nav tiers implemented as variables/classes per the table —
      not hardcoded per element
- [ ] `.guild-h2` (or equivalent) applied to every instance listed above
- [ ] Nav active state: weight + color + underline together, clearly
      distinct from inactive
- [ ] H1 and Caption tiers unchanged
- [ ] Visual sign-off: Robert confirms the whole top-to-bottom hierarchy on
      `/guild` and `/guild/build/queue` reads consistently — nav rows
      legible, H2 headers clearly subordinate to H1, no font clash
- [ ] If a size nudge is needed post-signoff, confirm it's a one-line
      variable change

---

## Commit

```bash
git add domains/guild/static/ domains/guild/templates/
git commit -m "style: Guild type scale as CSS variables — H2 (section/
column headers) and nav rows, applied consistently across all six Guild
pages. Supersedes the monospace/oversized H2 from the previous pass.
H1 and captions unchanged."
git push origin main
```

---

*Spec · Guild · 2026-06-13*
