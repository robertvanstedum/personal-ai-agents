# Spec — Surface Incomplete Reason + Document Link on Build Cards
*mini-moi · Guild*
*Created: 2026-06-13 06:23 CDT — Claude.ai*
*For: Claude Code*

---

## What's missing

When a spec is `incomplete`, the card shows a `⚠ incomplete spec` badge but
not *why* — the reason (which check failed: missing DoD? missing Commit? a
referenced file that doesn't exist?) is written to
`design_log_transitions.reason` when Design/Dev sets the status, but nothing
in the portal displays it. There's also no direct link from the card to the
`_working/<spec_file>` content itself.

Today, getting both requires asking Claude Code in chat. This spec puts both
on the card.

---

## What to build

1. **Reason text.** For any item with status `incomplete`, look up the most
   recent `design_log_transitions` row where `to_status = 'incomplete'` for
   that `design_log` id, and show its `reason` field as small text under the
   `⚠ incomplete spec` badge — e.g. "Missing: Commit section" or whatever
   Design/Dev actually wrote.

2. **Document link.** Each card already shows the filename
   (`spec_*.md`). Make it (or add a small "view" link next to it) link to
   the file's content — simplest is the GitHub blob view
   (`https://github.com/robertvanstedum/personal-ai-agents/blob/main/_working/<filename>`).
   If the `edit`/`hist` links in the Build Log table already do this, just
   confirm and reuse the same pattern on Queue cards; if not, add it.

Applies to both `/guild/build` (Build Log) and `/guild/build/queue` (Queue) —
anywhere an `incomplete` card can appear.

---

## Definition of Done

- [ ] `incomplete` cards show the specific reason from
      `design_log_transitions.reason` (most recent incomplete transition)
- [ ] Every card (incomplete or not) has a working link to view the
      `_working/<spec_file>` content
- [ ] Visual sign-off: Robert can look at an `incomplete` card, read why,
      click through to the document — no chat needed to get to that point

---

## Commit

```bash
git add domains/guild/templates/ domains/guild/routes/
git commit -m "feat: surface incomplete reason + document link on Build
Log/Queue cards

Reads design_log_transitions.reason for the most recent incomplete
transition and displays it on the card. Adds/confirms a link to the
_working/ file's content for every card."
git push origin main
```

---

*Spec · Guild · 2026-06-13*
