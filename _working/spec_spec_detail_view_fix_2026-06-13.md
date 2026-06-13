# Spec — Spec Detail View: Nav Fix + Download/Print
*mini-moi · Guild*
*Created: 2026-06-13 06:51 CDT — Claude.ai*
*For: Claude Code*

---

## What's wrong

`/guild/build/spec/<filename>` (the document-link view from the previous
spec) renders the spec content nicely, but:

1. **Row 2 incorrectly shows `BUILD LOG · QUEUE · ROADMAP` with `ROADMAP`
   highlighted** — likely inherited from reusing the Roadmap route/template
   for "render a markdown file." This page isn't Roadmap, and isn't any of
   the three tabs.
2. **No way back** except browser back — none of the three tabs represents
   "return to where I came from."
3. **No download/print** — no way to get the raw `.md` (e.g. to hand to
   Grok) or print/export as PDF for review elsewhere.

---

## The fix

**1. Nav — same pattern as `← Guild`, one level deeper.** A spec-detail
page is a drill-down *from* Build Log/Queue, not a peer of
Build Log/Queue/Roadmap — there's no "SPEC" tab, it's parameterized by
filename. Row 2 here should be:

```
← Build Log
```

Single back-link, replacing the three tabs entirely (not one of them
highlighted). Links to `/guild/build`. Build Log is the right destination
regardless of whether the user arrived from Build Log or Queue, since it
lists every spec.

**2. Download `.md`.** A link/button that serves the raw file content as a
download (`Content-Disposition: attachment`, or equivalent) — the
unrendered markdown, suitable for uploading elsewhere.

**3. Print / Save as PDF.** A button calling `window.print()`, with a print
stylesheet that hides the nav chrome (both rows, top bar) and uses a clean
white background — just the rendered spec content. Browser's native
print-to-PDF handles the rest; no PDF generation library needed.

---

## Definition of Done

- [ ] Row 2 on `/guild/build/spec/<filename>` shows `← Build Log` only —
      no Build Log/Queue/Roadmap tabs, nothing mis-highlighted
- [ ] `← Build Log` returns to `/guild/build`
- [ ] "Download .md" present, downloads the raw file content
- [ ] "Print / Save as PDF" present, print stylesheet hides nav/chrome and
      gives a clean white-background rendering of just the content
- [ ] Visual sign-off: Robert opens a spec detail page, confirms nav is
      correct, downloads the `.md`, and print-previews cleanly

---

## Commit

```bash
git add domains/guild/templates/ domains/guild/static/ domains/guild/routes/
git commit -m "fix: spec detail view — back-link nav (matches ← Guild
pattern), add .md download and print/PDF support

Row 2 now shows '← Build Log' instead of mis-highlighted section tabs.
Adds raw .md download and a print stylesheet for clean PDF export."
git push origin main
```

---

*Spec · Guild · 2026-06-13*
