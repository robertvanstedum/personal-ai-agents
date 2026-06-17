# Approach — Design Log Filing Rules for Dev Agent
*2026-06-17 — Claude Code*
*Status: Draft for review — not a spec yet*

---

## Problem

The Dev Agent currently picks up `.md` files from `_working/` and files them
into `guild.design_log` as specs. This pulls in non-build documents —
session summaries, handoff docs, approach docs — and creates junk rows in the
build queue with no lifecycle and no definition of done.

Example: `session_summary_2026-06-16.md` appeared in the queue as an
`incomplete` item with no title. It has no build phase, no spec, and no
"done" state. It does not belong there.

---

## What belongs in design_log

Only documents that have a build lifecycle:

| Should file | Filename pattern | Why |
|---|---|---|
| Specs | `spec_*.md` | Has a build phase, definition of done, commit |
| Build plans | `build_plan_*.md` | Tracks a multi-step build |
| GitHub issues | `issue_*.md` | Maps to tracked work |

## What does not belong in design_log

| Should NOT file | Filename pattern | Why |
|---|---|---|
| Session summaries | `session_summary_*.md` | Continuity artifact, no build phase |
| Handoff docs | `handoff_*.md` | Input to a build, not a build item itself |
| Approach docs | `approach_*.md` | Pre-spec thinking, review artifact |
| Design docs (non-spec) | `design_*.md` | Context, not a build item |
| Ways of working | `ways_of_working_*.md` | Process docs, not buildable |
| Plans (non-build) | `plan_*.md` | May or may not have a build phase — review before filing |
| Docs placed in `docs/` | `docs_*.md` | Already committed to repo, not a queue item |

---

## Proposed fix

The Dev Agent's filing logic should filter on filename prefix before
creating a `design_log` row. Only `spec_*` and `build_plan_*` files
should be auto-filed. All other `.md` files in `_working/` should be
ignored unless manually added via the `/guild/build/items/new` form.

**Allowlist approach (preferred over blocklist):**
```
auto-file if filename matches: spec_*.md | build_plan_*.md
ignore everything else
```

This is safer than a blocklist — new file types don't accidentally get
filed just because they weren't explicitly excluded.

---

## Cleanup needed now

- Delete `guild.design_log` row id 48 (`session_summary_2026-06-16.md`,
  no title, `incomplete`) — junk row, safe to delete
- Review any other rows with empty titles or non-spec filenames

---

## Open question for review

Should `handoff_*.md` ever be manually filed into the queue as the
"parent" of a spec? Currently a handoff doc is the input that produces
a spec — the spec is what gets filed. Keeping that separation seems
right, but worth confirming.

---

*For review by OpenClaw before becoming a spec or Dev Agent config change.*
