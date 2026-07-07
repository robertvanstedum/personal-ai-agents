# Design: Chief of Staff — Agent Evaluation + Phase 0 + Phase 1
**File:** `docs/design/design_cos_consolidated_v1.1_2026-07-06.md`
**Version:** 1.1
**Status:** Design — ready for Phase 0 build planning
**Date:** 2026-07-06
**Author:** Claude.ai design session + Grok review (three passes)
**Supersedes:** `design_cos_consolidated_v1.0_2026-07-06.md`
**References:** ROADMAP.md, design_memory_intelligence_layer_v3_2026-07-05.md

---

## Intent

The Chief of Staff is Robert's personal Cabinet leader and strategic thinking partner. Not a note-taker, task tracker, or scrum master. The central nervous system of the mini-moi operation — watching across all domains, maintaining memory of what has happened and why, questioning what needs questioning, and handing off to the right actor at the right time.

This document covers three sequential phases:
- **Phase 0** — Agent evaluation. Install and compare agents before wiring anything to the platform.
- **Phase 1** — CoS functional. Wire agents to platform via defined interface. CoS becomes daily-use.
- **Phase 2** — Memory and intelligence layer. Covered in `design_memory_intelligence_layer_v3_2026-07-05.md`.

**Do not start Phase 1 until Phase 0 evaluation is complete.**

---

## Role Definition

**Chief of Staff** — Cabinet leader, executive board member, personal strategic thinking partner.

| Dimension | Description |
|---|---|
| Level | Strategic / Executive |
| Scope | All domains + personal conversations + Robert's overall thinking |
| Nature | High-level advisor, cross-domain coordinator, proactive watcher |
| Memory | Writes decisions and actions naturally — not prompted |
| Autonomy | Watches, flags, and proposes handoffs within Phase 1 limits |
| Access | Read all domains. Full CoS domain access. |
| Visibility | No other agent sees CoS domain. Master Craftsman can notify only. |

**What CoS is not:** TPM, scrum master, ops director (Master Craftsman), chatbot, note-taker, vendor-locked tool.

---

## The Two Roles

| Role | Level | Focus | Domain | Access |
|---|---|---|---|---|
| **Chief of Staff** | Strategic | Thinking partner, cross-domain watching, decisions/memory | CoS Domain (private) | Reads all domains |
| **Master Craftsman** | Tactical | Build quality, standards, template compliance | Build Domain | Notify CoS only |

CoS is senior. One-way visibility upward.

---

## Access Model

| Actor | CoS Domain | Build Domain | All other domains |
|---|---|---|---|
| CoS | Full | Read | Read + propose handoff |
| Master Craftsman | Notify only | Full | Read (build-relevant) |
| Other agents | None | Via interface only | Own domain only |
| Robert | Full | Full | Full |

---

## Agent Architecture — Vendor Independence

**Core principle:** No Anthropic dependency for the CoS agent. Claude.ai handles design, Claude Code handles build — the CoS operational layer must be independently sourced.

### The Two Agents (locked decisions)

**Agent #1: OpenClaw (standalone)**
- Primary for Phase 0 evaluation and early Phase 1
- Known quantity — good Telegram/voice handling, Robert already uses it
- Runs as isolated mini-moi instance (`~/.openclaw-cos/`)
- Interface contract (cos_interface.md) enforced even here — swappability must remain possible
- Not the only long-term option by design

**Agent #2: Custom lightweight agent (Grok API)**
- Built by Claude Code against the cos_interface.md contract
- Permanent #2 — not Cowork, not a third-party framework
- Pure Python + Grok API — no framework dependency, fully auditable
- LLM-agnostic by design — Grok API today, swappable to Mistral/local model later
- Becomes the reusable agent template for future mini-moi agents (Master Craftsman, domain agents)
- Minimal viable implementation:

```python
# cos_agent_grok.py — custom CoS agent
# Accepts Telegram messages via webhook
# Calls Grok API for reasoning
# Writes to decisions.md and actions.md
# Reads from platform data sources
# Implements cos_interface.md exactly
# No framework dependency — pure Python + Grok API
```

**Cowork:** Evaluation baseline only. Explicitly temporary. Removed after Phase 0 comparison is complete. Anthropic-backed — not a permanent slot.

**MemGPT/Letta:** Investigate during Phase 0 as a potential memory substrate under the custom agent, not as the agent itself. Could provide memory patterns without full framework dependency.

### Agent Configuration — `config/cos_agents.json`

Same pattern as `config/models.json`. Agents are configurable, not hardcoded.

```json
{
  "_comment": "CoS agent config. Change here, not in code. No ECR push for agent swaps.",
  "_updated": "2026-07-06",
  "primary": "openclaw",
  "fallback": "cos_grok",
  "mode": "primary_with_fallback",
  "agents": {
    "openclaw": {
      "type": "openclaw",
      "port": 18889,
      "data_dir": "~/.openclaw-cos"
    },
    "cos_grok": {
      "type": "custom",
      "script": "agents/cos_agent_grok.py",
      "llm": "grok-4-1-fast-reasoning",
      "note": "Custom agent — permanent #2"
    },
    "cowork": {
      "type": "cowork",
      "note": "Temporary evaluation baseline — remove after Phase 0"
    }
  }
}
```

**Modes:**
- `primary_only` — one agent
- `primary_with_fallback` — failover to second on primary failure
- `ab_test` — split traffic, compare outputs
- `configurable` — Robert picks per session

### Memory Principle — Agent Independence

Never store in agent internal memory what isn't also in external stores. Internal memory is a working cache. External stores (decisions.md, actions.md, Postgres) are the canonical record. Switching agents mid-stream: platform memory fully intact, agent working context resets. Acceptable tradeoff.

---

## Entry Points — One Unified Interface

**Core principle (Grok-confirmed):** CoS must feel like one thinking partner. Robert never thinks about which agent is running. No multiple prefixes, no fragmentation.

### Entry Point Priority

| Entry Point | Priority | Status |
|---|---|---|
| Telegram `!cos` | Highest | Primary daily interface — always |
| Voice notes (Telegram) | Highest | Natural, seamless — Phase 1 |
| Voice notes (CoS web UI) | High | Same mic/VAD/Whisper pattern as Gespräche |
| CoS domain web UI | Medium | Review, deeper work, dashboard |
| Multiple prefixes (`!cos2`) | Avoid | Fragmented — never long-term |

**Internal routing:** `!cos` → `cos_agents.json` → primary agent (with fallback). Robert never sees the routing. The CoS is one entity.

---

## Phase 0 — Agent Evaluation

**Goal:** Learn before committing. Two agents, isolated, no platform wiring. Pure evaluation.

**Duration:** 2-4 weeks daily use before drawing conclusions.

**Agents:**
- OpenClaw — primary candidate
- Cowork — temporary baseline (Anthropic, evaluation only)

**Investigation task (before Phase 0 build):**
Produce `docs/design/design_agent_evaluation_candidates_2026-07.md`:
- MemGPT/Letta — memory substrate potential, Telegram support, self-hostable
- Grok API custom agent — build scope, tool calling maturity, structured output
- Mem0 — memory layer under custom agent, LLM-agnostic integration
- Confirm: Grok API tool calling and structured output capabilities for cos_agent_grok.py

This is also the first entry in the Curator Tech research thread.

**Evaluation criteria:**

| Criterion | What we test |
|---|---|
| Voice note handling | Telegram voice → transcription → storage |
| Memory persistence | Context across session end and restart |
| Conversation quality | Strategic thinking partner feel |
| Structured output | Natural decisions/actions writing |
| Developer experience | Config, extension, maintenance |
| Swappability | Interface contract compliance |
| Vendor independence | No Anthropic dependency |

**Phase 0 setup:**
```
Mac dev:
  OpenClaw:  port 18889, ~/.openclaw-cos/, !cos prefix
  Cowork:    separate config, !costest prefix (evaluation only)
  No changes to: docker-compose.prod.yml, portal, any domain server
```

**Phase 0 deliverable:**
`docs/design/design_agent_evaluation_results_2026-07.md` — findings, recommendation, learnings that inform cos_interface.md.

**Phase 0 exit criteria:**
- [ ] Both agents installed on Mac dev
- [ ] 2+ weeks daily use
- [ ] Evaluation results documented
- [ ] MemGPT/Letta investigation complete — decision on memory substrate
- [ ] cos_agent_grok.py scope defined
- [ ] cos_interface.md first draft written
- [ ] Robert: "I know which agent I want primary and which fallback"

---

## Phase 1 — CoS Functional

**Prerequisite:** Phase 0 complete. cos_interface.md written.

### Memory — Three Layers, Platform-Owned

**Layer 1 — Decisions** (`openclaw/decisions.md` — all actors read):
Strategic decisions, written naturally by CoS.
```
## 2026-07-06 — Custom Grok agent as permanent CoS #2
**Decision:** Build cos_agent_grok.py as permanent #2 — pure Python, Grok API,
no framework dependency. Becomes reusable agent template for mini-moi.
**Why:** Vendor independence, full control, auditable, LLM-agnostic.
**Actors:** Robert (decision), Claude.ai + Grok (design)
**Domain:** cross-domain
```

**Layer 2 — Actions** (`openclaw/actions.md` — all actors read):
Meaningful actions, timestamped, terse.
```
2026-07-06T09:14:00Z | cross-domain | cos | Flagged: leanings.json not volume-mounted
2026-07-06T09:31:00Z | guild | cos | Handoff proposed: register three idea-phase items
```

**Layer 3 — Raw conversations** (Postgres, ingested on schedule):
Full transcripts. Never lost. Separate from decisions — the conversation is how you got there, decisions.md is where you arrived.

### Voice Notes — First Class

**Via Telegram:**
Voice message → Whisper transcription → raw transcript stored → CoS extracts summary and decisions → writes to appropriate layer. Platform-owned, not agent-owned.

**Via website:**
Mic/VAD/Whisper (same as Gespräche). Button in CoS domain UI.

**Three-layer output:**
1. Raw transcript — stored, never lost
2. Summary — CoS generates
3. Decision (if any) — written to decisions.md

### Bidirectional Linking

Links already exist in data (specs → GitHub issues via ↗). Extend the convention:
- Decision → source conversation (timestamp)
- Decision → relevant spec (build queue number)
- Spec → design conversation (date + doc reference)
- Design conversation → decisions produced

Not a UI feature in Phase 1 — a data convention. Queryable when Phase 2 memory layer adds full-text search.

### Proactive Watching

**Phase 1 observation sources:**
- `build_queue.json` — specs stale, blocked, missing progress
- GitHub issues — unresolved, missing build queue entries
- `decisions.md` — decisions made but not logged
- `actions.md` — action patterns
- Domain health endpoints
- EC2 disk and backup status

**Cadence:** On-demand + hourly scheduled scans.

**Handoff:** actions.md record + Telegram message to Robert. Robert approves. No autonomous execution in Phase 1.

### Autonomous Behaviors (Phase 1)

**Permitted:**
- Write to decisions.md and actions.md
- Flag issues via Telegram
- Propose handoffs (not execute)
- Retrieve documents
- Transcribe and store voice notes
- Create GitHub issues for bugs Robert identifies

**Requires approval:**
- Any domain data file writes
- Any deployment
- Executing proposed handoffs
- Anything not listed above

### Phase 1 Exit Criteria

- [ ] CoS running reliably 2+ weeks
- [ ] Daily Telegram use — natural conversation
- [ ] Voice notes working via Telegram and website
- [ ] decisions.md writing automatically
- [ ] actions.md writing automatically
- [ ] 3+ proactive flags without prompting
- [ ] 1+ cross-domain handoff proposed
- [ ] Hourly watching confirmed
- [ ] Guild surfaces decisions and actions live
- [ ] A/B test OpenClaw vs cos_agent_grok — swappability confirmed
- [ ] cos_interface.md accurate and complete
- [ ] Robert: "CoS knows what's going on without me telling it"

---

## Containerization

```yaml
openclaw-cos:
  build:
    context: .
    dockerfile: docker/Dockerfile.openclaw-cos
  ports:
    - "18889:18889"
  volumes:
    - /opt/minimoi/openclaw:/app/openclaw
    - /opt/minimoi/.openclaw-cos:/root/.openclaw
  env_file: /opt/minimoi/.env
  restart: unless-stopped
```

Agent swap = replace Dockerfile. Everything else stays.

---

## Open Questions (resolve before Phase 0 build)

1. **MemGPT/Letta as memory substrate** — investigate during Phase 0. Decision before cos_agent_grok.py is built.
2. **Secrets** — Separate SSM prefix `/minimoi/cos/` or shared `.env`?
3. **Mac dev startup** — Manual or LaunchAgent for Phase 0?
4. **`./openclaw/` directory** — In repo (gitignored) or volume-mounted?
5. **cos_interface.md first draft** — Claude.ai drafts after Phase 0, OpenClaw reviews.

---

## Build Sequence

```
Investigation → Phase 0 → cos_interface.md → cos_agent_grok.py → Phase 1 → Phase 2

Investigation:       Research MemGPT/Letta, Grok API capabilities (1-2 weeks)
Phase 0:             OpenClaw + Cowork evaluation in isolation (2-4 weeks)
cos_interface.md:    Write interface contract from Phase 0 learnings
cos_agent_grok.py:   Build custom #2 agent (Claude Code)
Phase 1:             Wire to platform, daily use, A/B test (4+ weeks)
Phase 2:             Memory layer, fine-tuning foundation
```

---

## Document History

| Version | Date | Changes |
|---|---|---|
| v0.1 | 2026-07-05 | Initial draft — note-taker framing |
| v0.2 | 2026-07-05 | Cabinet leader reframe, Grok review 1 |
| v0.3 | 2026-07-05 | Grok refinements — watching scope, handoff, cadence |
| v1.0 | 2026-07-06 | Full consolidation — Phase 0, voice notes, bidirectional linking, vendor independence |
| v1.1 | 2026-07-06 | Grok review 3 — #2 agent locked as custom Grok build, Cowork temporary, unified entry point, MemGPT/Letta as substrate candidate |

---

*Design document · mini-moi · 2026-07-06 · v1.1*
*Three Grok review passes complete*
*Next: Claude Code commits to docs/design/, sync to EC2, add to build queue as design status*
*Then: investigation task → Phase 0 → cos_interface.md → cos_agent_grok.py → Phase 1*
