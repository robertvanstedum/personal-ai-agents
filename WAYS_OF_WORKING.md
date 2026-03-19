# Ways of Working — Principles & Conventions
**Mini-moi Personal AI Curator**
**Established:** March 15, 2026
**Applies to:** All agents, regardless of tool or model

---

## A Note on This Document

This document exists for human reference — Robert and any collaborator who needs
to understand how this project is run.

**The principles below are not rules to look up. They are ways of working that
every agent is expected to internalize.** If this file is deleted, the principles
survive in agent memory and practice. Documents can be lost. Ways of working cannot.

When a new agent joins this project — regardless of what tool or model it runs on —
it is expected to read this document once, internalize it fully, and operate from
these principles without being reminded.

---

## The Three Roles

This project uses three distinct agent roles. The same principles apply regardless
of which specific tool fills each role today or in the future.

### Strategy Agent
Thinks at the level of architecture, design, and decisions. Produces plans,
specs, and documentation. Works conversationally with Robert to reason through
problems before anything is built.

*Does not have direct access to local files. Does not know current system state
unless told or shown. Asks before assuming.*

Current tool: Claude.ai

### Memory Agent
Holds the full project context — every file, every decision, every commit.
Validates designs against local reality before the Implementation Agent builds.
Saves documentation to the repo. Maintains the CHANGELOG.

*Does not design from scratch. Its primary value is knowing what is actually
on disk and flagging when a plan contradicts it. Acts as the sanity check
between strategy and implementation.*

Current tool: OpenClaw

### Implementation Agent
Builds. Edits files, runs scripts, executes git operations. Works one step
at a time and confirms with Robert between each step.

*Does not set strategy. Does not jump ahead. Does not push to remote without
Robert's explicit confirmation. When something unexpected surfaces, stops and
flags rather than improvising a fix.*

Current tool: Claude Code

---

## Core Principles

These are the principles every agent internalizes. They apply always,
not just when this document is open.

**1. The repository is the source of truth.**
No agent's memory supersedes what is on disk. When in doubt, read the file.

**2. Intent is preserved separately from outcome.**
What was planned and what was built are two different things. The plan is never
rewritten to match the build. Divergences are documented honestly in the BUILD doc
under "What was planned vs what was actually built."

**3. One step at a time.**
The Implementation Agent confirms with Robert between each build step. No
jumping ahead. No bundling multiple steps into one session without confirmation.

**4. Validate before building.**
Before any plan goes to the Implementation Agent, the Memory Agent validates
it against local files. Not to redesign — to catch conflicts with what already exists.

**5. Robert confirms before anything is pushed.**
The Implementation Agent stages and commits locally. Robert says go before anything
reaches the remote. This is non-negotiable.

**6. Quiet paths over noise.**
If there is nothing meaningful to say, say nothing. This applies to code
(suppress empty observations), to documentation (don't pad build records),
and to agent communication (don't report success theater).

**7. Documents are for humans. Principles are for agents.**
Agents operate from internalized principles, not document lookups. A document
can be deleted. A way of working cannot.

---

## Build Workflow

Every build phase follows this sequence:

```
1. Strategy Agent produces PLAN doc (PDF or in-session)
2. Robert passes to Memory Agent for validation
3. Memory Agent answers open questions, flags conflicts, confirms file state
4. Robert confirms — Implementation Agent cleared to build
5. Implementation Agent builds — one step, confirm, next step
6. Implementation Agent writes build summary → tells Robert "pass to Memory Agent"
7. Memory Agent reviews summary, adds missing pre-conditions, saves BUILD doc to docs/
8. Memory Agent appends CHANGELOG entry
9. Memory Agent provides exact git commit command to Robert
10. Robert passes commit command to Implementation Agent
11. Implementation Agent commits locally
12. Robert confirms → Implementation Agent pushes
```

---

## Document Types

### PLAN doc — pre-build intent

Written by the Strategy Agent before any build begins. Converted from PDF to
markdown by the Memory Agent and saved to `docs/`.

**Required sections:**
- Problem and intent
- Exact files to be created or modified
- Step-by-step build sequence
- Open questions for the Memory Agent (answered before build starts)
- Verification steps

**Rule:** Never modified after the Implementation Agent starts building.
The plan is a snapshot of intent, not a living document.

**Naming:** `PLAN_{WS}_{Feature}_{YYYY-MM-DD}.md`
**Location:** `docs/`

Examples:
- `docs/PLAN_WS1_SourceScoring_2026-03-14.md`
- `docs/PLAN_WS2_WebSearch_2026-03-14.md`
- `docs/PLAN_WS5_Intelligence_2026-03-14.md`

---

### BUILD doc — post-build actuals

Written by the Implementation Agent as a summary after each phase completes.
Completed and saved by the Memory Agent, who adds missing pre-conditions and
any refactors that happened before the headline feature.

**Required sections:**
1. What Was Planned
2. Pre-conditions Completed ← Memory Agent adds this; often missing from Implementation Agent summary
3. What Was Built (files created/modified)
4. Confirmed Working Output (first real run result or test result)
5. Design Decisions Made During Build
6. Cost (if applicable)
7. Open Items Carried Forward
8. Next Phase Scope

**Rule:** Pre-conditions must be documented. File moves, schema changes, and
refactors that happened before the headline feature are not optional —
they affect every future agent reading the codebase.

**Naming:** `BUILD_{WS}_{Feature}_{YYYY-MM-DD}.md`
**Location:** `docs/`

Example:
- `docs/BUILD_WS5_PhaseA_2026-03-15.md`

---

### SPRINT doc — living project state

Written by the Strategy Agent, validated by the Memory Agent.
Updated at meaningful milestones — not after every commit.

**Naming:** `SPRINT_{version}_v{N}.md`
**Location:** repo root

Example: `SPRINT_1_0_v2.md`

---

### CHANGELOG — append only

Maintained by the Memory Agent. Never rewrite existing entries.

**Format:**
```markdown
## YYYY-MM-DD — {WS/Phase}: {short description}

Commits: see docs/BUILD_{...}.md for full record

**Result:** One sentence — what is now running in production.

### What Was Built
- Bullet per significant file changed or created

### Open Items → Next Phase
- Bullet per carry-forward item
```

**Location:** `CHANGELOG.md` in repo root

---

## Git Conventions

**Commit message format:**
```
{type}: {short description}

Types: feat | fix | refactor | docs | chore
```

**What gets committed:**
- Source code
- `docs/` (PLAN and BUILD docs)
- `CHANGELOG.md`
- `SPRINT_*.md`
- `WAYS_OF_WORKING.md`
- `CLAUDE.md`, `PROJECT_STATE.md`, other orientation docs

**What does not get committed:**
- Operational data: `curator_history.json`, `curator_signals.json`,
  `curator_preferences.json`, `priorities.json`, `intelligence_*.json`,
  `intelligence_state.json`, `curator_costs.json`
- Anything in `.gitignore`
- `_NewDomains/` (gitignored — private design docs)

---

## Session Start — Implementation Agent (Claude Code)

At the start of every session, before touching any file:

1. Read `CLAUDE.md` — agent orientation and protected file list
2. Read `WAYS_OF_WORKING.md` — this document
3. Read the current PLAN doc for this phase (Robert will specify which one)
4. Read the BUILD doc if one exists for prior phases of this workstream
5. Confirm with Robert which step to start on

**Do not assume continuity from a previous session.**
**Always verify current state from files, not from memory.**

---

## Memory Agent — Ingestion Protocol

When Robert says "pass to Memory Agent" or shares a build summary:

1. Review for missing pre-conditions (refactors, file moves, schema changes)
2. Add "Pre-conditions Completed" section with anything missing
3. Save as `docs/BUILD_{WS}_{Feature}_{YYYY-MM-DD}.md`
4. Append entry to `CHANGELOG.md`
5. Confirm: "Saved. [Any conflicts or notes.]"
6. Provide the exact git commit command:
   `git add docs/BUILD_{...}.md CHANGELOG.md && git commit -m "docs: {description}"`

When PDFs appear in `~/.openclaw/workspace/`:

1. Extract content via pdf tool
2. Save as `docs/PLAN_{WS}_{Feature}_{YYYY-MM-DD}.md`
3. Add "Converted from PDF to markdown {date}" at bottom
4. PDFs can be discarded after confirmed save

---

## What Goes Where — Quick Reference

| Content | Location |
|---|---|
| Design specs, plan docs | `docs/PLAN_*.md` |
| Build records | `docs/BUILD_*.md` |
| Portfolio write-ups, case studies | `docs/portfolio/` |
| Test reports | `docs/test-reports/` |
| Sprint plan and workstream status | `SPRINT_*.md` (repo root) |
| Running changelog | `CHANGELOG.md` (repo root, append only) |
| Agent orientation | `CLAUDE.md` (repo root, protected) |
| Private design docs | `_NewDomains/` (gitignored) |
| Operational state files | `~/.openclaw/workspace/` — never in repo |
| Superseded docs | `archive/` (repo root) |
| Superseded screenshots / visual artifacts | `docs/screenshots/archive/` |

## Archive Policy

**Archive, don't delete.** The build journey is part of the project.

Superseded documents, old drafts, replaced READMEs, and stale specs move to
`archive/` rather than being deleted. Old screenshots and visual artifacts move
to `docs/screenshots/archive/`. Both remain in git history and are visible on
GitHub.

Nothing is deleted unless it contains sensitive data or is truly valueless noise.
When in doubt, archive.

---

*Established: March 15, 2026*
*Proposed changes: raise with Robert, update by consensus*
*To record a change: append a dated note below rather than rewriting sections*

---

## Change Log

**2026-03-15 (initial):** Document created from Robert's draft + OpenClaw review.
Key additions: explicit divergence location (Principle 2), build workflow steps 9–12
(Memory Agent provides commit command, Robert confirms push), Pre-conditions section
made mandatory in BUILD docs, session start list updated to include WAYS_OF_WORKING.md.
Supersedes CONVENTIONS.md (deleted). BUILD doc naming updated to include date.

**2026-03-18:** Archive policy added. Superseded docs move to `archive/`, old screenshots
to `docs/screenshots/archive/`. Archive, don't delete — the build journey is part of
the project. What goes where table updated with archive locations.
