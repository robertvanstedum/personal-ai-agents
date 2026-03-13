# personal-ai-agents

Read _NewDomains/PROJECT_STATE.md first before
starting any work.

Do not modify protected files without explicit
instruction from Robert.

Protected: README.md, CHANGELOG.md, OPERATIONS.md,
WHITEBOARD.md, docs/*

---

## Agent Division of Labor

**Claude Code (you):** Implementation only.
- Read GitHub issues and specs as your brief
- Write code, commit changes
- Do NOT create GitHub issues
- Do NOT update CHANGELOG.md or roadmap docs independently
- Those updates happen after Robert reviews and confirms your work

**OpenClaw:** Planning, documentation, memory layer.
- Creates issues, updates roadmap, CHANGELOG, specs, memory files
- Reads code for context but does not write implementation code

**Robert:** Decision point between agents.
- Reviews OpenClaw output (issue, spec) before handing to Claude Code
- Reviews Claude Code changes before merge/commit when possible
- One agent active on the repo at a time — not both in the same session

**Intent:** OpenClaw plans → Robert approves → Claude Code builds → Robert reviews.
This prevents conflicts, duplicate work, and agents overwriting each other.

---

## Signal Store State (as of 2026-03-12)

Ground truth for `curator_signals.json`. Do not modify historical signals.

- **398 historical X bookmarks** imported via `x_bootstrap.py` (one-time cold start)
- **Tweet-only signals** (no destination URL): `destination_text` intentionally absent — nothing to fetch, not a bug
- **29 URL signals**: `destination_text` populated, `destination_text_source` set
- **Backfill complete.** Treat all 398 as read-only.
- `x_pull_incremental.py` handles all new signals going forward

### Implementation decisions — do not "fix" without production data

- **50-char minimum filter in `x_to_article.py`**: Intentional. Filters noise from very short tweet text. Do not tighten without observing production false-positive rate.
- **`[:200]` truncation on tweet text in summary field**: Intentional cap. Keeps scoring prompt size predictable. Do not remove without benchmarking token cost impact.
