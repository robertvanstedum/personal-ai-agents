# mini-moi — Solution Roadmap

**Location:** `ROADMAP.md` (repo root)
**Replaces:** `docs/OPERATIONS_ROADMAP.md`
**Created:** 2026-06-22 · **Updated:** 2026-07-05
**Status:** Living document — update as phases complete and decisions are made

---

## Where we are

mini-moi is a personal AI agent platform in daily production use since February 2026. Four domains are live on AWS EC2:

- **Curator** — daily intelligence briefing across geopolitics, finance, and technology. ~700 articles scored and ranked daily.
- **Mein Deutsch** — German practice built around real life: Vienna personas, reading lists tuned to Austria, feedback loops that turn session errors into the next drill.
- **Meu Português** — Portuguese practice anchored in Rio — news, culture, neighborhood life. Brazilian personas, inline translation, vocabulary that builds from what I actually read.
- **Guild** — where the agents and I coordinate. Ideas through specs, build queue, operations, and handoffs across Claude.ai, Claude Code, OpenClaw, and Grok.

The platform runs on a two-node architecture: EC2 primary (always-on), Mac standby (DNS-switchable). Infrastructure is stable. The multi-agent working model is established and working. The work ahead is about deepening capability — making the platform smarter, more autonomous within safe boundaries, and genuinely useful as a long-term personal system.

This document is where I think through where mini-moi goes from here. I'll read it again in six months and either it will have held up or I'll have learned something that changes it.

---

## The Multi-Agent Working Model

mini-moi is built and maintained by five actors in defined roles. This is not an accident — it is a deliberate design decision that I expect to evolve as the platform matures.

| Actor | Role | Scope |
|---|---|---|
| **Robert** | Decision and approval authority | All final decisions. Approves all deploys. Sets direction. |
| **Claude.ai** | Design and spec | Architecture, specs, design docs. No git access. |
| **Claude Code** | Build, git, deploy | Implements specs, commits, pushes to ECR on Robert's explicit approval. |
| **OpenClaw** | Memory and coordination | Context across sessions, file management, handoffs, task tracking. |
| **Grok** | Review | Independent review pass on specs and designs before build. |

The goal is not to automate Robert out of the loop — it is to make his judgment more effective by handling the coordination and memory work that currently creates friction. Each actor has a defined lane. Agents act only within explicitly defined scope.

---

## Guiding Principles

These don't change across phases. If a decision conflicts with one of these, the decision is wrong, not the principle.

**Boundaries first.** Agents act only within explicitly defined policies. What is not explicitly permitted is not permitted.

**JSON-first.** JSON files are the source of truth. Postgres is a rebuildable projection added only when query complexity demands it. Never migrate off JSON — add Postgres alongside it.

**Domain server owns its data.** No external process reads domain data files directly. All access goes through the domain server's HTTP endpoints.

**Auditability.** Every automated action is logged with reasoning, timestamp, and outcome. The log is reviewable at any time.

**Human override.** I can always pause automation, reverse an action, or restrict agent scope. No action is irreversible by design.

**Gradual trust.** Progress between phases depends on demonstrated reliability from the previous one. Each phase should produce usable evidence before the next one is pursued.

**Reuse and extend.** Every new domain is built from the domain template, not from scratch. The template is the floor, not the ceiling.

**No credentials in git.** Ever. Public or private repo. SSM + `.env` on host.

**Dev-first always.** Test on dev.minimoi.ai before any ECR push. Hotfix process requires explicit approval.

---

## The Domain Template

Every mini-moi domain follows a shared pattern. German and Portuguese are the reference implementations. The template is documented in `docs/architecture/DOMAIN_TEMPLATE.md` — read it before starting any new domain.

Current domains: Curator, Mein Deutsch, Meu Português, Guild.
Planned domains: Career (portfolio and job search tooling), others as needs emerge.

The template is what makes it possible to spin up a new domain without rebuilding from scratch. It covers directory structure, HTTP endpoint patterns, per-user data isolation, process management, tips system wiring, and the Docker/launchd deployment pattern.

---

## Phase Map — Agent Capability

The platform evolves across phases. Each phase requires demonstrated reliability from the previous one before moving forward.

```
Phase 1 (now)       Phase 2 (months)      Phase 3a (later)
CoS functional  →   Memory layer live  →   Bounded autonomy
"Take this note"    "Here's what we        "I handled it,
"File this bug"      decided and why"       here's the log"

Phase 3b (future)   Phase 4 (horizon)
Broader autonomy →  Mature agent ops
"Routine maintenance  "Weekly summary,
 handled"              exceptions only"
```

---

## Phase 1 — CoS Functional (Current Build)

**What this phase delivers:** OpenClaw as a real Chief of Staff tool for mini-moi — not just a title, but a daily-use capability. Right now the CoS role exists in name but has no dedicated tooling. Phase 1 closes that gap.

**What's being built:**
A dedicated mini-moi OpenClaw instance, isolated from personal OpenClaw, containerized from day one (Docker), and deployable to EC2 or Mac Mini without rework.

**Phase 1 use cases (tight scope):**
- Mobile note-taking → OpenClaw stores, timestamps, tags
- Bug/defect filing → OpenClaw creates GitHub issue, notifies via Telegram
- Document retrieval → OpenClaw pulls from known locations on demand
- Decision writing → OpenClaw writes to shared decisions store (visible in Guild)

**What is explicitly out of Phase 1 scope:**
- Autonomous actions without Robert approval
- Writing to prod systems
- Cross-agent orchestration
- Full memory layer (Phase 2)

**Why containerized from day one:**
Running OpenClaw in Docker means dev → EC2 → Mac Mini is the same move every other mini-moi service makes. No reinstall, no reconfiguration. Connectivity setup is the only variable.

**The two OpenClaw instances:**

| Instance | Purpose | Scope |
|---|---|---|
| Personal OpenClaw | Private, home use | Stays completely separate. Not part of this design. |
| mini-moi OpenClaw | Business, platform use | Bounded to mini-moi domains. This is what Phase 1 builds. |

**Exit criteria for Phase 2:**
- mini-moi OpenClaw instance running reliably for 4+ weeks
- Daily use proven: notes, bug filing, document retrieval all working via Telegram
- Decision writing producing readable output in Guild
- No incidents involving the personal OpenClaw instance

---

## Phase 2 — Memory and Intelligence Layer

**What this phase delivers:** Agent-agnostic memory that survives tool swaps. If OpenClaw is replaced by a better tool tomorrow, the new tool reads the external memory stores and picks up exactly where OpenClaw left off. Memory belongs to the platform, not to any one agent.

**Three memory layers:**

**Layer 1 — Decisions (public, real-time)**
OpenClaw's primary writing obligation. Not conversation, not every action — just decisions. What was decided, why, and by whom. Written automatically as decisions occur. All actors can read it. Guild surfaces it as a live feed. This replaces the decision log that isn't being used because it's manual.

**Layer 2 — Actions (summary, timestamped)**
What actually happened — meaningful actions and outcomes. Not back-and-forth conversation. Terse, permanent, queryable. "Spec #120 created and committed." "Postgres credentials rotated and moved to SSM." The audit trail.

**Layer 3 — Raw files (local-first, mine)**
The `./openclaw/` md files as-is. My data, local-first. Pulled, timestamped, and ingested into Postgres on my schedule. Ad hoc queryable. Feeds the intelligence layer when I need to investigate something. This is the raw data agent layer — not consumed in real time, but always available.

**The agent-agnostic principle:**
OpenClaw writes Layers 1 and 2 externally as it works. Any agent replacing OpenClaw reads those files and continues. Memory is stored with time as the primary division — timestamp everything. Domain tagging is secondary — add it when known, don't require it.

**What gets stored (store everything, tag lightly):**
- OpenClaw decisions and actions
- Design conversations (pre-spec sessions like this one)
- Agent interactions across all actors
- Raw OpenClaw md files
- Specs, handoffs, build outputs — already happening
- Code session summaries

**Tagging model (lightweight):**
- Domain: curator / german / portuguese / guild / cross-domain
- Actor: claude-ai / claude-code / openclaw / grok / robert
- Type: decision / action / design / spec / raw
- Time: timestamp (always)

**The retrieval philosophy:**
Don't over-design retrieval now. Store richly, tag lightly, let future AI (or me) query ad hoc. The value compounds over time.

**Exit criteria for Phase 3a:**
- All three memory layers live and writing
- Guild shows Layer 1 (decisions) and Layer 2 (actions) in real time
- Layer 3 queryable from Guild on demand
- At least one successful "what did we decide about X" retrieval from memory

---

## Phase 3a — Bounded Low-Risk Autonomy

**What this phase delivers:** CoS handles routine operations automatically within defined policies. Logs everything. Alerts after action.

**Example actions in scope:**
- Restart a stopped container after configurable timeout
- Clear temporary files when disk > 85% (defined directories only)
- Rotate logs older than configurable threshold
- Retry a failed cron job once with cooldown

**Example actions explicitly out of scope:**
- DNS changes
- Security group changes
- Database operations
- Anything touching EC2 instance configuration
- Any action affecting external services

**What needs to be built:**
- Policy file: YAML/JSON defining allowed actions, thresholds, limits
- Policy engine: CoS reads policy before acting — rejects anything not explicitly permitted
- Action log: structured, queryable, linked to Decision Records
- Circuit breaker: same action triggered 3+ times in 24h → stop, escalate

---

## Safety Mechanisms (all phases)

**Circuit Breaker**
If CoS triggers the same automated action 3+ times in 24 hours, automatic execution stops and I'm escalated: *"Curator has restarted 3 times today. Automatic restarts paused. Something may need investigation."*

**Rollback by Design**
Every automated action is designed to be undoable. Actions that cannot be rolled back require explicit pre-approval regardless of phase.

**Action Log**
Structured, queryable, permanent record of every automated action: what triggered it, what was done, what the outcome was, which policy permitted it.

```
2026-07-15T07:23:41Z | restart_container | curator |
reason: container_stopped_18min | outcome: healthy |
policy: phase_3a_container_restart | approved_by: policy
```

**Human Override**
I can always pause automation, reverse an action, or restrict agent scope at any time. No exceptions.

---

## Connection to mini-moi Architecture

**Guild as coordination surface:**
Automated actions, decisions, and memory are visible in Guild without pulling logs manually. The Guild Navigation Redesign (#117) adds the operational visibility layer. Operations Log view or integration with Build Log — to be decided in that spec's design session.

**Decision Records as policy history:**
Every policy change produces a Decision Record. The policy file and its DR history answer "why is this allowed?" I want to be able to read back through these and understand my own reasoning, not just see what the current policy says.

**Domain Template as reuse foundation:**
New domains (Career, and others) are built from the template. The CoS capability grows with the platform — not bolted on separately.

**Local LLM → Policy Intelligence (future):**
As the local LLM matures, it can help interpret whether a novel situation falls within the spirit of a policy. Phase 3b territory — worth designing for now.

---

## Portfolio Framing

Phase 1 is being built now and demonstrable within weeks. That's the honest starting point.

Phase 2 (memory layer) shows the right architecture for agent memory in a multi-agent system — agent-agnostic, time-stamped, queryable, owned by the platform not the tool. Very few teams have thought this through at this level.

Phase 3 (bounded autonomy with policy engine, circuit breakers, and structured action logs) is where mini-moi enters territory that serious AI operations teams are actively working on now.

The story: *"I built a personal AI platform with four live domains, a multi-agent working model, and a memory architecture designed to survive tool swaps. The CoS capability is Phase 1 of a deliberate progression toward bounded agent autonomy — designed safely, built incrementally, and grounded in daily production use."*

---

## Open Questions

**Answer before Phase 1 build:**
1. Telegram bot strategy for mini-moi OpenClaw — command prefix on existing `@minimoi_agent_bot` or dedicated test bot?
2. Secrets — separate SSM prefix (`/minimoi/cos/`) or shared with naming convention?
3. Startup — separate LaunchAgent or manual during early testing?

**Answer before Phase 2 build:**
4. Decisions file format — one file per day, per topic, or continuously updated?
5. Guild integration — new Operations Log view or fold into Build Log?
6. Design conversation ingestion — push (OpenClaw pulls from Claude.ai export) or pull (Robert pastes manually)?

**Answer before Phase 3a:**
7. Minimum action set for Phase 3a — start with one or two actions only, expand from evidence.
8. Policy change review process — same DR process as code decisions is the default.
9. Trust level delta between prod and dev — stricter policy on minimoi.ai than dev.minimoi.ai.

---

*ROADMAP.md · mini-moi · Living document*
*Update this document as phases complete and decisions are made*
*Short-term specs: see Guild Build Log at app.minimoi.ai/guild/build*
