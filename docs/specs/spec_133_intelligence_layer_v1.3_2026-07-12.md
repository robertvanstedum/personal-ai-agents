# Spec #133 v1.3: mini-moi Intelligence Layer
## CoS Domain + Memory + Local LLM — Multi-Agent, Lock-In-Free, Compounding

**File:** `spec_133_intelligence_layer_v1.3_2026-07-12.md`
**Status:** Spec Ready — Phase 0 complete, Phase 1 reconciled with reality and Grok-reviewed, ready for build
**Date:** 2026-07-12
**Build queue:** #133
**Author:** Claude.ai design session (Fable 5)
**Reviews:** Grok (2026-07-10 and 2026-07-12, both incorporated) · Claude frontier review (2026-07-10, incorporated) · Robert (sequencing, cost, data architecture, Phase 1 reality-check, ways-of-working — all approved)
**Companion documents:** `VISION_mini_moi_intelligence_layer_2026-07-11.md`, `design_memory_architecture_direction_2026-07-12.md` (pending artifact review — see Open Questions, may be superseded by existing content)

---

## Supersedes / Incorporates

| Document | Status |
|---|---|
| `spec_133_intelligence_layer_v1.2_2026-07-11.md` | **Superseded by this version** |
| `spec_133_intelligence_layer_v1.1_2026-07-10.md` | Superseded |
| `spec_133_intelligence_layer_2026-07-10.md` (v1.0) | Superseded |
| `spec_cos_domain_extraction_2026-07-11.md` | Incorporated as Phase 0 — complete, build queue #134 |
| `design_cos_consolidated_v1.1_2026-07-06.md` | Superseded |
| `spec_cos_v0_2026-07-10.md` | Superseded |
| `design_memory_intelligence_layer_v3_2026-07-05.md` | Superseded |
| `design_cos_phase1_2026-07-05.md` (all versions) | Superseded |
| `CoS_Test_Instance_Setup.md` | Superseded |
| `design_curator_tech_domain_2026-07-10.md` | Referenced — Curator domain concern |
| `spec_117_guild_navigation_redesign_2026-07-04.md` | Referenced — Guild split deferred |
| `config/cos_interface.md` (v0.2) | **Companion — Grok-reviewed and finalized 2026-07-12, no blocker remaining** |
| `design_memory_architecture_direction_2026-07-12.md` | **New — pending artifact review before registration** (see Open Questions); may duplicate existing content, not yet confirmed |

---

## Change Log — v1.2 → v1.3 (2026-07-12)

1. **`cos_interface.md` finalized as v0.2** — rechecked against real `chief_of_staff.py` internals, corrected from whole-agent-replacement framing to the coordination-layer/backend-call boundary. Grok-reviewed same day; implementation notes and a concrete conformance test added per that review. No blocker remains before Phase 1 build.
2. **Backend-swap target confirmed as OpenClaw**, not Gemini/Claude as first suggested — a dedicated, CoS-scoped Gateway instance, native tools scoped to observation only (see Scope Enforcement).
3. **Scope enforcement corrected** from a list of banned technologies ("no git access") to an observe/mutate principle — CoS gets real, unrestricted authority to observe and research; only mutation requires Robert's sign-off. Matches "chief of staff, not secretary."
4. **CoS role sharpened**: right-hand advisor with real authority, never the executor of domain processes. Architecture Principle #4 and the CoS Role section both updated to state this explicitly, closing a possible ambiguity rather than leaving it implicit.
5. **Phase 1 Deliverables and Rollback/Production Safety sections added**, per Grok's 2026-07-12 review — CoS is live in prod; containerization must not risk it.
6. **Memory taxonomy adopted**: `cos_memory.md` named explicitly as mini-moi's episodic memory tier, part of a three-tier (episodic/semantic/procedural) direction — see `design_memory_architecture_direction_2026-07-12.md`. Naming only, no implementation change.
7. **Correction (2026-07-12, same night):** a standalone `WAYS_OF_WORKING.md` was drafted and then retracted — a formal ways-of-working document already exists in GitHub, predating this session. Creating a second one would have been exactly the scattered-duplication problem this spec's Data Flow Model work was meant to prevent. See `investigation_artifact_review_2026-07-12.md` — grounding this in what actually exists is now a separate, open-ended investigation, not resolved here.

---

## Change Log — v1.1 → v1.2 (2026-07-11)

**The critical correction — read this first:**

Investigation during Phase 0 found that CoS was not a blank slate. `domains/cos/chief_of_staff.py` (Grok-backed, `grok-4-1-fast-reasoning`) is a substantial, already-working CoS agent — chat loop, tool calls, scheduled loops, its own memory (`cos_memory.md`), its own recommendation queue, and its own dedicated Telegram bot (`minimoi_cos_bot` prod / `minimoi_cos_test_bot` dev, fully separate from `@minimoi_agent_bot`). v1.1's Phase 1 was written before this was known, and inverted the reality in three places:

1. **Primary agent — corrected twice now.** v1.1 assumed OpenClaw would be built as the primary CoS backend, with `cos_agent_grok.py` as a lightweight skeleton. First correction: Grok is already primary and proven — `chief_of_staff.py` runs it in production today. **Second correction (this version, per Robert):** the real swap point isn't "which whole agent runs CoS" — it's the model/backend called *inside* `chief_of_staff.py`'s stable coordination code. `chief_of_staff.py` (routing, scope enforcement, memory, tool calls) stays platform-owned and permanent regardless of which model powers it. Grok is today's backend; Gemini, Claude, or others will rotate in over time — that's the normal, frequent swap. OpenClaw is one *possible* backend option, same tier as any model API, not a peer competing to replace `chief_of_staff.py` itself.
2. **Bot identity:** v1.1 planned to migrate `@minimoi_agent_bot` (OpenClaw #1's personal bot) into becoming CoS's bot, requiring a cutover sequence. Reality: **no cutover needed or wanted** — CoS already has its own bot. `@minimoi_agent_bot` stays OpenClaw #1's, permanently, untouched. The entire "Bot Inventory & Prod Cutover" section is removed.
3. **Memory format:** v1.1 specified `cos_memory.json` with structured/typed/UUID entries from day one. Reality: working memory is `cos_memory.md`, already has an LLM-judged storage-worthiness function (`_maybe_update_memory`). Phase 1 formalizes the existing format; the structured-JSON upgrade is retained as a documented future option, not a Phase 1 requirement.

Phase 1 below is rewritten around formalizing and containerizing what's proven to work, not building a parallel system.

**Everything else new since v1.1:**

4. **Phase 0 (CoS domain extraction) complete** — `chief_of_staff.py` moved to `domains/cos/`, memory relocated to `data/cos_memory.md`, dev/prod bot separation confirmed working, `GUILD_CHARTER.md` amended. Build queue #134, done.
5. **Data Flow Model added** — resolves dev/prod consolidation for design-decision history without dual sources of truth or routing everything through CoS. See dedicated section.
6. **mini-moi Portable Archive added** — per-agent directory structure, dual S3 + private-GitHub destination, explicit portability goal ("pick up mini-moi, put it anywhere"). Extends Architecture Principle #7 (no vendor lock-in) to cover the accumulated knowledge base itself, not just swappable components.
7. **SSH Mac↔EC2 resolved** — root cause was a local key mismatch, not infrastructure. Two-path sync architecture locked: `sync_docs.sh` (GitHub, public/shareable) and `sync_private.sh` (direct SSH, content that must never touch GitHub).
8. **Doc-sync-to-prod gap resolved** — manual trigger via fixed `sync_docs.sh`, deliberately not automated (weekly cadence doesn't justify polling infrastructure).
9. **Architecture Principle #2 extended** — agent-agnostic *and* environment-agnostic; dev-originated and prod-originated reasoning are the same class of content and must consolidate.
10. **Bot inventory fully reconciled** — all 9 bots identified via BotFather + code grep (not assumption), roles confirmed, `@rvsopenbot` protected (feeds Phase 3 Curator Intelligence preference learning).

---

## Intent

mini-moi is a process not a product. It is learning by doing, with reinforcement over time — seeing history, evaluating decisions, improving future choices across code, career, language, and life.

This spec is the intelligence layer that makes that compounding real: a working example of multi-agent coordination that avoids lock-in at every layer — model, agent framework, cloud provider, and hardware — while building toward genuine learning over time rather than a fixed pipeline. See `VISION_mini_moi_intelligence_layer_2026-07-11.md` for the full framing of why this matters beyond mini-moi itself.

Today mini-moi has four live domains and a CoS agent (Grok-backed) that already works but isn't yet formalized, containerized, or provably swappable. Design conversations happen in Claude.ai and risk being forgotten. Build sessions in Claude Code can repeat mistakes without a durable record. The German and Portuguese sessions start cold every time.

This spec builds the intelligence layer that makes mini-moi compound over time. Sequenced phases, each proven stable before the next begins. Sequencing governs; the timeline is indicative only.

---

## What Is Not Changing

- Guild domain: untouched (except new read-only CoS view)
- Build domain: untouched, no rename (Master Builder / Master Craftsman rename deferred to #117)
- German domain: untouched until Phase 3
- Portuguese domain: untouched until Phase 3
- Prod EC2 instance: no changes except agent_logs volume mount and CoS container
- Personal OpenClaw (#1): completely untouched — verified, not assumed (see Phase 1 verification)
- `@minimoi_agent_bot`: stays OpenClaw #1's, permanently. No cutover, no migration — CoS already has its own bot.

---

## Architecture Principles (locked)

1. **JSON-first** — JSON is source of truth, Postgres is rebuildable projection
2. **Agent- and environment-agnostic memory** — memory belongs to the platform, not to any one agent or any one environment (dev or prod). See Data Flow Model below for the consolidation rules.
3. **Two-stage processing** — local LLM indexes/structures, cloud LLM reasons/synthesizes; the index is a map with pointers to raw sources, never a lossy replacement for them
4. **Domains stand alone; CoS advises, it doesn't execute** — CoS is a right-hand advisor with real observational authority, not the delivery mechanism for domain processes. It calls/queries domain services and proposes actions; domains own and execute their own work. CoS is never the central point that "does" things — that stays with the domains and with Robert's sign-off on mutation.
5. **Reuse and extend** — one model serves all intelligence domains during evaluation; per-task model profiles are a Phase 3+ optimization, considered only with usage data
6. **Store everything, tag lightly** — let future intelligence decide what's relevant
7. **No vendor lock-in** — OpenClaw is swappable, LLM is swappable, cloud provider is swappable, and the hardware path ends local
8. **Spend follows attention** — compute exists only while it is doing something Robert asked for; no always-on inference infrastructure

---

## Data Flow Model — Dev/Prod Consolidation (resolved 2026-07-11)

**Two distinct bars — don't conflate (clarified 2026-07-11):**

- **Preservation (broad, simple):** never lose any md/JSON file, regardless of source (mini-moi or not), versioned, mineable later by whatever tool exists then — including agents not yet adopted (e.g. Grok Build, or successors to the reported Cursor acquisition). See **mini-moi Portable Archive** below for the concrete structure.
- **Live consolidation (narrow, careful):** what the *active* intelligence loops (Build/Curator Intelligence, CoS) need to read consistently right now to reason correctly. This is what the four rules below govern — scoped only to the data those loops actually consume (`agent_logs`, `cos_memory`). Does not extend to OpenClaw #1's memory or other purely-archival content.

The rules below are Bar 2 only.

Resolves: dev-originated design/build reasoning (Claude.ai, Claude Code, Grok) and prod-originated reasoning (CoS conversations) need to consolidate for the intelligence layer to reason over the full picture, without creating dual sources of truth or forcing everything through a single live process.

**Four rules:**

1. **Canonical home is always prod.** Same pattern already applied to `cos_memory.md` and specs-via-git, extended to `agent_logs/`. One authoritative copy per data type, never two.
2. **Writes flow dev → prod, one direction only.** Content authored locally (Mac sessions) is a byproduct of where it was written, not a second source of truth — it pushes up via the existing private sync mechanism. Nothing pushes back down and overwrites.
3. **Reads flow prod → dev freely, as a read-only cache.** Dev tools may pull recent history down for local context; the pulled copy is never edited in place — new entries are always authored fresh and pushed up.
4. **`agent_logs` and `cos_memory` stay separate stores — never merged, and neither is routed through the other.** `agent_logs` is comprehensive/low-filter ("store everything"); `cos_memory` is curated/high-filter (CoS's own judgment of what's worth remembering). Forcing agent_logs through CoS would narrow it to CoS's filter and lose the point of a comprehensive store. It would also make CoS a mandatory router for all platform data — the "general intelligence orchestrator" role Grok's original review specifically warned against (see Grok review, 2026-07-10). CoS remains a producer/consumer of `cos_memory` specifically, not a gatekeeper for everything else.

**Mechanism:** no new infrastructure. `agent_logs/` joins the same private sync path already built for `cos_memory.md` (`sync_private.sh` / `sync_private_repo.sh`) — same direction, same schedule, same privacy treatment.

**Why not route through CoS (considered and rejected):** would add a mandatory live dependency for data that doesn't need to be live (recency risk if CoS is busy/down), and would collapse the comprehensive/curated distinction between the two stores.

---

## mini-moi Portable Archive (Bar 1 — Preservation, added 2026-07-11)

The concrete shape of "never lose it, mineable later, portable to anywhere." Not part of the live-consolidation rules above — this governs raw preservation only.

**Structure — per-agent directories, extensible:**

```
agent_logs/
  claude_ai/
  claude_code/
  grok_review/       (design/review sessions)
  openclaw/          (OpenClaw #1's personal memory — same package, own folder;
                        no longer a separate repo, per Robert's preference for one
                        portable package)
  robert/            (manual voice-note entries)
  [new agent]/       ← added the same way whenever adopted (e.g. Grok Build,
                        or whatever emerges from the reported Cursor acquisition) —
                        no redesign needed
```

**Dual destination, different jobs:**
- **Private GitHub** — versioned via commits, diffable, dev-friendly history browsing
- **S3, with bucket versioning explicitly enabled** — bulk/portable, the natural shape for future graph DB or LLM ingestion, and the concrete deliverable for spec #122's third backup tier (previously vague "S3 daily backup," now a specific target)

**Portability test:** `git clone` or `aws s3 sync` from a clean machine should reproduce the entire archive in one command. This is the literal mechanism for "pick up mini-moi and put it somewhere else."

---

## Phasing

| Phase | Focus | Gate to next phase |
|---|---|---|
| **Phase 0 (complete — 2026-07-11)** | CoS domain extraction — `chief_of_staff.py` moved to `domains/cos/`, memory relocated to `data/cos_memory.md`, dev/prod bot separation (`minimoi_cos_bot` / `minimoi_cos_test_bot`) confirmed working | ✅ Done — see `spec_cos_domain_extraction_2026-07-11.md`, build queue #134 |
| **Phase 1 (this week, committed)** | CoS containerization + swappability verification + memory capture + agent_logs | Phase 1 DoD + verification passed; one week of stable daily use |
| **Phase 2 (next)** | Local LLM infrastructure (start/stop) + Build Intelligence | First evaluation model passes/fails documented; scheduled runs stable for two weeks |
| **Phase 3 (after Phase 2 stable)** | Curator Intelligence + Language Intelligence + CoS routing to all domains | — |
| **Month-3 decision gate** | Hardware: Mac mini as permanent local home vs continue cloud | Decided with real usage + cost data |

**Phase 0 note:** CoS was found already running as a substantial, working agent (`chief_of_staff.py` on Grok, with chat loop, tool calls, scheduled loops, and its own memory/agenda) rather than needing to be built from scratch — Phase 0 extracted it into its own domain and cleaned up dev/prod bot separation so Phase 1 containerization wraps a standalone domain instead of code still nested in Guild's import tree. Phase 1 below now formalizes and containerizes what's already running, rather than building new CoS logic. **Phase 1 is blocked on OpenClaw's review of `config/cos_interface.md` before build starts.**

---

# Phase 1 — Formalize & Containerize CoS

**Committed for this week. Faster is fine. Nothing else is committed this week.**

**Reframe from v1.1:** this is not "build CoS." CoS exists and works. This phase formalizes it — containerizes it, verifies its swappability contract, closes real operational gaps (scope enforcement, credential scoping) — without disrupting what's already proven.

## The Experience (already working, being formalized)

Robert opens Telegram and talks to `minimoi_cos_bot` (prod) or `minimoi_cos_test_bot` (dev) naturally. No prefix, no commands. This is already how it works today.

```
Robert: "I'm thinking the Curator Tech domain should
         start inside Curator, not standalone"

CoS: "That makes sense — inherits the scoring engine
      immediately. Worth noting as a direction decision.
      What's driving the timeline?"

Robert: [voice note] "Remind me to follow up with Paul
         at Bennett Moore next week"

CoS: "Noted — follow up with Paul at Bennett Moore.
      I'll flag it when next week starts."
```

## Routing (already live, no change needed)

CoS has its own dedicated bot — there is no prefix-routing problem to solve on it. Domain commands (German, Curator, etc.) live entirely on `@minimoi_cmd_bot`, a separate bot. `minimoi_cos_bot` receives conversational text only; everything on it goes to CoS by construction, not by a routing rule that needs building.

```
minimoi_cos_bot / minimoi_cos_test_bot  → chief_of_staff.py:_chat (always)
@minimoi_cmd_bot                          → drills, commands, curator pipeline (unchanged)
@minimoi_agent_bot                        → OpenClaw #1, personal, untouched
```

## CoS Role — Right-Hand Advisor, Not Executor (already substantially built)

Not a note-taker. Not a task tracker. Not an intelligence orchestrator. Not the thing that "does" — **an advisor with real authority to observe and propose, never the delivery mechanism for domain work.** The conversational entry point and memory capture layer — already functional:

- Full chat loop on Grok (`grok-4-1-fast-reasoning`), already deployed
- Tool calls already exist: ops status, ops log, domain health, queue recommendation — **Phase 1 task: verify none of these mutate state; treat as write-capable until confirmed read-only**
- Writes decisions/notes to `cos_memory.md` via `_append_memory()`, already gated by an LLM judgment call (`_maybe_update_memory`) on what's worth keeping
- Recommendation queue already writes to `guild.cos_agenda` (Postgres) — **Phase 1 task: confirm this is read-only for Robert's manual action, never auto-executed**
- Does **not** own Build/Curator/Language Intelligence — those are domain services it will call in later phases, not build itself

## Scope Enforcement — corrected 2026-07-12: observe vs. mutate, not a list of banned technologies

**Principle corrected per Robert:** CoS is a chief of staff, not a secretary. It should have real authority to observe, check, and research — git status/log, local files, cron/heartbeat logs, system health, web search — genuinely unrestricted, not filtered through a list of barred tools. The earlier "anything requiring git access" rule was wrong in kind, not just degree: git isn't special: reading is always fine, mutating still isn't. The actual boundary is **whether an action observes state or commits/changes it** — not which specific technology is involved.

- **Observation — unrestricted, no redirect needed:** git status/log/diff, checking whether a push or sync succeeded, reading logs, health checks, web search and research, reading any file relevant to the question asked. `chief_of_staff.py` and its backend(s) should have real access for this, not a narrowed tool list.
- **Mutation — still proposes, doesn't execute, without Robert's sign-off:**
  1. Deploy, push code, or force-push / rewrite git history
  2. Change prod infrastructure, containers, or credentials
  3. Execute domain operations directly (e.g., run a German session) — CoS points to the domain command instead
  4. Any irreversible or state-changing action triggered autonomously by content CoS read (the actual risk in giving it real tool access — see Backend Tool Access below)

Redirect behavior for mutation requests: acknowledge, offer to capture as an action item, name the right actor.

## Backend Tool Access (OpenClaw backend, added 2026-07-12)

The CoS-dedicated OpenClaw instance should have real observational tool access enabled — git, local files, web search — matching the principle above, not the earlier "no tools at all" recommendation. The risk worth designing against isn't tool access itself, it's a *mutating* action triggered autonomously by adversarial content the backend reads (a malicious instruction embedded in a webpage or article causing an unreviewed destructive command) — this is a documented OpenClaw attack class (prompt injection via external content), not a hypothetical. Mitigation: observation stays unrestricted; anything that would mutate state still routes through the same propose-not-execute boundary as the rest of CoS, regardless of what triggered it.

## Memory Store (reality, not the v1.1 plan)

**Naming note:** `cos_memory.md` is mini-moi's *episodic* memory tier, per the three-tier taxonomy (episodic/semantic/procedural) adopted 2026-07-12 — see `design_memory_architecture_direction_2026-07-12.md` for the full mapping and grounding. No implementation change here; naming only.

**`data/cos_memory.md`** — platform-owned, relocated from Guild's domain in Phase 0 (#134). Currently flat markdown with dated `[chat]` entries, a 7,500-char cap, and LLM-judged storage-worthiness via `_maybe_update_memory()`.

**Phase 1 formalizes this format as-is.** The structured/typed/UUID-keyed JSON schema originally planned for v1.1 is retained as a documented future option — worth doing eventually for queryability by Build/Curator Intelligence, but a real migration, not a Phase 1 requirement. Don't conflate "formalize what's running" with "redesign the format" in the same pass.

**Storage criteria (already close to this, worth confirming against real behavior, not assuming):**

CoS decides what gets stored. The rule: **store anything Robert would be annoyed to have to repeat.** Decisions, action items, open questions, meaningful observations — yes. Casual exchanges, domain commands, acknowledgments — no.

These five examples are the acceptance tests — **replay against the real `_maybe_update_memory` behavior, don't assume it already passes them:**

| # | Input | Expected |
|---|---|---|
| 1 | "Curator Tech should start inside Curator, not standalone" | Stored: decision, domain curator |
| 2 | [voice] "Remind me to follow up with Paul at Bennett Moore next week" | Stored: action, with date context |
| 3 | "Should Language Intelligence do German only first, or both languages?" | Stored: question |
| 4 | "thanks, looks good" | Not stored |
| 5 | "!german session" | Not applicable — this bot never receives domain commands (see Routing) |

## Container (containerizing the real process, not a new build)

Currently `chief_of_staff.py` runs as a bare launchd-managed process (both Mac dev and, per Phase 0, EC2 prod). Phase 1 wraps the *existing* process in a container — same code, same Grok backend, same bot tokens.

```yaml
cos:
  build:
    context: .
    dockerfile: docker/Dockerfile.cos
  ports:
    - "8769:8769"
  volumes:
    - ./domains/cos:/app/domains/cos
    - ./data:/app/data
    - ./docs:/app/docs:ro
    - ./build_queue.json:/app/build_queue.json:ro
    - ./agent_logs:/app/agent_logs
  env_file: /opt/minimoi/.env.cos
  restart: unless-stopped
```

**Credential scoping:** dedicated `/opt/minimoi/.env.cos` — Grok API key, `minimoi_cos_bot` token, Postgres access **scoped to the `guild` schema only** (needed for `cos_agenda` — this is a real requirement, not an oversight; least-privilege doesn't mean zero-privilege). No other domain API keys.

**Isolation from personal OpenClaw (#1):** simpler than v1.1 assumed, since CoS was never OpenClaw-based. The only real requirement: nothing in the CoS container touches `~/.openclaw/` or port 18789. Verify, don't assume — see Phase 1 verification.

## Bot Inventory (confirmed 2026-07-11 — final, no cutover needed)

| Bot | Confirmed role | Change needed |
|---|---|---|
| `minimoi_cos_bot` | CoS, prod (already live) | None |
| `minimoi_cos_test_bot` | CoS, Mac dev (already live, confirmed working) | None |
| `@minimoi_agent_bot` | OpenClaw #1, personal — permanent | **None — stays exactly as-is** |
| `@minimoi_cmd_bot` | Drills/commands/German + curator pipeline commands | None |
| `@rvsopenbot` | Guild build-queue notices + Portuguese session summaries + (claimed) curator feedback — a shared relay across domains, not curator-specific as originally assumed | None — protected regardless of exact scope, pending full clarification |
| `minimoi_system_bot` | Daily curator morning briefing (confirmed via live observation) | None |
| `minimoi_opts_bot`, remaining `_test_bot`s | Unresolved | Low priority — grep when convenient, not blocking |

## Voice Notes

Telegram voice → Whisper transcription → CoS processes → stores raw + summary → decisions/actions extracted to `cos_memory.md`. Platform-owned, not agent-owned.

**Build decision required (Claude Code, day 1):** Whisper local (whisper.cpp on EC2 CPU — free, slower) vs OpenAI Whisper API (fast, ~$0.006/min). Recommendation: API for Phase 1 speed-to-value; revisit local when the Mac mini lands. Record the choice in the build log.

## Agent Logs — see mini-moi Portable Archive (above) for full structure

**Directory:** `agent_logs/`, organized per-agent (subdirectories, not flat-prefixed files — see Portable Archive section). Prod-canonical (Data Flow Model), dual-synced to private GitHub + S3.

| Actor | Directory | Trigger |
|---|---|---|
| Claude.ai | `agent_logs/claude_ai/` | "write to log" or end of session |
| Claude Code | `agent_logs/claude_code/` | End of every session automatically — **unverified, audit pending** |
| Grok (review sessions) | `agent_logs/grok_review/` | "write to log" at end of review |
| CoS (chief_of_staff.py) | via `cos_memory.md`, separate store — see Data Flow Model rule 4 for why this isn't merged into agent_logs | Continuous |
| Robert | `agent_logs/robert/` | Manual via Telegram voice note |

No structure required within each actor's files. Timestamp, what happened, what matters. Two files on Claude.ai conversation reset: session log + operational handoff (already in practice).

## CoS View in Guild

New read-only section in Guild: decisions feed, actions feed, recent agent_logs entries. CoS writes, Guild displays. Guild Build tab unchanged. (Tab vs section: builder's choice, note in build log.)

## Backend Swappability — Phase 1 Exit Criterion (corrected 2026-07-11 — two-layer model)

**Two layers, not one — this is the corrected mental model, replacing the earlier "OpenClaw vs. Grok as competing whole agents" framing:**

- **Coordination layer (`chief_of_staff.py`) — stable, platform-owned, not swapped.** Routing, scope enforcement, memory read/write, tool calls. This is mini-moi's own code and stays constant regardless of which model powers it.
- **Backend layer — the actual swap point.** Whichever model generates the reasoning inside that stable shell. Grok today; will rotate to other models over time as the field moves — this is the routine, frequent swap, not a rare architectural event.

The platform must work identically regardless of which backend sits behind the coordination layer's single call boundary — not regardless of which whole framework runs CoS.

- **`config/cos_interface.md` needs a recheck against real `chief_of_staff.py` internals before Phase 1 build** — verify the contract describes the coordination-layer/backend boundary correctly (a clean `call_backend(prompt, context, tools) → response` interface), not a whole-agent-replacement contract. **This is the single remaining blocker before build starts.**
- **Phase 1 exit test: swap the backend, not the coordination layer.** Implement the backend call as its own module (`backends/grok_backend.py`, current); prove the boundary is clean by also implementing a second backend (a different model — Gemini or Claude are the natural next candidates, since that's the swap that will actually recur) and confirming equivalent platform state through the same coordination code.
- **OpenClaw's role:** one possible backend option, architecturally supported by the same boundary — not a Phase 1 requirement to build or test. The interface should be clean enough to support it later if it becomes the right choice, without needing to prove that today.

## Definition of Done

This spec's DoD is phase-gated — see **Phase 1 Definition of Done** and **Phase 2 Definition of Done** below for the checklists. Phase 3 DoD is written with the Phase 3 spec, per the phasing table above. Spec-level done means: Phase 1 DoD passed, Phase 1 post-build verification passed, and Robert has approved moving to Phase 2.

## Phase 1 Deliverables (added 2026-07-12, per Grok review — scannable summary, detail in Build Tasks below)

1. `call_backend(prompt, context, tool_policy) → response` — the coordination-layer/backend boundary itself, extracted as a clean abstraction
2. `grok_backend.py` — mostly extraction/wrapping of what already runs; low risk
3. `openclaw_backend.py` — new: WebSocket client to a dedicated, CoS-scoped OpenClaw Gateway instance
4. Updated containerization for the full CoS service (see Rollback / Production Safety below — this is live in prod today)
5. Updated `cos_interface.md` (done — v0.2) + the conformance test proving mid-conversation backend swap

## Rollback / Production Safety (added 2026-07-12, per Grok review)

CoS is live in production today — real conversations, real memory accumulating. Containerization must not risk that.

- **Parallel, not in-place:** build and test the containerized version on a different port alongside the existing bare-process deployment. Don't stop the running process until the container passes full Phase 1 DoD.
- **Cutover only after verification:** switch `minimoi_cos_bot`'s polling to the container only once the containerized version has passed the conformance test and all Phase 1 post-build verification steps against a **copy** of `cos_memory.md`, not the live file.
- **Backup before touching anything:** snapshot `data/cos_memory.md` before any migration or container cutover step — this is exactly what `sync_private.sh` already does; run it explicitly as a pre-cutover step, not just on its normal schedule.
- **Quick revert path, documented before cutover starts:** `docker stop` the container, restart the bare `chief_of_staff.py` process from its last-known-good state. This should be a single documented command, not something worked out under pressure if the container misbehaves.

## Phase 1 Build Tasks (Claude Code)

1. Pre-build: verify personal OpenClaw #1's mount points and port; confirm nothing in scope touches `~/.openclaw/` or 18789
2. Recheck `config/cos_interface.md` against real `chief_of_staff.py` internals (`_chat`, `_append_memory`, `_maybe_update_memory`, tool calls) — adapt the contract or adapt the code so they match. **Do this before anything else below.**
3. Confirm tool calls (ops status, ops log, domain health) are read-only; confirm `guild.cos_agenda` is never auto-executed
4. Create `/opt/minimoi/.env.cos` — least-privilege, scoped to `guild` schema Postgres access + Grok API key + `minimoi_cos_bot` token, values from SSM where applicable
5. `docker/Dockerfile.cos` + compose service (containerize the existing `chief_of_staff.py` process, above)
6. Build scope enforcement — the observe/mutate boundary and the four mutation classes; genuinely new, unbuilt work. Observation stays unrestricted — nothing to build there beyond ensuring access exists.
7. Whisper decision + wiring (API recommended)
8. Extract the backend call into its own module (`backends/grok_backend.py`) behind a clean `call_backend()` boundary
9. Implement OpenClaw as the second backend behind the same boundary — a dedicated instance (own port, own config dir, isolated from personal OpenClaw #1), called by `chief_of_staff.py` for its conversational quality and memory continuity. This is the real backend choice going forward, not just an interface test. OpenClaw's own memory stays its own — captured separately via `agent_logs/openclaw/` (Portable Archive), never merged into `cos_memory.md`.
10. A/B harness: replay the five storage acceptance examples through both backends, same coordination code
11. Guild CoS view (read-only)
12. Reorganize `agent_logs/` into per-agent directories (mini-moi Portable Archive); confirm auto-write is actually firing per-actor
13. Commit this spec to `docs/specs/`, `cos_interface.md` to `config/`, design docs to `docs/design/`, session logs to `agent_logs/`; update `build_queue.json`; run `sync_docs.sh`

All work dev-first. No ECR push without Robert's explicit approval.

## Phase 1 Definition of Done

- [ ] `cos_interface.md` rechecked and accurate to real `chief_of_staff.py` behavior (or code adapted to match)
- [ ] Tool calls confirmed read-only; `cos_agenda` confirmed manual-only
- [ ] CoS container running, containerizing the existing process — isolated from personal OpenClaw #1
- [ ] `minimoi_cos_bot` / `minimoi_cos_test_bot` unaffected in behavior — same bot, same conversations, now containerized
- [ ] Text message + voice note → CoS response → memory write, unchanged from current behavior
- [ ] `cos_memory.md` accumulating entries as before
- [ ] All five storage acceptance examples pass against real behavior (not assumed)
- [ ] Scope enforcement: all four mutation classes redirected correctly; observation (git status/log, health, web search) confirmed genuinely unrestricted — both halves verified, not just the redirect half
- [ ] CoS view in Guild showing decisions + actions
- [ ] `agent_logs/` reorganized per-agent, receiving entries, dual-synced (GitHub + S3)
- [ ] Backend-swap test passed: same coordination code, two different backends (Grok + one other), equivalent platform state
- [ ] `config/cos_interface.md` committed and accurate to what was built
- [ ] Robert confirms: CoS behaves exactly as it did before containerization — nothing regressed

## Phase 1 Post-Build Verification

Run after deploy, before declaring done:

1. **Isolation:** send a message to `@minimoi_agent_bot` (OpenClaw #1) → confirm zero writes to `data/cos_memory.md`. Confirm `~/.openclaw/` mtimes untouched by CoS traffic. Ports 18789 and the CoS container port both bound, no conflicts.
2. **Credential scope:** `docker exec` into CoS container, dump env → confirm no credentials beyond Grok API key, `minimoi_cos_bot` token, and `guild`-scoped Postgres access.
3. **Behavioral regression:** send the same test conversations pre- and post-containerization → identical responses, identical memory writes.
4. **Storage acceptance:** replay the five examples end-to-end via `minimoi_cos_test_bot`; verify entries (or absence) in `cos_memory.md`.
5. **Voice path:** send a voice note → transcript stored, action extracted.
6. **Persistence:** `docker restart` the CoS container → memory intact, conversation resumes, no re-auth required.
7. **Backup:** confirm `agent_logs/` and `data/cos_memory.md` present in both the private-GitHub and S3 archive.
8. **Prod regression:** all four existing domains healthy (Curator, Mein Deutsch, Meu Português, Guild); portal login OK; `@minimoi_agent_bot`, `@minimoi_cmd_bot`, `@rvsopenbot`, `minimoi_system_bot` all unaffected.
9. **Scope probes — both directions:** ask CoS to "push the fix to ECR" and "rotate the Postgres password" → verify redirect, no attempt (mutation). Ask "did last night's sync succeed" and "check git log for recent commits" → verify it actually answers directly, no unnecessary redirect (observation). Both halves matter — an over-restrictive backend fails this test as much as an over-permissive one.

---

# Phase 2 — Local LLM + Build Intelligence

**Begins when Phase 1 gate passes. Timeline indicative, not committed.**

## Infrastructure — start/stop only

**No always-on instance. No nightly run.** (Changed from v1.0.)

| Item | Spec |
|---|---|
| Instance | g4dn.xlarge, spot, started only for evaluation sessions and scheduled runs |
| Storage | 100GB gp3 EBS (~$8/month while live) |
| Idle pattern | Weeks idle → AMI snapshot, delete volume (snapshot ≈ half live EBS cost) |
| Run cadence | 2–3 focused runs/week, 2–3 hours each — not nightly |
| Dev work (non-GPU) | On existing prod EC2 — no new instance |

**Optional zero-cost path first:** if Robert's Mac has ≥16GB unified memory, run the entire model evaluation locally via Ollama (Qwen2.5-14B Q4 needs ~9GB) before provisioning any AWS. AWS is provisioned only for scheduled unattended runs.

**Run reliability (resolved before first run, not during):**
- Every run writes a start marker and a completion marker to `agent_logs/`
- All work writes to volume-mounted files incrementally (idempotent re-runs)
- Telegram digest fires from the completion marker; a missing completion marker triggers an **incomplete-run digest** — silence never means "nothing found"
- Spot interruption → next run resumes from last written state

## Local LLM — Evaluation

**First candidate: Qwen2.5-14B-Instruct (Q4)** — fits the g4dn.xlarge 16GB T4, meaningfully stronger than Llama 3.1 8B on code and long-context tasks.

**Why not Qwen2.5-32B (Grok's pick):** 32B Q4 needs ~20GB VRAM — does not fit a T4. If 14B fails evaluation, that failure is the documented justification for a g5.xlarge (A10G 24GB) and re-running with 32B. Decision recorded, not deferred.

**Fallback/lightweight:** Llama 3.1 8B or Qwen2.5-7B for fast simple queries.

**One model at a time during evaluation** — per-task model profiles are a Phase 3+ optimization.

**Evaluation tasks (pass all three → proceed; fail any → next candidate or hardware step-up):**
1. Spec/code consistency: correctly identifies specs with no corresponding implementation (against a hand-verified answer key)
2. Retrieval: given a question, returns the relevant curator archive pointers
3. Summarization: produces a usable session context brief from a language transcript

## Two-Stage Processing — hardened

```
Stage 1 — Local LLM:
  Reads raw data (code, specs, articles, transcripts)
  Produces a structured INDEX: entries with tags, one-line
  summaries, and a POINTER to the raw source (path + span)
  The index is a map, never a replacement

Stage 2 — Cloud LLM (Claude or Grok):
  Reads the index, reasons and synthesizes
  May request raw excerpts via pointers when the index
  is insufficient — structured retrieval, not lossy compression
```

Compression targets (~5M tokens → ~50K index) are working estimates to be validated in evaluation, not commitments.

## Build Intelligence — a Build domain service

**Owner: Build domain.** CoS calls it; CoS does not own it. This is the future Master Craftsman capability; the rename waits for #117.

**Approach: deterministic first, LLM assist second.** "List specs with no corresponding code" is mostly parsing and set-diff — spec numbers extracted from `docs/specs/`, references grepped from code and `build_queue.json`, sets compared. The LLM assists on fuzzy matching and explanation, and reads results into briefs. This keeps the verifier trustworthy at 14B.

**Capabilities:**
- Spec vs code consistency; dead code detection; drift detection (spec marked done, code doesn't match)
- Regression test execution and reporting
- Verifier role: Claude Code builds, Build Intelligence verifies independently
- **Session start brief** — before every Claude Code session, produce the standing-rules brief (dev-first/no-ECR-push rule, port map, known trap list e.g. #75 container names, volume mounts before force-recreate, SSH ranges). Delivered manually in Phase 2, automatically in Phase 3.

**First scheduled run contains exactly one job:** the spec/code consistency check, plus digest. Expand only after it proves useful (Grok's "start simple," adopted).

## Phase 2 Build Tasks (Claude Code)

1. (If local Mac eval done) provision g4dn.xlarge spot from launch template; else provision for eval
2. Ollama install; Qwen2.5-14B Q4 pulled; fallback model pulled
3. Deterministic spec/code consistency tooling + hand-verified answer key
4. Indexing pipeline: codebase + specs + agent_logs → pointer-index
5. Run harness: start marker, incremental writes, completion marker, digest, incomplete-run digest
6. Start/stop automation (EventBridge or manual script) for scheduled runs
7. Evaluation report: candidate results against the three tasks
8. AMI snapshot procedure documented and tested once

## Phase 2 Definition of Done

- [ ] Evaluation complete: ≥2 candidates compared against the three tasks, results written to agent_logs
- [ ] Model recommendation documented (continue / step up hardware)
- [ ] Consistency check passes against the answer key
- [ ] Pointer-index built for codebase + specs; spot-checked pointers resolve
- [ ] First scheduled run completed end-to-end with completion marker + digest
- [ ] Interrupted-run behavior tested (kill mid-run → incomplete digest → clean resume)
- [ ] Monthly cost tracking to date recorded (target: ≤$20 POC month)
- [ ] Claude Code session brief produced and used in a real session

## Phase 2 Post-Build Verification

1. Stop instance → confirm compute billing stops; volume cost only
2. Restart from stopped → run completes without manual fixes
3. Simulate spot interruption mid-run → incomplete-run digest received; next run resumes cleanly
4. Pointer audit: sample 10 index entries → all pointers resolve to correct raw spans
5. Consistency check false-positive review: every flagged spec manually confirmed
6. Digest arrives on Telegram; absence of digest is detectable (incomplete-run path tested)
7. Prod untouched: no changes on prod EC2 beyond Phase 1 footprint

---

# Phase 3 — Curator Intelligence, Language Intelligence, Full Wiring

**Design altitude only. Detailed spec written after Phase 2 is stable — deliberately not overspecified now ("let it emerge from use").**

**Curator Intelligence** (Curator domain service): reads full archive (214+ editions, saves, dives, Leanings, history). Retrospective synthesis, gap detection, connection surfacing, adaptive focus. Open and conversational posture. Invoked through CoS via two-stage pattern.

**Language Intelligence** (Language domain service, German + Portuguese unified): reads transcripts, corrections, Lesen/Leitura, Schreiben/Escrita, Wörter/Palavras. Session continuity, error pattern tracking, cross-surface connections (read → speak), suggested-next. Hybrid posture. Whether German ships first or both together: decided at Phase 3 spec time.

**Full wiring:** CoS routes natural-language queries to Build / Curator / Language Intelligence + `agent_logs/` + `cos_memory.md`. Build Intelligence session brief delivered automatically at Claude Code session open. Scheduled runs expand to cover active domains — only jobs that have individually proven their digest value.

Phase 3 DoD and verification: written with the Phase 3 spec.

---

## Hardware Path & Month-3 Decision Gate

**Endpoint: local.** A Mac mini–class machine with sufficient unified memory replaces the AWS instance entirely — scheduled runs, indexing, always-on CoS-adjacent services, at electricity cost. Local-first made literal.

**Target class:** M4 Pro 48GB (273GB/s bandwidth; runs 32B-class quantized comfortably) or M5 successor.

**Why not now:** memory shortage has cut configurations and raised prices (M4 Pro capped at 48GB, base price hikes June 2026), wait times run weeks to months, and the M5 refresh may land late 2026. Buying at month 3 means real usage data and possibly a better market.

**Gate criteria (evaluate at month 3):**
- Intelligence layer used weekly and digests acted on? (attention test)
- Cloud spend trajectory vs ~$2K one-time (payback math)
- Model tier actually needed (14B sufficient → cheaper config qualifies)
- Market: M5 availability, shortage pricing

Until the gate: cloud start/stop only. If the layer doesn't prove out, total sunk cost is tens of dollars.

---

## Cost Model (replaces v1.0)

```
Phase 1 (CoS container):           $0 — runs on existing prod EC2

Phase 2 POC month:
  Spot compute (~10 × 4hr × $0.16)   ~$6.50
  EBS 100GB gp3                       ~$8
  Whisper API (light use)             ~$1
  Total                               ~$15–20

Steady state (months 2–3):
  2–3 runs/week × 2–3hr spot          ~$5–8/month
  EBS or snapshot                     ~$4–8/month
  Total                               <$15/month, ≈$0 in idle months

Ceiling: any month tracking >$25 triggers a review before continuing.
```

No always-on instances. No nightly runs. Local Mac evaluation path costs $0.

---

## Open Questions

**Resolved this session (2026-07-11 through 2026-07-12):**

- **SSH Mac→EC2.** Root cause was a local SSH key mismatch (`id_rsa` assumed, `id_ed25519` actual), not infrastructure. Two-path sync locked: `sync_docs.sh` (GitHub, public/shareable) and `sync_private.sh` (direct SSH, never touches GitHub).
- **Doc-sync-to-prod gap.** Fixed SSH restores manual `sync_docs.sh` — deliberately not automated; weekly cadence doesn't justify polling infrastructure. Event-driven webhook noted as a future option if cadence increases.
- **Dev/prod design-history consolidation.** Full rule set in Data Flow Model section above.
- **Bot inventory.** All 9 bots identified via BotFather + code grep. No cutover needed anywhere — see Bot Inventory table above.
- **Primary CoS agent identity.** Grok/`chief_of_staff.py`, proven and already running — corrected from v1.1's OpenClaw-first assumption. See Change Log.
- **`cos_interface.md` recheck against real `chief_of_staff.py` internals — done.** v0.2 finalized, Grok-reviewed same day. No blocker remains.
- **Scope enforcement principle** — corrected to observe/mutate; CoS confirmed as right-hand advisor, never domain executor.

**No remaining blockers to Phase 1 build.**

**Still pending — doesn't block Phase 1, tracked for completion:**

1. Confirm `data/` is `.gitignore`'d as the structural safety net for `sync_private.sh` content
2. Confirm spec #122 (three-tier backup) is actually running on EC2, not just designed, and covers the full private-data path list
3. Confirm `agent_logs/` auto-write is actually firing per-actor (audit); add `agent_logs/` to `sync_private_repo.sh`'s `SYNC_PATHS` (currently missing)
4. Confirm S3 bucket for the mini-moi Portable Archive exists (per #122) and has versioning enabled
5. `@rvsopenbot`'s exact scope — confirmed as a multi-domain relay, not curator-specific as first assumed. Protected regardless; full scope clarification is low priority.
6. `minimoi_opts_bot` and remaining unidentified `_test_bot`s — low priority
7. Weekly reminder digest cadence — not yet confirmed by Robert

**Low-stakes, builder's discretion:**

8. Robert's Mac unified memory size → decides whether Phase 2 evaluation needs AWS at all
9. CoS view in Guild: tab vs section
10. `agent_logs` retention: keep all forever vs rolling window (default: keep all; revisit at 1GB)
11. Cross-agent code double-check — deliberately deferred. Robert will discuss with CoS once operational, have it draft the proposal, and register it as a new build task then — not scoped here.
12. Semantic memory layer — named gap, not yet built. See `design_memory_architecture_direction_2026-07-12.md`. Direction: emerges from Phase 2's Build/Curator Intelligence work, not a separate project.
13. Front-end / CoS-domain spec — direction agreed 2026-07-11, not yet written. Dedicated session, not folded into #133.

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This spec | `docs/specs/spec_133_intelligence_layer_v1.3_2026-07-12.md` | Claude Code |
| v1.2, v1.1, v1.0 specs | Retained in `docs/specs/`, marked superseded | Claude Code |
| `VISION_mini_moi_intelligence_layer_2026-07-11.md` | `docs/design/` | Claude Code |
| `design_memory_architecture_direction_2026-07-12.md` | **Hold — do not commit yet.** Pending artifact review; may duplicate existing content | — |
| `cos_interface.md` v0.2 (finalized, Grok-reviewed) | `config/cos_interface.md` | Claude Code |
| Session log + handoff + this round's log | `agent_logs/claude_ai/` | Claude Code |
| `build_queue.json` | #133 → spec_ready (v1.3) | Claude Code |
| Sync | `sync_docs.sh` (fixed, no IP argument needed) | Claude Code |

Registration approval: Robert.

---

## Connection to ROADMAP.md

Implements ROADMAP.md Phases 1 and 2. Compound stack mapping: Layer 1 primitives = existing domains; Layer 2 orchestration = CoS (proven, Grok-backed, right-hand advisor not executor); Layer 3 memory = `agent_logs/` (episodic, per-agent, dual-synced) + `cos_memory.md` (episodic, platform) + future semantic layer + future pointer-indexes; Layer 4 self-improvement = scheduled runs + feedback loops, growing only as fast as each loop proves its value.

---

*Spec #133 v1.3 · 2026-07-12 · Claude.ai (Fable 5)*
*Reviews incorporated: Grok 2026-07-10 and 2026-07-12 · Claude frontier 2026-07-10 · Robert (sequencing, cost, data architecture, Phase 1 reality-check, ways-of-working)*
*Status: Spec Ready — no remaining blockers, ready for Phase 1 build*
