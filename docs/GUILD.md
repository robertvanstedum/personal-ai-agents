# Guild — Personal Operating System
*mini-moi · personal-ai-agents*

- **Version:** 1.0 — 2026-06-11 07:50 CDT (12:50 UTC)
- **Last updated:** 2026-06-11
- **Status:** Active design — builds in progress
- **Technical reference:** `docs/GUILD_AGENTS_DESIGN.md` for agent build specs
- **Build history:** `docs/GUILD_BUILD_LOG.md`

---

## What Guild is

Guild is the personal operating system layer of mini-moi. Where Curator handles
geopolitical and financial intelligence, and Mein Deutsch handles language learning,
Guild handles the operating context: career, work pipeline, build health, and the
daily executive view that pulls everything together.

The framing is a Chief of Staff model. The CoS agent runs continuously, monitors
all domains, surfaces what matters, and manages the agents beneath it. Robert is the
executive — he receives briefings, makes decisions, confirms or dismisses recommendations.
He does not manage the system; the system manages itself and surfaces what needs him.

Guild is also where mini-moi manages its own development. The same pipeline pattern
that tracks career opportunities tracks build items, spec reviews, and design decisions.
The same daily briefing that surfaces career follow-ups surfaces build decisions that
are waiting on Robert. The system is self-referential by design.

---

## Architecture overview

```
Guild
├── Agent layer (always running)
│   ├── Operations (port 8768)        — infrastructure steward
│   ├── Chief of Staff (port 8769)    — proactive intelligence partner
│   └── Design/Dev (port 8770)        — build memory and traffic cop
│
├── Interface layer (portal pages)
│   ├── /guild                        — Daily Briefing (homepage)
│   ├── /guild/career                 — Positions (pipeline table)
│   ├── /guild/career/active          — Active Pipeline (board)
│   └── /guild/build                  — Build Clarity
│
└── Communication layer
    ├── Telegram (scheduled briefings + critical alerts)
    └── Portal (full detail on all pages)
```

Guild is the fourth top-level domain in mini-moi's navigation:
`mini-moi | Curator | German | Guild`

All Guild pages are **owner-only** — not accessible to family or guest tiers.
Career data, build state, and agent logs are private by default.

---

## The agent layer

Three persistent Flask services run under launchd, continuously monitoring and acting
within their defined authority. Full technical specifications are in
`docs/GUILD_AGENTS_DESIGN.md`.

### Operations (port 8768 · `com.user.operations`)

Infrastructure steward. Monitors system health, manages disk and logs, restarts
failed services, and escalates issues to the Chief of Staff rather than directly
to Robert.

**Authority tiers:**
- Tier 1: Act silently (disk cleanup, log rotation, service restart, cron re-run)
- Tier 2: Act and notify CoS (briefing missed and re-run, emergency cleanup)
- Tier 3: Escalate to CoS with options (service instability, unexpected disk growth)
- Tier 4: Escalate directly to Robert (service down >15 min, disk >95%, data integrity)

Operations writes to `guild.ops_escalation_queue`. CoS reads this queue hourly and
routes escalations appropriately. Tier 4 bypasses CoS and sends a direct Telegram
critical alert regardless of system state.

### Chief of Staff (port 8769 · `com.user.cos`)

Proactive intelligence partner across all domains. Runs six concurrent loops on
different cadences, maintains memory, and surfaces findings to Robert via Telegram
and the portal recommendations queue.

**Intelligence loops:**
| Loop | Cadence | Function |
|---|---|---|
| A — Career scout | Daily 06:00 + 18:00 | Tavily + RSS career opportunity search, scoring, Telegram alerts |
| B — German watch | Sunday 09:00 | Practice cadence check, emerging language tool search, tutor/group search |
| C — Curator scout | Sunday 10:00 | Emerging topic search → Desk "Suggested by CoS" queue |
| D — Novelty watch | 1st + 15th 08:00 | mini-moi competitive scan, threat/complement/incorporate evaluation |
| E — Operations oversight | Hourly | Reads ops escalation queue, routes by tier, detects patterns |
| F — Domain health | Daily | Activity checks across all domains, practice cadence, stale item detection |

CoS maintains two memory surfaces: `data/guild/memory/cos_memory.md` (distilled,
8,000-char cap, monthly Sonnet compaction) and `guild.agent_log` (full structured
history). The context file at `domains/guild/config/cos_context.json` is Robert's
primary control surface — editing it changes CoS behavior on the next cycle.

**The /chat endpoint** accepts natural language queries via Telegram (`!cos` or `!chief`
prefix). Grok → Haiku → mistral:7b fallback chain. Emergency direct-data fallback when
all LLMs are unavailable.

### Design/Dev agent (port 8770 · `com.user.devagent`)

Level 1: traffic cop and build memory. Watches `_working/` and `docs/` for new and
modified files. Classifies each document (Haiku call), logs to `guild.design_log`,
appends to `data/guild/memory/devagent_memory.md`, and notifies both Robert and CoS
simultaneously via parallel threads.

Manages `_working/` lifecycle: active files in root, superseded files auto-archived
to `_working/archive/YYYY-MM/`, trash held for confirmation before deletion.
Tracks which files need pushing to the private GitHub remote and surfaces the push
command on request (`!dev push-private`).

**Autonomy dial:** current level 1 (watch, log, notify, flag). Level 2 adds routing;
level 3 adds first-pass testing; level 4 is full. Configurable in
`domains/guild/config/devagent_config.json`.

---

## The interface layer

### Guild Daily Briefing — `/guild`

The Guild homepage. A one-page executive summary generated by CoS each morning,
covering the four areas Robert needs to know to navigate the day.

**Four sections:**

**Systems** — pulled from Operations `/status`. One-line status indicator.
If all clear: a single green line. If anything needs attention, it surfaces here
with a link to detail.

**Career** — pulled from `jobs.career_opportunities` (will become `pipeline.items`
after the schema rename). August 1 countdown. Active pipeline
summary (starred applications, interview count). New opportunities from Loop A.
Follow-up attention items (applications >7 days with no response).

**Build** — pulled from `guild.design_log` + `guild.cos_agenda`. What is actively
being built, what is spec-ready and queued, what is waiting on a decision from Robert.
The "needs decision" row is the most actionable — items blocked on Robert surface here
and also generate an immediate Telegram ping when created.

**Ahead** — CoS forward-looking intelligence. Upcoming scheduled events, contract
countdown, German practice cadence, novelty watch findings, any items CoS has flagged
as emerging.

The briefing is also delivered via Telegram as a condensed version with a link to the
full page (see Communication layer).

### Pipeline — `/guild/career` and `/guild/career/active`

The pipeline is a general-purpose opportunity and work tracker. The data model is
domain-agnostic via a `context` field:

| Context | Use case |
|---|---|
| `career` | Job search — current primary use |
| `contract` | Contract work and consulting opportunities |
| `sales` | Business development and sales leads |
| `build` | mini-moi feature and build tracking |

**Two pages serve different purposes:**

**Positions** (`/guild/career` — default) — the full inventory table. Every item
regardless of status. Filters by status, type, geo, and priority. Sortable. The
working surface for volume management: reviewing suggestions, changing statuses,
marking follow-ups. Backend reporting source — all analysis queries run against this.

**Active Pipeline** (`/guild/career/active`) — kanban board. Only items that have
motion: starred applications, items in reviewing or interview, and recently closed
items during a configurable grace period (default: 5 days). This is the view for
managing real conversations — opened when Robert has companies engaging with him.

A card appears on the board when any of these is true:
```
priority = TRUE AND status = 'applied'        (Robert's real bets)
OR status IN ('reviewing', 'interview')        (company engaged)
OR (status = 'closed'
    AND closed_at > NOW() - INTERVAL '5 days') (recently closed grace period)
```

Rejected cards never appear on the board. The board stays clean.

**Priority flag (★):** one-click toggle on any item. Starred items sort to the top
in the table and appear on the board. Unstarring removes a card from the board.

**Close reason:** when an item moves to CLOSED, a required reason distinguishes the
outcome — Filled, Rejected, Declined, or Accepted. ACCEPTED triggers an immediate
CoS Telegram notification suggesting deactivation of Loop A.

**Schema:** `pipeline.items` — single table, all contexts. *(Schema currently
`jobs.career_opportunities`; rename to `pipeline.items` is a prerequisite for the
two-page design and is planned as the first migration in that build.)*
Key columns: `title`,
`company`, `context`, `status`, `close_reason`, `priority`, `fit_score`,
`fit_narrative`, `warm_lead`, `url`, `created_at`, `closed_at`.

### Build Clarity — `/guild/build`

*(Designed — not yet built)*

A single view of the mini-moi build state, removing the need to track build
status across conversations, `_working/` documents, and GitHub issues.

Four rows:
- **Active** — what Claude Code is currently building (from Design/Dev agent logs)
- **Queued** — handoff docs in `_working/` that are spec-ready and waiting
- **Issues** — open GitHub issues (bugs, UI fixes)
- **Deferred** — documented but not scheduled

CoS prunes this view via Loop F: items inactive for >21 days are surfaced for
deprecation. Robert's action is a one-click dismiss. Deprecated items disappear
from active views but remain in `guild.design_log` for history.

GitHub issues and pipeline `build` context items are not auto-synced — CoS
surfaces discrepancies and Robert decides which matter.

---

## Communication layer

### Two-tier Telegram model

**Scheduled briefings** — generated on a daily timer, require the full system
to be running. Rich content, all sections, links to portal.

**Critical alerts** — fire immediately on event, any hour, backend-independent.
A direct HTTP POST to the Telegram API — no database query, no CoS routing, no
Flask dependency. Works when everything else is down.

```python
def send_critical_telegram(message: str):
    """Direct API call. No dependencies. Always works."""
    token = keyring.get_password("rvsopenbot", "token")
    chat_id = keyring.get_password("rvsopenbot", "chat_id")
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id,
              "text": f"⚠️ Guild critical\n{message}\nminimoi.ai/guild"},
        timeout=5
    )
```

This function is imported by Operations, CoS, and Design/Dev. It is the last
line of communication that survives any system failure.

**Critical alert triggers:**
- Operations: service down >15 min, disk >95%, data integrity
- Career: ACCEPTED outcome (search over), interview tomorrow (prep reminder)
- Build: build failure detected by Design/Dev agent
- Any `flag_to_robert, priority: high` escalation from any agent

### Telegram channels

Two separate bots with distinct personalities:

| Bot | Channel | Purpose |
|---|---|---|
| minimoi_cmd_bot | Command channel | German drills, Curator briefings, phrasebook commands |
| Rvsopenbot (→ minimoi_cos_bot) | Agent channel | Guild briefings, career alerts, build notifications, `!cos` / `!ops` / `!dev` |

All Guild communications go through Rvsopenbot. The name will be updated to
`minimoi_cos_bot` in Telegram BotFather settings — no code change required.

### Daily delivery timing

| Domain | Time | Channel |
|---|---|---|
| Guild Daily Briefing | 07:00 | Rvsopenbot |
| Curator Daily Briefing | 07:30 | minimoi_cmd_bot |
| German Morning Brief | 08:30 | minimoi_cmd_bot |

Staggered 30-minute intervals so each arrives as a distinct read.
Timezone: `America/Chicago` (configurable in `cos_context.json`).

### Telegram command reference

| Command | Routes to | Function |
|---|---|---|
| `!cos [query]` | CoS `/chat` | Natural language — any question about system or goals |
| `!chief [query]` | CoS `/chat` | Alias for `!cos` |
| `!ops status` | Operations `/status` | Full agent state, uptime, escalations |
| `!ops disk` | Operations `/status` | Disk usage and services summary |
| `!ops log` | Operations `/log` | Last 5 maintenance actions |
| `!dev status` | Design/Dev `/status` | Memory size, watching paths, autonomy level |
| `!dev push-private` | Design/Dev | Generate private repo sync commands |
| `!dev summary` | Design/Dev | Today's design log summary |

`!ops` and `!cos` are fast shortcuts; `!cos` always goes through the LLM and can
answer any natural language question including all `!ops` queries.

---

## Design principles

**CoS prunes, Robert decides.** The system surfaces what needs attention and proposes
actions. Robert confirms or dismisses. Nothing consequential happens without his
explicit approval. Agents can act autonomously within their pre-declared tiers.

**Spend follows attention.** Background loops use Haiku or Ollama for filtering.
Sonnet and Grok fire only when findings have passed the filter and will be seen by
Robert. The CoS does not generate noise.

**Pipeline is reusable.** The same two-page design (table + board), data model, and
CoS integration works for career opportunities, contract work, sales leads, and build
tracking. Context is a field, not a schema.

**Bones first.** Each phase adds to a running system. No phase requires a prior phase
to be complete before the system is useful. The agent scaffold went in before any
intelligence loops; the loops went in before the portal pages.

**JSON is source of truth.** `cos_context.json` drives CoS behavior. `ops_maintenance_rules.json`
drives Operations tiers. `challenger_config.json` drives the Synthesizer + Challenger
pattern. Databases are rebuildable projections from these source files.

**Test before commit.** No agent build is marked done without a functional test.
`test_schema.py` runs after every schema migration. Screenshot baselines are
taken before and after portal changes.

---

## Current state

### Built and running (as of 2026-06-11)

| Component | Status | Notes |
|---|---|---|
| Operations agent | ✅ Running | Port 8768, 24h+ uptime, Tier 1 actions firing |
| CoS Flask service + /chat | ✅ Running | Port 8769, `!cos` / `!chief` on Rvsopenbot |
| CoS Loop A — career scout | ✅ Running | Twice daily, 11 opportunities found, Comcast applied |
| CoS Loops B/C/D | ✅ Scheduled | First fire Sunday 2026-06-15 |
| CoS Loop E — ops oversight | ✅ Running | Hourly |
| CoS Loop F — domain health | ✅ Running | Daily |
| Design/Dev agent Level 1 | ✅ Running | Port 8770, watching `_working/` and `docs/` |
| ChallengerService Phase 1 | ✅ Built | Foundation, first exchange confirmed (NVIDIA factual catch) |
| Guild nav in portal | ✅ Live | Fourth top-level domain — `mini-moi | Curator | German | Guild` |
| Synthesizer + Challenger | ✅ Phase 1 complete | Foundation built, first exchange confirmed; Phase 2 (Curator) in build |
| schema_phase4.sql applied | ✅ Done | guild.*, jobs.* tables live |

### In build (Claude Code active queue)

| Item | Spec |
|---|---|
| Career Focus → Guild domain move | `_working/handoff_career_to_guild_2026-06-10.md` |
| Archive page navigation fix | `_working/spec_archive_navigation_2026-06-10.md` |
| ChallengerService Phase 2 — Curator | `_working/handoff_challenger_phase2_curator_2026-06-10.md` |

### Spec ready, not yet in build

| Item | Spec |
|---|---|
| Career two-page design (Positions + Active Pipeline) | `_working/spec_career_two_page_2026-06-11.md` |
| Curator UI consistency (3 sprints) | `_working/spec_curator_ui_consistency_2026-06-10.md` |
| Scan → Deeper Dive one-click | `_working/handoff_scan_to_dive_2026-06-10.md` |
| Synthesizer + Challenger — full spec | `_working/spec_challenger_pattern_2026-06-10.md` |

### Designed, not yet spec'd for build

| Item | Notes |
|---|---|
| Guild Daily Briefing (`/guild`) | Design in this document |
| Build Clarity (`/guild/build`) | Design in this document |
| Pipeline schema rename (`jobs` → `pipeline`) | One migration, prerequisite for Career two-page |
| CoS pruning loop (Loop F extension) | Design in this document |
| Challenger Phase 3 — German | After Phase 2 ships |
| Challenger Phase 4 — Guild career assessment | After Phase 3 |
| ChallengerService Ollama jobs (Phase 5 Neo4j) | Deferred — trigger: 20+ tagged sources |

---

## Roadmap

**Current sprint (Brazil build window, closing Jun 13):**
Career Focus → Guild, pipeline schema rename, Challenger Phase 2 Curator,
Curator UI Sprint 1, Archive navigation fix.

**Next sprint:**
Career two-page design (Positions + Active Pipeline), Guild Daily Briefing,
Build Clarity page, CoS Loop F pruning extension.

**Following:**
Challenger Phase 3 German, Challenger Phase 4 Guild career, Design/Dev Level 2
(routing and doc lifecycle management), Loop B/C/D calibration after first runs.

**Ongoing:**
Loop A career scout calibration (target: 0–3 pings/run), German practice tracking,
Curator domain scouting, private repo sync via `scripts/sync_private_repo.sh`.

---

*Guild · mini-moi · personal-ai-agents*
*docs/GUILD.md · authored 2026-06-11 07:50 CDT · Robert van Stedum*
*For technical agent specs: `docs/GUILD_AGENTS_DESIGN.md`*
*For build history: `docs/GUILD_BUILD_LOG.md`*
