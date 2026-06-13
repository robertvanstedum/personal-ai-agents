# Spec — Two Small Follow-ups: `/guild` Nav Contrast + Dashboard Subtitle
*mini-moi · Guild*
*Created: 2026-06-13 07:08 CDT — Claude.ai*
*For: Claude Code*

Two small, unrelated, quick items — the type scale revision otherwise
looks good and doesn't need more iteration.

---

## 1. `/guild` Row 2 contrast

On `/guild`, Row 2 (`CAREER FOCUS · BUILD`) renders both items in the
muted/inactive nav color — low contrast against the dark background, hard
to read. On other pages this muted color works *because* it's contrasted
against one bright/active tab. On `/guild`, neither item is "the current
page" — they're the two primary entry points *from* here, not "other tabs."

**Fix:** on `/guild` specifically, both `CAREER FOCUS` and `BUILD` should
use the bright/bold/active nav styling (same as the current-tab treatment
elsewhere) — not the muted inactive style.

---

## 2. Dashboard card subtitle (`/dashboard`)

The Guild card currently reads "Career Focus · Agent operations." Leading
with Career Focus represents Guild as the domain that happened to get built
first, not what it fundamentally is — the operations/build/meta layer for
the whole platform (the gear icon already signals this). Career Focus is one
domain *within* Guild, not the headline.

**Fix:** reword to lead with operations/build, e.g. "Build · Operations ·
Career Focus" — exact wording/order is a judgment call, Career Focus just
shouldn't be first.

---

## Definition of Done

- [ ] `/guild`: `CAREER FOCUS` and `BUILD` both render in the bright/active
      nav color
- [ ] `/dashboard` Guild card subtitle reworded, Career Focus no longer
      first
- [ ] Visual sign-off

---

## Commit

```bash
git add domains/guild/templates/ domains/portal/templates/
git commit -m "style: /guild nav contrast for both entry links; reword
Guild dashboard card subtitle to lead with Build/Operations"
git push origin main
```

---

*Spec · Guild · 2026-06-13*
