# Ways of working — personal-ai-agents

Live document. Founding original: [WAYS_OF_WORKING.md](WAYS_OF_WORKING.md) —
this file is the current, maintained practice; the founding document records
where it came from and why. Kept at root, not archived, since it's still
referenced as the origin of current practice.

Read `_NewDomains/PROJECT_STATE.md` first before starting any work.

## Roles

The roles are durable even as which agent fills them changes:

- **Design** — Claude.ai. Architecture decisions, design sessions, review of
  proposed plans before build.
- **Implementation** — Claude Code and OpenAI Codex. Read issues and specs as
  the brief, write code, commit changes. Do not create GitHub issues or
  update CHANGELOG/roadmap docs independently — those updates happen after
  Robert reviews and confirms the work.
- **Planning, documentation, memory** — OpenClaw. Creates issues, updates
  roadmap/CHANGELOG/specs/memory files, reads code for context but does not
  write implementation code. Also coordinates locally across agents.
- **Review and revision** — Grok, and whichever implementer did not write the
  change under review. Second-pass review, working from the actual diff, not
  the first agent's summary — this consistently catches what the first pass
  missed.
- **Decision point** — Robert. Reviews planning output before it reaches an
  implementer, reviews implementation changes before merge/commit, and is the
  only one who approves protected-file changes, production writes, and
  merges.

## Standing rules

- **Nothing ships without explicit approval of the reviewed diff.** A plan
  being approved is not the same as the resulting diff being approved —
  both gates apply.
- **Multi-reviewer discipline.** More than one agent verifies every
  non-trivial build.
- **One agent active on the repo at a time** — not simultaneous editing.
- **Working cycle:** spec → approve → build → confirm → ship.

## Protected files

Do not modify without explicit instruction from Robert:
`README.md`, `CHANGELOG.md`, `OPERATIONS.md`, `ARCHITECTURE.md`,
`ROADMAP.md`, `WHITEBOARD.md`, `docs/*`.

## Signal Store State (as of 2026-03-12)

Ground truth for `curator_signals.json`. Do not modify historical signals.

- **425 total signals** (398 historical cold start + 27 from first incremental pull on 2026-03-12)
- **Tweet-only signals** (no destination URL): `destination_text` intentionally absent — nothing to fetch, not a bug
- **URL signals**: `destination_text` populated, `destination_text_source` set
- **Backfill complete.** Treat all 398 historical signals as read-only.
- `x_pull_incremental.py` handles all new signals going forward
- `x_pull_state.json` is the authoritative pull tracker — `last_pull_at: 2026-03-13T01:53:39Z`

### Implementation decisions — do not "fix" without production data

- **50-char minimum filter in `x_to_article.py`**: Intentional. Filters noise from very short tweet text. Do not tighten without observing production false-positive rate.
- **`[:200]` truncation on tweet text in summary field**: Intentional cap. Keeps scoring prompt size predictable. Do not remove without benchmarking token cost impact.
- **`--limit=N` does not advance `last_pull_at`**: Intentional. Prevents test runs from poisoning the production early-stop marker. Production cron always runs without `--limit`.
