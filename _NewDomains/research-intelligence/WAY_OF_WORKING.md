# Way of Working: Agent Collaboration Protocol
**Project:** research-intelligence (personal-ai-agents agentic extension)  
**Created:** March 20, 2026  
**Applies to:** OpenClaw, Claude Code, Claude.ai  
**Status:** Active — PoC phase  

---

## Purpose

This document defines how three AI tools collaborate to build and operate the Research Intelligence Agent. It is a binding protocol, not a suggestion. All three tools should read this before doing any work on the project.

The goal is safe, documented, cost-controlled collaboration where Robert stays in control of all consequential decisions, bugs are captured even when tools catch them themselves, and everything is reproducible from the Git history.

---

## Tool Roles — Hard Boundaries

### Claude.ai (design collaborator)
- Architecture, direction documents, way-of-working documents, reviewing specs
- Reviewing what OpenClaw produces before it gets built
- No code execution, no file writes to the project repo directly
- Communicates with Robert in web chat; outputs saved to disk by Robert

### OpenClaw (orchestrator and research agent)
- Runs the research sessions autonomously per the direction document
- Writes to the local library (`~/research-intelligence/`)
- Decides what needs to be built and writes specs for Claude Code
- Triggers Claude Code with a spec — never asks it to "figure it out"
- Maintains session logs, budget ledger, CHANGELOG
- Communicates with Robert via Telegram and web chat
- Does NOT merge to GitHub without Robert's approval

### Claude Code (implementation only)
- Receives specs from OpenClaw or Robert, builds exactly what is specced
- Does not design, does not refactor outside the spec scope
- Proposes code, waits for review before any file is written to the repo
- Captures bugs it finds during implementation in `BUGS.md`
- Does not communicate with Robert directly — all output reviewed by OpenClaw or Robert before action

---

## The Build Protocol

Every piece of code or infrastructure follows this sequence. No exceptions.

```
1. NEED identified (OpenClaw or Robert)
        ↓
2. SPEC written (OpenClaw drafts, Claude.ai reviews if architectural)
        ↓
3. ROBERT APPROVES spec before build starts
        ↓
4. Claude Code BUILDS per spec
        ↓
5. OpenClaw or Robert REVIEWS output
        ↓
6. Robert APPROVES merge
        ↓
7. CHANGELOG updated, commit message written
        ↓
8. MERGE to branch, PR to main
```

**Step 3 and Step 6 are Robert's. They cannot be delegated.**

If Claude Code finds a bug or scope issue during Step 4, it stops, documents in `BUGS.md`, and surfaces to OpenClaw. It does not self-expand scope to fix adjacent things.

---

## Repository Structure

During PoC phase, lives in `_NewDomains/research-intelligence/` within personal-ai-agents repo.  
When pilot succeeds, graduates to its own repo: `research-intelligence/`.

```
research-intelligence/
├── README.md                        # Project overview, status, how to run
├── CHANGELOG.md                     # Every meaningful change, maintained by OpenClaw
├── BUGS.md                          # Bug log — open and closed, maintained by all tools
├── WAY_OF_WORKING.md                # This document
├── docs/
│   ├── direction/
│   │   ├── openclaw_direction_v2.md         # Primary direction document
│   │   ├── origin_conversation.md           # How this started (historical)
│   │   └── openclaw_approach.md             # OpenClaw's own approach additions
│   └── specs/
│       └── [feature]-spec-[date].md         # One spec per Claude Code build task
├── agent/
│   └── [OpenClaw scripts and config]
├── library/                         # Symlink or copy of ~/research-intelligence/
│   ├── README.md
│   ├── session-log.md
│   ├── sources/
│   ├── topics/
│   ├── translations/
│   ├── essays/
│   └── reading-list.md
├── web/                             # HTML reading interface (see spec below)
│   ├── index.html
│   ├── generate.js                  # Static site generator, run by OpenClaw
│   └── [generated HTML files]
└── curator-candidates/              # Validated sources ready to propose to Curator
    └── [source]-[date].md
```

---

## Git Protocol

**Branch strategy:**
- `main` — stable, working, Robert-approved only
- `poc/[feature]` — PoC work branches, one per feature
- Never commit directly to main

**Commit message format:**
```
[type] short description of what changed

Why: one sentence on why this change was made
Spec: link to spec file if Claude Code build
Cost: API cost of this session if applicable (OpenClaw commits only)
```

Types: `feat`, `fix`, `doc`, `refactor`, `bug`, `config`

Examples:
```
feat: add static HTML generator for library index

Why: Robert needs a browsable reading interface without a database
Spec: docs/specs/html-reader-spec-2026-03-21.md
```

```
bug: fix budget ledger not reading cumulative total on session start

Why: hard stop was behavioral not technical, could be missed
```

**Pull requests:** OpenClaw opens them. Robert merges them. No auto-merge.

---

## Bug Documentation Protocol

All bugs go into `BUGS.md` immediately when found, regardless of who finds them.

**Format:**
```markdown
## BUG-[number]: [short title]
**Found by:** [OpenClaw / Claude Code / Robert]  
**Date:** YYYY-MM-DD  
**Status:** OPEN / FIXED / WONTFIX  
**Severity:** low / medium / high  

**What happened:**  
[Description]

**Expected:**  
[What should have happened]

**Fix:**  
[What was done, or blank if open]

**Commit:**  
[Commit hash when fixed]
```

Even trivial bugs get logged. The PoC is a learning artifact — the bug log is part of the record.

---

## The HTML Reading Interface — First Claude Code Spec

This is the first concrete build task for Claude Code. OpenClaw triggers this after the library directory structure exists and has at least a few entries.

**What it is:** A static HTML site generated from the local library markdown files. No server, no database, runs offline on Robert's MacBook. OpenClaw regenerates it by running `node web/generate.js` after adding new content.

**Requirements:**
- Single `index.html` entry point with search and filter
- Reads from `library/README.md` master index table
- Filter by: topic, language, type (paper/book/article/essay), date range
- Search by keyword across title, summary, source fields
- Each item links to: local file path (opens in editor) or URL (opens in browser)
- Essays section: lists agent-written essays with title, date, 2-sentence summary, link
- Reading list section: books flagged for Robert with rationale
- Sessions log section: last 10 session entries so Robert can see recent activity
- No external dependencies — pure HTML/CSS/JS, works offline
- Parchment aesthetic to match Curator (Robert's preference, established in existing UI)
- Mobile-readable but MacBook is primary

**What it is NOT:**
- Not a web app, not a backend, not a database
- Not integrated with Curator (yet — that's Phase 2)
- Not auto-refreshing — OpenClaw runs the generator, that's the refresh

**Claude Code instruction:**
> Build `web/generate.js` — a Node.js script that reads `library/README.md`, parses the markdown table, and generates `web/index.html` with filter, search, and the sections listed above. Parchment color palette (cream/tan background, dark brown text, muted amber accents). No frameworks, no build tools, no npm dependencies beyond Node.js built-ins. The script should be runnable as `node web/generate.js` from the project root. Show me the script before writing any files.

---

## Budget Enforcement — Technical Implementation

The budget ledger must be a technical mechanism, not a behavioral one. OpenClaw implements this as follows:

**`library/session-log.md` ledger format:**
```
| Date | Session | Duration | Cost | Cumulative | Notes |
|------|---------|----------|------|------------|-------|
| 2026-03-21 | burst | 4min | $0.12 | $0.12 | Kotkin citation search |
```

**On every session start, OpenClaw:**
1. Reads the cumulative total from session-log.md
2. Reads today's total (sum of today's rows)
3. Checks against limits: $3/day, $10/week, $20 total
4. If within limits: proceeds, logs start
5. If at warning threshold ($2.50/day or $18 total): messages Robert before proceeding
6. If at hard limit: stops, messages Robert, does not proceed

This logic should be in OpenClaw's session initialization, not dependent on memory.

---

## The Design Question — Answered

OpenClaw asked: when I find something relevant but outside the two stated frames (geopolitics + monetary systems) — flag and ask, or skip?

**Answer: flag and ask. Never skip.**

The citation graph leads where it leads. A thread that starts in monetary systems might surface a development economist whose framework connects back in ways that aren't obvious until you've read it. The frames are attractors, not walls.

**Flag format for out-of-frame finds:**
```
[Research Intel] 🔀 Outside stated frames — worth pursuing?

Found: [source/title]
Domain: [what it's actually about]
Connection: [how it might connect to geopolitics or monetary systems]
Cost to pursue: [estimated, e.g. "2-3 Haiku calls, ~$0.08"]

Reply YES / NO / LATER
```

Robert answers and that becomes part of the record. If he says yes repeatedly to a new domain, that's a signal to propose expanding the frames formally.

---

## Graduation Criteria — PoC to Own Repo

The project graduates from `_NewDomains/` to its own `research-intelligence/` repo when:

1. Pilot succeeds (3 of 5 success criteria met)
2. HTML reader is built and working
3. At least one essay produced
4. Budget ledger is technical (not behavioral)
5. CHANGELOG and BUGS.md have real entries
6. Robert decides it's ready

At graduation: repo is created, `_NewDomains/research-intelligence/` history is preserved via git subtree or manual copy with origin note, Phase 2 formalization begins.

---

## Test Protocol — Build Verification

Every Claude Code build must be accompanied by a test checklist before merge is approved.
OpenClaw runs the tests and reports pass/fail to Robert. Robert approves merge only after
all required tests pass or a WONTFIX decision is recorded for each failure.

**Format:**

```markdown
### Test: [build-name] — [date]
Run by: OpenClaw / Robert
Branch: poc/[branch]

| # | Test | Method | Pass criteria | Result |
|---|------|--------|--------------|--------|
| 1 | ... | ... | ... | PASS / FAIL / SKIP |
```

Results go in `docs/test-reports/[build-name]-[date].md`.
Failures that block merge go into `BUGS.md` before the report is closed.

---

### Test Cases: poc/research-source-quality

The following cases verify the three phases of `b4bd4af`.
Run order: Phase 1 → Phase 2 → Phase 3 (each adds cost — run in sequence, one session).

**Prerequisites:**
```bash
# Dry-run that costs only Haiku (skip if Ollama available — that costs $0.00)
python agent/research.py --session-name verify-001 \
  --topic empire-landpower --estimated-cost 0.10
```

---

**Phase 1 — Ollama preflight + Telegram expansion**

| # | What to verify | How | Pass criteria |
|---|---------------|-----|--------------|
| 1 | Single Ollama log line at session start | Read stdout | Exactly one line matching `Ollama: available` or `Ollama: unavailable — using Haiku fallback` appears in `[0] Pre-flight` block; none in `[2] Triage` block |
| 2 | Ollama unavailable → no per-candidate timeout waste | Shut down Ollama, run session, check elapsed time | `[2] Triage` block completes in < 30s for 12 candidates (not 60s+) |
| 3 | Ollama unavailable → all triage falls back to Haiku | Run with Ollama down | `Ollama: 0` in triage summary line; cost_log has `triage-N` entries |
| 4 | Telegram message has triage model label | Check Telegram receipt or findings file | Message body contains either `gemma3:1b` (Ollama up) or `claude-haiku-4-5` (Ollama down) |
| 5 | Telegram message has `N local / N Haiku` counts | Check Telegram receipt | Counts present and match stdout triage summary |
| 6 | Telegram message has top-find URL on its own line | Check Telegram receipt | URL line present below `⭐ Top find:` sentence |
| 7 | `ollama_health_endpoint` read from config | Edit config to wrong URL, confirm unavailable reported | Ollama reported unavailable; no hardcoded fallback to `localhost:11434` bypasses the bad URL |

---

**Phase 2 — Institution-targeted searches**

| # | What to verify | How | Pass criteria |
|---|---------------|-----|--------------|
| 8 | Institution queries reported in log | Read stdout Step 1 | Line: `Institution searches: 5 configured, N slots available, N run` |
| 9 | Institution results tagged in candidates file | Open `sources-candidates-verify-001.md` | Rows from institution queries show `[institution]` prefix in Notes column |
| 10 | Institution sources present | Scan candidates file URLs | At least one URL from clacso.org, swp-berlin.org, carnegieendowment.org, nuevasociedad.org, or csstoday.net |
| 11 | Session searches are never truncated for institution slots | Set `max_searches: 3` in config temporarily, run session with 3 session queries | All 3 session queries run; institution queries log `0 run`; no session query dropped |
| 12 | Institution queries dropped silently when no slots | Fill all 8 slots with session searches | Log line shows `0 run`; no error; session completes normally |

---

**Phase 3 — Citation graph chase**

| # | What to verify | How | Pass criteria |
|---|---------------|-----|--------------|
| 13 | Citation chase skipped when no ≥4 candidates | Run session with deliberately weak queries; confirm no ≥4 scored | Stdout: `Citation chase skipped — no candidates scored ≥4.` |
| 14 | Citation chase runs when ≥4 candidates exist | Run normal session | Stdout: `[2.5] Citation graph chase...` followed by chase entries |
| 15 | OpenAlex lookup fires for each ≥4 candidate | Check stdout Step 2.5 | One `Chasing: [title]` line per candidate with score ≥4 |
| 16 | Citation extras triaged | Read stdout | `[cite-N/M]` lines appear after step 2.5 header |
| 17 | Cited-by label in candidates file | Open candidates md | ≥4 citation extras show `[citation_chain · cites: ...]` in Notes column |
| 18 | Citation cost tracked | Open session findings md | `cite-N` rows present in Cost Breakdown table |
| 19 | No OpenAlex auth errors | Read stdout | No `401` or `403` in warnings — OpenAlex is unauthenticated |
| 20 | `quote()` handles non-ASCII titles | Manually call `chase_citations({"title": "依存論と大国競争"})` in Python REPL | No exception; returns list (possibly empty if no OpenAlex match — that is expected) |

---

**Regression — briefing unaffected**

| # | What to verify | How | Pass criteria |
|---|---------------|-----|--------------|
| 21 | Curator daily briefing still runs | `curl http://localhost:8766/` | Returns HTML; no 500 error |
| 22 | Research routes still load | `curl http://localhost:8766/research/observe` | Returns 200 |
| 23 | research_routes.py not modified | `git diff HEAD~1 research_routes.py` (in personal-ai-agents repo) | No diff |

---

## A Note on This Document

This way-of-working document was written before any code exists. That's intentional. The protocol comes first, the build follows it. If any tool finds this document unclear or contradictory during a build session, it should stop and surface the ambiguity to Robert rather than interpret its way through it.

This document can be updated. Changes go through the same process as everything else: propose, review, Robert approves, commit with reason.

---
*Last updated: March 22, 2026*
*Owner: Robert*  
*All three tools are bound by this document*
