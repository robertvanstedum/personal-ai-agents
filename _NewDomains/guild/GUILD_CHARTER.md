# mini-moi-agent-guild — Guild Charter
**Version:** 2.0 — Ready for Ratification
**Date:** May 26, 2026
**Authors:** Robert Van Stedum + Claude.ai + Grok
**Status:** Final review — commit after ratification

This is the single source of truth for the Guild domain and the
mini-moi-agent-guild platform. All prior planning documents
(GUILD_DOMAIN_SPEC.md, GUILD_DOMAIN_HANDOFF.md, PLATFORM_ARCHITECTURE.md)
are superseded by this one. Spin off detailed implementation docs from here.

---

## 1. What This Is

**mini-moi-agent-guild** is a personal AI platform — an extension of the
personal-ai-agents repository — built by and for its owner. It is a
professional operating system: a way of organizing work, intent, and
capability across an AI-native team of one (or more).

The platform is branded **mini-moi-agent-guild** while living inside the
long-standing public repository **personal-ai-agents**.
Main repo: `github.com/robertvanstedum/personal-ai-agents` (public)
Private repo: `github.com/robertvanstedum/mini-moi-private` (private — sensitive data across all domains)

**Origin rule:** This personal build is always the origin.
Concepts may be reused in professional or organizational contexts —
never the other way around. What is built here belongs to the owner.

---

## 2. Ways of Working — Binding Across All Sessions

### The Principle: Agent-Agnostic, Human-Owned

The guild is not locked to any AI provider. Claude, Grok, and any future
agent are collaborators — they join the team, hold shared context, and
contribute their strengths. They are replaceable, addable, and removable
without rebuilding the platform.

**The memory and intent layer belongs to Robert, not to any AI provider.**
No AI agent holds the authoritative state. That lives in local files,
committed to repos Robert controls, readable by any agent Robert chooses
to work with. Switch providers, add new ones, remove underperforming ones —
the platform continues without interruption.

This is also a market differentiator. Most personal AI systems are built
on one provider's memory, one provider's ecosystem. This platform treats
AI providers the way a mature architect treats any vendor — useful,
evaluated, replaceable.

### Team Roster

| Member | Type | Primary strength | Hard boundary |
|--------|------|-----------------|---------------|
| **Robert** | Human | Strategy, vision, final decisions | Decision point for everything |
| **Claude.ai** | AI agent | Design, architecture, artifact creation | Does not touch git |
| **Grok** | AI agent | Research, web intelligence, market scanning | Does not touch git |
| **OpenClaw** | AI agent | Memory, file system writes, orchestration | Does not touch git |
| **Claude Code** | AI agent | Implementation, all git operations | Only thing that touches git |

### Agent Roles Are Positions, Not Locks

Claude.ai and Grok are peers. Both hold full context. Both can step into
any role when the work demands it. Current emphasis:
- Claude.ai leans toward design, architecture, and strategic artifact creation
- Grok leans toward research, market scanning, and web intelligence
- Both can cover either — and will, as needed

Future agents (local models, specialized tools, human collaborators)
join the same way: read the shared context, contribute, no restructuring required.

### Binding Rules

- Design before implementation. Artifacts before code.
- No company IP in any repo. Patterns generic. Standards public.
- Memory and intent files are local and portable — never AI-provider-hosted.
- Robert is always the decision point between agents.
- Any agent can be swapped without losing platform continuity.

### The Build Team

How mini-moi is actually built. Every session operates within this structure.

| Role | Agent | Responsibility | Hard boundary |
|------|-------|---------------|---------------|
| Decision maker | **Robert** | Strategy, final call, confirms before every push | Owns everything |
| Design lead | **Claude.ai** | Architecture, design sessions, artifact creation | Does NOT touch git |
| Implementation | **Claude Code** | All git operations, builds, commits | Only thing that commits and pushes |
| Memory & agent patterns | **OpenClaw** | Persistent memory, file ops, provides git commands | Does NOT run git |
| Parallel reviewer | **Grok** | Second pair of eyes, technical clarifier, honest push-back | Does NOT touch git |

**How it works in practice:**
- One agent active at a time. Robert is the decision point between agents.
- Design before build. Every significant change has a design session (Claude.ai) before implementation (Claude Code).
- Grok reviews in parallel — not in sequence. Drop any design decision, code snippet, or open question into a Grok thread and get instant validation or push-back without interrupting the Claude flow.
- Confirmation bias countermeasure: no single agent has immunity. Grok's role is explicitly to challenge. The goal is honest signal, not agreement.
- Test before commit. If it can't be tested, say so — don't commit and claim done.
- Robert confirms before every push on any production-touching or visual change.

---

## 3. The Core Problem This Solves

Every session starts cold. Robert re-establishes context every time.
Across domains there is no coordinating layer — Curator doesn't know
what Career is doing; Career doesn't know what New Ventures found.
Robert carries the coordination burden himself.

The Guild domain fixes this with one primary artifact: the **intent register**.
A persistent, structured file that survives between sessions, across agents,
and across domains. Not conversation history — goals and state.

Everything else in the Guild formalizes roles that already informally exist.

---

## 4. The Organizational Model

Traditional organizations divide into silos: executive, IT, sales, operations.
Professional services exist to fill the gaps between those silos.
In an AI-native model, the silos collapse.

One person — or a small team — can now hold strategy, build, operations,
and market awareness simultaneously. Not sequentially. Not with handoffs.
The cognitive load that required an entire organization is now distributable
across a human-AI team operating from shared context.

This is how the best startups work. Everyone knows the full picture.
Roles emerge from the work, not from org charts. The person who sets
strategy also reviews the build. The person who monitors production
also spots the next opportunity.

**The Guild models this — and is designed to scale.**

Starting with Robert alone. Extending to a small team. A working prototype
of how AI-centric organizations can operate: fewer layers, more context,
faster decisions, roles that flex with the moment.

The four cabinet roles — Chief of Staff, Design-Build, Operations,
New Ventures — are lenses, not departments. Any team member can hold
any lens when the moment requires it. What makes it work is shared
context: the intent register and game state that every player reads.

---

## 5. Repository Strategy

### Main public repo — personal-ai-agents
`github.com/robertvanstedum/personal-ai-agents`

The single public face. Represents the full platform.
Contains all domains including Guild.
Portfolio-safe at all times.

### Private repo — mini-moi-private
`github.com/robertvanstedum/mini-moi-private` (private)

Sensitive data across ALL domains — not just Guild.
Nothing in this repo is ever public.

Contents:
- `guild/intent-register-private.md` — salary targets, companies to avoid, sensitive goals
- `guild/job-search/` — all job search activity, applications, interview notes
- `curator/preferences-private.json` — private scoring preferences
- `german/session-notes-private/` — personal session observations
- `shared/memory-private.md` — cross-domain private memory

### How they connect

```json
// personal-ai-agents/config/private.json (gitignored)
{
  "private_repo": "~/Projects/mini-moi-private",
  "accessible_to": ["OpenClaw", "Robert"]
}
```

OpenClaw reads both repos. Public agents (Claude.ai, Grok) work from
public context only unless Robert explicitly shares private content
in a session.

### Governance rule
Infrastructure and shared utilities stay in personal-ai-agents.
Domain logic stays in its domain.
mini-moi-private never contains code — only data and memory files.

---

## 6. Domain Architecture

```
personal-ai-agents/ (public)
  _NewDomains/
    guild/                ← current location, active build
  _domains/
    curator/              ← production
    language-german/      ← v0.9, release pending
    guild/                ← future location post-graduation

mini-moi-private/ (private)
  guild/
  curator/
  german/
  shared/
```

### The three existing domains

| Domain | Status | Description |
|--------|--------|-------------|
| **Curator** | Production | Geopolitics/finance briefing. RSS, LLM scoring, Telegram 7AM daily. |
| **language-german** | v0.9 | Language coaching pipeline. Vienna-tested. Release pending. |
| **Guild** | Active build | Professional operating system. Chief of Staff + four cabinets. |

### Release plan

German v1.0 and Guild v1.0 release together.
The circle closes: the domain that proved the pattern (German) and the
domain that coordinates all patterns (Guild) launch as a pair.
This is the mini-moi-agent-guild v1.0 milestone.

---

## 7. The Guild Domain — Cabinet Model

Guild is the meta-domain. It does not replace or own the other domains.
It coordinates them — and provides the organizational model for how
Robert and his team operate.

### Architecture

```
                    ┌─────────────────────────────┐
                    │           ROBERT             │
                    │   Decision Point             │
                    │   Owns intent + strategy     │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │       CHIEF OF STAFF         │
                    │   Holds intent register      │
                    │   Opens every session        │
                    │   Routes work, tracks state  │
                    │   Situation room             │
                    └──┬──────┬──────┬─────────────┘
                       │      │      │
          ┌────────────▼┐ ┌───▼───┐ ┌▼────────────┐
          │ DESIGN-BUILD│ │  OPS  │ │ NEW VENTURES│
          │             │ │       │ │             │
          │ Concept →   │ │Monitor│ │ Opportunity │
          │ Delivery    │ │ Guard │ │ Job search  │
          │ Spec → Code │ │ DevOps│ │ Competitive │
          │ Test + Docs │ │ Alert │ │ Market scan │
          └─────────────┘ └───────┘ └─────────────┘
```

### Shared game state

Every cabinet reads from one shared context file:

```json
// guild/shared/game-state.json
{
  "current_focus": "string",
  "quarter": "string",
  "domain_status": {
    "curator": "string",
    "german": "string",
    "guild": "string"
  },
  "active_players": [],
  "open_items": [],
  "next_milestone": "string",
  "last_updated": "ISO date"
}
```

---

## 8. The Four Cabinets

### 8.1 Chief of Staff
The most important role. Not a domain expert — the generalist who holds
the full picture and makes sure every session starts with context, not
a blank page.

**Primary artifacts:**
- `intent-register.md` — public goals, 90-day priorities, north stars.
  Claude.ai and Grok read this at session start. Committed to main repo.
- `intent-register-private.md` — sensitive goals. Lives in mini-moi-private.
- `domain-health.md` — cross-domain status. Manually triggered.
- `situation-room.md` — escalation protocol and incident log.

**Trigger phrases:** "Brief me" · "What should I focus on?" ·
"New idea: [X]" · "What's the status?" · "We have a problem with [X]"

**Maps to:** Claude.ai + Grok in strategic mode

---

### 8.2 Design-Build
Formalizes what already exists. Concept to delivery with persistent
memory of why decisions were made.

**Primary artifact:** `career/api-toolkit/` — TMF622 specs, patterns,
diagrams, interview scenarios. Stays where it is. Referenced here.

**Memory:** design-decisions.md — the "why" behind every build choice.
This is what gets lost between sessions today. Design-Build captures it.

**Trigger phrases:** "Let's design [X]" · "Build [X]" ·
"Why did we decide [X]?" · "What's the spec for [X]?"

**Maps to:** Claude.ai (architect) + Grok (research) +
Claude Code (implementation) + OpenClaw (product memory)

---

### 8.3 Operations
Production guardian. Formalizes what already informally exists across
Curator and German pipelines.

**First deliverable:** OPS_HEALTH_MONITOR_SPEC_v1.0.md (exists in _working/)
slots directly in. Nothing new to design — just needs a home.

**Trigger phrases:** "Health of [domain]?" · "Deploy [X]" ·
"Something broke in [X]" · "Run final test on [X]"

**Maps to:** launchd + monitoring scripts + Claude Code

---

### 8.4 New Ventures
Looks outward. Finds where Robert's capabilities meet market opportunity.

**Career vs New Ventures boundary:**
Career = what Robert can do (toolkit, specs, patterns, artifacts)
New Ventures = where Robert applies it (roles, companies, opportunities)

**Privacy:** job-search/ lives entirely in mini-moi-private.
Competitive intel is public. Opportunity log is private.

**Trigger phrases:** "What roles are out there?" · "Who else is doing this?" ·
"Brief me on the market" · "New opportunity: [X]"

**Maps to:** Grok (primary research engine) + Claude.ai

---

## 9. Example Flows

### New idea
```
Robert → CoS: "New idea: [X]"
CoS: logs, assesses vs current priorities
CoS → Design-Build: brief
Design-Build: concept spec → Robert decision → build → Ops handoff
```

### Production incident
```
Ops: detects failure → CoS: situation room
CoS: briefs Robert → Design-Build: root cause + fix
Ops: validates, closes → CoS: logs resolution
```

### Job search match
```
New Ventures: strong role found → CoS: cross-references Career readiness
CoS → Robert: brief includes role + readiness gap
Robert: decides → Design-Build: tailors resume + narrative
```

---

## 10. File Structure

Note: Guild builds in `_NewDomains/guild/` and graduates to a top-level
`guild/` folder at v1.0 release — the same graduation pattern used by
all mini-moi domains.

```
_NewDomains/guild/               ← active build location → graduates to guild/ at v1.0
  MASTER.md                      ← this document
  README.md                      ← public charter (brief)
  GUILD.md                       ← platform charter
  shared/
    game-state.json              ← shared context, all cabinets read this
  chief-of-staff/
    README.md
    intent-register.md           ← public goals (committed)
    domain-health.md
    situation-room.md
  design-build/
    README.md
    design-decisions.md
  operations/
    README.md
    runbooks/
      briefing-failure.md
      pipeline-down.md
  new-ventures/
    README.md
    competitive-intel.md         ← public

mini-moi-private/guild/          ← sensitive, separate private repo
  intent-register-private.md
  job-search/
    criteria.md
    active-roles.md
    applied.md
  opportunity-log.md
```

---

## 11. Non-Duplication Contract

| Existing thing | How Guild uses it | Does NOT rebuild |
|---|---|---|
| Telegram bots | CoS delivery channel | No new bot |
| Curator pipeline | Ops monitors it | No new scoring |
| MEMORY.md / OpenClaw | CoS reads as input | No new memory infra |
| Claude Code for git | Still only git agent | Unchanged |
| career/api-toolkit | Design-Build artifact | No copy or rename |
| PROJECT_STATE.md | CoS reads, does not modify | — |
| GUILD.md at repo root | Platform charter, stays | Not duplicated |

---

## 12. Build Sequence

| Phase | What | Owner | Trigger |
|-------|------|-------|---------|
| **Phase 0** | Commit MASTER.md to _NewDomains/guild/ | Claude Code | Now |
| **Phase 1** | Scaffold structure + game-state.json + intent-register | Claude.ai + Robert | After commit |
| **Phase 2** | Chief of Staff briefing template — first working session opener | Claude.ai + Robert | After Phase 1 |
| **Phase 3** | New Ventures — job search structure + first competitive scan | Grok + Claude.ai | After Phase 2 |
| **Phase 4** | Design-Build formalization — OAuth pattern, interview scenarios | Claude.ai | Already on roadmap |
| **Phase 5** | Operations — health monitor + runbooks | Claude Code | After Phase 4 |
| **Release** | German v1.0 + Guild v1.0 launch together | All agents | Phase 5 complete |

### 2026-07-11 — Chief of Staff graduated from Guild to standalone domain

`chief_of_staff.py` moved from `domains/guild/agents/` to `domains/cos/` (spec #134, prerequisite to spec #133 Intelligence Layer Phase 1). CoS now runs as its own top-level domain — same Flask service, same port 8769, same bots. Memory file relocated from `data/guild/memory/cos_memory.md` to `data/cos_memory.md` (agent-agnostic memory principle: memory must outlive any single agent implementation). The four-cabinet Guild model remains; CoS cabinet is now externalized. `telegram_cos_bot.py` import path updated.

---

## 13. The Bigger Idea

> *"Not a general intelligence — a specific one. Yours."*

The guild has persistent memory of Robert's intent. Not conversation
history — structured, queryable state that survives between sessions,
across agents, and across domains.

It is a working prototype of a new organizational model: AI-native,
startup-fluid, designed to scale from one person to a team without
retrofitting the architecture.

---

> *The craft compounds. That is the point.*
