# Spec — Remove "How this works" from Roadmap Page
*Created: 2026-06-17 — Claude.ai*
*Status: Ready for `_working/` — quick fix, one commit*

---

## Changes

### 1. Remove "How this works" section from ROADMAP.md

Delete the entire `## How this works` section and its content from
`_working/ROADMAP.md`. The page structure (Agreed targets / Discussion
split, status badges, source doc links) is self-explanatory.
The process documentation belongs in `docs/DECISION_RECORD_PRACTICE.md`
and `docs/HANDOFF_PROCESS.md`, not on the roadmap view.

**Delete this block from ROADMAP.md:**
```markdown
## How this works

Agreed targets are committed to `docs/` in the repo and listed below
with their source document. Discussion items that haven't been agreed
live in session summaries — not here.

The act of committing is the promotion. If it's not in `docs/`, it's
not an agreed target.

When an item is ready to build: write a spec in `_working/` with
DoD + Commit, then update the item's status here to `queued`.
```

Replace with nothing — the German domain card follows directly after
the subtitle line.

### 2. Fix subtitle line in ROADMAP.md

Current (renders as one run-on italic line):
```markdown
*Living document — versioned in GitHub Updated: via git — see commit
history for changes*
```

Replace with:
```markdown
*Living document — versioned in GitHub*
```

The date already shows from git log. "See commit history for changes"
is redundant. Keep it clean.

---

## Definition of Done

- "How this works" section does not appear on the rendered roadmap page
- Subtitle renders as a single clean italic line:
  "Living document — versioned in GitHub"
- German domain card appears immediately below the subtitle
- No other content changed
- No regression on domain cards, status badges, source doc links

## Commit

`Roadmap: remove How this works section, clean up subtitle.`

---

*Spec · 2026-06-17 · Claude.ai*
