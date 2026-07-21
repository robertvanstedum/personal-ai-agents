# GUILD — Autonomous Agents Design

> Founding design document, preserved as written, 2026-07-21. Build window
> (2026-06-08 to 2026-06-13) completed; the agents it designed have since been
> built and Guild's structure has evolved (CoS split into its own domain).
> Current practice: [ARCHITECTURE.md § CoS and Guild: separate roles, active
> redesign](../../../ARCHITECTURE.md#cos-and-guild-separate-roles-active-redesign).

*mini-moi · personal-ai-agents*

- **Version:** FINAL v1 — 2026-06-08
- **Status:** Phase 0 decisions locked — ready for build
- **Authored by:** Claude.ai (design) + Grok (review) + Claude Code (codebase verification)
- **Build window:** 2026-06-08 through 2026-06-13 · Brazil
- **Strategic priority:** TOP — transition from pipeline system to genuinely agentic platform

---

## Locked Decisions (Phase 0 resolved)

*These are decided. Not for discussion in Phase 0 — proceed directly to Q1–Q11.*

| Decision | Resolution |
|---|---|
| Memory layer | Native mini-moi Claw Layer (Option C). Each agent maintains a human-readable Markdown memory file alongside Postgres/Neo4j. No OpenClaw fork. |
| Inspectable memory | Borrow OpenClaw's best idea: `cos_memory.md` and `ops_memory.md` in `data/guild/memory/`. Not a dependency — a pattern. |
| Memory compaction | See Section 5a. Weekly (Operations) and monthly (CoS) with Haiku/Sonnet respectively. |
| Telegram channels | Two separate channels. Operations = terse system register. CoS = thoughtful intelligence partner. |
| Ollama for graph jobs | `mistral:7b` for Phase 5 reasoning tasks (cluster detection, path suggestion). `gemma3:1b` for high-frequency triage. |
| Guild branch approach | Do NOT full-merge — 38 commits behind main, 8+ conflict files. Cherry-pick only the `db/` files from guild onto main + `domains/guild/` reorg. Full 38-commit merge deferred indefinitely to avoid conflicts. |
| PostgreSQL | Resolved: `docker compose up -d` — `docker-compose.yml` exists with Postgres + Neo4j. Database `personal_agents`, port 5432. Q8 closed. |
| launchd naming | `com.user.*` for new persistent agent services (ports 8768 and 8769). |
| Port allocations | 8768 Operations · 8769 CoS · no conflicts with existing 8766/8767/5001. |
| `domains/guild/` | Does not exist yet. Created in Phase 1 by cherry-picking `db/` from guild branch + reorganizing to `domains/guild/`. |
| Phase 5 timeline | May accelerate to this week if `poc_verify.py` confirms Q2 (non-obvious Neo4j traversal) is satisfied. |

---

## 1 — Vision

mini-moi today is a powerful pipeline system. It ingests, scores, delivers, and responds to
commands. Robert drives everything. It is reactive and scheduled. It does not initiate.

mini-moi after this build is a genuinely agentic platform. Two agents — Chief of Staff and
Operations — run continuously, observe across all domains, make bounded autonomous decisions,
initiate contact when something warrants it, and improve their judgment over time.

**What "genuinely agentic" means here:**

Four properties define a genuine agent. mini-moi currently has #3 partially. This build adds all four.

1. **Autonomy** — acts without being asked, within pre-declared bounds
2. **Proactivity** — initiates based on its own assessment, not just in response to a trigger
3. **Reactivity** — responds appropriately to changes in state (partially exists)
4. **Learning** — improves its judgment based on feedback over time

The governing constraint does not change: **spend follows attention for consequential decisions**.
Agents add to queues, surface recommendations, and manage infrastructure autonomously. They do not
take external actions on Robert's behalf without confirmation. They do not delete data. They do not
send messages to third parties. Robert remains the decision point for anything irreversible.

What changes is that Robert is no longer the *only* initiating force in the system.

---

## 2 — Current State

**What exists:**
- Curator: daily pipeline running in production. Scoring, briefing, Telegram, Flask IA complete.
- Mein Deutsch: German coaching live, multi-user, session review, Anki generation.
- Research Intelligence: threaded deep research, Synthesizer + Challenger pattern.
- Guild: GUILD_CHARTER.md v2.1 committed. Four-cabinet model ratified.
  - **Guild branch status (Claude Code verification):** Stop-Gate 3 complete.
    Graph traversal working. `db/` files cherry-picked onto main 2026-06-08.
  - **`poc_verify.py` result (2026-06-08):** graph is empty — 0 sources, 0 tag edges.
    Phase 5 is confirmed deferred until 20+ sources are tagged via Investigate modal.
  - **Postgres + Neo4j:** containers running on main. `docker-compose.yml` volume fix
    committed 2026-06-08 (Postgres 18 format change required new volume mount path).
- Portal: minimoi.ai on MacBook, three Flask apps, Cloudflare tunnel, auth tiers.
- LLM Registry: `docs/LLM_REGISTRY.md` committed to main.

**What does not exist:**
- Any agent that runs without a cron trigger or user action
- Any agent that maintains state across runs
- Any agent that initiates contact based on its own assessment
- `domains/guild/` directory (code lives in `db/` on guild branch)
- A Jobs/career domain with any content
- Always-on intelligence loops watching the external landscape

---

## 3 — The Two Agents

### Chief of Staff
**Role:** Cross-domain proactive intelligence partner and caretaker of the mini-moi vision.
Watches ALL domains (Curator, German, Research, Jobs, mini-moi itself), the external landscape,
Robert's habits and commitments, and the health of Operations. First escalation receiver from
Operations. Surfaces what matters; escalates what needs a decision; manages what it can.

**What CoS owns:**
- Knowing whether each domain is healthy and still novel
- Watching the external landscape for each domain (language learning tools, AI briefing tools,
  competitive platforms, job market)
- Watching Robert's practice cadence and commitment habits
- Receiving Operations escalations — deciding what reaches Robert
- Discovering and surfacing opportunities (jobs, tutors, communities, research angles)
- Running periodic competitive/novelty watch ("is there already a mini-moi out there?")
- Maintaining the recommendation queue Robert works through on his own schedule

**What CoS is not:**
- It does not take external actions without confirmation
- It does not auto-add Curator topics or Research threads
- It does not send messages to third parties
- It does not replace Robert's judgment on anything consequential

### Operations
**Role:** Infrastructure steward with bounded autonomous action authority.
Monitors system health, maintains infrastructure, self-heals within pre-approved tiers.
Escalates to CoS first (not Robert directly) for mid-level issues.

---

## 4 — Authority Model

Declared in GUILD_CHARTER. Written down before anything runs.

### Operations tiers

| Tier | Condition | Agent action | Robert notified? |
|---|---|---|---|
| 1 | Routine maintenance threshold | Act silently, log it | Morning summary only |
| 2 | Handled, but worth knowing | Act + notify CoS | CoS decides |
| 3 | Needs human decision | Escalate to CoS with options | CoS decides |
| 4 | Critical — service down, data integrity | Escalate CoS + Robert immediately | Yes, immediately |

**Tier 1 pre-approved actions:**
- Disk >75%: archive screenshots >30 days not in `current/` baselines
- Log files >60 days: compress and archive
- Failed launchd service: restart once, wait 2 min, verify
- Missed cron within same day: re-run once

**Escalation chain:** Operations → CoS → Robert (Tier 3 and below).
CoS adds context before escalating. Robert gets a decision-ready message, not a raw alert.

### CoS tiers

| Tier | Condition | CoS action |
|---|---|---|
| Background | Routine cycle, nothing notable | Log, add to morning brief |
| Awareness | Something worth knowing, no decision | Morning brief + portal queue |
| Decision | Actionable, needs Robert's choice | Telegram ping + portal queue |
| Never | External action on Robert's behalf | Not permitted |

---

## 5 — Chief of Staff: Full Spec

### Architecture
Persistent Flask service on port 8769. Registered as `com.user.cos` launchd service.
Background thread runs multiple loops concurrently at different cadences. State persisted
in PostgreSQL. Exposes `/status` and `/agenda` endpoints.

Two memory surfaces (see Section 5a):
- `data/guild/memory/cos_memory.md` — human-readable, inspectable, git-backed
- PostgreSQL `guild.agent_log` — queryable, structured, full history

### 5a — Memory file specification (Q11 resolved — OpenClaw production pattern)

**The problem:** append-only memory files grow without bound and eventually overflow context.
OpenClaw solved this in production (prevented a $20 API burn). Reuse their pattern.

**Two-layer architecture — hard rule:**
- `cos_memory.md` / `ops_memory.md` = **distilled only**. No raw session detail here.
- `memory/cos/YYYY-MM-DD.md` / `memory/ops/YYYY-MM-DD.md` = **daily files** for raw/session-level
  detail. MEMORY.md is the essence extracted from these files, not a log of them.

**Hard cap (non-negotiable):**
- **8,000–10,000 characters maximum** on each MEMORY.md file. Never exceed.
- The first memory write helper (Phase 1) must include a size check on every write.
  If the file approaches 8,000 characters: either refuse the write or trigger an immediate
  "distill now" before writing. This prevents the problem before it becomes one.

**MEMORY.md format:**
```markdown
# CoS Memory — distilled
Last distilled: YYYY-MM-DD | Size: ~NNNN chars

## Enduring facts and decisions
[What is always true about Robert's context, preferences, confirmed decisions]

## Active agenda
[What CoS is currently working on, open items — updated on each distillation]

## Recurring patterns
[Cross-domain connections, signals that have appeared more than once]

## What Robert acted on (recent)
[Recommendations confirmed vs dismissed — the feedback signal, pruned to most recent]

## Job search state
[Current pipeline summary — updated on each distillation, not raw log]

## Domain health standing notes
[Per-domain observations that don't expire]
```

**Distillation schedule:**
- **Weekly (Sunday)** — automated via launchd, same pattern as existing weekly jobs.
  Haiku call for Operations (mechanical), Sonnet call for CoS (preserves nuance).
  Reads the week's daily files → extracts essence → updates MEMORY.md → archives daily files.
- **Monthly (1st)** — archive: move old daily files to `memory/cos/archive-YYYY-MM.md`
  (per agent). Old daily files gzipped or deleted after 90 days.
- **On size threshold** — if MEMORY.md approaches 8,000 chars before the weekly run,
  trigger immediate distillation. Do not wait for the schedule.

**What stays in MEMORY.md (keep):**
- Enduring facts: Robert's preferences, confirmed decisions, career context, German level
- Recurring patterns: signals that have appeared in multiple sessions
- Active agenda: current open items with status
- Key relationships: recurring contacts, trusted sources, important threads

**What goes to daily files only (drop from MEMORY.md):**
- One-off events that resolved
- Temporary context from a single session
- Verbose reasoning that no longer applies
- Raw escalation logs (these live in Postgres anyway)

**Human review gate:** First 2–3 distillation cycles are reviewed manually by Robert
before trusting automated compaction. Same discipline used in OpenClaw testing (end of May).
After that, automated distillation runs unattended.

**Why this works:** MEMORY.md stays under 10k chars indefinitely. Full session history
lives in daily files (then archived). Postgres has the structured queryable record.
Three readers, three formats — each serves a different purpose. One bloated file can't
poison both personalities (separate files, separate distillation cadences).

### 5b — Context file

`domains/guild/config/cos_context.json` — Robert owns and updates this file. CoS reads it
on each cycle. Changes take effect on the next cycle without restart.

```json
{
  "job_search": {
    "active": true,
    "target_roles": [],
    "target_geos": [],
    "german_speaking_companies": true,
    "narrative": "",
    "avoid": []
  },
  "german": {
    "current_level": "B1-B2",
    "practice_target_sessions_per_week": 0,
    "remind_after_days": 0,
    "watch_for": "emerging language learning tools, AI tutors, conversation groups",
    "local_search": "Chicago",
    "online_search": true
  },
  "curator": {
    "scout_for": "",
    "competitive_watch": true
  },
  "mini_moi": {
    "novelty_watch": true,
    "watch_terms": []
  }
}
```
*Robert fills in the zero-value fields in the morning session.*

### 5c — Concurrent loops

**Loop A — Job search** (twice daily while job_search.active = true)
Sources: TBD in Phase 0 (Q2). Haiku filter → Sonnet evaluation → Jobs domain write → Telegram if above threshold.

**Loop B — German domain watch** (weekly)
Search for emerging language learning tools, compare vs Mein Deutsch, surface tutors and groups,
check practice cadence, send reminder if days since last session > `remind_after_days`.

**Loop C — Curator domain scout** (weekly)
Search for emerging topics matching `scout_for`. Write suggested threads to Desk "Suggested by CoS"
queue. Robert confirms to activate. Never auto-adds.

**Loop D — mini-moi novelty watch** (bi-weekly)
Search `watch_terms`. Sonnet evaluation: threat / complement / incorporate / ignore.
Surfaces findings to recommendations queue with a comparison narrative.

**Loop E — Operations oversight** (hourly)
Read `ops_escalation_queue`. Apply tier judgment. Route to morning brief or immediate Telegram.
Detect patterns across escalations.

**Loop F — Domain health** (daily)
Check each domain's activity, Robert's habits, stale items. Surface findings to morning brief.

### 5d — Recommendation interface

**Telegram (CoS channel):** Time-sensitive items. Thoughtful colleague register. Morning brief.
**Portal — CoS Recommendations page:** Structured inbox by domain. Confirm / Defer / Dismiss.
Items persist until acted on. CoS never re-surfaces a dismissed item.

---

## 6 — Operations Agent: Full Spec

### Architecture
Persistent Flask service on port 8768. Registered as `com.user.operations` launchd service.
Main loop: 5-min shallow health check, hourly deeper audit, weekly maintenance calendar.
Exposes `/status` and `/log` endpoints. Writes escalations to `guild.ops_escalation_queue`.

### Memory
`data/guild/memory/ops_memory.md` — weekly compaction (Haiku), monthly archive.
Same structure as CoS memory, tuned for infrastructure events.

### Maintenance rules
`domains/guild/config/ops_maintenance_rules.json` — Robert owns and tunes. Defines tiers,
thresholds, timing, notification rules. Agent reads this at startup and on each cycle.

### Proactive maintenance calendar
- Daily: health check log summary to `guild.agent_log`
- Weekly (Sunday): disk audit, log file review, launchd service status report + memory compaction
- Monthly (1st): log rotation, screenshot baseline cleanup check, DB vacuum

---

## 7 — Jobs Domain

### Data model
```sql
jobs.job_postings      (id, source, title, company, geo, date_found, fit_score, fit_narrative,
                        status, cos_notes, robert_notes, date_updated)
jobs.job_applications  (id, posting_id, date_applied, contact_id, cover_letter_path,
                        status, last_activity, next_action, next_action_date)
jobs.career_contacts   (id, name, company, role, relationship, first_contact, last_contact,
                        notes, source)
jobs.follow_ups        (id, entity_type, entity_id, due_date, description, status, created_by)
```

### Portal page
Pipeline view (columns per status). CoS adds to "Suggested." Robert moves through pipeline.
Contacts tab. Follow-ups tab.

---

## 8 — Neo4j Activation

### Current state (Claude Code verification)
Guild branch Stop-Gate 3 is complete. Graph traversal working. Files in `db/` on guild branch:
`schema.sql`, `migrate.py`, `postgres.py`, `neo4j_driver.py`, `poc_verify.py`, `reconcile.py`, `graph_seed.py`.

**Before Phase 1 starts:** run `poc_verify.py` on guild branch.
- If Q2 returns non-obvious traversal: Phase 5 moves to this week (run concurrent with Phase 3).
- If Q2 not yet non-obvious: Phase 5 remains sequentially after Phase 3 (trigger at 20+ tagged sources).

```bash
git checkout guild && python3 db/poc_verify.py && git checkout main
```

### Three background Ollama jobs

**Model for all three Phase 5 jobs: `mistral:7b`** (already pulled locally, zero cost,
better reasoning than `gemma3:1b` for cross-thread pattern finding).

**Job 1 — Cluster detection (weekly):** Finds concepts appearing in multiple threads not yet
explicitly connected. Output: suggested connection for Robert to confirm or dismiss.

**Job 2 — Gap identification (after each observe.py run):** Reads "missing piece" section
from Sonnet observations. Checks whether an existing session/source/candidate fills it.
Surfaces: "You identified this gap. You have a source that fills it."

**Job 3 — Path suggestion (weekly or on-demand):** Given current thread state, suggests
2–3 directions the research hasn't gone. Reasoning from what the graph shows is absent.

### CoS integration
Once Ollama jobs run, their output feeds Loop C (Curator domain watch) and Loop F (domain
health). CoS gains cross-domain pattern intelligence beyond metric-watching.

---

## 9 — Portal Expansion

**Agent status panel (on the Desk page):**
Two rows: CoS and Operations. State, last check-in, open items count, last action. Status indicators.

**CoS Recommendations page (new):**
Organized by domain. Each recommendation: domain badge, date, what/why/confidence, action buttons.
Filter by domain, status, date.

**Jobs page (in existing Jobs portal tab):**
Pipeline + contacts + follow-ups.

---

## 10 — Data Model (new tables)

```sql
-- Agent infrastructure (guild schema)
guild.agent_state           -- current state of each agent, last check-in, current agenda item
guild.agent_log             -- full record of observations, decisions, actions
guild.agent_feedback        -- Robert's responses (implicit click-through, explicit confirm/dismiss)
guild.cos_agenda            -- CoS open work items, domain, status, next_check_at
guild.ops_escalation_queue  -- Operations → CoS escalation buffer
guild.ops_maintenance_log   -- all maintenance actions taken, tier, outcome

-- Jobs domain
jobs.job_postings
jobs.job_applications
jobs.career_contacts
jobs.follow_ups
```

---

## 11 — Build Phases

### Phase 0 — Investigation and design (morning, Jun 8)

See Section 12 (11 questions). Do not start Phase 1 until all 11 are resolved.

**Before Phase 0 even starts:**
1. `docker compose up -d` — verify PostgreSQL is up
2. `git checkout guild && python3 db/poc_verify.py && git checkout main` — check graph state
3. Robert provides goals/direction → `cos_context.json` values filled in

### Phase 1 — Foundation (Jun 8–9)

- [x] **Cherry-pick `db/` files from guild branch onto main** — ✅ done 2026-06-08
- [x] **Docker containers running** — Postgres + Neo4j ✅ done 2026-06-08
      (`docker-compose.yml` volume fix committed; Postgres 18 required new mount path)
- [x] **`poc_verify.py` result** — graph empty (0 sources, 0 tag edges). Phase 5 deferred.
- [ ] **Create `minimoi` Postgres user** — required by `poc_verify.py` and all guild DB ops
  ```sql
  CREATE USER minimoi WITH PASSWORD '...';
  GRANT ALL ON DATABASE personal_agents TO minimoi;
  ```
- [ ] **Reorganize `db/` → `domains/guild/`**
- [x] **Directory scaffold created** — `domains/guild/config/`, `domains/guild/agents/loops/` ✅
- [x] **Memory files initialized** — `cos_memory.md`, `ops_memory.md` ✅
- [ ] `guild` + `jobs` PostgreSQL schemas created (`db/schema_guild.sql`)
- [ ] `domains/guild/config/cos_context.json` — created with Robert's morning input
- [ ] `domains/guild/config/ops_maintenance_rules.json` — Tier 1–4 defaults
- [ ] `data/guild/memory/` directory created; `cos_memory.md` and `ops_memory.md` initialized
- [ ] Jobs portal page (pipeline view, manual entry — CoS fills it in Phase 3)
- [ ] Agent status panel scaffold on Desk page (shows "not yet running")
- [ ] launchd plist templates for 8768 and 8769 (scaffold, no logic yet)

**Definition of done:** `docker compose up -d` + `pg_isready` succeeds.
Guild schema tables exist. Memory files initialized. Portal shows Jobs page and agent status scaffold.

### Phase 2 — Operations agent (Jun 9–10)

- [ ] Operations Flask service (port 8768, `com.user.operations` launchd service)
- [ ] Main loop: 5-min health check, hourly audit
- [ ] `ops_maintenance_rules.json` reader
- [ ] Tier 1 actions: disk cleanup, log archive, service restart, cron re-run
- [ ] Escalation writer → `guild.ops_escalation_queue`
- [ ] `/status` endpoint returning valid JSON
- [ ] Weekly maintenance calendar (Sunday trigger)
- [ ] `ops_memory.md` writer (appends daily summary entry)
- [ ] Tier 4 direct Telegram (bypasses CoS queue)

**Definition of done:** Operations running 24h without intervention. At least one Tier 1 action
logged. `/status` returns valid JSON. CoS can query it.

### Phase 3 — Chief of Staff core (Jun 10–11)

- [ ] CoS Flask service (port 8769, `com.user.cos` launchd service)
- [ ] Context file reader (reloads on each cycle)
- [ ] Loop E: Operations oversight (reads `ops_escalation_queue`, routes by tier)
- [ ] Loop A: Job search (sources from Q2 decision; Haiku filter, Sonnet eval, Jobs domain write)
- [ ] Loop F: Domain health (all domains, stale detection, morning brief)
- [ ] CoS morning brief: daily Telegram summary
- [ ] CoS Recommendations page in portal
- [ ] `cos_memory.md` writer (daily append)
- [ ] Escalation chain verified: test Tier 3 ops issue, confirm CoS routes correctly

**Definition of done:** CoS surfaced ≥1 job posting to Jobs domain. Ops escalation received
and routed correctly. Morning brief sent. Memory file appending.

### Phase 4 — Domain intelligence loops (Jun 11–12)

- [ ] Loop B: German domain watch (tool search, practice cadence, tutor/group search, reminder)
- [ ] Loop D: mini-moi novelty watch (watch_terms search, Sonnet evaluation, comparison narrative)
- [ ] Loop C: Curator domain scout (emerging topic search, "Suggested by CoS" queue on Desk)
- [ ] Feedback tracking: log what Robert does with each recommendation (implicit signal)
- [ ] Monthly compaction scheduled (CoS, Sonnet) — first run end of month

**Definition of done:** All four loops run ≥once. ≥1 recommendation in portal queue.
≥1 German reminder sent. `cos_memory.md` growing correctly.

### Phase 5 — Neo4j activation (deferred — trigger after build window)

**Status: DEFERRED.** `poc_verify.py` ran 2026-06-08: graph is empty (0 sources, 0 tag edges).
Phase 5 does not start this week.

**Trigger:** After 20+ sources are tagged through the Investigate modal, run `poc_verify.py`.
If Q2 (Neo4j traversal) returns a non-obvious connection, wire the bridge and proceed.

**Note:** `poc_verify.py` also requires a `minimoi` Postgres user — created in Phase 1.
The script will work correctly once that user exists and sources are tagged.

- [ ] Bridge wired: dual-write from `curator_research.py` and `german_domain.py`
- [ ] `db/reconcile_guild.py` confirms zero drift
- [ ] Ollama Job 1 (cluster detection, `mistral:7b`) weekly
- [ ] Ollama Job 2 (gap identification, `mistral:7b`) after each `observe.py` run
- [ ] CoS Loop C reads cluster detection output
- [ ] CoS Loop F reads gap identification output

**Definition of done:** ≥1 cluster detection or gap identification finding surfaced to
Robert via CoS recommendations queue.

### Phase 6 — Learning and calibration (ongoing)

- Feedback table populating from behavioral signals
- Threshold tuning in `ops_maintenance_rules.json` from first week of Operations data
- Memory compaction running on schedule (verify first automated run)
- Job search scoring calibrated

---

## 12 — Phase 0 Design Session Agenda (11 questions)

*Resolve all 11 before Phase 1 code starts.*

**Q1** — External search mechanism: Tavily / Brave Search / SerpAPI — or per-loop?
Cost modeling needed. Which mechanism for job search vs. language learning watch vs. novelty watch?

**Q2** — Job search sources: which company career pages to monitor? Which job boards have
reliable RSS or API access? Initial watch list for Phase 3.

**Q3** — Authority model pressure test: anything missing from Tier 1 (too risky to act silently)?
Anything missing from Tier 4 (should trigger immediate escalation)?

**Q4** — Neo4j entity model: auto-extract conceptual entities (Ollama pass over observations)
vs. Robert manually labels vs. hybrid (Ollama proposes, Robert confirms)?

**Q5** — Feedback signal design: hybrid (implicit click-through + explicit confirm/dismiss)?
Confirm final design for `guild.agent_feedback` table.

**Q6** — *(resolved)* Telegram channels: two separate confirmed. Operations = system register.
CoS = intelligence partner.

**Q7** — Timing and cost estimate: walk through each CoS loop, estimate call volume/week,
model tier, projected incremental cost. Validate within $35–45/month existing budget.

**Q8** — *(resolved)* PostgreSQL prerequisite: `docker compose up -d` — `docker-compose.yml`
already exists on the MacBook with both Postgres (`personal_agents`, port 5432) and Neo4j.
Just start the containers and verify with `pg_isready`. No setup needed.

**Q9** — Ollama model for graph jobs: *(resolved)* `mistral:7b` for Phase 5 reasoning jobs;
`gemma3:1b` for high-frequency triage. Grok to confirm or challenge.

**Q10** — Memory layer: *(resolved)* native Option C with inspectable Markdown files.
No OpenClaw fork. Grok to confirm.

**Q11** — Memory compaction policy: *(resolved in Section 5a)* Monthly Sonnet compaction for CoS;
weekly Haiku compaction for Operations. Archive naming: `cos_memory_YYYY-MM.md`. Active file
size limits enforced. Grok to confirm or refine.

---

## 13 — Success Criteria

**End of Brazil build window (Jun 13):**
- Operations running ≥3 days autonomous; ≥3 Tier 1 actions logged
- CoS running with job search and domain health loops active
- ≥5 job postings surfaced to Jobs domain
- ≥1 Ops escalation received, routed, acted on correctly
- Morning brief arriving via Telegram daily
- `cos_memory.md` and `ops_memory.md` growing correctly

**Month 1:**
- German practice reminders firing correctly
- mini-moi novelty watch run ≥2 times with findings surfaced
- Curator domain scout suggested ≥2 new threads
- ≥20 recommendation outcomes recorded in feedback table

**Enduring:**
- Robert's first question when something goes wrong is "what did Operations do?" not "what happened?"
- CoS morning brief is a useful daily read, not noise
- The system surfaces things Robert would have wanted to know but couldn't have been watching for
- mini-moi remains novel — CoS has caught any significant competitive development

---

## 14 — What Robert brings to Phase 0

*(Confirmed: Robert has these goals and direction ready.)*

- `cos_context.json` values: job search target roles, geos, career narrative, mini-moi watch terms
- German practice target: sessions per week, remind-after-days
- Operations Tier 1 comfort level: any actions to add or remove from the pre-approved list
- Initial company career page watch list for Loop A
- Any company/recruiter-specific job search context

---

## 15 — File Structure

```
domains/guild/
├── config/
│   ├── cos_context.json           ← Robert's priorities — he owns and updates
│   └── ops_maintenance_rules.json ← Operations tier config and thresholds
├── agents/
│   ├── chief_of_staff.py          ← CoS Flask service + loop scheduler
│   ├── operations.py              ← Operations Flask service + loop scheduler
│   └── loops/
│       ├── cos_job_search.py      ← Loop A
│       ├── cos_german_watch.py    ← Loop B
│       ├── cos_curator_watch.py   ← Loop C
│       ├── cos_novelty_watch.py   ← Loop D
│       ├── cos_ops_oversight.py   ← Loop E
│       ├── cos_domain_health.py   ← Loop F
│       ├── ops_health_check.py
│       ├── ops_maintenance.py
│       └── ops_escalation.py
├── data/
│   └── memory/
│       ├── cos/
│       │   ├── YYYY-MM-DD.md      ← Daily raw/session-level detail (auto-pruned after 90d)
│       │   └── archive-YYYY-MM.md ← Monthly archives (permanent)
│       ├── ops/
│       │   ├── YYYY-MM-DD.md      ← Daily ops log detail (auto-pruned after 90d)
│       │   └── archive-YYYY-MM.md ← Monthly archives (permanent)
│       ├── cos_memory.md          ← Distilled only, hard cap 8k–10k chars
│       └── ops_memory.md          ← Distilled only, hard cap 8k–10k chars
db/
├── schema_guild.sql               ← guild.* and jobs.* table definitions
├── migrate_guild.py               ← populates guild tables from existing JSON
└── reconcile_guild.py             ← drift check between JSON and Postgres
```

---

## 16 — Principles (unchanged)

- **JSON is source of truth.** Config files and memory files are JSON/Markdown.
  Databases are rebuildable projections.
- **Spend follows attention.** CoS loops use Haiku for filtering. Sonnet fires only when
  something has passed the filter and will be seen by Robert. Ollama handles graph
  intelligence at zero cost.
- **Bones first.** Service scaffold and database schema before any loop logic.
  Each loop is additive.
- **Test-before-commit.** If an agent action cannot be tested in dev, say so.
- **Robert confirms before merge.** Screenshot baseline re-run after any portal change.
- **Agents propose; Robert decides.** CoS never auto-adds, never sends externally,
  never takes irreversible action without confirmation.

---

*GUILD_AGENTS_DESIGN_2026-06-08_FINAL.md*
*Claude.ai + Grok + Claude Code · build window Jun 8–13 · Brazil*
