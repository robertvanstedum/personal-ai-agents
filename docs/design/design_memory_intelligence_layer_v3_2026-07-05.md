# Design: Memory and Intelligence Layer — v3
**File:** `docs/design/design_memory_intelligence_layer_v3_2026-07-05.md`
**Status:** Idea/Design — not build-ready
**Date:** 2026-07-05
**Supersedes:** `design_memory_intent_layer_conceptual_2026-06-29.md` (v2)
**Author:** Claude.ai design session
**References:** ROADMAP.md (Phase 2), design_cos_phase1_2026-07-05.md

---

## Intent

mini-moi is built and maintained by five actors. Each actor generates valuable context — design decisions, build actions, code changes, conversation summaries, specs. Today that context lives in fragments: handoff docs, spec files, OpenClaw memory, GitHub issues, Claude.ai conversation history. None of it is queryable. None of it compounds over time in a way that makes future work easier.

The memory and intelligence layer changes that. It gives the platform a shared, queryable, agent-agnostic memory that all actors write to and all actors can read from. Memory belongs to the platform — not to any one tool. If OpenClaw is replaced tomorrow, the new tool reads the external stores and continues. If a new domain is added next year, the design conversations from 2026 are available as context.

This is Phase 2 of the solution roadmap. Phase 1 (CoS functional) introduces the first two memory files. Phase 2 builds the full architecture.

---

## The Core Insight

**Store everything. Tag lightly. Let AI retrieve.**

Don't design the perfect schema now. Design for volume and retrieval flexibility. The value compounds over time — a design conversation from today might be exactly what's needed in six months when building a new domain. A decision made in July 2026 explains why the code looks the way it does in January 2027.

Time is the one dimension that is always true and always queryable. Timestamp everything. Domain tagging is secondary — add it when obvious, don't require it.

---

## Three Memory Layers

### Layer 1 — Decisions (public, real-time, shared)

**What it is:** The authoritative record of decisions made across the platform. Not conversation, not every action — just decisions. What was decided, why, and by whom.

**Who writes:** OpenClaw primarily, but any actor can write. Robert can add a decision directly. Claude.ai can produce a decision summary at the end of a design session.

**Format:** Append-only markdown file. One decision block per entry.
```
## 2026-07-05 — Spec naming convention established
**Decision:** All spec files follow numbered convention: spec_<number>_<name>_<date>.md
**Why:** Build queue number in filename makes it immediately clear what spec maps to queue item.
**Actors:** Robert (decision), Claude.ai (proposed)
**Domain:** cross-domain
**Tags:** process, naming
```

**Where it lives:** `openclaw/decisions.md` — volume-mounted, not in git (contains operational detail). Readable by all actors. Guild surfaces it as a live feed.

**What it replaces:** The decision log that exists but isn't being used because it's manual. This is automatic.

---

### Layer 2 — Actions (summary, timestamped, shared)

**What it is:** A terse, permanent record of meaningful actions and their outcomes. Not conversation. Not every shell command. Just the things that matter.

**Who writes:** OpenClaw writes actions as it works. Claude Code writes a summary action at the end of each session. Robert can add an action manually via Telegram.

**Format:** Append-only log. One line per action.
```
2026-07-05T14:23:00Z | note | cross-domain | robert | "SEI meeting next week — prep needed"
2026-07-05T14:31:00Z | github_issue | guild | openclaw | "#121 created — pipeline.items unavailable"
2026-07-05T15:44:00Z | spec_created | guild | claude-ai | "spec_117_guild_navigation_redesign_2026-07-04.md"
2026-07-05T16:12:00Z | deploy | portal | claude-code | "volume mount fix deployed to prod — guests.json now persists"
```

**Where it lives:** `openclaw/actions.md` — volume-mounted, not in git. Readable by all actors. Guild surfaces it alongside decisions.

---

### Layer 3 — Raw Files (local-first, mine, queryable on demand)

**What it is:** Everything else. The raw data agent layer. Pulled and archived on my schedule, not in real time.

**What gets stored:**
- Raw `./openclaw/` md files — OpenClaw's internal memory, pulled and timestamped
- Design conversations (pre-spec sessions) — fed by Robert, stored by OpenClaw
- Handoff documents — already being generated, need to be ingested
- Spec files — already in git, need to be indexed
- Claude Code session summaries — to be defined
- Grok review outputs — already in docs, need to be indexed

**Storage:** Postgres. Pulled on demand or on schedule. Ad hoc queryable. Not consumed in real time.

**Schema (lightweight, not overspecified):**
```sql
CREATE TABLE memory_store (
  id UUID PRIMARY KEY,
  timestamp TIMESTAMPTZ NOT NULL,
  actor VARCHAR(50),           -- robert, claude-ai, claude-code, openclaw, grok
  type VARCHAR(50),            -- decision, action, design, spec, raw, handoff
  domain VARCHAR(50),          -- curator, german, portuguese, guild, cross-domain
  content TEXT NOT NULL,
  source_file VARCHAR(500),    -- original file path if applicable
  tags JSONB,                  -- flexible tagging
  ingested_at TIMESTAMPTZ DEFAULT NOW()
);
```

**The ingestion pattern:**
- Robert feeds a design conversation → OpenClaw stores it to Layer 3
- `sync_openclaw.sh` runs on schedule → pulls `./openclaw/` md files, ingests to Postgres
- Specs committed to git → indexer picks them up on next run
- Manual ad hoc: Robert asks CoS to retrieve something → CoS queries Layer 3

---

## Agent-Agnostic Design

The most important principle: **memory belongs to the platform, not to any one tool.**

OpenClaw writes to external stores (Layers 1 and 2) as it works. If OpenClaw is replaced by a better tool:
1. New tool reads `openclaw/decisions.md` and `openclaw/actions.md`
2. New tool reads Layer 3 from Postgres
3. New tool continues exactly where OpenClaw left off

OpenClaw keeps its own internal memory (`~/.openclaw-cos/`) — that's fine. But the canonical record lives externally. The internal memory is a working cache, not the source of truth.

This same principle applies to all actors. Claude.ai produces design conversations that get ingested. Claude Code produces session summaries. Grok produces review outputs. All of it flows into the shared layer over time.

---

## What Gets Stored — Taxonomy

| Source | Type | Layer | Cadence |
|---|---|---|---|
| Design sessions (Claude.ai) | design | 3 | On demand — Robert feeds to OpenClaw |
| Specs | spec | 3 | On commit — indexed from git |
| Handoff documents | handoff | 3 | On creation — synced with docs |
| OpenClaw decisions | decision | 1 | Real-time — OpenClaw writes |
| OpenClaw actions | action | 2 | Real-time — OpenClaw writes |
| Raw OpenClaw md files | raw | 3 | Scheduled pull |
| Claude Code session summaries | action | 2/3 | End of session |
| Grok review outputs | design | 3 | On creation |
| GitHub issues | action | 3 | Webhook or scheduled pull |

---

## Guild Integration

Layer 1 (decisions) and Layer 2 (actions) surface in Guild as a live feed — the operational memory of the platform made visible. Two options for the Guild view:

- **New Operations Log tab** — dedicated view for decisions and actions, separate from Build Log
- **Integrated into Build Log** — actions and decisions appear alongside specs as a unified timeline

Decision to be made in the Guild Navigation Redesign (#117) design session.

Layer 3 is queryable from Guild on demand — "what did we decide about X" or "what happened on Y date" — powered by Postgres full-text search.

---

## Swappable Agent Design

OpenClaw is the first actor in this layer. It will not be the last. The architecture is designed for experimentation:

- Try a different memory tool → plug it into the same external stores
- Add a new actor to the team → define its write obligations, add it to the taxonomy
- Run two memory tools in parallel → both write to the same Layer 1/2 files, both read from Layer 3

The two OpenClaw instances (personal and mini-moi) are the first demonstration of this — same tool, different scope, different memory stores.

---

## What This Is Not

- **Not a personal knowledge base** — this is platform memory, bounded to mini-moi domains
- **Not a chat history dump** — conversation back-and-forth is not stored; decisions and actions are
- **Not overspecified** — the schema is intentionally lightweight; let future AI decide what's relevant
- **Not real-time for Layer 3** — Layer 3 is a pull/archive operation, not a live feed

---

## Open Questions (design phase — not blocking Phase 1)

1. **Design conversation ingestion** — push (OpenClaw pulls from Claude.ai export) or pull (Robert pastes manually to Telegram)?
2. **Decisions file format** — one file continuously updated, or one file per month? Lean toward continuous with timestamps — simpler, more queryable.
3. **Layer 3 indexing** — manual trigger or scheduled? Start manual, automate once pattern is proven.
4. **Guild integration** — new Operations Log tab or fold into Build Log timeline?
5. **Cross-instance memory** — should personal OpenClaw ever write to the mini-moi memory layer? No — hard boundary. Personal stays personal.

---

## Relationship to v2

`design_memory_intent_layer_conceptual_2026-06-29.md` (v2) covered:
- Exploratory archetype
- Archetypes as product
- Portable instances
- Seed personas

Those concepts are not abandoned — they are deferred. v3 focuses on the practical foundation: get the three memory layers working, prove the agent-agnostic pattern, then layer in the more advanced concepts. v2 is archived as reference.

---

## Next Steps (in order)

1. Phase 1 (CoS functional) proven stable — 4+ weeks of daily use
2. Grok review of this document
3. Decisions file and actions file format finalized with OpenClaw
4. Layer 3 schema reviewed and approved
5. Guild integration decided (Operations Log vs Build Log)
6. Build spec written — `spec_memory_intelligence_layer_v3.md`

---

*Design document · mini-moi · 2026-07-05 · Idea/design phase — not build-ready*
*Supersedes: design_memory_intent_layer_conceptual_2026-06-29.md*
*Next: build spec after Phase 1 proven stable*
