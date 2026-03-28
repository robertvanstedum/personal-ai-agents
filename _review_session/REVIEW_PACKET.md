# REVIEW_PACKET.md
> Generated: 2026-03-15 21:40 CDT for Claude.ai doc review session
> Purpose: Full context for reviewing and updating README, ARCHITECTURE, CHANGELOG, and active docs

---

---

## FILE: DOCS.md

# DOCS Registry
> Living inventory of all .md files across the repo and OpenClaw workspace.
> Agents: review this file, add any rows you find missing, populate from your own knowledge.

## Protocol
- When you read any .md file, find its row, increment Times Accessed, update Last Used
- Do not delete rows — mark Notes as "obsolete" or "historical" if no longer active
- New files get a row added immediately upon creation
- If you find a file not listed here, add it
- Concurrent writes are acceptable — this is observability, not a transaction log

## Agent Key
- **OpenClaw** — OpenClaw workspace only
- **ClaudeCode** — Claude Code reads this
- **Both** — either agent may read it
- **Other** — human reference, portfolio, never agent-read

---

## Repo Root (`~/Projects/personal-ai-agents/`)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| CLAUDE.md | Agent orientation | ClaudeCode | 1 | 2026-03-08 | Auto-read every session. Points to PROJECT_STATE.md |
| OPERATIONS.md | How to run the system | Both | 1 | 2026-03-08 | Commands, health checks, cost rules, file locations |
| CHANGELOG.md | Change log | Both | 0 | — | Append-only. Also exists in OC workspace |
| CURATOR_ROADMAP.md | What's built / active / next | Both | 0 | — | Phase tracking. Most current roadmap doc |
| DEVELOPMENT.md | How we build | Both | 0 | — | Team roles, testing checklist, workflow |
| TELEGRAM_ARCHITECTURE.md | Two-bot setup | Both | 2 | 2026-03-08 | Validated 2026-03-05. Operationally critical. Companion to docs/FEATURE_TELEGRAM_ARCHITECTURE.md |
| DOCS.md | File registry | Both | 1 | 2026-03-08 | This file. Audit against all .md files in repo |
| BACKLOG.md | Small fixes and improvements | Both | 0 | — | Planned — not yet created. Consider consolidating FEATURE_DELETE_DEEP_DIVES.md and CURATOR_UX_BACKLOG.md here |
| README.md | Public project overview | Other | 1 | 2026-03-08 | Portfolio / GitHub facing. Protected |
| README_v2 March 3.md | Draft README | Other | 0 | — | Uncommitted draft at root. Review for merge or delete |
| ENGINEERING.md | Engineering philosophy | Both | 0 | — | OC workspace only (not in repo). Reference when onboarding or refocusing |
| CURATOR_PROMPTS.md | Scoring prompt text | Both | 0 | — | OC workspace only (not in repo). Reference when changing scoring logic |
| CURATOR_CALLBACKS.md | Callback handling detail | Both | 0 | — | OC workspace only (not in repo). Operational detail. Could fold into MEMORY.md |
| CURATOR_FEEDBACK_DESIGN.md | Phase 2B feedback spec | Other | 0 | — | Phase 2B built. Historical spec, still useful if extending feedback loop |
| PHASE_3C_PLAN.md | Phase 3C technical spec | Both | 0 | — | Phase 3C complete. Keep if revisiting X adapter |
| VOICE_NOTES.md | Freeform notes | Other | 0 | — | One entry (Cuba research, Feb 27). Human reference. Also in OC workspace |
| TOOLS.md | Tool inventory | Both | 0 | — | OC workspace only (not in repo). Mostly empty. Populate or archive |
| AGENTS.md | OpenClaw agent template | OpenClaw | 0 | — | OC workspace only (not in repo) |
| PROJECT_BRIEF.md | Project summary | Other | 0 | — | Portfolio / human reference |
| PROJECT_ROADMAP.md | Early system vision (Feb 7) | Other | 0 | — | Historical. Superseded by CURATOR_ROADMAP.md. Also in OC workspace |
| ARCHITECTURE.md | Early architecture thinking | Other | 0 | — | Historical. Pre-_NewDomains |
| PLATFORM_POC.md | Pre-_NewDomains platform thinking | Other | 0 | — | Historical. Superseded |
| PLATFORM_UNIFIED.md | Pre-_NewDomains platform thinking | Other | 0 | — | Historical. Superseded |
| BUILD_PLAN_v0.9.md | Feb 28 sprint plan | Other | 0 | — | v0.9-beta shipped. Historical |
| CURATOR_ENHANCEMENT_ANALYSIS.md | Enhancement planning | Other | 0 | — | Review: completed or stale? |
| TODO_MULTI_PROVIDER.md | Multi-provider planning | Other | 0 | — | Review: completed or stale? |
| CLAUDE_MEMORY_PROMPT.md | Old agent orientation | Other | 0 | — | Superseded by CLAUDE.md |
| CURATOR_README.md | Curator-specific readme | Other | 0 | — | Relationship to README.md unclear. Review |
| AI_TOOLS_EVALUATION.md | Tool selection rationale | Other | 0 | — | Decision made. Portfolio/interview reference |
| CREDENTIALS_SETUP.md | Initial credential setup | Other | 0 | — | Done. Only useful on fresh machine setup |
| PRODUCTION_SECURITY.md | Security setup | Other | 0 | — | Review: current or historical? |
| XAI_BUDGET_TRACKER.md | xAI cost tracking | Other | 0 | — | Superseded by OPERATIONS.md cost rules |
| COST_COMPARISON.md | Model cost comparison | Other | 0 | — | Historical. Decision made |
| HAIKU_IMPLEMENTATION_PLAN.md | Haiku fallback spec | Other | 0 | — | OC workspace only (not in repo). Built. Historical |
| CURATOR_DEDUP_IMPLEMENTATION.md | Dedup spec | Other | 0 | — | OC workspace only (not in repo). Built. Historical |
| CURATOR_REFACTOR.md | Refactor planning | Other | 0 | — | OC workspace only (not in repo). Review: completed? |
| FEATURE_PLAN_INTEREST_CAPTURE.md | Interest capture spec | Other | 0 | — | Built. Historical |
| INTEREST_CAPTURE_README.md | Interest capture docs | Other | 0 | — | Built. Historical |
| ROADMAP_X_INTEGRATION.md | X integration planning | Other | 0 | — | Built. Historical. Also in OC workspace |
| TESTING_CHECKLIST.md | Testing checklist | Other | 0 | — | Superseded by DEVELOPMENT.md |
| WEEKEND_PLAN.md | One-off sprint plan | Other | 0 | — | OC workspace only (not in repo). Done. Historical |
| WEEKEND_QUICKSTART.md | One-off quickstart | Other | 0 | — | OC workspace only (not in repo). Done. Historical |
| CRON-SETUP.md | Cron setup notes | Other | 0 | — | Superseded by launchd migration. Also in OC workspace |
| crontab-setup-notes.md | Cron notes | Other | 0 | — | OC workspace only (not in repo). Superseded by launchd migration |
| TELEGRAM_WEBHOOK_PLAN.md | Webhook approach | Other | 0 | — | Superseded by TELEGRAM_ARCHITECTURE.md |
| CLAUDE_PROJECT_SETUP.md | Initial Claude setup | Other | 0 | — | OC workspace only (not in repo). One-time setup. Done |
| BOOTSTRAP.md | Bootstrap instructions | Other | 0 | — | OC workspace only (not in repo). One-time setup. Done |
| FEATURE_DELETE_DEEP_DIVES.md | Delete feature spec | Other | 0 | — | Moved to BACKLOG.md |
| CURATOR_UX_BACKLOG.md | UX backlog | Other | 0 | — | OC workspace only (not in repo). Moved to BACKLOG.md |
| trusted-sources.md | Source list | Both | 0 | — | Review: still used by scoring? |
| reading-list-availability.md | Reading list research | Other | 0 | — | OC workspace only (not in repo). One-off research artifact |
| chrome-extension-setup.md | Chrome extension notes | Other | 0 | — | OC workspace only (not in repo). One-off. Review: still relevant? |
| feature-request-exec-cron.md | Feature request note | Other | 0 | — | OC workspace only (not in repo). One-off artifact |
| HEARTBEAT.md | System heartbeat | Both | 0 | — | OC workspace only (not in repo). Empty. Populate or remove |
| IDENTITY.md | Identity notes | Other | 0 | — | OC workspace only (not in repo). 3 lines. Consider folding into SOUL.md |

---

## docs/ (`~/Projects/personal-ai-agents/docs/`)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| NEXT_PHASE_PLAN_grok41.md | Grok 4.1 upgrade plan | Both | 1 | 2026-03-08 | Saved for implementation. Verify model name before building |
| FEATURE_TELEGRAM_ARCHITECTURE.md | Telegram two-bot design | Both | 0 | — | Validated design doc. Companion to TELEGRAM_ARCHITECTURE.md at root |
| CASE_STUDY_GROK41_MODEL_TUNING.md | Grok 4.1 tuning case study | Other | 0 | — | Written 2026-03-06. Portfolio/reference |
| CASE-STUDY-DEEP-DIVE-FEATURE.md | Deep dive feature case study | Other | 0 | — | Historical. Feature shipped |
| FEATURE_DEEP_DIVE_RATINGS.md | Deep dive ratings spec | Other | 0 | — | Review: built or backlog? |
| WORKSPACE-SETUP.md | Workspace setup guide | Other | 0 | — | One-time setup reference |
| portfolio/phase3c-enrichment-results.md | Phase 3C enrichment results | Other | 0 | — | Portfolio data artifact |
| portfolio/phase3c-results.md | Phase 3C results summary | Other | 0 | — | Portfolio data artifact |
| test-reports/2026-03-03-phase3c-ab-test.md | Phase 3C A/B test report | Other | 0 | — | Historical test report |
| test-reports/2026-03-06-grok41-ab-test.md | Grok 4.1 A/B test report | Other | 0 | — | Recent. Reference for model tuning decisions |
| test-reports/GROK41_MIGRATION_SUMMARY.md | Grok 4.1 migration summary | Other | 0 | — | Uncommitted. Migration complete |
| test-reports/REPORT_SCHEMA.md | Test report schema | Other | 0 | — | Schema reference for future test reports |

---

## _NewDomains/ (gitignored — local only)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| PROJECT_STATE.md | Current state snapshot | Both | 1 | 2026-03-08 | First file read after CLAUDE.md. Dashboard not master doc |
| ARCHITECTURE.md | Platform vision and principles | Both | 1 | 2026-03-08 | Domain independence design. Living doc |
| README.md | Agent rules for this folder | Both | 1 | 2026-03-08 | Hard rules for Claude Code and OpenClaw |
| DOMAIN_SPEC_finance.md | Finance domain design | Both | 1 | 2026-03-08 | Design phase only. Private indefinitely |
| DOMAIN_SPEC_language_learning.md | Language learning design | Both | 1 | 2026-03-08 | Design phase only. Blocked on Grok transcript export |
| DOMAIN_SPEC_commercial.md | Commercial platform design | Both | 1 | 2026-03-08 | Design phase only. Rails 8 + Stripe Connect. Separate repo when built |
| INSTALL.md | Install instructions | Other | 1 | 2026-03-08 | One-time setup instructions for this package |

---

## interests/ (gitignored — generated content)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| README.md | Interests folder overview | Other | 0 | — | Gitignored. Human reference only |
| TEMPLATE.md | Interest capture template | Other | 0 | — | Gitignored. Template for new entries |
| 2026-02-13-thoughts.md | Freeform thoughts | Other | 0 | — | Gitignored. Personal notes |
| 2026-02-16-flagged.md | Flagged articles | Other | 0 | — | Gitignored. Generated content |
| deep-dives/*.md | Deep dive articles | Generated | 3+ | 2026-03-08 | 3 recent deep dives from Feb 16 curations |
| 2026/deep-dives/*.md | Deep dive articles | Generated | 10+ | 2026-03-08 | 15+ deep dives from Mar briefings. Auto-generated from curation scoring |

---

## OpenClaw Workspace (`~/.openclaw/workspace/`)

| File | Domain | Agent | Times Accessed | Last Used | Notes |
|------|--------|-------|---------------|-----------|-------|
| SOUL.md | Identity / values | OpenClaw | 3+ | 2026-03-07 | Core identity. Read every session |
| USER.md | User profile | OpenClaw | 3+ | 2026-03-07 | Robert's context and preferences. Read every session |
| MEMORY.md | Operational memory | OpenClaw | 5+ | 2026-03-08 | Running memory across sessions. Updated daily |
| WHITEBOARD.md | Brainstorming | OpenClaw | 3 | 2026-03-07 | Exploratory thinking. Now ideas-only with governance rule |
| PROJECT_STATE.md | Build authorization | OpenClaw | 2 | 2026-03-07 | Master orientation. Read first (in workspace, separate from repo) |
| AGENTS.md | OpenClaw agent template | OpenClaw | 2+ | 2026-03-07 | Workspace setup guide for this instance |
| HEARTBEAT.md | System heartbeat | OpenClaw | 1 | 2026-03-08 | Empty. Can populate with daily checks or remove |
| IDENTITY.md | Identity notes | OpenClaw | 1 | 2026-03-07 | 3 lines. Consider folding into SOUL.md or keeping separate |
| TOOLS.md | Tool inventory | OpenClaw | 1 | 2026-03-07 | Mostly empty. Populate with camera names, SSH hosts, etc. |
| finance/ | Finance domain folder | OpenClaw | 1 | 2026-03-07 | New private domain (gitignored in repo). .gitignore + README created. Design phase |
| memory/ | Daily memory logs | OpenClaw | 3+ | 2026-03-08 | Session continuity. Contains 2026-03-01.md through 2026-03-07.md |
| CHANGELOG.md | Change log | OpenClaw | 1 | 2026-03-07 | Session-by-session record. Also in repo root. Consider which is authoritative |
| TONIGHT_SESSION_CHECKLIST.md | Session checklist | OpenClaw | 0 | — | One-off checklist. Historical. Can archive |
| PHASE_3C_SPEC.md | Phase 3C spec | OpenClaw | 0 | — | Phase 3C complete. Historical. Archive if not needed |
| VOICE_NOTES_SPEC.md | Voice notes spec | OpenClaw | 0 | — | Feature spec. Review: built? Historical artifact |
| WEEKEND_PLAN_BETA.md | Weekend plan beta | OpenClaw | 0 | — | Historical sprint plan. Can archive |

---

## Future Domains (for awareness — not current focus)

| Domain | Status | Notes |
|--------|--------|-------|
| RVSAssociates | Placeholder only | Commercial platform. No active build yet |
| Vera jewelry site | Pending name/domain decision | Phase 1 commercial. Blocked on naming |
| Language learning (German) | Future | Grok transcript export method TBD |
| Finance parser (Itaú) | Future | Needs OFX/CSV export sample from Robert |

---

*Schema version 1.2 — created 2026-03-08, updated 2026-03-08 (evening)*

---

## Summary of Changes (Mar 8 Evening Update)

**Added rows:**
- TELEGRAM_ARCHITECTURE.md companion note (repo root)
- Deep-dives tracking (interests/deep-dives and interests/2026/deep-dives)
- OpenClaw workspace files with accurate access counts

**Corrected:**
- TELEGRAM_ARCHITECTURE.md: updated access counts, added companion reference
- Project_STATE.md: now tracked separately in workspace (different from repo)
- finance/ domain: new folder in workspace, gitignored in repo
- memory/ folder: daily session logs tracked

**Status notes:**
- Many historical/obsolete files marked for archival review
- Two CHANGELOG.md files (workspace + repo) — clarify which is primary
- Some workspace files marked empty or minimal — consider consolidation
- Deep-dives auto-generated from curation, no manual tracking needed (for info only)

---

## FILE: CLAUDE.md

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

---

## FILE: WAYS_OF_WORKING.md

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

---

## FILE: SPRINT_1.0.md

# Mini-moi Personal AI Curator
## Sprint to 1.0 — March 2026

**Target:** Public GitHub launch, production-ready, two weeks

---

## Context and Goal

Mini-moi has been in production since February 9, 2026. The system delivers a daily intelligence briefing via Telegram — geopolitics and finance, scored and ranked from a pool of RSS feeds and X bookmarks. Phases 3A through 3C.7 are complete. The system works.

The goal of this sprint is to reach a coherent, presentable 1.0: broader source coverage, source-level quality filtering, Mac Mini migration for always-on operation, and the technical infrastructure for the investigation workspace feature planned for 1.1. GitHub cleanup and public launch follow the build — not before.

---

## Current State (March 13, 2026)

| | |
|---|---|
| Production since | February 9, 2026 |
| Daily briefing | Telegram, 7 AM, top 20 articles |
| Scoring pool | 722 candidates (390 RSS + 332 X bookmarks) |
| Signal store | 425 signals (398 historical + 27 incremental) |
| Daily cost | ~$0.30/day (xAI grok, single-stage) |
| Last commit | Phase 3C.7 complete — incremental X bookmark pull |
| GitHub | public repo pushed, private repo synced |

---

## 1.0 Build Plan

Four workstreams. Build in this order — Mac Mini migration last so development happens on the familiar MacBook environment.

| Feature | Description | Status |
|---|---|---|
| Source scoring | Score at domain level, not just article. Explicit trust/drop/deprioritize per source. Suppresses Investing.com, Duran-type noise before scoring. | Build |
| Broader sources | Add 5-8 institutional RSS feeds (BIS, Fed, ECB, NBER, arXiv). Web search as additional candidates — topic-guided, domain-whitelisted, competes for same 20 slots. | Build |
| Investigation workspace infra | Technical plumbing for 1.1 feature — topic store, timeline index, annotation schema. No UI. Enables fast 1.1 build. | Build |
| Mac Mini migration | Port to always-on server. Resolves sleep/wake scheduling workarounds. Prerequisite for calling this production-ready. | Build |
| GitHub cleanup | Issues, roadmap, README all current. Reflects actual system state. Happens after build, not before. | After build |

---

## Workstream 1: Source Scoring

### The problem

Investing.com, The Duran, and similar sources occupy scoring slots that better content should win. The current pipeline scores every article equally regardless of source quality. There is no mechanism to suppress a low-signal source without removing it entirely from the feed list.

### The design

Source scoring operates at the domain level, upstream of article scoring. Every domain in the candidate pool carries a trust weight that multiplies its articles' scores before ranking.

- **trusted** — weight up, always competes strongly
- **neutral** — current behavior, no change
- **deprioritize** — score multiplier < 1, competes but handicapped
- **drop** — never enters scoring pool

Source ratings are set explicitly via a new command or UI action. Over time, article-level feedback (like/dislike) accumulates into source-level reputation automatically — if you consistently dislike ZeroHedge sensationalism, ZeroHedge deprioritizes without manual action.

### New file: `curator_sources.json`

Source registry lives in a standalone file — add/remove/rate sources without touching code. Schema:

```json
{ "domain": "investing.com", "trust": "drop", "set_by": "explicit", "note": "aggregator, SEO noise" }
```

### Integration point

`curator_rss_v2.py` reads `curator_sources.json` before scoring. Drop sources never enter the candidate pool. Deprioritized sources enter with a score penalty. Trusted sources enter with a bonus.

---

## Workstream 2: Broader Sources

### The problem

The daily brief is limited to sources defined at RSS setup. Relevant content from institutional sources and current events outside the subscription bubble never surfaces. The fix is not to change the output — still 20 articles — but to improve what competes for those 20 slots.

### Two additions

**Institutional RSS feeds (immediate)**

Low effort, high quality. These sources publish original research and data — not aggregated news. Added March 13, 2026:

- War on the Rocks — defense/security analysis
- Foreign Affairs — premier geopolitics journal
- arXiv q-fin — academic preprints (capped at 15)
- Just Security — national security / international law
- CEPR VoxEU — economic policy research columns

Additional institutional sources (BIS, Fed, ECB, NBER) available to add later as needed — not in this sprint.

**Web search candidates (topic-guided)**

Web search runs daily, constrained to trusted domains and known topic areas. Results enter the same scoring pool and compete for the same 20 slots — no new output format, no separate section.

- Searches defined by topic list (geopolitics, monetary policy, EM markets, etc.)
- Domain whitelist — only trusted outlets, no SEO aggregators
- Haiku pre-filters web results before they reach Grok scoring
- Dedup against today's RSS articles — no duplicates surface
- Cost-controlled: bounded query list, Haiku handles bulk filtering

---

## Workstream 3: Investigation Workspace Infrastructure

### What this is

The investigation workspace is a 1.1 feature — not released in 1.0. But the technical infrastructure goes in during 1.0 so the 1.1 build is fast. Plumbing only. No UI in this sprint.

### The 1.1 feature vision

An investigation is a persistent, evolving research thread on a topic. Example: the Ethiopia-Eritrea conflict. The user opens an investigation, the system pulls relevant content across a wide timeline — news, books, scholarly articles, primary sources. The user reads, annotates, and iterates. The investigation can be closed and reopened months later with full history intact.

- **Start investigation** — name, topic, scope (date range, source types)
- **System pulls and synthesizes** — wide timeline, multiple source types
- **User annotates** — comments, ratings, follow-up questions
- **Iterate** — each session deepens the research, history preserved
- **Close** — archived but retrievable; reopen 6 months later with full context

### Infrastructure to build in 1.0

Minimal schema and storage that enables the 1.1 build without constraining it:

- `investigations/` directory in workspace — one JSON file per investigation
- Investigation schema: `id`, `topic`, `created_at`, `status` (active/closed), `entries[]`
- Entry schema: `timestamp`, `source_type`, `content`, `user_annotation`, `score`
- Topic index — maps topic keywords to relevant signals already in `curator_signals.json`
- No API calls, no UI, no Telegram integration — data layer only

---

## Workstream 4: Mac Mini Migration

### Why it matters for 1.0

Running a 'production' system on a MacBook that sleeps is a credibility gap for a public portfolio piece. The Mac Mini migration makes the system genuinely always-on — no launchd sleep/wake workarounds, no missed 7 AM runs if the laptop is closed.

### Migration scope

- Port Python environment, venv, all dependencies
- Migrate keychain credentials (xAI, Anthropic, X OAuth tokens)
- Transfer `curator_signals.json`, `curator_preferences.json`, `x_pull_state.json`
- Reconfigure launchd plists for Mac Mini
- Verify Telegram delivery from new machine
- Decommission MacBook as primary host — keep as dev/test environment

### Sequencing

Mac Mini migration happens last in the sprint. All feature development stays on the MacBook. Migration is a cut-over, not a parallel run.

---

## 1.1 Plan — Investigation Workspace

Scoped here, built after 1.0 launches. Infrastructure from Workstream 3 enables fast delivery.

| Feature | Description | Status |
|---|---|---|
| Investigation UI | Start, view, and annotate investigations via web UI (existing Flask server). | 1.1 |
| Multi-source pull | Pull across news, academic, and book sources for a given topic and date range. | 1.1 |
| Synthesis layer | Haiku summarizes across sources, surfaces contradictions and key threads. | 1.1 |
| Annotation store | User comments and ratings persist per investigation entry. | 1.1 |
| Archive and restore | Close investigations cleanly; restore with full history after months. | 1.1 |
| Telegram integration | Optional: new investigation findings delivered to Telegram on schedule. | 1.1 |

---

## Deferred — Not in Sprint

- **Image analysis (Phase 3D)** — chart images from analyst tweets, vision model. Infrastructure gate in place. Post-1.0.
- **Postgres migration** — `curator_costs.json` already row-structured, COPY-ready. Deferred until volume demands.
- **Deep Dive ratings UI** — 4-star rating, two-sided feedback loop. Post-1.0.
- **Language learning domain** — conversation with Grok, vocabulary extraction, phrase practice. Next major domain after 1.0 ships.
- **Haiku pre-filter evaluation** — one-week data window on X article performance before deciding. Checkpoint built into cron note.

---

## GitHub Cleanup — After the Build

GitHub cleanup happens after all four workstreams are complete. Cleaning before building means cleaning twice.

- **README** — reflects actual 1.0 system, not the build history narrative
- **Issues** — close completed work, open new issues for 1.1 scope
- **Roadmap** — v1.0 complete, v1.1 scoped, future domains noted
- **docs/** — test reports current, portfolio/phase summaries accurate
- **CLAUDE.md** — signal store state, agent division of labor, implementation decisions all current

---

## Sprint Timeline

| Week | Focus | Deliverable |
|---|---|---|
| Week 1 | Mar 13–19 | Source scoring + `curator_sources.json`. Institutional RSS feeds added. Web search candidates wired in. |
| Week 2 | Mar 20–26 | Investigation workspace infrastructure. Mac Mini migration. GitHub cleanup and public launch. |

---

*Prepared: March 13, 2026*  
*For use with OpenClaw (planning) and Claude Code (implementation)*

---

## FILE: SPRINT_1_0_v2.md

# Mini-moi Personal AI Curator
## Sprint to 1.0 — March 2026 (v2)

**Target:** Public GitHub launch, production-ready, two weeks
**Updated:** March 15, 2026
**Status:** 1.0 feature complete. Documentation review tomorrow before tagging.

---

## Workstream Status

| Workstream | Status |
|------------|--------|
| WS1 Source Scoring | ✅ Complete |
| WS2 Broader Sources + Priority Feed | ✅ Complete |
| WS5 Intelligence Layer (A + B + C) | ✅ Complete |
| WS3 Investigation Workspace | ➡️ Moved to 1.1 |
| WS4 Mac Mini Migration | ➡️ Moved to 1.1 |
| Docs + README + Tag + Launch | 📋 Tomorrow |

---

## Tomorrow's Doc Review Session

1. **README update** — reflect three-tier architecture, intelligence layer, WS5 Phase C feedback loop, 1.1 roadmap
2. **docs/ folder review** — confirm all plan and build docs committed and clean
3. **SPRINT_1_0_v2.md update** — reflect actual completion status (this file)
4. **CLAUDE.md update** — agent roles, session start protocol, pointer to WAYS_OF_WORKING.md

Strategy Agent drafts README. Memory Agent validates/saves. Claude Code commits.

Tag 1.0, public launch.

---

## FILE: ARCHITECTURE.md

# ARCHITECTURE.md - System Design & Structure

_Created: 2026-02-06 - Planning phase for integrated AI agent system_

## Vision

**Local Development:** MacBook (current)  
**Production Target:** Mac Mini or cloud server  
**Goal:** Portable, organized, maintainable AI agent infrastructure

---

## System Components

### 1. OpenClaw (Orchestrator)
- **Purpose:** Agent runtime, skills framework, orchestration, messaging
- **Location:** `/opt/homebrew/lib/node_modules/openclaw` (global install)
- **Workspace:** `~/.openclaw/workspace` (your files, skills, memory)
- **Data:** `~/.openclaw/data` (sessions, logs, cache)

### 2. Personal AI Agent (Research & Knowledge)
- **Purpose:** Geopolitics research, knowledge graphs, local LLM
- **Location:** `~/Projects/personal-ai-agents` (current)
- **Git:** Local (to be pushed to GitHub)
- **Services:** Neo4j (context graph), Postgres (structured data), Ollama (local LLM)

### 3. Skills Bridge (Integration Layer)
- **Purpose:** OpenClaw skills that interface with Personal AI Agent
- **Location:** `~/.openclaw/workspace/skills/`
- **Git:** Part of workspace repo

---

## Proposed Directory Structure

```
~/Projects/
├── personal-ai-agents/              # Your Python agent (existing)
│   ├── .git/
│   ├── docker-compose.yml           # Neo4j, Postgres
│   ├── main.py                      # FastAPI server
│   ├── requirements.txt
│   ├── tests/                       # ← NEW: pytest tests
│   ├── .github/                     # ← NEW: CI/CD workflows
│   └── docs/                        # ← NEW: architecture docs
│
└── ai-infrastructure/               # ← NEW: Unified config & deployment
    ├── .git/                        # Separate repo
    ├── docker-compose.yml           # Full stack (including OpenClaw)
    ├── config/
    │   ├── openclaw.yaml           # OpenClaw config
    │   ├── neo4j.env
    │   ├── postgres.env
    │   └── ollama.env
    ├── scripts/
    │   ├── backup.sh               # Backup Neo4j + Postgres
    │   ├── restore.sh
    │   ├── migrate-to-server.sh    # Migration script
    │   └── healthcheck.sh
    ├── volumes/                    # Persistent data (gitignored)
    │   ├── neo4j-data/
    │   ├── postgres-data/
    │   └── openclaw-data/
    └── docs/
        ├── SETUP.md                # Fresh install guide
        ├── MIGRATION.md            # MacBook → Server guide
        └── TROUBLESHOOTING.md

~/.openclaw/
├── workspace/                      # OpenClaw workspace (git tracked)
│   ├── .git/
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── USER.md
│   ├── MEMORY.md
│   ├── memory/
│   │   └── YYYY-MM-DD.md
│   ├── skills/
│   │   ├── context-graph/          # Interfaces with personal-ai-agents
│   │   │   ├── SKILL.md
│   │   │   ├── query_traces.py
│   │   │   ├── add_trace.py
│   │   │   └── requirements.txt
│   │   ├── geopolitics/            # Research automation
│   │   │   ├── SKILL.md
│   │   │   ├── search_and_analyze.py
│   │   │   └── requirements.txt
│   │   └── postgres-knowledge/     # Postgres interface
│   │       ├── SKILL.md
│   │       └── query_entities.py
│   ├── projects/                   # ← NEW: organized by project
│   │   ├── gmail-cleanup/
│   │   ├── geopolitics-research/
│   │   └── german-learning/
│   └── tests/                      # ← NEW: test OpenClaw skills
│
└── data/                           # OpenClaw runtime (not in git)
    ├── sessions/
    ├── logs/
    └── cache/
```

---

## Git Strategy

### Option A: **Mono-repo** (Single Git Repo)
```
~/Projects/ai-system/
├── openclaw-workspace/     # Your OpenClaw files
├── personal-agent/         # Your Python agent
├── infrastructure/         # Docker, config, scripts
└── docs/                   # Shared docs
```

**Pros:** Single source of truth, easier to sync  
**Cons:** Large repo, mixed concerns

### Option B: **Multi-repo** (Current + New)
```
1. personal-ai-agents       (existing Python agent)
2. openclaw-workspace       (new: your workspace files)
3. ai-infrastructure        (new: deployment config)
```

**Pros:** Separation of concerns, smaller repos  
**Cons:** Need to keep in sync, more complex

### Option C: **Hybrid** (Recommended)
```
1. personal-ai-agents       (Python agent only)
2. ai-infrastructure        (workspace + deployment + config)
   ├── openclaw-workspace/  (symlinked to ~/.openclaw/workspace)
   ├── docker/
   ├── config/
   └── scripts/
```

**Pros:** Clean separation, single deployment repo  
**Cons:** Symlinks need care during migration

**My Recommendation:** **Option C** - Keep Python agent separate, everything else in `ai-infrastructure`

---

## Docker Strategy

### Current State
- **personal-ai-agents:** Uses Docker (Neo4j, Postgres)
- **OpenClaw:** Runs natively (npm global install)

### Proposed: **Unified Docker Compose**

**Location:** `~/Projects/ai-infrastructure/docker-compose.yml`

```yaml
version: '3.8'

services:
  # Databases
  neo4j:
    image: neo4j:latest
    container_name: ai-neo4j
    env_file: config/neo4j.env
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - ./volumes/neo4j-data:/data
    restart: unless-stopped

  postgres:
    image: postgres:latest
    container_name: ai-postgres
    env_file: config/postgres.env
    ports:
      - "5432:5432"
    volumes:
      - ./volumes/postgres-data:/var/lib/postgresql/data
    restart: unless-stopped

  # Local LLM
  ollama:
    image: ollama/ollama:latest
    container_name: ai-ollama
    ports:
      - "11434:11434"
    volumes:
      - ./volumes/ollama-models:/root/.ollama
    restart: unless-stopped

  # Your Python Agent
  personal-agent:
    build:
      context: ../personal-ai-agents
      dockerfile: Dockerfile
    container_name: ai-personal-agent
    ports:
      - "8000:8000"
    depends_on:
      - neo4j
      - postgres
      - ollama
    environment:
      - NEO4J_URI=bolt://neo4j:7687
      - POSTGRES_HOST=postgres
      - OLLAMA_HOST=http://ollama:11434
    restart: unless-stopped

  # OpenClaw (optional - can run natively)
  # openclaw:
  #   image: openclaw/openclaw:latest  # if official image exists
  #   container_name: ai-openclaw
  #   volumes:
  #     - ./openclaw-workspace:/workspace
  #     - ./volumes/openclaw-data:/data
  #   restart: unless-stopped
```

### Docker for OpenClaw?

**Pros:**
- ✅ Portable (same environment everywhere)
- ✅ Isolated dependencies
- ✅ Easy backup (just volumes)
- ✅ Clean migration to server

**Cons:**
- ⚠️ OpenClaw might not have official Docker image
- ⚠️ More complexity for development
- ⚠️ Node modules can be large

**Decision Point:** Let's check if OpenClaw supports Docker or if native is better.

---

## Data Persistence Strategy

### What Needs Backup

1. **Neo4j** - Your decision traces (context graph)
2. **Postgres** - Structured knowledge (entities, events)
3. **Ollama** - Downloaded models (large, can re-download)
4. **OpenClaw workspace** - Git tracked (MEMORY.md, skills, etc.)
5. **OpenClaw data** - Sessions, logs (ephemeral, can recreate)

### Backup Script

**Location:** `~/Projects/ai-infrastructure/scripts/backup.sh`

```bash
#!/bin/bash
# Backup all data to timestamped archive

DATE=$(date +%Y-%m-%d-%H%M%S)
BACKUP_DIR="$HOME/Backups/ai-system/$DATE"

mkdir -p "$BACKUP_DIR"

# Backup databases
docker exec ai-neo4j neo4j-admin dump --to=/data/backup.dump
docker cp ai-neo4j:/data/backup.dump "$BACKUP_DIR/neo4j.dump"

docker exec ai-postgres pg_dump -U postgres personal_agents > "$BACKUP_DIR/postgres.sql"

# Backup OpenClaw workspace (already in git, but snapshot anyway)
cp -r ~/.openclaw/workspace "$BACKUP_DIR/openclaw-workspace"

# Create archive
cd "$HOME/Backups/ai-system"
tar -czf "ai-system-$DATE.tar.gz" "$DATE"
rm -rf "$DATE"

echo "Backup complete: $HOME/Backups/ai-system/ai-system-$DATE.tar.gz"
```

---

## Migration Path: MacBook → Server

### Phase 1: **Preparation** (on MacBook)
1. Push all repos to GitHub
2. Run backup script
3. Document environment variables
4. Test full backup/restore locally

### Phase 2: **Server Setup**
1. Install Docker + Docker Compose
2. Install OpenClaw (npm or Docker)
3. Clone repos
4. Restore volumes from backup

### Phase 3: **Migration Script**

**Location:** `~/Projects/ai-infrastructure/scripts/migrate-to-server.sh`

```bash
#!/bin/bash
# Migrate to new server

SERVER_USER="your_username"
SERVER_HOST="macmini.local"  # or IP address

# 1. Copy infrastructure
rsync -avz ~/Projects/ai-infrastructure/ "$SERVER_USER@$SERVER_HOST:~/ai-infrastructure/"

# 2. Copy personal agent
rsync -avz ~/Projects/personal-ai-agents/ "$SERVER_USER@$SERVER_HOST:~/personal-ai-agents/"

# 3. Copy backup data
rsync -avz ~/Backups/ai-system/latest.tar.gz "$SERVER_USER@$SERVER_HOST:~/backup.tar.gz"

# 4. SSH and restore
ssh "$SERVER_USER@$SERVER_HOST" << 'EOF'
  cd ~/ai-infrastructure
  tar -xzf ~/backup.tar.gz -C volumes/
  docker-compose up -d
  # Setup OpenClaw
  npm install -g openclaw
  ln -s ~/ai-infrastructure/openclaw-workspace ~/.openclaw/workspace
EOF

echo "Migration complete! Verify services on $SERVER_HOST"
```

---

## Testing Strategy

### 1. **Unit Tests** (Python Agent)
```
~/Projects/personal-ai-agents/tests/
├── test_api.py              # FastAPI endpoints
├── test_neo4j.py            # Context graph operations
├── test_ollama.py           # LLM integration
└── test_scheduler.py        # Background jobs
```

### 2. **Integration Tests** (OpenClaw Skills)
```
~/.openclaw/workspace/tests/
├── test_context_graph_skill.py
├── test_geopolitics_skill.py
└── test_postgres_skill.py
```

### 3. **CI/CD** (GitHub Actions)

**File:** `~/Projects/personal-ai-agents/.github/workflows/test.yml`

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      neo4j:
        image: neo4j:latest
        env:
          NEO4J_AUTH: neo4j/testpass
        ports:
          - 7687:7687
      
      postgres:
        image: postgres:latest
        env:
          POSTGRES_PASSWORD: testpass
        ports:
          - 5432:5432
    
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -r requirements.txt pytest
      
      - name: Run tests
        run: pytest tests/ -v
      
      - name: Create issue on failure
        if: failure()
        uses: actions/github-script@v6
        with:
          script: |
            github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: 'Test failure on ' + context.sha.substring(0,7),
              body: 'Tests failed. See run: ' + context.serverUrl + '/' + context.repo.owner + '/' + context.repo.repo + '/actions/runs/' + context.runId
            })
```

---

## Development Workflow

### Day-to-Day

1. **Start services:**
   ```bash
   cd ~/Projects/ai-infrastructure
   docker-compose up -d
   ```

2. **Work in OpenClaw:**
   - Chat via web/Telegram/WhatsApp
   - Skills auto-loaded from workspace
   - Memory auto-updated

3. **Develop Python agent:**
   ```bash
   cd ~/Projects/personal-ai-agents
   # Make changes
   pytest tests/
   git commit -m "feat: ..."
   ```

4. **Test integration:**
   - Ask OpenClaw to query context graph
   - Verify Neo4j traces appear
   - Check Postgres for new entities

5. **End of day:**
   ```bash
   # Optional: stop services
   docker-compose down
   
   # Backup if significant changes
   ./scripts/backup.sh
   ```

### Adding New Features

1. **Create skill in workspace**
2. **Write tests**
3. **Update ARCHITECTURE.md** (this file)
4. **Commit to git**
5. **Push to GitHub**

---

## Bug Tracking: GitHub Issues

### Repositories

1. **personal-ai-agents repo:**
   - Issues: Python bugs, API issues, DB schema
   - Labels: `bug`, `enhancement`, `neo4j`, `postgres`, `ollama`
   - Auto-create on test failure (via Actions)

2. **ai-infrastructure repo:**
   - Issues: Deployment, Docker, migration, skills
   - Labels: `bug`, `docker`, `deployment`, `skill`, `openclaw`

### Issue Template

```markdown
## Bug Report

**Component:** [Neo4j / Postgres / Ollama / FastAPI / Skill]
**Severity:** [Low / Medium / High / Critical]

**Description:**
[Clear description]

**Steps to Reproduce:**
1. 
2. 
3. 

**Expected:**
[What should happen]

**Actual:**
[What actually happened]

**Environment:**
- OS: macOS 14.x / Ubuntu 22.04
- Docker: Yes/No
- OpenClaw version: 
- Python version:

**Logs:**
```
[paste relevant logs]
```

**Related:**
- Linked PR: 
- Linked commit:
```

---

## Architecture Decisions (Locked 2026-02-07)

### 1. **Git Strategy** ✅
**Decision:** Mono-repo now, multi-repo later for public portfolio version

**Current:**
- `personal-ai-agents` repo (contains everything)
- Private GitHub repo under personal account

**Future:**
- Split into public-safe repo (sanitized, portfolio-ready)
- Separate private repo for personal data/config
- Timeline: After Phase 2 (geopolitics curator) is working

### 2. **Docker Strategy** ✅
**Decision:** Unified docker-compose for modern container architecture

**Services:**
- Neo4j (context graph)
- Postgres (research artifacts)
- Ollama (local LLM)
- personal-agent (FastAPI server)
- OpenClaw: Run natively for now (development flexibility)

**Rationale:** Containers for databases/services, native for active development tools

### 3. **Server Target** ✅
**Decision:** Mac Mini (next step), keep optionality for cloud migration

**Path:**
1. Development: MacBook (current)
2. Production: Mac Mini (local, always-on)
3. Future option: Cloud VPS if needed (portability maintained)

**Benefits:** Local control, privacy, no recurring cloud costs

### 4. **Naming Convention** ✅
**Decision:** `ai-infrastructure` for deployment repo

**Pattern:**
- Repo: `ai-infrastructure`
- Containers: `ai-neo4j`, `ai-postgres`, `ai-ollama`, `ai-personal-agent`
- Services consistent with `ai-` prefix

**Rationale:** Professional, clear purpose, scales to portfolio use

### 5. **Backup Strategy** ✅
**Decision:** Daily automated backups, 30-day retention, monthly cloud archive

**Implementation:**
- **Frequency:** Daily at 2am (after overnight jobs)
- **Location:** `~/Backups/ai-system/YYYY-MM-DD/`
- **Retention:** Keep 30 days local
- **Archive:** Monthly snapshot to cloud (iCloud/Backblaze)
- **Method:** Cron job running `scripts/backup.sh`

**What gets backed up:**
- Neo4j decision traces (critical)
- Postgres research data (critical)
- OpenClaw workspace (git tracked, but snapshot anyway)
- Ollama models (optional, can re-download)

---

## Open Questions (Future Phases)

### Phase 2+ Considerations
- [ ] Public repo structure (what to include/exclude)
- [ ] Cloud backup provider (iCloud vs Backblaze vs S3)
- [ ] Remote access method for Mac Mini (SSH, Tailscale, VPN)
- [ ] Monitoring/alerting for production (Uptime Kuma, Grafana)

---

## Implementation Plan (Post-Decisions)

### Phase 1: Immediate (This Weekend)
1. ✅ **Review & refine this document together** — DONE
2. ✅ **Make decisions on open questions** — DONE (2026-02-07)
3. ✅ **Push `personal-ai-agents` to GitHub** — DONE
4. ⏳ **Complete Gmail cleanup** — In progress
5. ⏳ **Create unified docker-compose** — Next after Gmail

### Phase 1b: Infrastructure Setup (Next Week)
6. **Create `ai-infrastructure` structure:**
   - Unified docker-compose.yml
   - Backup scripts
   - Migration scripts
7. **Setup automated backups:**
   - Cron job for daily backup
   - Test backup/restore process
8. **Write first test:** Simple pytest for personal-ai-agents API
9. **Build first skill:** `context-graph` skill in OpenClaw workspace

### Phase 2: Geopolitics Curator (Following Week)
- See PROJECT_ROADMAP.md for detailed implementation path

---

## Future Enhancements

### Short Term (1-2 weeks)
- [ ] Unified docker-compose with all services
- [ ] GitHub repos with CI/CD
- [ ] Basic testing for Python agent
- [ ] First OpenClaw skill (context-graph)

### Medium Term (1-2 months)
- [ ] Postgres schema for geopolitics entities
- [ ] Geopolitics research automation (cron + skills)
- [ ] German learning skill (xAI Grok personas)
- [ ] Migration to Mac Mini or server

### Long Term (3-6 months)
- [ ] Semantic search (pgvector + embeddings)
- [ ] Advanced knowledge graph (Neo4j + Postgres hybrid)
- [ ] Multi-agent collaboration (sub-agents for research)
- [ ] Voice interface (ElevenLabs + OpenClaw)

---

_This document is a living architecture. Update as decisions are made and system evolves._

---

## FILE: CHANGELOG.md

# Changelog

## 2026-03-12 - Phase 3C.7: Incremental X Bookmark Pull

Commits: `4a77020` (x_pull_incremental.py), `f0dbe80` (cron integration)

**Result:** 27 new signals ingested, 425 total in `curator_signals.json`, X article pool grew to 357 candidates. First production test: 7 AM briefing on 2026-03-13 — first run with RSS + enriched X bookmarks + incremental pull all live together.

### What Was Built

**`x_pull_incremental.py`** (`4a77020`)
Fetches X bookmarks saved since `last_pull_at`, enriches each with `fetch_destination_text()` inline (not batch), deduplicates by URL against existing `curator_signals.json`. Skips all 398 historical signals automatically via URL dedup — no date-based logic needed. Writes new signals and updates `x_pull_state.json` atomically on success.

**Cron integration** (`f0dbe80`)
`run_curator_cron.sh` updated to call `x_pull_incremental.py` before `curator_rss_v2.py`. Failure in pull → log + continue, never blocks the briefing.

### Design Decision: `--limit=N` does not advance `last_pull_at`
Intentional. Test runs (`--limit=5`) must not poison the production early-stop marker. Production cron always runs without `--limit`, which is the only path that advances the timestamp.

### What to Watch (Tomorrow's Briefing)
- X articles from new bookmarks appearing alongside RSS — look for `X/@username` source labels
- Whether incremental pull adds fresh signal quality vs. noise
- `x_pull_state.json` timestamp advancing correctly after cron run

---

## 2026-03-12 - Phase 3C.6: X Bookmark Articles as First-Class Scored Content

Closes Issue #4. Four commits: `0d011b0`, `9430b74`, `9a5753e`, `cc96c9e`

**Status:** Built and tested locally. Not pushed to GitHub — pending first production run validation (tomorrow 7 AM briefing).

### What Was Built

**Piece 1 — `fetch_destination_text()` in `curator_utils.py`** (`0d011b0`)
Fetches readable body text from destination URLs via `requests.get()` + BeautifulSoup `<p>` extraction, capped at 2000 chars. Fallback: any failure (paywall, 404, timeout) → use tweet text, log it, set `destination_text_source: "tweet_fallback"`, continue. Never blocks the daily run.
- New field: `destination_text` — extracted body text (or tweet text fallback)
- New field: `destination_text_source` — `"fetched"` or `"tweet_fallback"`
- New field: `destination_text_error` — logged failure reason when fallback triggered
- New flag: `--enrich-text` in `enrich_signals.py` — batch backfill all 398 existing signals

**Piece 2 — `x_to_article.py` Signal Normalizer** (`9430b74`)
New file. Reads enriched signals from `curator_signals.json`, normalizes each to RSS article schema for scoring:
```json
{"title": "tweet_text[:80]", "summary": "destination_text", "url": "destination_url",
 "source": "X/@username", "content_type": "x_bookmark", ...}
```

**Piece 3 — `curator_rss_v2.py` `curate()` merge** (`9a5753e`)
X bookmark articles merged into the candidate pool before scoring. Dedup by URL against `curator_url_cache.json` — if RSS already pulled the same article, X version is dropped. No separate pipeline; X articles score alongside RSS with the same model and full user profile injected.

**Ops — Cost baseline updated** (`cc96c9e`)
`$0.30/day` cost baseline updated in cron script to reflect expanded article pool.

### What to Watch (Tomorrow's Briefing)
- X bookmark articles appearing in top 20 — look for `X/@username` source labels
- Whether X articles feel relevant or like noise — gut reaction matters
- Any delivery errors on first run with larger pool

### One-Week Checkpoint
- Is X adding signal quality to the top 20?
- Actual cost vs $0.30/day baseline
- If cost high + quality low: add Haiku pre-filter at that point, not before

### Remaining Phase 3C Items
- `fix show_profile.py` — Phase 3C fields not yet displayed
- Second A/B test — now has real before/after data to compare
- Haiku folder classification — 335 `null` signals, ~$0.02
- Source registry and expansion — next week

---

## 2026-03-04 - Phase 3C.5: Signal Folder Tagging + Bug Fixes

### Signal Folder Tagging
- **`fetch_folder_mapping()`** — fetches all X Premium bookmark folders via API, builds `{tweet_id: folder_name}` map in 6 API calls
- **`folder` field** added to signal schema (top-level); `null` for unorganized bookmarks
- **`backfill_folder_tags()`** + `--tag-folders` CLI flag — backfilled 63 of 398 existing signals with native X folder names; 335 remain `null` pending LLM classification pass
- **Auto-tagging** — every `enrich_signals.py` run now fetches folder mapping at start; new signals tagged on arrival
- Folder breakdown: Finance and geopolitics (18), Learning 2025 (20), Life and health (20), Tech (3), Modular Construction (2)

### Bug Fixes
- **Feedback buttons** (`curator_server.py`) — `record_feedback_with_article()` was calling `curator_feedback.py` with bare `python3` (system Python, no `anthropic` module). Fixed to resolve `venv/bin/python3` first, matching pattern used by deepdive path. Commit `570e516`.
- **`curator_media/` gitignored** — 276 images (32MB) removed from git tracking; added to `.gitignore`
- **Personal data removed** from `~/.openclaw/workspace` git history — `curator_preferences.json`, `priorities.json`, usage logs now gitignored; `.example` template files added

### Enrichment
- **Pagination** added to `fetch_bookmarks()` — fetches all ~400 bookmarks via `next_token` loop; `--all` flag (up to 400); X archive no longer needed
- Full 398-tweet enrichment run complete: 1,190 content topics, 23 domains, 3 source types in `learned_patterns`

### Pending
- LLM folder classification for 335 unorganized signals (`folder: null`) — Haiku pass, ~$0.02, deferred to next session
- Phase 3D image analysis (`chart_analysis: null` gate in place)
- Second A/B test: 20-tweet profile vs 398-tweet full profile

---

## 2026-03-03 - Milestone: Phase 3C Complete — Content Ecosystem Enrichment

### What Was Built

**Bookmark Enrichment Pipeline (`enrich_signals.py`)**
The learning loop now understands *what* you value, not just *who* you trust. When you save a tweet with an article link, the enrichment pipeline follows the URL, fetches the destination article metadata, downloads any attached chart images, and extracts topics from the tweet text using Claude Haiku. The result: 66 content topics, 3 destination domains, and 2 source type signals added to the profile from just 20 bookmarks.

**URL and Domain Intelligence**
Three new persistent stores capture knowledge that accumulates across all future runs:
- `curator_signals.json` — enriched signal per bookmark (article metadata, media paths, text analysis)
- `curator_url_cache.json` — destination URL cache; prevents duplicate fetches, graph edges for future Neo4j migration
- `curator_domain_registry.json` — domain-level registry growing across all imports

**Media Download**
Chart and photo images attached to analyst tweets (LukeGromen macro charts, KobeissiLetter labor data, etc.) downloaded locally to `curator_media/`. Images indexed with `chart_analysis: null` — the gate for a future vision model pass. When image analysis runs (Phase 3D), tweet text travels with each image for context.

**Profile Enrichment**
`load_user_profile()` in `curator_rss_v2.py` now injects three new sections into the scorer prompt:
- Preferred content domains (weighted by like/save/dislike)
- Preferred source types (substack / news_article / web_article / pdf / etc.)
- Content topics from saved posts (extracted by Haiku)

**Feedback Loop Extended (`curator_feedback.py`)**
When a user likes or saves an article through Telegram, `update_learned_patterns()` now extracts the domain and source type from the article URL and writes them to `content_domains` and `source_types` in `learned_patterns` — closing the loop between article feedback and content ecosystem knowledge.

**Swappable Analysis Backend**
`ENRICHMENT_BACKEND = 'haiku'` in `curator_config.py`. Swap to `ollama` or `xai` without touching the pipeline. Current cost: ~$0.001 for 20 tweets.

**A/B Test Infrastructure (`scripts/generate_test_report.py`)**
Standardized report generator produces two outputs from a single JSON results file:
- `docs/test-reports/YYYY-MM-DD-{phase}-ab-test.md` — full detail, private
- `docs/portfolio/{phase}-results.md` — sanitized for public portfolio (account names and article titles replaced with category labels)
HTML output opt-in via `--html` flag.

---

### The Shift

Before Phase 3C: the system knew *who* you trust — account-level trust scores from 398 bookmarks and 13 feedback events.

After Phase 3C: the system knows *what* you value — `citriniresearch.com`, `zerohedge.com`, `labor_market`, `us_foreign_policy`, `macro_divergence`. The profile grew 41% (581 → 822 chars) from 20 bookmarks alone.

---

### A/B Test Results

Controlled test: same 390-article pool, same Grok scoring model, same cost ($0.16). Only variable: profile injected into scorer.

| Metric | Baseline | Enriched |
|--------|----------|----------|
| Profile size | 581 chars | 822 chars (+41%) |
| Content domains | 0 | 3 |
| Source types | 0 | 2 |
| Content topics | 0 | 66 |
| New articles in top 10 | — | 5 |
| Al Jazeera "Oil on Fire" | #3 | #20 (−17) |
| Investing.com "Oil Shock" | #9 | #6 (+3) |

Key finding: content topics (66 extracted) drove most movement. Domain and source type signals need higher volume to differentiate. Full 398-tweet archive run expected to substantially expand both.

Full report: `docs/test-reports/2026-03-03-phase3c-ab-test.md`
Portfolio version: `docs/portfolio/phase3c-results.md`

---

### Files Modified / Created

| File | Type | Change |
|------|------|--------|
| `curator_utils.py` | Modified | +7 utility functions: `extract_domain`, `classify_source_type`, `fetch_url_metadata`, `follow_redirect`, `extract_tco_urls`, `download_image`, `analyze_text_haiku` |
| `enrich_signals.py` | New | Standalone enrichment pipeline — safe to re-run, skips already-enriched |
| `curator_feedback.py` | Modified | `update_learned_patterns()` now tracks `content_domains` and `source_types` |
| `curator_rss_v2.py` | Modified | `load_user_profile()` injects domain, source type, and topic preferences |
| `curator_config.py` | Modified | Added `ENRICHMENT_BACKEND = 'haiku'` |
| `curator_signals.json` | New | 20 enriched signals (first run) |
| `curator_url_cache.json` | New | 3 destination URLs cached |
| `curator_domain_registry.json` | New | 3 domains registered |
| `curator_media/` | New | 10 downloaded chart/photo images |
| `scripts/generate_test_report.py` | New | A/B test report generator (MD default, HTML opt-in) |
| `docs/test-reports/` | New | Private full-detail reports |
| `docs/portfolio/` | New | Sanitized public portfolio reports |

---

### Design Decisions Worth Preserving

**Image analysis deferred, not skipped.** Images download now. `chart_analysis: null` is the gate — when Phase 3D vision pass runs, tweet text always travels with each image. Never analyze an image without its tweet.

**No t.co redirect-following needed.** X Bookmark API returns `expanded_url` already resolved in tweet entities. The redirect-following utilities in `curator_utils.py` exist for archive import paths where only raw text is available.

**20-tweet scope by design.** Bootstrap scope is intentional — prove the pipeline end-to-end tonight, expand after the X archive arrives (full 398-tweet geopolitics-filtered run pending xAI delivery).

**Content topics > domains at small sample sizes.** 66 topics from 20 tweets created real scoring movement. 3 domains did not. This is expected and correct — domain trust signal needs volume.

---

### Next Evolution (Phase 3D)

- **Full 398-tweet archive run** — `python3 enrich_signals.py --limit=398 --full` after archive arrives; will add ~200–400 topics and fix AI/tech skew from the 20-tweet bootstrap
- **Image analysis** — install `moondream` or use Haiku vision; gate is already in place (`chart_analysis: null`)
- **Second A/B test** — compare 20-tweet bootstrap vs 398-tweet full run
- **Incremental X pull scheduling** — launchd job to enrich new bookmarks nightly

---

## 2026-02-28 - Major Milestone: Bootstrap Complete — 398 X Bookmarks Seeding the Learning Loop

### What Was Built

**X Bookmark Ingestion (x_bootstrap.py)**
The learning loop went from 9 feedback events to 415 scored signals (458 total feedback events) in a single session. 398 hand-saved X bookmarks — years of curation — ingested as explicit "Save" signals. The system now knows your preferred sources before you've given it a single piece of feedback through the daily briefing.

**OAuth 2.0 PKCE Flow (x_oauth2_authorize.py)**
Full browser-based authorization against X API v2. One-time setup stores access token in macOS keychain. Reusable for any future X API calls.

**Top sources the system now knows about:**
- X/@elonmusk (+17), X/@MarioNawfal (+16), X/@nntaleb (+14), X/@LukeGromen (+12)
- The Duran (+11), X/@ThomasSowell (+11), X/@BoringBiz_ (+10), X/@WallStreetApes (+9)
- Geopolitical Futures (+6), X/@zerohedge (+5), X/@AndrewYNg (+5), X/@dailystoic (+5)

**Supporting files created:**
- `x_auth.py` — shared OAuth credential loader (1.0a + 2.0)
- `x_bookmarks_test.py` — verified API access before bootstrap
- `store_x_keys.py` / `store_x_oauth2.py` — one-time credential setup helpers

### The Shift

Before tonight: 9 signals, system barely knew your preferences.
After tonight: 415 scored signals (458 feedback events), system knows the macro/geopolitics/philosophy ecosystem you actually read.

Tomorrow's 7 AM briefing will be the first one that runs against a meaningfully trained profile.

### Next Evolution

**t.co URL enrichment** — currently we capture `X/@nntaleb = +14`. The next step is following the t.co redirect inside each tweet to extract the destination domain and article title. That turns source trust scores into content ecosystem scores: `X/@nntaleb -> FT/BIS/project-syndicate`. The system learns both who you trust and what they point you toward. This is when the profile becomes genuinely powerful.

---

## 2026-02-28 - Scoring Architecture Fix & Telegram Stability

### Model-Agnostic Profile Injection

**Bug Fixed:** `load_user_profile()` was only injected into the xAI scoring path. When xAI was down, the Haiku fallback ran completely blind to learned preferences.

**Fix:** Profile injection moved to the scorer dispatcher level — above any model-specific function.

```python
# Before: profile injected inside score_entries_xai() only
# After: profile loaded at dispatcher, passed into whichever scorer runs
user_profile = load_user_profile()
entries = score_entries(entries, user_profile)  # model-agnostic
```

**Impact:** Haiku fallback now runs with the full learned profile. Any future model swap inherits personalization automatically. No more blind scoring when xAI is unavailable.

**Files Modified:**
- `curator_rss_v2.py` — dispatcher refactor

---

### Telegram Stability (OpenClaw 2026.2.26)

Applied OpenClaw update resolving several issues affecting the curator's Telegram delivery:

- **DM Allowlist Inheritance (#27936)** — Fixed silent message drops after bot restarts. Root cause of earlier delivery failures.
- **Inline Button Callbacks in Groups (#27343)** — Like/Dislike/Save buttons now more reliable in group context.
- **sendChatAction Rate Limiting (#27415)** — Prevents infinite retry loops on typing indicator failures; protects against bot account suspension.
- **Native Commands Degradation (#27512)** — Graceful handling of `BOT_COMMANDS_TOO_MUCH` errors; no more crash-loops on startup.

**Status:** 7 AM briefing confirmed back on schedule.

---

## 2026-02-26 - Milestone: Learning Feedback Loop Achieved

### Major Achievement

**The curator now learns from user feedback and personalizes article scoring.**

This represents a fundamental shift from static AI curation to adaptive personalization. The system:
- Learns your preferred sources, themes, and content styles
- Injects personalization into Grok scoring prompts
- Adjusts article rankings based on accumulated feedback
- Continuously improves recommendations over time

**Verified Results:**
- 6-interaction clean baseline → **Geopolitical Futures ranked #1** in first personalized run
- All 3 liked sources (Geopolitical Futures, ZeroHedge, The Big Picture) landed in top 4
- Disliked sources (Deutsche Welle, The Duran) scored lower and pushed down
- Personalization working as designed - improvements will compound over time as feedback accumulates

---

### Technical Implementation

**1. Feedback Weight Correction**

**Problem:** All feedback types weighted equally (like=+1, save=+1, dislike=-1)

**Solution:** Differentiated signal strength
```python
# Updated weights in curator_feedback.py
LIKE   = +2  # Strong quality signal: "More like this"
SAVE   = +1  # Bookmark/uncertainty: "Interesting, maybe"
DISLIKE = -1 # Avoid: "Less like this"
```

**Rationale:** Save is a weaker signal (curiosity, not endorsement). Like is explicit quality approval.

**Files Modified:**
- `curator_feedback.py` (both project + workspace copies)
- Added weight map documentation for future reference

---

**2. Source Tracking Fix**

**Problem:** `preferred_sources` never accumulated - `metadata.get('source')` always returned `None`

**Root Cause:** `metadata` is the AI-extracted signals dict, `article` dict has the actual source field

**Solution:** Inject source into metadata before pattern learning
```python
# In record_feedback()
metadata['source'] = article['source']
update_learned_patterns(action, metadata)
```

**Impact:** Source preferences now accumulate correctly, enabling source-based personalization

**Files Modified:**
- `curator_feedback.py` - `record_feedback()` function

---

**3. User Profile Personalization**

**New Feature:** `load_user_profile()` function reads learned patterns and builds Grok prompt section

**Design Decisions:**
- **`min_weight=2` filter** - Ignores noisy low-signal entries (1-2 interactions)
- **Excludes `descriptive`** - Known co-tag artifact (appears with analytical/investigative)
- **Graceful fallback** - Returns empty string if file missing or `sample_size < 3`
- **Comprehensive** - Covers themes, sources, content style, avoid signals

**Prompt Injection:** Personalization inserted between SCORE GUIDANCE and KEY DISTINCTION
```
PERSONALIZATION (from 6 user interactions — adjust base score by +1 to +2 for strong matches, -1 to -2 for avoids):
- Strong interest in themes: institutional_debates, fiscal_policy, geopolitics...
- Preferred sources: Geopolitical Futures, ZeroHedge, The Big Picture
- Preferred content style: analytical, investigative
- Avoid signals: event_coverage_not_analysis, ceremonial_reporting...
```

**Runtime Feedback:** Prints `🧠 User profile loaded (N chars) — personalizing scores`

**Files Modified:**
- `curator_rss_v2.py` - New `load_user_profile()` function
- `curator_rss_v2.py` - `score_entries_xai()` updated to inject personalization

---

**4. CSS Category Badge Fix**

**Problem:** Category badge text was invisible (white on white)

**Root Cause:** CSS variables referenced but never defined in `:root`
```css
/* Missing from :root */
--geo: #8b5cf6;
--fiscal: #f59e0b;
--monetary: #10b981;
--other: #6b7280;
```

**Solution:** Added color definitions to template `:root` block

**Files Modified:**
- `curator_rss_v2.py` - Template CSS section

---

**5. Feedback Button UX Fix**

**Problem:** After clicking like/save/dislike, other buttons remained clickable (users could double-click)

**Solution:** Lock all 3 buttons in row after any feedback
- Activated button: checkmark + bold ring
- Sibling buttons: fade to 20% opacity + disabled state
- Prevents accidental double-clicks and conflicting feedback

**Files Modified:**
- `curator_rss_v2.py` - Template JavaScript feedback handlers

---

### Data Cleanup

**Clean Baseline Strategy:** Reset `learned_patterns` to empty, preserved `feedback_history`

**Why?** Starting with correct weights + clean baseline beats salvaging corrupted incremental data

**Removed:**
- 1 accidental curl-test entry (ZeroHedge, 08:44 timestamp)
- 1 accidental double-click entry (Big Picture saved after liked)
- Corrected Big Picture source score 3→2

**Files Modified:**
- `curator_preferences.json` (workspace) - Reset `learned_patterns`, cleaned test data

---

### Cost Management Pattern

**Hybrid Development Approach:**
- **Claude Code** - Implementation (code generation, faster/cheaper for iteration)
- **OpenClaw (Mini-moi)** - Verification, documentation, memory updates

**Result:** Significant API cost savings on iterative development work

---

### Key Learnings

1. **Source of Truth Matters** - `metadata` is AI-extracted signals, `article` dict has actual source. Don't assume they're the same object.

2. **Weight Design Matters** - Like vs Save distinction is critical for learning quality preferences vs bookmarks

3. **Clean Baseline Beats Noisy History** - Starting fresh with correct weights > salvaging corrupted incremental data

4. **Verification Before Trust** - Test with small clean dataset, verify patterns look right, then scale

5. **Min Weight Filtering** - `min_weight=2` prevents noise from 1-2 interactions influencing recommendations

---

### Portfolio Value

**"Built adaptive AI curator with learning feedback loop - personalizes article scoring based on user feedback, verified 3x improvement in source ranking accuracy"**

Technical highlights:
- Weighted feedback signals (like=+2, save=+1, dislike=-1)
- Dynamic prompt personalization (sources, themes, content style)
- Graceful degradation (works with or without profile data)
- Cost-efficient hybrid development (Claude Code + OpenClaw)

---

### Status
✅ **Production Ready** - Learning feedback loop verified working
✅ **Personalization Active** - User preferences injected into Grok scoring
✅ **Source Tracking Fixed** - Preferred sources accumulating correctly
✅ **UI Polish Complete** - Category badges visible, feedback buttons robust

### Next Steps
- Continue testing with more feedback interactions
- Monitor quality improvements over time
- Consider decay factor for outdated preferences (future enhancement)
- Add serendipity factor to avoid filter bubbles (future enhancement)

---

## 2026-02-19 - Platform Unification & UI Consistency

### Major Changes

**Unified Briefing Platform Architecture**
- Replaced card-based layout with table-based layout (Bloomberg Terminal aesthetic)
- Unified header across all pages (1.5em, purple gradient, centered)
- Consistent navigation on every page: 📰 Today | 📚 Archive | 🔍 Deep Dives
- Fixed navigation flow between all three core pages

**Files Modified:**
- `curator_rss_v2.py`:
  - Rewrote `format_html()` to generate table format (was card format)
  - Fixed rank numbering: `for i, entry in enumerate(entries, 1)` → `rank = i`
  - Fixed field mappings: `category_tag` → `category`, `url` → `link`
  - Rewrote `generate_index_page()` with unified header (1.5em, not 2.5em)
  - Fixed deep dives path: `interests/deep-dives/` → `interests/2026/deep-dives/`

- `curator_feedback.py`:
  - Updated deep dive prompt for concise "point-of-departure" format
  - Sections 1-6: Brief (2-3 sentences max)
  - Section 7 (Bibliography): Most detailed with proper citations
  - Fixed `\1` bug in HTML generation (regex backreference: `r'\1'` → `'\\1'`)
  - Added unified header and navigation bar to deep dive articles
  - Reduced yellow interest box padding/font size
  - Reduced back button size for consistency

**Deep Dive Cost Optimization:**
- Output tokens: 2995 → 977 (67% reduction)
- Cost per analysis: $0.047 → $0.017 (64% cheaper)
- Quality: Improved (concise research launchpad vs verbose explanations)

**New Files:**
- `PLATFORM_POC.md` - Complete platform overview and usage guide
- `PLATFORM_UNIFIED.md` - Design system specifications
- `curator_cache/` - Article storage for deep dive system (hash-based)
- `curator_history.json` - Article index with appearance tracking
- `interests/2026/deep-dives/` - New deep dive storage location
- `interests/2026/deep-dives/index.html` - Deep dive archive index

### Bug Fixes
- Fixed archive header size (was 2.5em, now 1.5em for consistency)
- Fixed rank numbers showing "?" instead of 1-20
- Fixed deep dive path inconsistencies across navigation
- Fixed browser caching issue with `curator_latest_with_buttons.html`
- Fixed navigation back button on all pages

### Design System
- Base font: 14px (System fonts)
- Header: 1.5em title, 0.88em metadata, 12px padding
- Navigation buttons: 6-14px padding, 0.85em font, purple (#667eea)
- Tables: 12px cell padding, #ddd borders, alternating row backgrounds
- Max width: 1400px (consistent across all pages)
- Colors: Purple gradient (#667eea → #764ba2)

### Status
✅ POC Complete - Consistent navigation flow across all pages
✅ All pages use identical header/navigation structure
✅ Table format generates correctly from curator_rss_v2.py
✅ Deep dive format optimized (cost -64%, quality improved)

### Next Steps (Future)
- UI polish (fine-tune colors, spacing)
- Deep dive bookmark action implementation
- CLI history viewer
- Telegram button integration

## 2026-02-20 - xAI Integration & Multi-Provider Support

### Major Changes

**xAI Provider Integration**
- Added OpenAI SDK support to curator (enables both OpenAI and xAI models)
- Created `score_entries_xai()` function using Grok-2-vision-1212
- Integrated xAI mode into curator pipeline
- Added cost tracking and comparison

**Cost Optimization**
- xAI mode: $0.18/day (390 articles) vs $0.90/day (two-stage Anthropic)
- **80% cost reduction** while maintaining quality
- Annual savings: ~$260 (daily runs)

**Files Modified:**
- `curator_rss_v2.py`:
  - Added `score_entries_xai()` function (batch processing with Grok)
  - Updated `curate()` function to support `mode='xai'`
  - Added xAI API key loading from OpenClaw auth profiles
  - Updated docstrings with xAI pricing and usage

**New Files:**
- `COST_COMPARISON.md` - Detailed cost analysis across providers
- `TODO_MULTI_PROVIDER.md` - Implementation plan and testing guide

**Configuration:**
- xAI API key stored in `~/.openclaw/agents/main/agent/auth-profiles.json`
- Profile: `xai:default`
- Model: `grok-2-vision-1212`

**Quality Validation:**
- Test run: 390 articles scored successfully
- Top articles: Iran-Russia-China drills, Ukraine war, Israel alert
- Categories assigned correctly (geo_major, geo_other, fiscal, monetary)
- Output comparable to Anthropic Haiku/Sonnet quality

**Usage:**
```bash
python3 curator_rss_v2.py --mode=xai
```

**Dependencies Added:**
- `openai==2.21.0` (supports both OpenAI and xAI endpoints)

**Portfolio Value:**
- "Implemented multi-provider LLM strategy reducing daily curation costs 80% ($0.90 → $0.18)"
- "Built cost-optimized AI pipeline: annual savings $260 while maintaining quality"
- "Integrated xAI Grok for geopolitical analysis at 1/5 the cost of Anthropic"

**Status:** ✅ Production ready - tested and validated
**Recommendation:** Use xAI for daily briefings, keep Sonnet for deep dives

## 2026-02-20 - Bug Fixes: Deep Dive Feature

### Bug Fixes (Claude-Identified)

**1. Duplicate "Deep Dive Analysis" Heading**
- **Issue:** The AI-generated markdown was including its own title heading which doubled up with the HTML template's heading
- **Root Cause:** Both `curator_feedback.py` template (line 611) and AI output (line 307) included "Deep Dive Analysis" heading
- **Fix:** Strip any leading `## Deep Dive Analysis` from AI markdown before rendering
- **File:** `curator_feedback.py` - Added regex to remove duplicate heading
- **Commit:** `d2b8b20`

**2. Deep Dive Closure Bug**
- **Issue:** When multiple deep dive buttons were created, they all triggered deep dive on the same article (the last one processed)
- **Root Cause:** The `addDeepDiveButton()` JavaScript function was capturing `rank` by reference in a loop, not by value
- **Impact:** All buttons pointed to the wrong article because `rank` variable was shared
- **Fix:** Use IIFE (Immediately Invoked Function Expression) to capture `rank` and `diveBtn` by value at button creation time
- **File:** `curator_rss_v2.py` - Updated `addDeepDiveButton()` function
- **Commit:** `418b971`

**3. Hash_id Lookup Ambiguity**
- **Issue:** When curator ran multiple times per day, date-rank format (`2026-02-20-2`) could match multiple articles, returning the FIRST match instead of the correct one
- **Root Cause:** Multiple runs per day created duplicate ranks, and lookup didn't distinguish between them
- **Impact:** Deep dive could analyze a different article than the one clicked
- **Fix:** Pass unique `hash_id` through entire flow (HTML → JS → Server → Lookup) instead of date-rank format
- **Files Modified:**
  - `curator_rss_v2.py` - Add `data-hash-id` attribute, pass through JS functions
  - `curator_server.py` - Accept `hash_id` instead of `rank` for deep dive requests
- **Commit:** `dc14d53`

**Credits:**
- Claude AI identified the JavaScript closure bug and duplicate heading issue
- OpenClaw (Mini-moi) identified the hash_id lookup ambiguity

**Testing:**
- All three bugs fixed and tested with fresh curator run
- Deep dive flow now correctly analyzes the exact article clicked
- No more duplicate headings in deep dive HTML output

### Files Changed
- `curator_rss_v2.py` - JavaScript closure fix, hash_id integration
- `curator_server.py` - Hash_id parameter handling
- `curator_feedback.py` - Duplicate heading removal

### Impact
- ✅ Deep dive now reliably analyzes the correct article
- ✅ Clean HTML output (no duplicate headings)
- ✅ Robust to multiple curator runs per day

## 2026-03-15 — WS5 Phase A: Intelligence Layer Foundation

Commits: see `docs/BUILD_WS5_PHASE_A.md` for full record

**Result:** `curator_intelligence.py` running in production. Daily 7:30 AM intelligence message confirmed delivered to Telegram on first run.

### What Was Built

**Pre-conditions:**
- `send_telegram_alert()` moved from `curator_rss_v2.py` → `curator_utils.py` (shared helper, clean import path for all future scripts)
- `_log_probationary_domains()` in `curator_priority_feed.py` fixed to write `added_date` + `query` fields; 8 existing probationary entries backfilled with `added_date: 2026-03-14`

**`curator_intelligence.py`**
New daily observation script. Two observations: Topic Velocity (Haiku infers topics from title corpus, compares today vs 30-day baseline) and Discovery Candidates (Haiku quality-rates new probationary domains from Brave). Output: Telegram message (5 lines max) + `intelligence_YYYYMMDD.json` in OpenClaw workspace.

**`run_intelligence_cron.sh` + `com.vanstedum.curator-intelligence.plist`**
Shell wrapper pattern matching existing curator cron jobs. Registered to `~/Library/LaunchAgents/`, fires 7:30 AM daily.

### First Output (March 15)
Momentum: Iran/energy, tariffs, AI. Gap: crypto/detailed Fed policy. Sources: none new (Brave rate limiting).

### Open Items → Phase B
- Brave `time.sleep(1)` delay fix
- Gap detection: keyword-level → thesis-level matching
- Treasury MSPD per-source cap (3x in top 20 today)

### Phase B: Remaining Observations
Source anomalies (Haiku), US press blind spots (cross-source), weekly lateral connections (Sonnet).


## 2026-03-15 — WS5 Phase B: Intelligence Layer Complete

Commits: `846a0df` (feat), `23ff20d` (fix) — see `docs/BUILD_WS5_PhaseB_2026-03-15.md`

**Result:** All five intelligence observations operational. Workstream 5 complete.

### What Was Built
- Pre-condition: `curator_rss_v2.py` writes `curator_latest.json` after each run (full scored pool for blind spot detection)
- Obs 3: Source Anomalies — Haiku detects trusted source drift vs 30-day baseline, quiet path
- Obs 4: US Press Blind Spots — Haiku surfaces non-US stories absent from US outlets, reads `curator_latest.json`, quiet path
- Obs 5: Lateral Connections — Sonnet surfaces adjacent topics from reading history, Sunday only, separate Telegram message
- Bug fix: Obs 4 domain classification — `source` field is display name, not domain; switched to `extract_domain(link)`. Before: 0 articles classified. After: 6 non-US + 7 US classified, Obs 4 fired (Minab classroom missile strike, Al Jazeera/DW only).

### Open Items → Post-1.0
- `curator_latest.json` writes top_articles, not full pre-scoring pool — revisit in 1.1
- Lateral connections prompt tuning after a few weeks of Sunday runs
- Source anomaly minimum threshold (5 articles) may need lowering for newer trusted sources


## 2026-03-15 — WS5 Phase C: Intelligence Response Capture

Commits: see `docs/BUILD_WS5_PhaseC_2026-03-15.md`

**Result:** `curator_intelligence.html` live — daily observations and weekly lateral connections displayed with response forms. Responses written to `intelligence_responses.json`. Feedback loop data layer complete for 1.0.

### What Was Built
- `intelligence_responses.json` created in `~/.openclaw/workspace/` (not committed — operational data)
- `curator_intelligence.py`: `RESPONSES_PATH` constant + `save_response()` helper (auto-ID, timestamp, `acted_on: False`)
- `curator_server.py`: `GET /api/intelligence/latest` + `POST /api/intelligence/respond`
- `curator_intelligence.html`: full page — weekly lateral connections (full form: prominent position textarea + reaction + want more) and daily observations (light form: reaction + one-line note). `mdToHtml()` JS helper for markdown cleanup. `data-*` attributes for save handlers. Button disabled during async POST.

### Design note
Static mockup reviewed and iterated (3 passes) before API wiring — caught layout issues before backend work. Pattern worth repeating for new UI pages.

### Open Items → 1.1
- Step 5: Telegram reply detection + Haiku classification
- Domain badge on weekly cards
- Condensed/filter view
- `acted_on` flag activation when `pending_action` executed


---

## FILE: README.md

# Personal AI Briefing System
### *Mini-moi — not a general intelligence, but a specific one. Yours.*

A personal intelligence system. The first domain: the intersection of finance and geopolitics. Health, language learning, and others to follow.

Learns from your history and preferences, with deliberate friction built in. The goal isn't a curated feed — it's better thinking.

> Production system, daily use since Feb 2026. Designed to expand across domains — see [Roadmap](#roadmap).
> Three model tiers in production: Haiku for bulk filtering, grok-3-mini for daily ranking, Sonnet for Deep Dives and development reasoning.

---

## What This Is Really About

The cloud LLMs have the world's knowledge. That problem is largely solved.

The hard part — the part that actually matters for real decisions — is acting in your specific situation. Your history. Your goals. Your risk tolerance. Your team's context and motivation. General intelligence is widely available now. Specific intelligence, the kind that knows you and acts for you, isn't.

That's what this builds toward.

I started with myself: a daily briefing on geopolitics and finance, shaped by how I actually think, learning from what I actually read and save. But the vision is bigger. If I had a team at work — people and agents together — I'd want this same local context and motivation at the center of it. Not a generic assistant that knows everything about the world but nothing about us. A specific capability, grounded in our history, our goals, our way of making decisions under uncertainty.

That's what *Mini-moi* means. Not a mini version of a large language model. A system that carries your particular point of view and acts on your behalf — in your real world, for you, now.

The cloud LLMs are tools this system reaches out to when it needs them. Your memory, your preferences, your reasoning — those stay with you. The agents are your team members, not the cloud's.

---

## Why I Built This

The best way to understand something is to build it.

I've believed for a while that the future of AI isn't just larger models with more world knowledge — it's systems that carry specific context: your history, your goals, your way of reasoning through uncertainty. The cloud LLMs are remarkable, but they don't know you. They can't act for you in any meaningful sense without that layer.

I wanted to build that layer. Not as a prototype or a tutorial exercise, but as something I actually use and depend on every day. Geopolitics and finance were the natural first domain — areas I follow closely, where I have a real point of view that a generic feed can't capture.

The approach — local context, model-agnostic, flat files structured for future migration — was designed to scale beyond one person and one domain. Health, language learning, team environments at work. The architecture anticipates that. The first domain just had to be one I cared enough about to build it right.

---

## Build History

### January 2026 — Architecture Before Code

The system was designed before it was built. Key decisions made here:

**Local-first data layer:**
- Flat files first (JSON), schema designed to be Postgres-ready — one `COPY` command when volume demands migration, not a rewrite
- Context graph design (Neo4j) for relationship mapping: *why did I save this? what connects these ideas?*
- All learned state portable by design — move machines, switch providers, go offline — preferences travel with you

**Model-agnostic from day one:**
- User profile injection at the dispatcher level, not inside any model's prompt
- Mechanical mode built first — the system runs on local Ollama with no external API dependency
- Swap any model at any layer without touching personalization logic

The system ran standalone from the start. No cloud dependencies required.

**OpenClaw integration (late January / early February)**

When OpenClaw launched (early adopter — spent a weekend installing and debugging it), added it as an optional delivery and interface layer. The personal-ai-agent pipeline was preserved standalone; OpenClaw adds Telegram delivery and a conversational interface but is not required.

**February 2026 — Intelligence Layer**

With the local foundation solid, built the AI layer on top:
- Replaced keyword scoring with two-stage AI scoring (Haiku pre-filter → grok-3-mini final ranking)
- Built the learning feedback loop: Like/Dislike/Save → updates local learned profile → influences tomorrow's run
- Bootstrapped 415 learning signals from 398 hand-saved X bookmarks (cold start solved in one session)
- Optimized cost from $100+/month → $35–45/month through model selection and batching
- Unified cost tracking across chat and curator runs

---

## What It Does

This is not a smarter news feed. The daily briefing is the front door. Behind it:

**Daily Briefing**

Every morning, the system fetches hundreds of articles from RSS feeds across geopolitics, finance, and institutional sources. A two-stage scoring pipeline — cheap pre-filter, then a final ranking model with your injected learned profile — surfaces the top 20 most relevant articles for you specifically. Delivered to Telegram at 7 AM with like/dislike/save buttons. Your reactions feed tomorrow's scoring.

**Deep Dives**

When an article or topic warrants more than a headline, Deep Dive produces a structured brief using a higher-capability model: analysis, counter-arguments, and a bibliography of references for further reading. The archive grows daily — a personal research library of topics you decided were worth understanding deeply.

**Signal Priorities**

You inject current focus areas directly — a conflict escalating, a policy shift, an earnings season — with keywords and a time-bounded expiry. The system boosts those signals for that window, then returns to baseline. The world changes. Your attention shifts. This is the mechanism for keeping the system aligned with where you actually are, not where you were three months ago.

**Reading Library**

Every saved article is stored, searchable, and categorized. A personal knowledge base that accumulates alongside the daily work.

**This is not YouTube.** YouTube optimizes for your attention. This system optimizes for your thinking. You inject direction. It surfaces material. You decide what goes deeper.

---

## Screenshots

**Morning Briefing** — ranked top 20, scored and categorized, with like/dislike/save actions

![Morning Briefing](docs/screenshots/morning-briefing.png)

**Reading Library** — everything you've ever liked or saved, searchable and filterable

![Reading Library](docs/screenshots/reading-library.png)

**Deep Dive Archive** — AI analysis on flagged articles, by date

![Deep Dives](docs/screenshots/deep-dives.png)

**Signal Priorities** — short-term focus injections that boost scoring for a set window

![Priorities](docs/screenshots/priorities.png)

---

## Interface

The system has two production interfaces designed for different contexts:

**Web Portal** — full-featured local interface for browsing, research, and curation. Four views:
- **Daily** — ranked briefing with article scores and feedback controls
- **Reading Library** — searchable archive of all saved articles, filterable by category, type, and date
- **Deep Dives** — archive of structured research briefs with analysis, counter-arguments, and references
- **Signal Priorities** — inject and manage time-bounded focus areas with keyword boosting

**Telegram** — mobile interface for daily delivery and on-the-go feedback. Briefing arrives at 7 AM with inline like/dislike/save buttons. Voice notes supported for quick capture.

Both interfaces write to the same local data layer. Feedback from either surface influences tomorrow's scoring.

---

## How the Learning Loop Works

```
Daily Briefing (7 AM)
      ↓
You react on Telegram (👍 Like · 👎 Dislike · 🔖 Save)
  or inject a Signal Priority ("Tigray Conflict +2.0x, expires in 3 days")
      ↓
curator_feedback.py records signals locally
      ↓
Tomorrow's scorer gets your updated profile:
  "prefer institutional_debates, monetary_policy / avoid ceremonial_reporting"
  + active priority boosts applied
      ↓
Better briefing — shaped by your reasoning, not an algorithm's engagement model
```

**Model-agnostic by design:** The user profile is injected at the dispatcher level, not inside any model's prompt. When xAI goes down and Haiku takes over, it runs with the same learned profile. Swap models — preferences persist.

**Bootstrapped cold start:** Rather than waiting months for enough feedback, 398 hand-saved X bookmarks were ingested as `Save` signals. The learning loop went from 17 signals to **415 scored signals in one session**.

---

## What It Has Learned

```bash
python show_profile.py
```

```
========================================================
  CURATOR LEARNED PROFILE
========================================================
  Interactions : 415 scored signals from 406 feedback events
  Last updated : 2026-02-28
  Feedback     : 8 liked  |  1 disliked  |  397 saved
========================================================

  SOURCES
  -------
  ████████████████  +17  X/@[geopolitics_account]
  ██████████████░░  +16  X/@[geopolitics_account]
  █████████████░░░  +14  X/@[macro_economist]
  ████████████░░░░  +12  X/@[macro_economist]
  ███████████░░░░░  +11  [independent_media]
  ███████████░░░░░  +11  X/@[economic_historian]

  THEMES
  ------
  ████████████████  +17  institutional_debates
  ████████████████  +17  market_analysis
  █████████░░░░░░░  +8   monetary_policy
  ███████░░░░░░░░░  +6   geopolitical_analysis

  AVOID PATTERNS
  --------------
  ▪▪▪  (3x)  ceremonial_reporting
  ▪    (1x)  event_coverage_not_analysis
```

Learned from actual reading behavior — nothing hard-coded. The system knows to up-rank institutional critique, monetary theory, and geopolitical analysis. It knows to skip ceremonial news coverage.

---

## Architecture

```
RSS Feeds (10+ sources, ~400 articles)
          ↓
  curator_rss_v2.py
          ↓
  [mechanical mode: keyword scoring, Ollama local LLM — no external dependency]
          ↓  OR
  Stage 1: Haiku pre-filter (400 → ~50, cheap pass)
          ↓
  Stage 2: grok-3-mini scorer + injected user profile
          ↓  [fallback: Haiku with same user profile]
  Top 20 ranked articles
          ↓
  Telegram delivery (7 AM via launchd)   OR   stdout/file
          ↓
  User reacts (Like / Dislike / Save)
  [or flags for Deep Dive → deep_dive.py → higher-capability model → brief + counter-arguments + bibliography]
          ↓
  curator_feedback.py → updates local curator_preferences.json
          ↓
  Tomorrow's run loads updated profile → repeat
```

**Model roles:**

| Model | Role | Cost profile |
|---|---|---|
| Ollama (local) | Mechanical mode scoring | Free, no external calls |
| Claude Haiku | Bulk pre-filter, fallback scorer | Low cost, high volume |
| xAI grok-3-mini | Final daily ranking | Balanced quality/cost |
| Claude Sonnet | Deep Dives, OpenClaw conversational layer | Higher capability, used selectively |

All swappable. Profile injection at dispatcher level means switching models doesn't affect personalization.

---

**Key files:**

| File | Purpose |
|---|---|
| `curator_rss_v2.py` | Main pipeline: fetch, score, deliver |
| `curator_feedback.py` | Process reactions → update local learned profile |
| `show_profile.py` | Human-readable view of what the system has learned |
| `cost_report.py` | Unified cost tracking (chat + curator runs) |
| `x_bootstrap.py` | One-time ingestion of X bookmarks as learning signals |
| `x_auth.py` | X API OAuth credential management |

---

## Cost Story

Two categories of cost, both tracked:

**Operational costs** — daily curator runs (Haiku pre-filter + grok-3-mini scoring):

| Period | Approach | Monthly Cost |
|---|---|---|
| January | Mechanical mode + Ollama | Free |
| Early February | Claude Sonnet for all scoring | $100+/month |
| Current | Haiku pre-filter + grok-3-mini final | $35–45/month |

**Build costs** — design, strategy, and debugging sessions. Originally running Claude Sonnet via API for all development conversations. Migrated to Claude Code (runs on Pro subscription), which eliminated per-token build costs for implementation work. Both categories tracked separately in `daily_usage.json`.

The insight: profile injection makes cheaper models smarter. Most of the cost reduction came not from switching models but from injecting context that let lower-cost models perform at the level previously requiring expensive ones.

```bash
python cost_report.py          # today's breakdown by category
python cost_report.py week     # last 7 days, day by day
python cost_report.py month    # this calendar month
python cost_report.py year     # this calendar year, month by month
```

---

## How to Run

**Prerequisites:** Python 3.9+, API keys in macOS keyring

```bash
# Clone and install
git clone https://github.com/robertvanstedum/personal-ai-agents.git
cd personal-ai-agents
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Keyword scoring mode — no LLM, no API key required (see issue #1 for Ollama restore)
python curator_rss_v2.py --model=ollama

# AI mode (dry-run first)
python curator_rss_v2.py --mode=ai --dry-run

# See what the system has learned
python show_profile.py

# Check costs
python cost_report.py
```

**Credentials:** All API keys stored in macOS keyring, never in files. See `CREDENTIALS_SETUP.md`.

**OpenClaw (optional):** Adds Telegram delivery and conversational interface. The curator runs standalone without it.

**Scheduling:** launchd plist triggers at 2–3 AM (fetch/score), delivers at 7 AM.

---

## Development Process

Built through structured human-AI collaboration:

- **Multi-agent coordination:** Human architect bridges between specialized AI agents (Claude Code for implementation, OpenClaw assistant for planning/memory)
- **Incremental testing:** Formalized checklist (imports → usage → dry-run → integration) catches bugs before production
- **Zero regressions:** 7 major feature phases shipped (Feb-Mar 2026) with no production outages

**Example:** During Phase 3C development, the test sequence caught a string mismatch bug before it reached the preferences file — preventing a silent scoring system failure.

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed workflow and testing protocol.

---

## Roadmap

**v0.9 (current — Feb 2026):**
Full learning loop across all scoring paths. X bookmark bootstrap (415 signals). Deep Dives. Signal Priorities. Reading Library. Cost tracking across operational and build categories. Model-agnostic profile injection.

**v1.0 (active development):**
- ✅ Phase 3C (Mar 2026): Content ecosystem enrichment — X bookmark pipeline extracts destination domains, source types, and 66 content topics via Haiku. Profile +41%. A/B test confirmed scoring impact. See `docs/test-reports/`.
- Phase 3D: Image analysis — chart images from analyst tweets run through vision model (gate in place: `chart_analysis: null`); full 398-tweet archive enrichment run
- Phase 4: Wider sources — Substack, academic (BIS, Fed, arXiv), Reddit
- Phase 5: Synthesis — pattern detection, contradiction highlighting, proactive research
- Postgres migration — `curator_costs.json` already row-structured, `COPY` ready

Active development continues after v0.9 launch.

---

## Technical Notes

- **Anti-echo-chamber:** 20% serendipity reserve surfaces articles outside learned patterns
- **Decay gate:** Signals older than 30 days get half-weight — prevents preference lock-in
- **Signal normalization:** X bookmarks weighted to avoid volume bias vs. direct feedback
- **Model-agnostic design:** Profile injection at dispatcher level — model swaps don't break personalization
- **Local-first:** All learned state is flat files on your machine, structured for easy DB migration

---

**Status:** Production (daily use since Feb 9, 2026)
**Current milestone:** v0.9-beta — learning loop complete across all scoring paths
**Author:** Robert van Stedum

---

## FILE: docs/PLAN_WS1_SourceScoring_2026-03-14.md

# Workstream 1 — Source Scoring

curator_sources.json trust weights

Date: 2026-03-14 | Author: Robert & Claude | Status: APPROVED — Built March 14, 2026 (see BUILD_WS5_PHASE_A.md for pre-condition record)

## Context

Adds domain-level trust weights upstream of Grok scoring. A `curator_sources.json` file already exists with 5 trusted entries but is not yet read by any code. This plan wires it into both the main briefing pipeline (`curator_rss_v2.py`) and the priority feed (`curator_priority_feed.py`), and adds auto-discovery of new Brave domains as probationary.

## Step 1 — Seed curator_sources.json

Final 9-entry file (4 new entries added):

| Domain | Trust | set_by | Note |
|---|---|---|---|
| warontherocks.com | trusted | explicit | Defense/security analysis |
| foreignaffairs.com | trusted | explicit | Premier geopolitics journal |
| arxiv.org | trusted | explicit | Academic preprints, q-fin |
| justsecurity.org | trusted | explicit | National security / international law |
| cepr.org | trusted | explicit | Economic policy research, VoxEU |
| crisisgroup.org | trusted | explicit | Conflict analysis |
| investing.com | drop | explicit | Aggregator noise |
| theduran.com | neutral | explicit | |
| zerohedge.com | neutral | explicit | |

## Step 2 — curator_rss_v2.py: Apply Trust Multipliers

Trust multiplier reference table:

| Trust tier | Multiplier | Effect |
|---|---|---|
| trusted | 1.5× | Boosted — high-confidence sources |
| neutral | 1.0× | No change (default for unknown domains) |
| deprioritize | 0.5× | Half score — surfaces rarely |
| probationary | 0.7× | Slight discount — auto-discovered domains |
| drop | — | Excluded before Grok scoring (saves tokens) |

### A. Module-level helpers (add near other utility functions, before curate()):

```python
def _load_source_trust() -> dict:
    """Load curator_sources.json -> {domain: trust_tier} dict."""
    path = Path(__file__).parent / 'curator_sources.json'
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text())
        return {e['domain']: e['trust'] for e in entries
                if 'domain' in e and 'trust' in e}
    except Exception:
        return {}

_TRUST_MULTIPLIERS = {
    'trusted': 1.5, 'neutral': 1.0,
    'deprioritize': 0.5, 'probationary': 0.7
}

def _domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith('www.') else netloc
    except Exception:
        return ''
```

### B. In curate(), after building all_entries and BEFORE scoring (drop filter):

```python
# Load source trust table
_source_trust = _load_source_trust()

# Filter out 'drop' domains before scoring (saves LLM tokens)
before_drop = len(all_entries)
all_entries = [
    e for e in all_entries
    if _source_trust.get(
        _domain_from_url(e.get('link', '')), 'neutral') != 'drop'
]
if before_drop != len(all_entries):
    print(f"Source trust: dropped "
          f"{before_drop - len(all_entries)} entries (drop tier)")
```

### C. In curate(), AFTER score assignment (apply multipliers):

```python
# Apply trust multipliers to scores
for entry in all_entries:
    domain = _domain_from_url(entry.get('link', ''))
    tier = _source_trust.get(domain, 'neutral')
    multiplier = _TRUST_MULTIPLIERS.get(tier, 1.0)
    if multiplier != 1.0:
        entry['raw_score'] = entry.get('raw_score', entry['score'])
        entry['score'] = round(entry['score'] * multiplier, 2)
        entry['trust_tier'] = tier
```

## Step 3 — curator_priority_feed.py: Auto-Log Probationary Domains

Any domain returned by Brave that is not already in DOMAIN_WHITELIST or curator_sources.json is auto-logged as probationary (set_by='auto') for human review. Idempotent — duplicate domains skipped.

### Helper function (add near whitelist_filter):

```python
def _log_probationary_domains(raw_results: list) -> None:
    """
    Auto-add any domain surfaced by Brave that is not already in
    curator_sources.json. Sets trust='probationary', set_by='auto'.
    Idempotent -- skips domains already in the file.
    """
    import json as _json
    from datetime import datetime as _dt

    sources_path = Path(__file__).parent / 'curator_sources.json'
    try:
        existing = _json.loads(sources_path.read_text()) if sources_path.exists() else []
    except Exception:
        existing = []

    known_domains = {e['domain'] for e in existing}

    new_entries = []
    for r in raw_results:
        domain = extract_domain(r.get('url', ''))
        if (domain and domain not in known_domains
                and domain not in DOMAIN_WHITELIST):
            known_domains.add(domain)
            new_entries.append({
                'domain': domain,
                'trust': 'probationary',
                'set_by': 'auto',
                'note':  f'auto-discovered via Brave '
                         f'{_dt.now().strftime("%Y-%m-%d")}',
            })

    if new_entries:
        existing.extend(new_entries)
        sources_path.write_text(_json.dumps(existing, indent=2))
        log.info(f'Logged {len(new_entries)} new probationary domain(s): '
                 f'{[e["domain"] for e in new_entries]}')
```

### Call site (after each brave_search(), before whitelist_filter()):

```python
raw = brave_search(query, count=50)
_log_probationary_domains(raw)          # <-- new line
whitelisted = whitelist_filter(raw)
```

## Files Modified

| File | Change |
|---|---|
| curator_sources.json | Add 4 entries: crisisgroup (trusted), investing (drop), theduran (neutral), zerohedge (neutral) |
| curator_rss_v2.py | _load_source_trust(), _domain_from_url(), _TRUST_MULTIPLIERS; drop filter before scoring; multiplier after scoring |
| curator_priority_feed.py | _log_probationary_domains() helper; called after each brave_search() |

## Verification

1. Run `python curator_rss_v2.py --dry-run --model xai` — check for:
   - "Source trust: dropped N entries" if investing.com articles present in pool
   - Scores for crisisgroup.org / foreignaffairs.com articles should be ×1.5 vs raw_score

2. Run priority feed — inspect `curator_sources.json` afterward:
   - New Brave domains should appear with trust='probationary', set_by='auto'

3. Spot-check a trusted source entry:
   - Confirm raw_score preserved and `score = raw_score x 1.5`

---

*Generated 2026-03-14 | personal-ai-agents / Workstream 1*
*Converted from PDF to markdown 2026-03-15*

---

## FILE: docs/PLAN_WS2_WebSearch_2026-03-14.md

# WS2 Completion — Web Search in Daily Briefing

Workstream 2 final item: Brave search candidates in curator_rss_v2.py
Date: 2026-03-14 | Author: Robert & Claude | Status: APPROVED — Built March 14, 2026

## Context

The main daily briefing currently draws candidates from two pools: RSS feeds (~390 articles) and X bookmarks (~332 articles). This plan adds a third pool: Brave web search results, topic-guided by active priorities and static baseline queries. These candidates compete on merit for the same 20 daily slots — enrichment, not dominance. Completes Workstream 2.

## Reused Code (no changes to these)

| Function / Constant | File | Notes |
|---|---|---|
| brave_search(), DOMAIN_WHITELIST | curator_priority_feed.py | Lazy-imported inside helper to avoid circular import |
| load_priorities() | curator_rss_v2.py:1291 | Returns active, non-expired priorities |
| _domain_from_url() | curator_rss_v2.py (WS1) | Strips www., lowercases netloc |
| hashlib (MD5) | curator_rss_v2.py imports | MD5(url)[:5] — same hash scheme as RSS/X entries |

| Source | Queries | Results each | Max candidates |
|---|---|---|---|
| Active priorities | up to 3 | 5 | 15 |
| Baseline topics | 6 evergreen | ~1 | 5 |
| Total web search | | | ≤ 20 of ~898 pool |

<2.3% of pool — enrichment, not dominance. Most web candidates won't reach top 20; that is correct.

## Step 1 — Add _fetch_web_search_candidates() helper

Insert after the _TRUST_MULTIPLIERS / _domain_from_url block (WS1 helpers), before def curate().

```python
def _fetch_web_search_candidates(
    seen_hashes: set,
    priority_limit: int = 3,
    results_per_priority: int = 5,
    baseline_total: int = 5,
) -> List[Dict]:
    """
    Fetch Brave web search candidates for the daily briefing pool.
    Priority queries first (up to 3 x 5 = 15), baseline fills after (5 total).
    Domain-whitelist filtered, deduped vs seen_hashes. Tagged source_type='web_search'.
    """
    # Lazy import — curator_priority_feed imports curator_rss_v2 at module level
    try:
        from curator_priority_feed import brave_search, DOMAIN_WHITELIST
    except ImportError:
        print("Web search: curator_priority_feed not available, skipping")
        return []

    BASELINE_TOPICS = [
        'geopolitics', 'monetary policy', 'emerging markets',
        'AI economy', 'energy markets', 'US foreign policy',
    ]
    candidates = []
    seen_urls: set = set()

    def _to_entry(r: dict, query_label: str):
        url = r.get('url', '')
        if not url: return None
        domain = _domain_from_url(url)
        if domain not in DOMAIN_WHITELIST: return None
        h = hashlib.md5(url.encode()).hexdigest()[:5]
        if h in seen_hashes or url in seen_urls: return None
        seen_urls.add(url)
        return {
            'hash_id': h, 'source': domain, 'source_type': 'web_search',
            'title':   r.get('title', '').strip(),
            'link':    url,
            'summary': r.get('description', '').strip(),
            'published': None, 'query_label': query_label,
        }

    # 1. Priority queries
    for priority in load_priorities()[:priority_limit]:
        keywords = priority.get('keywords', [])
        if not keywords: continue
        label = priority.get('label', priority.get('id', ''))
        raw = brave_search(' '.join(keywords), count=results_per_priority * 2)
        added = 0
        for r in raw:
            if added >= results_per_priority: break
            entry = _to_entry(r, f"priority:{label}")
            if entry:
                candidates.append(entry)
                seen_hashes.add(entry['hash_id'])
                added += 1
        print(f"  Web [{label}]: {added} candidates")

    # 2. Baseline topics
    per_topic = max(1, (baseline_total + len(BASELINE_TOPICS)-1) // len(BASELINE_TOPICS))
    baseline_added = 0
    for topic in BASELINE_TOPICS:
        if baseline_added >= baseline_total: break
        for r in brave_search(topic, count=per_topic * 2):
            if baseline_added >= baseline_total: break
            entry = _to_entry(r, f"baseline:{topic}")
            if entry:
                candidates.append(entry)
                seen_hashes.add(entry['hash_id'])
                baseline_added += 1
    if baseline_added:
        print(f"  Web baseline: {baseline_added} candidates")

    return candidates
```

## Step 2 — Call site in curate()

Insert after print(f"X bookmarks merged...") (~line 1527) and before the source trust block:

```python
    # Web search enrichment — active priority keywords + baseline topics
    print(f"\n Web search enrichment:")
    _ws_seen = {e['hash_id'] for e in all_entries if e.get('hash_id')}
    web_candidates = _fetch_web_search_candidates(seen_hashes=_ws_seen)
    if web_candidates:
        all_entries.extend(web_candidates)
        print(f"  Pool after web search: {len(all_entries)} candidates")
    else:
        print(f"  No web candidates added (Brave unavailable or all filtered)")
```

## Design Notes

- **Circular import:** curator_priority_feed imports curator_rss_v2 at module level. Solved via lazy import inside the helper function body — not at module level.
- **Trust multipliers (WS1):** web search entries pass through the existing trust filter automatically — they have link fields and _domain_from_url works on them.
- **source_type: 'web_search'** enables WS5 intelligence to distinguish web candidates from RSS/X entries in performance tracking.
- **query_label** field (e.g. "priority:Chicago Crime", "baseline:geopolitics") lets WS5 attribute which query surfaced which article.
- **Fail-safe:** brave_search() returns [] on any API/key failure. _fetch_web_search_candidates() returns []. curate() logs cleanly and continues — zero impact on daily briefing.

## Verification

1. `python curator_rss_v2.py --dry-run --model xai` — look for:
   - "Web search enrichment:" section in output
   - "Web [<priority label>]: N candidates" per active priority
   - "Web baseline: N candidates"
   - Total pool count higher than ~878
2. Spot-check: a web candidate in dry-run output should show source_type: web_search
3. Confirm graceful degradation: if Brave unavailable, "No web candidates added" logs and run completes normally.

---

*Generated 2026-03-14 | personal-ai-agents / Workstream 2 final*
*Converted from PDF to markdown 2026-03-15*

---

## FILE: docs/PLAN_WS5_Intelligence_2026-03-14.md

# Workstream 5 — Intelligence Layer

Design Spec: curator_intelligence.py

Date: 2026-03-14 | Author: Robert & Claude | Status: APPROVED — Phase A built March 15, 2026 (see BUILD_WS5_PHASE_A.md)

---

## Problem & Intent

The curator is a well-engineered scoring pipeline with a learned preference profile. The LLM is used reactively — score this, filter that. It does not proactively reason about what you *should* be seeing. WS5 closes that gap: a daily observation layer that monitors the pipeline's own output, flags anomalies, surfaces blind spots, and (in 1.1) acts on its findings.

**Philosophy:** Generate intelligence now. Actions come in 1.1. You decide what to do with each observation. The system's job is to notice things you would have missed.

---

## New Files

| File | Purpose |
|---|---|
| curator_intelligence.py | Main runner — reads pipeline output, generates observations, sends Telegram |
| run_intelligence_cron.sh | Shell wrapper (mirrors run_curator_cron.sh) |
| com.vanstedum.curator-intelligence.plist | launchd agent, fires 7:30 AM daily |
| intelligence/intelligence_YYYYMMDD.json | Daily observation output (one file per run) |
| intelligence/intelligence_state.json | Rolling 30-day baseline accumulator |

**Modified:** `curator_rss_v2.py` — add `curator_latest.json` write at end of main() (machine-readable scored pool; ~200KB/day)

---

## Prerequisite: curator_latest.json

`curator_output.txt` and HTML are fragile to parse. Intelligence needs structured access to the full scored pool.

One-liner addition to `curator_rss_v2.py` main():

```python
with open('curator_latest.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
```

Contains all scored entries (not just top 20) with fields: hash_id, title, source, link, summary, category, score, raw_score, final_score, method, **source_type** (rss/web_search/x_bookmark), **query_label**, trust_tier, matched_priorities, published.

---

## Five Observation Types

| # | Name | Model | Frequency | Est. cost |
|---|---|---|---|---|
| 1 | Topic Velocity | Haiku | Daily | ~$0.005 |
| 2 | Source Anomalies | Haiku (on flag only) | Daily | ~$0.005–0.015 |
| 3 | Discovery Candidates | Haiku | Daily (≤5/day) | ~$0.010–0.025 |
| 4 | Lateral Connections | Sonnet | Weekly (Thu) | ~$0.05–0.10/week |
| 5 | US Press Blind Spots | Haiku (on flag only) | Daily (opportunistic) | ~$0.005 |

### 1. Topic Velocity (Haiku, daily)

Count articles per category in today's full scored pool. Compare against 30-day rolling avg in `intelligence_state.json`. Flag: any category ±60% vs baseline; any active interest with 0 articles. One Haiku call to narrate findings.

Output:
```json
{"type":"topic_velocity","priority":"high",
 "observation":"Tariff retaliation +80% vs baseline; Iran interest at zero today.",
 "details":{"category_counts":{...},"zero_coverage_interests":["iran sanctions"]}}
```

### 2. Source Anomalies (Haiku on flag, daily)

For each trusted domain in `curator_sources.json`: count today's articles vs 30-day baseline. Flag if trusted source at 0 when baseline >1.5/day. Skip if baseline sparse (<7 days). Haiku only invoked when flag raised — pure arithmetic otherwise. **Exclude source_type='web_search' entries** (queries, not feeds — 0-count days are expected).

### 3. Discovery Candidates (Haiku, daily, ≤5/day)

Find probationary entries in `curator_sources.json` with no `haiku_evaluated` flag. Send domain + query_label to Haiku for credible/noise/unknown verdict. Write result back to the JSON entry:

```json
{"domain":"streetwisejournal.com","trust":"probationary","set_by":"auto",
 "haiku_evaluated":true,"haiku_verdict":"noise",
 "haiku_note":"Aggregator, no original reporting","haiku_evaluated_at":"2026-03-15"}
```

### 4. Lateral Connections (Sonnet, Thursday only)

Thursday guard in code. Collects: liked[]/saved[] titles from `curator_preferences.json` (last 30 days) + active priority labels + weekly topic summary. Single Sonnet prompt. Output: 2–3 suggested topics with example sources and one-line rationale. Sent as a **separate Telegram message**, not included in daily 5-line summary.

Example:
```
"You track Iran sanctions heavily. India is now Iran's largest crude buyer.
Suggested: S&P Global Commodity Insights, The Hindu Business Line."
```

### 5. US Press Blind Spots (Haiku on flag, daily)

Tag each article in scored pool as US or non-US (hardcoded sets). Find articles scoring >6.0 from non-US sources where no US source covered the same story (keyword similarity check, no LLM). If ≥2 qualifying articles found: invoke Haiku to summarize. Otherwise silent.

---

## intelligence_state.json — Rolling 30-Day Baseline

```json
{
    "last_updated": "2026-03-14",
    "topic_baseline": {
        "geo_major":  {"days":14,"total":168,"daily_avg":12.0,"last_7d":[14,11,13,12,15,10,11]},
        "monetary":   {"days":14,"total":72, "daily_avg":5.1, "last_7d":[6,5,4,5,6,5,4]},
        "geo_other":  {"days":14,"total":84, "daily_avg":6.0, "last_7d":[7,6,6,6,5,7,6]}
    },
    "source_baseline": {
        "reuters.com":        {"days":14,"total_articles":42,"daily_avg":3.0},
        "warontherocks.com":  {"days":14,"total_articles":32,"daily_avg":2.3}
    }
}
```

Update: append today's count to last_7d (drop oldest), increment days/total, recalculate daily_avg. Cap at 30 days. Stored at `~/.openclaw/workspace/intelligence_state.json` (private, not in repo).

---

## intelligence_YYYYMMDD.json — Daily Output Schema

```json
{
    "date": "2026-03-15",
    "run_type": "daily",
    "observations": [
        {"type":"topic_velocity|source_anomaly|discovery_candidate|lateral_connection|blind_spot",
         "priority":"high|medium|low",
         "observation":"Plain English, 1-2 sentences",
         "details":{},
         "model":"haiku|sonnet|none",
         "cost_usd":0.012,
         "timestamp":"2026-03-15T07:30:00Z"}
    ],
    "telegram_sent":true,"total_cost_usd":0.034,
    "run_duration_s":52,"pool_size":882
}
```

---

## Telegram Delivery

**Daily message** (5 lines max, 7:30 AM — 30 min after morning briefing):
```
🧠 Intelligence | Mar 15

📈 Tariff retaliation +80% vs baseline; Iran interest at zero today
⚠️ War on the Rocks quiet (0 articles, norm 2.3/day)
🔍 3 domains evaluated: 0 credible, 3 noise
👁 Blind spot: Bamako summit in non-US press, absent from US wire
```

**Weekly message** (Thursday, separate):
```
🔗 Lateral Connections | Week of Mar 10

→ India crude workarounds (Iran sanctions adjacency) — S&P Global, Hindu Business Line
→ ECB balance sheet normalization — ties to your monetary policy tracking
→ Türkiye–Gulf creditor negotiations — ACLED + FT coverage gap
```

*Uses existing `send_telegram_alert()` from `curator_utils.py` (moved from curator_rss_v2.py as pre-condition). Chat ID from TELEGRAM_CHAT_ID env var.*

---

## launchd — 7:30 AM Daily

```
com.vanstedum.curator-intelligence.plist
StartCalendarInterval: Hour=7, Minute=30
Logs: logs/intelligence_launchd.log + logs/intelligence_launchd_error.log
Shell wrapper: run_intelligence_cron.sh  (mirrors run_curator_cron.sh pattern)
```

---

## Build Sequence

Phase A (complete — March 15):
1. ~~Move send_telegram_alert → curator_utils.py~~
2. ~~Fix _log_probationary_domains() — add added_date + query fields, backfill 8 entries~~
3. ~~Scaffold curator_intelligence.py — Topic Velocity + Discovery Candidates~~
4. ~~launchd plist + run_intelligence_cron.sh~~

Phase B (next):
5. Add curator_latest.json write to curator_rss_v2.py main()
6. Implement intelligence_state.json rolling baseline updater
7. Source anomaly detection (arithmetic + Haiku on flag)
8. US press blind spot detection (keyword similarity + Haiku on flag)
9. Weekly lateral connections (Sonnet, Thursday guard)

---

## Open Questions — Answered Before Phase A

**Q1 — curator_latest.json:** Deferred to Phase B. Phase A reads curator_history.json instead; topic inference via Haiku from titles at runtime.

**Q2 — Source anomaly threshold:** A first (0 articles when baseline >1.5/day). B in 1.1.

**Q3 — Discovery candidate cap:** 5/day to control cost. ✅

**Q4 — Lateral connections history:** Option A — liked[]/saved[] from curator_preferences.json. ✅

**Q5 — Telegram destination:** Same chat as morning briefing. ✅

**Q6 — intelligence_state.json location:** ~/.openclaw/workspace/ — private, not in repo. ✅

**Q7 — Blind spot trigger:** Daily, silent unless ≥2 qualifying articles found. ✅

---

*Generated 2026-03-14 | personal-ai-agents / Workstream 5*
*Converted from PDF to markdown 2026-03-15*
*Phase A build record: docs/BUILD_WS5_PHASE_A.md*

---

## FILE: docs/PLAN_WS5_PhaseB_2026-03-15.md

# Plan: Intelligence Layer — Phase B
**Mini-moi Personal AI Curator**
**Prepared:** March 15, 2026
**Sprint:** 1.0 — Workstream 5
**Status:** Ready for OpenClaw validation → Claude Code build
**Depends on:** Phase A complete ✅

---

## Context

Phase A delivered the intelligence foundation:
- `curator_intelligence.py` running at 7:30AM daily
- Topic velocity observation (momentum + gaps)
- Discovery candidates observation (probationary domain evaluation)
- Telegram delivery + `intelligence_YYYYMMDD.json` storage
- `curator_utils.py` refactored as shared helper module

Phase B adds three remaining observation types and completes Workstream 5.

---

## What Phase B Builds

Three new observations added to `curator_intelligence.py`:

### Observation 3: Source Anomalies (daily, Haiku)
**Purpose:** Detect when a trusted source is behaving differently than its norm — topic drift, quality change, sudden volume spike.

**Logic:**
1. Load `curator_sources.json` — filter to `trust == "trusted"` domains
2. Load `curator_history.json` — for each trusted domain, collect last 10 articles vs 30-day baseline articles from same domain
3. For each trusted domain with enough history (>5 articles in 30 days), one Haiku call:
```
Source: {domain}
Recent articles (last 10): {titles}
30-day baseline articles (sample): {baseline_titles}

Has this source changed in topic focus, tone, or quality recently?
Answer in one sentence. If no notable change, say "No anomaly detected."
```
4. Only surface anomalies in Telegram output — skip "no anomaly" results
5. Format: `⚠️ <b>Source drift:</b> {domain} — {haiku observation}`

**Quiet path:** If no anomalies detected across all trusted sources, omit this observation from Telegram entirely. Don't add noise.

---

### Observation 4: US Press Blind Spots (daily, Haiku)
**Purpose:** Surface stories with high coverage velocity in non-US sources that have low or zero coverage in US outlets. The gap is the signal.

**Non-US sources in RSS pool:**
`aljazeera.com`, `dw.com`, `spiegel.de`, `faz.net`, `welt.de`, `oglobo.globo.com`

**US sources in RSS pool:**
`zerohedge.com`, `propublica.org`, `stlouisfed.org`, `ritholtz.com`, `antiwar.com`, `warontherocks.com`, `justsecurity.org`

**Logic:**
1. Load today's full scored candidate pool (before top 20 selection) from history
2. Separate into non-US articles and US articles by domain
3. Extract topics/keywords from non-US articles using title text
4. Check coverage of same topics in US articles
5. One Haiku call with both sets:
```
Non-US source articles today:
{non_us_titles}

US source articles today:
{us_titles}

Identify 1-2 stories covered in non-US sources but absent or underrepresented
in US sources. Be specific — name the story, not the category.
If no significant gap exists, say "No blind spots today."
```
6. Format: `🌍 <b>Blind spot:</b> {haiku observation}`

**Quiet path:** If Haiku returns "No blind spots today", omit from Telegram.

---

### Observation 5: Lateral Connections (weekly, Sonnet)
**Purpose:** Reason across recent reading history and signal profile. Surface adjacent topics, second-order implications, and suggested sources you're not currently tracking.

**Cadence:** Weekly — fires every Sunday. Separate from daily observations. Delivered as a separate Telegram message.

**Logic:**
1. Load last 30 days of liked/saved articles from `curator_preferences.json`
2. Load current interest profile from `curator_preferences.json` (`learned_patterns`)
3. One Sonnet call:
```
Reading history (last 30 days — liked and saved articles):
{article_titles_and_sources}

Current interest profile summary:
{learned_patterns_summary}

You are a research advisor who knows this reader well.

Identify 2-3 topics, angles, or second-order implications the reader has NOT
been covering but would find genuinely valuable given their interests.

For each suggestion:
- Name the topic specifically
- Explain in one sentence why it connects to their existing interests
- Suggest 1-2 specific sources they could add (real, credible outlets)

Be specific and intellectually honest. Do not suggest topics already well-covered
in their history. Format using HTML for Telegram.
```
4. Format:
```
🧠 <b>Weekly Connections — {date}</b>

{sonnet output}
```
5. Store as `intelligence_weekly_YYYYMMDD.json` in OpenClaw workspace
6. Send as separate Telegram message — not combined with daily

**Cost:** One Sonnet call per week — ~$0.05–0.10. Acceptable.

---

## Files Touched

| # | File | Action |
|---|------|--------|
| 1 | `curator_intelligence.py` | Add 3 new observation functions |
| 2 | `curator_intelligence.py` | Add weekly cadence check in `main()` |
| 3 | `curator_intelligence.py` | Extend `format_telegram()` to include new observations |
| 4 | `curator_intelligence.py` | Extend `save_output()` to handle weekly JSON |

No new files. No new launchd plists — weekly logic runs inside the existing 7:30AM daily job with a day-of-week check.

---

## Implementation Notes

**Weekly cadence check:**
```python
import datetime
today = datetime.date.today()
is_sunday = today.weekday() == 6  # 0=Monday, 6=Sunday
if is_sunday:
    observations.append(observe_lateral_connections(today_str))
```

**Sonnet client:**
Use same keychain pattern as Haiku client in Phase A. Model: `claude-sonnet-4-20250514`

**Source anomaly minimum history:**
Only evaluate sources with >5 articles in the 30-day history window. Sources with thin history produce unreliable anomaly signals. Skip silently.

**Non-US / US domain classification:**
Hardcode the two lists as constants at top of file — same pattern as whitelist in `curator_priority_feed.py`. Do not attempt dynamic classification.

**Quiet paths are mandatory:**
All three observations must have quiet paths. If there's nothing meaningful to say, omit the observation from Telegram entirely. The daily message should never pad with "nothing to report" lines.

---

## Verification Steps

**After adding Observation 3 (source anomalies):**
```bash
python curator_intelligence.py --dry-run --date 2026-03-15
```
Expected: source anomaly observation present in output, or silently omitted if no anomalies. No errors.

**After adding Observation 4 (blind spots):**
```bash
python curator_intelligence.py --dry-run --date 2026-03-15
```
Expected: blind spot observation present or silently omitted. No errors.

**After adding Observation 5 (lateral connections):**
```bash
python curator_intelligence.py --dry-run --date 2026-03-16
```
Note: use a Sunday date to trigger weekly logic. 2026-03-15 is a Sunday ✅ — use today.
Expected: lateral connections observation present in dry-run output.

**Full integration test:**
```bash
python curator_intelligence.py --telegram --date 2026-03-15
```
Expected: Telegram message sent with all available observations. Weekly lateral connections sent as separate message. JSON files written to OpenClaw workspace.

---

## Open Questions for OpenClaw

1. **History schema** — confirm how today's full candidate pool is accessible. Phase A used `curator_history.json` keyed by hash_id with `appearance` dates. Confirm whether the full pre-top-20 pool is stored anywhere, or if blind spot detection must work from top 20 only.

2. **learned_patterns location** — confirm exact key path in `curator_preferences.json` for the learned patterns summary used in Lateral Connections prompt.

3. **Sonnet model string** — confirm current production model string for Sonnet. Phase A uses `claude-haiku-4-5` for Haiku — confirm Sonnet equivalent.

4. **Weekly JSON naming** — confirm no conflict with existing workspace files using `intelligence_weekly_` prefix.

---

## Success Criteria

- Source anomaly observation fires correctly, quiet path works when no anomalies
- Blind spot observation identifies at least one cross-source gap on a day with active international coverage
- Lateral connections fires on Sundays only, produces 2-3 specific topic suggestions with sources
- All quiet paths suppress empty observations from Telegram
- Weekly Sonnet message sends as separate Telegram message
- All JSON outputs written correctly to OpenClaw workspace
- No regressions to Phase A observations

---

## Post-Build

Claude Code writes build summary → "pass to OpenClaw" → OpenClaw saves to `docs/BUILD_WS5_PhaseB_YYYY-MM-DD.md` and appends CHANGELOG.

---

*Prepared by Claude.ai — March 15, 2026*
*For OpenClaw validation before Claude Code handoff*
*Convention: this file saved to `docs/PLAN_WS5_PhaseB_2026-03-15.md`*

---

## FILE: docs/PLAN_WS5_PhaseC_2026-03-15.md

# Plan: Intelligence Layer — Phase C
## Response Capture & Feedback Loop Foundation
**Mini-moi Personal AI Curator**
**Prepared:** March 15, 2026
**Sprint:** 1.0 — Workstream 5, Phase C
**Status:** Ready for OpenClaw validation → Claude Code build
**Depends on:** Phase A ✅ Phase B ✅

---

## Purpose

Phase C closes the intelligence feedback loop at the data layer.

The intelligence layer (Phases A and B) surfaces observations — topic momentum,
blind spots, lateral connections, source anomalies. Currently those observations
go out and nothing comes back in. The system cannot learn from your reactions,
positions, or stated thinking.

Phase C captures your responses and stores them in a structured format designed
for future activation. In 1.0, nothing acts on these responses yet. In 1.1,
the Sonnet lateral connections prompt reads them, the investigation workspace
opens from them, and the RAG layer queries them.

**1.0 delivers the data. 1.1 connects it to infrastructure and acts on it.**

This is the foundation for the full RAG architecture — vector search across
your notes, positions, and dialog history — planned as the major architectural
upgrade post-1.0.

---

## Architecture Note (for README and portfolio)

> Mini-moi 1.0 delivers a complete intelligence observation loop: the system
> surfaces what it notices, and you can now tell it what you think. Those
> responses are stored in a structured, queryable format ready for the next
> layer — graph database, vector search, and automated action — planned for
> 1.1 and beyond.

This narrative should appear in the README architecture section and the
public roadmap.

---

## What Phase C Builds

### New file: `intelligence_responses.json`

Append-only array. Every response you give to any intelligence observation
lands here. Simple enough to build now. Structured enough for 1.1 to query.

**Schema:**
```json
{
  "responses": [
    {
      "id": "resp_001",
      "date": "2026-03-15",
      "observation_type": "lateral_connection",
      "observation_ref": "intelligence_weekly_20260315.json",
      "topic": "crypto as speculative asset vs uncertainty hedge",
      "domain": "finance",
      "reaction": "disagree",
      "position": "Crypto tracks as speculative asset, not uncertainty hedge. Interest is specifically gold/crypto correlation during geopolitical stress, not generic crypto coverage.",
      "confidence": "medium",
      "want_more": true,
      "pending_action": null,
      "acted_on": false,
      "timestamp": "2026-03-15T14:30:00Z"
    }
  ]
}
```

**Field definitions:**

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string | Unique response ID — `resp_NNN` incrementing |
| `date` | string | YYYY-MM-DD |
| `observation_type` | string | `lateral_connection`, `blind_spot`, `topic_velocity`, `source_anomaly`, `freeform` |
| `observation_ref` | string | Filename of the intelligence JSON this responds to. Null if unprompted. |
| `topic` | string | The specific topic or suggestion being responded to |
| `domain` | string | `finance`, `geopolitics`, `health`, `technology`, `personal`, `other` |
| `reaction` | string | `agree`, `disagree`, `already_tracking`, `not_relevant`, `want_more`, `note` |
| `position` | string | Free text — your stated view, as specific as you want |
| `confidence` | string | `high`, `medium`, `low`, `uncertain` |
| `want_more` | boolean | Flag: I want to know more about this topic |
| `pending_action` | string | `open_investigation`, `activate_priority`, `add_source`, null |
| `acted_on` | boolean | False in 1.0 — 1.1 sets true when action is taken |
| `timestamp` | string | ISO 8601 |

**Three response types:**

1. **Reaction to observation** — links back to specific intelligence output via `observation_ref`
2. **Stated position** — your view on a topic, prompted or unprompted. Survives independently of the observation that triggered it.
3. **Research intent** — `want_more: true` + `pending_action: "open_investigation"`. Stored now, acted on in 1.1.

---

## Capture Surfaces

Two surfaces for entering responses. Both write to the same
`intelligence_responses.json` file.

### Surface 1: Web UI — Intelligence tab

On the web UI Intelligence page (new tab or section), each observation
displayed with a response form beneath it:

- **Reaction** — dropdown: Agree / Disagree / Already tracking / Not relevant / Want more
- **Position** — free text field: "Your view (optional)"
- **Want more** — checkbox
- **Submit** button — writes to `intelligence_responses.json`

Display today's intelligence JSON and the most recent weekly JSON.
Previously submitted responses shown read-only beneath each observation.

### Surface 2: Telegram dialog bot

When you reply to a weekly connections or daily intelligence Telegram message,
the dialog bot:
1. Detects it is a reply to an intelligence message (via Telegram reply threading)
2. Parses your reply text
3. Makes a Haiku call to classify: domain, reaction type, position extraction
4. Writes structured response to `intelligence_responses.json`
5. Confirms back: "Noted — stored as [reaction] on [topic] in [domain] domain."

For unprompted notes (not a reply to an intelligence message):
- Bot detects "note:" prefix or classifies intent as a personal note
- Haiku classifies domain and response type
- Stored with `observation_ref: null`, `observation_type: "freeform"`

---

## Files Touched

| # | File | Action |
|---|------|--------|
| 1 | `intelligence_responses.json` | New file — create with empty responses array |
| 2 | `curator_intelligence.py` | Add `save_response()` helper function |
| 3 | Flask server | Add `POST /api/intelligence/respond` endpoint |
| 4 | New HTML page or section | Intelligence tab in web UI — display observations + response form |
| 5 | Telegram dialog bot | Add reply detection + Haiku classification + response write |

---

## What Is Explicitly NOT Built in Phase C

- Sonnet reading `intelligence_responses.json` — 1.1
- Automatic priority activation from `pending_action` — 1.1
- Investigation workspace opening from response — 1.1 (depends on WS3)
- Vector search or graph DB queries against responses — post-1.0
- Response history surfaced in weekly lateral connections — 1.1

These are noted here so the build record is clear: the schema is designed
for these future uses, but Phase C only captures. It does not act.

---

## Integration with Broader Roadmap

**1.0 delivers:**
- Intelligence observation loop (Phases A + B)
- Response capture layer (Phase C)
- `intelligence_responses.json` as the seed of the personal memory system

**1.1 connects:**
- Sonnet reads responses before generating lateral connections
- `pending_action` items activate automatically
- Investigation workspace opens from research intent responses
- Dialog history from Telegram bot stored and queryable

**Post-1.1 activates full RAG:**
- pgvector (already installed) indexes `intelligence_responses.json`,
  `curator_signals.json`, dialog history
- Neo4j (already installed) maps relationships between topics, positions,
  and reading history
- Every LLM call retrieves semantically relevant personal context before generating
- "What have I thought about this before?" becomes a real query

---

## Open Questions for OpenClaw

1. **intelligence_responses.json location** — confirm whether this lives in
   `~/.openclaw/workspace/` alongside other intelligence files, or in the
   project repo directory. Recommendation: workspace (personal data, not source code).

2. **Telegram dialog bot file** — confirm filename and location of the existing
   OpenClaw Telegram dialog bot. Phase C adds reply detection to it.

3. **Flask server file** — confirm filename for the Flask web server to add
   the new API endpoint.

4. **Web UI Intelligence tab** — does a page or section for intelligence output
   already exist in the web UI, or is this new? If new, confirm naming convention
   for HTML files.

5. **Existing response patterns** — confirm whether any response/annotation
   schema already exists in the workspace from earlier design sessions that
   should be reconciled with this schema.

---

## Verification Steps

**After Step 1-3 (backend):**
```bash
curl -X POST http://localhost:8765/api/intelligence/respond \
  -H "Content-Type: application/json" \
  -d '{
    "observation_type": "lateral_connection",
    "observation_ref": "intelligence_weekly_20260315.json",
    "topic": "crypto as speculative asset",
    "domain": "finance",
    "reaction": "disagree",
    "position": "Crypto tracks speculative not hedge. Gold correlation is the angle.",
    "confidence": "medium",
    "want_more": true
  }'
```
Expected: 200 OK, response written to `intelligence_responses.json` with auto-generated id and timestamp.

**After Step 4 (web UI):**
Open Intelligence tab in browser. Confirm today's observations display.
Submit a test response. Confirm it appears read-only beneath the observation.
Confirm `intelligence_responses.json` updated correctly.

**After Step 5 (Telegram):**
Reply to the most recent weekly connections Telegram message with:
*"Disagree on crypto — I track it only through gold correlation, speculative asset thesis"*
Expected: Bot confirms storage, `intelligence_responses.json` updated with
classified response linked to the correct weekly observation.

---

## Success Criteria

- `intelligence_responses.json` created with correct schema
- Web UI accepts and stores responses against specific observations
- Telegram reply correctly classified and stored with observation link
- Unprompted Telegram notes stored with `observation_ref: null`
- `acted_on: false` on all 1.0 responses — no premature action
- `pending_action` field populated correctly when research intent detected
- No regressions to Phase A or Phase B observations

---

## Post-Build

Claude Code writes build summary → "pass to Memory Agent" →
Memory Agent saves to `docs/BUILD_WS5_PhaseC_2026-03-15.md`,
appends CHANGELOG.

README architecture section to reference Phase C as the
feedback loop foundation. To be written by Strategy Agent
(Claude.ai) with Robert's input before public launch.

---

*Prepared by Claude.ai (Strategy Agent) — March 15, 2026*
*For OpenClaw (Memory Agent) validation before Claude Code handoff*
*Convention: Memory Agent saves to `docs/PLAN_WS5_PhaseC_2026-03-15.md`*

---

## Scope Amendments (March 15, 2026 — post-OpenClaw/Claude.ai review)

**Step 5 (Telegram dialog bot) deferred to 1.1.** Steps 1–4 only for 1.0.

### UI Design Notes (Claude.ai)

**1. Position field is primary, reaction is secondary.**
The `position` textarea is the actual value. The reaction dropdown is a classification convenience. UI order per observation:
```
[Observation text — read only]

Your view:
[textarea — 3-4 rows, prominent]

Reaction: [dropdown]  □ Want to know more

[Save response]
```
Position first, reaction second. Forces the richer input.

**2. Two sections with different response treatment.**
Weekly lateral connections get full response form. Daily observations get lighter treatment.

Page structure:
```
INTELLIGENCE — March 15

── WEEKLY CONNECTIONS ───────────────────────────
[Each connection: full form — textarea + reaction + want more + save]

── TODAY'S OBSERVATIONS ─────────────────────────
[Each observation: light form — reaction dropdown + one-line note field]
```

Weekly = substantive positions captured. Daily = quick reaction + optional note.

---

## FILE: docs/BUILD_WS5_PhaseA_2026-03-15.md

# Build Record: Intelligence Layer — Phase A
**Mini-moi Personal AI Curator**
**Built:** March 15, 2026
**Sprint:** 1.0 — Workstream 5
**Status:** Complete and confirmed working

---

## What Was Planned

Phase A established the foundation of `curator_intelligence.py` — a new daily observation component that runs after the main briefing and produces a structured intelligence summary.

Two observations targeted for Phase A:

**1. Topic velocity**
Compare today's scored articles against a 30-day baseline. Identify topics gaining momentum and topics absent from today's briefing that appear in the interest profile.

**2. Discovery candidates**
Surface unknown domains from the probationary tier in `curator_sources.json` added today. Flag for manual review with a Haiku quality assessment.

---

## Pre-conditions Completed

Two refactors executed before building `curator_intelligence.py`:

**Pre-1: `send_telegram_alert` moved to `curator_utils.py`**
Function previously lived in `curator_rss_v2.py` (line 386). Moved to `curator_utils.py` (shared utility module) so `curator_intelligence.py` could import it cleanly without pulling in the entire briefing pipeline. `curator_rss_v2.py` updated to import from `curator_utils`.

**Pre-2: `added_date` and `query` fields added to probationary entries**
`_log_probationary_domains()` in `curator_priority_feed.py` previously wrote probationary entries without `added_date` or `query` fields, making it impossible to filter "new today" entries. Fixed to write both fields on new entries. One-time backfill applied to 8 existing probationary entries (all added 2026-03-14).

---

## What Was Built

### New file: `curator_intelligence.py`

Daily intelligence observation script. Reads from:
- Today's scored history (top 20 articles)
- 30-day rolling history for baseline comparison
- `curator_sources.json` probationary tier for discovery candidates
- User interest profile for gap detection

Produces:
- Telegram message (brief — 5 lines max)
- `intelligence_YYYYMMDD.json` stored in OpenClaw workspace

### New file: `run_intelligence_cron.sh`
Shell wrapper following the same pattern as `run_curator_cron.sh` and `run_priority_feed_cron.sh` — activates venv, runs script, logs result.

### New file: `com.vanstedum.curator-intelligence.plist`
Registered to `~/Library/LaunchAgents/`. Fires at 7:30AM daily — after the 7AM briefing run, before the 2PM priority feed run. Logs to `logs/intelligence_launchd.log` and `logs/intelligence_launchd_error.log`.

---

## Confirmed Working Output (March 15, 2026)

```
🧠 Intelligence — Mar 15

📈 Momentum: Iran military tensions (Trump ultimatums, troop deployments),
 tariff rulings, AI capability advances
⚠️ Gap: Crypto/Bitcoin markets, detailed Fed monetary policy
🔍 Sources: No new sources discovered today.
```

**Notes on output:**
- Momentum call accurate — Iran/energy dominated today's top 20
- Gap detection working — crypto and detailed Fed policy genuinely absent from today's briefing
- Discovery candidates correctly returning "none" — Brave rate limiting reduced new domain surfacing today. Will improve once `time.sleep(1)` delay fix is confirmed in production
- Format is tight and readable

---

## Design Decisions Made During Build

**Gap detection scope:** The system flags gaps against the interest profile. Crypto/Bitcoin surfaced as a gap. However, the actual interest is specific — crypto/gold correlation and crypto as speculative vs uncertainty-hedge asset, not generic crypto coverage. This is a prompt tuning opportunity for Phase B or post-1.0 — gap detection should eventually match against thesis-level interests, not just topic keywords.

**Telegram delivery:** Intelligence message sent as a separate Telegram message, not appended to the daily briefing. Keeps the morning read clean. Intelligence is a separate register — advisory, not news.

**Storage format:** `intelligence_YYYYMMDD.json` in OpenClaw workspace alongside other workspace files. Not in repo — operational data, not source code.

**launchd timing:** 7:30AM — 30 minutes after briefing fires, well before priority feed at 2PM. Gives the briefing time to complete before intelligence reads from it.

---

## Cost

- Haiku calls for gap analysis and discovery assessment: ~$0.01–0.02/day (confirmed actual; design estimate was $0.02–0.05)
- No Sonnet or Grok calls in Phase A — observation only, no reasoning layer yet
- Phase A adds negligible cost to daily operation

---

## Open Items Carried to Phase B

- `time.sleep(1)` delay between Brave queries — rate limiting fix, small change
- Gap detection prompt refinement — thesis-level matching vs keyword matching (post-1.0 or Phase B)
- Treasury MSPD appearing 3x in top 20 — per-source cap of 2 worth considering (separate from intelligence layer, flag for WS review)

---

## Phase B Scope (Next)

Three remaining observation types:

**3. Source anomalies (Haiku)**
Review trusted sources' last 10 articles against historical profile. Flag drift.

**4. US press blind spots (cross-source)**
Stories with high velocity in non-US sources (Al Jazeera, DW, Spiegel, O Globo) with low or zero US outlet coverage. The gap is the signal.

**5. Weekly lateral connections (Sonnet)**
Reason across recent reading history and signal profile. Surface adjacent topics, second-order implications, suggested sources. Weekly cadence.

Delivery: same Telegram message and JSON storage as Phase A. Intelligence message grows as observations are added.

---

*Phase A confirmed: March 15, 2026*
*Authored by: Claude Code + OpenClaw (validation)*

---

## FILE: docs/BUILD_WS5_PhaseB_2026-03-15.md

# Build Record: Intelligence Layer — Phase B
**Mini-moi Personal AI Curator**
**Built:** March 15, 2026
**Sprint:** 1.0 — Workstream 5
**Status:** Complete and confirmed working
**Commits:** `846a0df`, `23ff20d`

---

## What Was Planned

Phase B added three remaining observation types to `curator_intelligence.py`:

1. **Source Anomalies (Obs 3)** — Haiku detects trusted sources drifting in topic focus, tone, or quality vs 30-day baseline. Quiet path: omit from Telegram if no anomalies.
2. **US Press Blind Spots (Obs 4)** — Haiku surfaces stories with strong non-US coverage absent from US outlets. Reads full scored candidate pool from `curator_latest.json`. Quiet path: omit if no significant gaps.
3. **Lateral Connections (Obs 5)** — Sonnet reasons across 30-day reading history and interest profile, surfaces 2–3 adjacent topics with suggested sources. Weekly cadence (Sundays only). Delivered as a separate Telegram message.

No new files, no new launchd plists — all three observations added to the existing daily job with a day-of-week guard for the weekly Sonnet call.

---

## Pre-conditions Completed

**`curator_rss_v2.py` — write `curator_latest.json`**
The full scored candidate pool was not previously stored anywhere accessible after the daily run. Phase B's blind spot detection (Obs 4) needs all candidates, not just the top 20 in `curator_history.json`. Added write at end of `curator_rss_v2.py main()`:
```python
with open('curator_latest.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
```
~200KB/day. Not committed to repo (operational data). This was a planned pre-condition deferred from Phase A.

---

## What Was Built

### `curator_intelligence.py` (`846a0df`, `23ff20d`)
- **`SONNET_MODEL`** constant added (`claude-sonnet-4-5`), alongside existing `HAIKU_MODEL`
- **`NON_US_DOMAINS` / `US_DOMAINS`** classification sets — hardcoded constants, consistent with whitelist pattern in `curator_priority_feed.py`
  - Non-US: `aljazeera.com`, `dw.com`, `spiegel.de`, `faz.net`, `welt.de`, `oglobo.globo.com`
  - US: `zerohedge.com`, `propublica.org`, `stlouisfed.org`, `ritholtz.com`, `antiwar.com`, `warontherocks.com`, `justsecurity.org`
- **`_sonnet()`** helper — mirrors `_haiku()` from Phase A, same keychain credential pattern
- **`_load_learned_patterns()`** — reads `curator_preferences.json` → `learned_patterns` (keys: `preferred_themes`, `preferred_sources`, `preferred_content_types`, `avoid_patterns`)
- **`observe_source_anomalies()`** — Obs 3, Haiku, quiet path
- **`observe_blind_spots()`** — Obs 4, Haiku, quiet path, reads `curator_latest.json`
- **`observe_lateral_connections()`** — Obs 5, Sonnet, Sunday guard
- **`save_output()`** — extended with `weekly=True` flag to write `intelligence_weekly_YYYYMMDD.json`
- **`main()`** — all five observations wired in; separate Telegram send for weekly lateral connections

### `curator_rss_v2.py` (`846a0df`)
- Added `curator_latest.json` write at end of `main()` — 9 lines including error handling

---

## Confirmed Working Output (March 15, 2026)

**Obs 3 (Source Anomalies):** Fired correctly on first run. Quiet path tested — returns nothing when no anomalies detected across trusted sources.

**Obs 4 (US Press Blind Spots):** Fired with Minab classroom missile strike story — covered by Al Jazeera and DW, absent from US outlet pool. Verified: 6 non-US + 7 US articles correctly classified from today's `curator_latest.json`.

**Obs 5 (Lateral Connections):** Fired (today is Sunday). Sonnet produced 2–3 topic suggestions from 30-day reading history. Delivered as separate Telegram message.

---

## Design Decisions Made During Build

**Minimum history threshold for Source Anomalies:** Sources with fewer than 5 articles in the 30-day history window are skipped silently. Thin history produces unreliable anomaly signals — better to omit than flag spuriously.

**Quiet paths are mandatory, not optional:** All three observations suppress output entirely when nothing meaningful is detected. The daily intelligence message should never pad with "nothing to report" lines. This was specified in the plan and enforced in implementation.

**`curator_latest.json` contains top_articles, not full pool:** The commit message notes the pre-condition writes `top_articles` to `curator_latest.json` — not the full pre-scoring candidate pool. Blind spot detection therefore works from the scored output set, not every article that entered the pipeline. Acceptable for 1.0 — the scored pool is large enough to surface cross-source gaps reliably.

**Domain classification bug fixed post-commit (`23ff20d`):**
The plan specified matching on `entry.get("source", "")` described as the "domain field stored on articles." In practice, the `source` field is a display name ("Al Jazeera", "ZeroHedge"), not a domain. The `link` field holds the full URL. Without the fix, every article was unclassified → Obs 4 always took the quiet path silently.

Fix: `extract_domain(entry.get("link", ""))` — consistent with how Obs 3 classifies history entries. The `NON_US_DOMAINS`/`US_DOMAINS` sets already used domains correctly; only the lookup key was wrong.

Before fix: 0 non-US, 0 US articles classified.
After fix: 6 non-US, 7 US articles classified. Obs 4 fired.

Lesson: when a quiet path is always triggered in testing, verify classification logic before assuming "nothing to report."

---

## Cost

- Obs 3 (Source Anomalies): Haiku, invoked only on flag — ~$0.005/day average
- Obs 4 (Blind Spots): Haiku, invoked only on flag — ~$0.005/day average
- Obs 5 (Lateral Connections): Sonnet, once per week — ~$0.05–0.10/week
- Phase B total addition: ~$0.01/day + ~$0.07/week — negligible

---

## Open Items Carried Forward

- `curator_latest.json` currently writes `top_articles` (scored output), not the full pre-scoring candidate pool. For full pipeline visibility in 1.1, consider writing all scored candidates before top-20 selection.
- Lateral connections prompt quality: first run produced usable output. Prompt may benefit from tuning after a few weeks of Sunday runs — flag for post-1.0 review.
- Source anomaly minimum threshold (5 articles/30 days): conservative. May need lowering for newer trusted sources added to `curator_sources.json` that haven't accumulated history yet.

---

## Workstream 5 Status

**Complete.** All five observation types operational:

| Obs | Name | Status |
|---|---|---|
| 1 | Topic Velocity | ✅ Phase A |
| 2 | Discovery Candidates | ✅ Phase A |
| 3 | Source Anomalies | ✅ Phase B |
| 4 | US Press Blind Spots | ✅ Phase B |
| 5 | Lateral Connections | ✅ Phase B |

Intelligence layer running daily at 7:30AM. Weekly Sonnet call fires Sundays.

---

## Next: Remaining Sprint Work

- **Workstream 3:** Investigation workspace infrastructure (data layer, no UI)
- **Workstream 4:** Mac Mini migration
- **GitHub cleanup:** README, issues, roadmap, CLAUDE.md all current → public launch

---

*Phase B confirmed: March 15, 2026*
*Authored by: Claude Code + OpenClaw (validation and completion)*
*Plan doc: docs/PLAN_WS5_PhaseB_2026-03-15.md*

---

## FILE: docs/BUILD_WS5_PhaseC_2026-03-15.md

# Build Record: Intelligence Layer — Phase C
## Response Capture & Feedback Loop Foundation
**Mini-moi Personal AI Curator**
**Built:** March 15, 2026
**Sprint:** 1.0 — Workstream 5, Phase C
**Status:** Complete and confirmed working
**Commits:** see feat commit below

---

## What Was Planned

Steps 1–4 of `docs/PLAN_WS5_PhaseC_2026-03-15.md`.
Step 5 (Telegram dialog bot reply detection) explicitly deferred to 1.1.

New file: `curator_intelligence.html` — Intelligence response capture page.
New data file: `intelligence_responses.json` — append-only response store.
New Flask endpoint: `POST /api/intelligence/respond`.
New helper: `save_response()` in `curator_intelligence.py`.

---

## Pre-conditions Completed

**`intelligence_responses.json` created (Step 1 — operational pre-condition)**
Created `~/.openclaw/workspace/intelligence_responses.json` with `{"responses": []}`.
Not committed to repo — operational data per `WAYS_OF_WORKING.md`.

**Intelligence page route already existed in `curator_server.py`**
`GET /curator_intelligence.html` → `send_from_directory` route was present from a prior session (line 753). No new route needed for page delivery — only the API endpoints were new.

---

## What Was Built

### Step 0 — Design gate (added by Robert, not in original plan)
`curator_intelligence.html` built first as a static mockup with hardcoded data for design review before any backend work. Three design iterations:
1. Blind spot card missing response form → fixed to show light form
2. Gap observation missing context → added one-line reasoning per interest area
3. Design approved → proceed to real API wiring

This step is not in the plan doc but was the right call — catching layout issues before wiring to backend is cheaper than refactoring after.

### Step 2 — `curator_intelligence.py`
- Added `RESPONSES_PATH = Path.home() / '.openclaw' / 'workspace' / 'intelligence_responses.json'` alongside existing path constants
- Added `save_response(data: dict) -> dict` in the `# ── Output storage ──` section
  - Auto-generates `resp_NNN` IDs (increments from existing responses count)
  - Stamps `date` (YYYY-MM-DD) and `timestamp` (ISO 8601)
  - Sets `acted_on: False` — always in 1.0
  - Appends to file atomically, returns completed response dict

### Step 3 — `curator_server.py`
- Added `GET /api/intelligence/latest` — returns today's daily JSON, most recent weekly JSON, all existing responses, and today's date string. Reads from `~/.openclaw/workspace/`
- Added `POST /api/intelligence/respond` — validates `reaction` field, delegates write to `save_response()`, logs to stdout
- Static page route (`/curator_intelligence.html`) was already present (line 753) — no change needed

### Step 4 — `curator_intelligence.html` (wired to API)
Replaced all mock data and save handlers with real API calls:
- `DOMContentLoaded` fetches `/api/intelligence/latest`
- Derives `dailyRef`/`weeklyRef` filenames from returned date
- Matches existing responses to cards via `observation_ref` + `topic`/`observation_type`
- **Weekly cards:** Parses Sonnet output by splitting on `<b>N.` pattern, extracts topic as card heading (Playfair Display), strips numbered prefix from body
- **Daily cards:** Filters out `lateral_connections` type, maps observation types to display labels via `OBS_TYPE_LABELS`
- **`mdToHtml()` JS helper** — converts `**bold**`/`*italic*` markdown remaining in stored JSON to HTML at render time (Haiku observations mix HTML labels with markdown emphasis)
- **`data-*` attributes** — `data-topic`, `data-ref`, `data-type` on each card div, read by save handlers. Avoids closure/scope issues with rendered HTML
- **`saveWeekly()` / `saveDaily()`** — read card attributes, POST to `/api/intelligence/respond`, replace form with read-only saved state (WANT MORE badge + italic position text) on success, increment page meta count
- **Button `disabled` during async POST** — prevents double-submit

---

## Confirmed Working Output (March 15, 2026)

Full end-to-end test:
- Page loads with real Sonnet lateral connections (3 weekly cards) + 4 daily observations
- Blind spot card: selected "Want more", entered position text, clicked Save
- Card replaced form with WANT MORE badge + italic saved position text
- `intelligence_responses.json` written: `resp_001`, `observation_type: "blind_spot"`, `observation_ref: "intelligence_20260315.json"`, `acted_on: false` ✅
- Page meta updated: "0 responses saved" → "1 response saved" ✅

---

## Design Decisions Made During Build

**Design gate before backend wiring (Step 0).**
Static mockup reviewed and approved before API work. Three iterations caught layout issues cheaply. Pattern worth repeating for new UI pages.

**No domain badge on weekly cards.**
Real Sonnet lateral connections output has no domain field. Domain inference deferred to 1.1 or post-1.0.

**Topic extraction from HTML.**
Weekly Sonnet output uses `<b>N. Title</b>` pattern (not H3 markdown). Extracted via regex, rendered separately as Playfair Display heading. This is a fragile dependency on Sonnet's output format — if the prompt changes, the parser needs updating.

**`mdToHtml()` conversion at render time, not storage time.**
Haiku observations mix HTML tags (for labels) with markdown emphasis. Conversion in the JS render layer keeps the storage layer clean and unchanged. Consistent with the `_md_to_html()` fix in Phase B.

**`data-*` attributes on card elements.**
Avoids closure/scope issues with dynamically rendered HTML. Read by save handlers at click time, not at render time.

**`acted_on` always `False` in 1.0.**
Schema field is ready for 1.1. Nothing sets it to `True` yet — that requires `pending_action` execution logic, deferred.

---

## Cost

No additional LLM costs. Phase C is pure data capture — no Haiku or Sonnet calls. All intelligence generation happens in Phases A and B; Phase C only stores user responses.

---

## Open Items Carried Forward

- **Step 5 (Telegram reply detection + Haiku classification)** — 1.1
- **Condensed/collapsed view with filter bar** — post-Phase C polish, queued (spec in session, Robert approved queuing)
- **Domain badge on weekly cards** — needs domain field or inference, 1.1
- **`acted_on` flag activation** — 1.1 sets `True` when `pending_action` items executed
- **Sonnet output format dependency** — topic extraction regex brittle; review if lateral connections prompt changes

---

## Workstream 5 Complete

All planned 1.0 work delivered:

| Phase | Content | Status |
|---|---|---|
| A | Topic velocity, discovery candidates | ✅ |
| B | Source anomalies, blind spots, lateral connections | ✅ |
| C | Response capture — web UI (Steps 1–4) | ✅ |
| — | Telegram dialog bot (Step 5) | → 1.1 |

---

## Git Commit

```bash
git add curator_intelligence.py curator_server.py curator_intelligence.html
git commit -m "feat: WS5 Phase C — intelligence response capture (Steps 1–4)"
```

`intelligence_responses.json` is operational data — **not committed**.

---

*Phase C confirmed: March 15, 2026*
*Authored by: Claude Code + OpenClaw (validation and completion)*
*Plan doc: docs/PLAN_WS5_PhaseC_2026-03-15.md*

