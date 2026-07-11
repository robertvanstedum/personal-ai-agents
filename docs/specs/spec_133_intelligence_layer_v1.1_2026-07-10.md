# Spec #133 v1.1: mini-moi Intelligence Layer
## CoS Domain + OpenClaw #2 + Memory + Local LLM

**File:** `spec_133_intelligence_layer_v1.1_2026-07-10.md`
**Status:** Spec Ready — reviews complete, approved for build
**Date:** 2026-07-10
**Build queue:** #133
**Author:** Claude.ai design session (Fable 5)
**Reviews:** Grok (2026-07-10, incorporated) · Claude frontier review (2026-07-10, incorporated) · Robert (resequencing + cost model approved)

---

## Supersedes / Incorporates

| Document | Status |
|---|---|
| `spec_133_intelligence_layer_2026-07-10.md` (v1.0) | **Superseded by this version** |
| `design_cos_consolidated_v1.1_2026-07-06.md` | Superseded |
| `spec_cos_v0_2026-07-10.md` | Superseded |
| `design_memory_intelligence_layer_v3_2026-07-05.md` | Superseded |
| `design_cos_phase1_2026-07-05.md` (all versions) | Superseded |
| `CoS_Test_Instance_Setup.md` | Superseded |
| `design_curator_tech_domain_2026-07-10.md` | Referenced — Curator domain concern |
| `spec_117_guild_navigation_redesign_2026-07-04.md` | Referenced — Guild split deferred |
| `config/cos_interface.md` | **Companion document** — required before Phase 1 build |

---

## Change Log — v1.0 → v1.1

1. **Resequenced per Grok review + Robert's direction ("sequencing > timeline").** Phase 1 (CoS + memory capture) is this week's only committed deliverable. Phase 2 (local LLM + Build Intelligence) follows when Phase 1 is stable. Phase 3 (Curator + Language Intelligence + full wiring) follows Phase 2. Day-by-day calendar removed.
2. **Build Intelligence reassigned.** It is a standalone service in the Build domain that CoS calls — not owned by CoS. Resolves the Master Craftsman overlap and keeps principle #4 honest.
3. **cos_agent_grok.py restored to spec body** as the committed permanent CoS agent #2 (was Open Question in v1.0).
4. **Model decision updated.** First evaluation candidate is Qwen2.5-14B-Instruct (Q4), not Llama 3.1 8B. Grok's Qwen2.5-32B recommendation does not fit the specced g4dn.xlarge (16GB T4 VRAM; 32B Q4 needs ~20GB). Decision path recorded below.
5. **Cost model rebuilt around start/stop.** No always-on t3.medium. No nightly run. Spot instance started only for scheduled/on-demand runs. POC month ~$15–20; steady state under $15/month; near zero in idle months.
6. **Hardware path added.** Month-3 decision gate: Mac mini (M4 Pro 48GB class) as permanent local home if the intelligence layer proves its value. Optional zero-cost model evaluation on local Mac via Ollama if RAM permits.
7. **Two-stage processing hardened.** Stage 1 index is a map-with-pointers, not a lossy summary. Spec/code consistency is deterministic tooling with LLM assist, not pure LLM.
8. **Operational gaps closed.** Memory storage criteria with worked acceptance examples; scope enforcement defined; dedicated `.env.cos` (least privilege); spot run completion marker + incomplete-run digest; checkpoint pattern resolved before first run, not during; agent_logs explicitly added to spec #122 backup scope; Whisper transcription decision flagged for Phase 1 build.
9. **Post-build verification checklists added** for Phase 1 and Phase 2.

---

## Intent

mini-moi is a process not a product. It is learning by doing, with reinforcement over time — seeing history, evaluating decisions, improving future choices across code, career, language, and life.

Today mini-moi has four live domains but no persistent intelligence layer connecting them. Design conversations happen in Claude.ai and are forgotten. Build sessions in Claude Code repeat the same mistakes. CoS exists as a title in Guild with no real capability. The German and Portuguese sessions start cold every time.

This spec builds the intelligence layer that makes mini-moi compound over time. Three sequenced phases, each proven stable before the next begins. Sequencing governs; the timeline is indicative only.

---

## What Is Not Changing

- Guild domain: untouched (except new read-only CoS view)
- Build domain: untouched, no rename (Master Builder / Master Craftsman rename deferred to #117)
- German domain: untouched until Phase 3
- Portuguese domain: untouched until Phase 3
- Prod EC2 instance: no changes except agent_logs volume mount and CoS container
- Personal OpenClaw (#1): completely untouched — verified, not assumed (see Phase 1 verification)

---

## Architecture Principles (locked)

1. **JSON-first** — JSON is source of truth, Postgres is rebuildable projection
2. **Agent-agnostic memory** — memory belongs to the platform, not to any one agent
3. **Two-stage processing** — local LLM indexes/structures, cloud LLM reasons/synthesizes; the index is a map with pointers to raw sources, never a lossy replacement for them
4. **Domains stand alone** — CoS executes against domains, domains don't depend on CoS; intelligence services live in their domains, CoS calls them
5. **Reuse and extend** — one model serves all intelligence domains during evaluation; per-task model profiles are a Phase 3+ optimization, considered only with usage data
6. **Store everything, tag lightly** — let future intelligence decide what's relevant
7. **No vendor lock-in** — OpenClaw is swappable, LLM is swappable, cloud provider is swappable, and the hardware path ends local
8. **Spend follows attention** — compute exists only while it is doing something Robert asked for; no always-on inference infrastructure

---

## Phasing

| Phase | Focus | Gate to next phase |
|---|---|---|
| **Phase 1 (this week, committed)** | CoS domain + OpenClaw #2 + memory capture + agent_logs | Phase 1 DoD + verification passed; one week of stable daily use |
| **Phase 2 (next)** | Local LLM infrastructure (start/stop) + Build Intelligence | First evaluation model passes/fails documented; scheduled runs stable for two weeks |
| **Phase 3 (after Phase 2 stable)** | Curator Intelligence + Language Intelligence + CoS routing to all domains | — |
| **Month-3 decision gate** | Hardware: Mac mini as permanent local home vs continue cloud | Decided with real usage + cost data |

---

# Phase 1 — CoS Domain + OpenClaw #2

**Committed for this week. Faster is fine. Nothing else is committed this week.**

## The Experience

Robert opens Telegram and talks to `@minimoi_agent_bot` naturally. No prefix, no commands, no friction. CoS is the default handler for everything that isn't a domain command.

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

## Routing

```
Message arrives on @minimoi_agent_bot
  ├── !german  → German domain
  ├── !curator → Curator domain
  ├── !cos     → CoS explicit (testing/verification only)
  └── Everything else → CoS by default
```

`@minimoi_cmd_bot` routing is unchanged.

## CoS Role — Cabinet Leader

Not a note-taker. Not a task tracker. Not an intelligence orchestrator (Phase 1). The conversational entry point and memory capture layer:

- Responds as a strategic thinking partner
- Writes decisions, actions, notes, and open questions naturally — not prompted
- Proposes handoffs, does not execute them (Phase 1 limit)
- Reads all domains, writes only to CoS stores (`cos_memory.json`, agent_logs)
- Does **not** own Build/Curator/Language Intelligence — those are domain services it will call in later phases

## Scope Enforcement — Defined

Since everything non-command routes to CoS, "out of scope" must be explicit. CoS must redirect (not attempt) requests to:

1. Modify, deploy, or push code (→ "That's a Claude Code task — want me to log it as an action?")
2. Change prod infrastructure, containers, or credentials
3. Execute domain operations directly (e.g., run a German session) — CoS points to the domain command
4. Anything requiring git access

Redirect behavior: acknowledge, offer to capture as an action item, name the right actor.

## Memory Store

**`/opt/minimoi/openclaw/cos_memory.json`** — platform-owned, agent-agnostic.

```json
{
  "version": "1.0",
  "entries": [
    {
      "id": "uuid-v4",
      "timestamp": "2026-07-10T14:23:00Z",
      "actor": "robert",
      "channel": "telegram_text",
      "type": "note",
      "domain": "cross-domain",
      "content": "raw message or transcript",
      "summary": "CoS-generated one liner",
      "tags": ["minimoi", "curator-tech"],
      "linked_to": [],
      "stored_by": "cos-v0"
    }
  ]
}
```

Entry types: `note`, `decision`, `action`, `question`, `observation`, `conversation`

### Storage Criteria — with acceptance examples

CoS decides what gets stored. The rule: **store anything Robert would be annoyed to have to repeat.** Decisions, action items, open questions, meaningful observations — yes. Casual exchanges, domain commands, acknowledgments — no.

These five examples are the acceptance tests for storage behavior:

| # | Input | Expected |
|---|---|---|
| 1 | "Curator Tech should start inside Curator, not standalone" | Stored: `decision`, domain `curator` |
| 2 | [voice] "Remind me to follow up with Paul at Bennett Moore next week" | Stored: `action`, with date tag |
| 3 | "Should Language Intelligence do German only first, or both languages?" | Stored: `question` |
| 4 | "thanks, looks good" | Not stored |
| 5 | "!german session" | Not stored by CoS — routed to German domain |

## Container

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
    - /opt/minimoi/docs:/app/docs:ro
    - /opt/minimoi/build_queue.json:/app/build_queue.json:ro
    - /opt/minimoi/agent_logs:/app/agent_logs
  env_file: /opt/minimoi/.env.cos
  restart: unless-stopped
```

**Credential scoping (changed from v1.0):** the container uses a dedicated `/opt/minimoi/.env.cos` containing only what CoS needs — Telegram bot token, transcription access, its own paths. It does **not** receive the full prod `.env` (no Postgres credentials, no domain API keys). Least privilege; consistent with the June credential-hygiene work.

**Isolation from personal OpenClaw (#1):**

| | Personal OpenClaw (#1) | mini-moi CoS (#2) |
|---|---|---|
| Memory | `~/.openclaw/` | `/opt/minimoi/.openclaw-cos/` (container volume) |
| Port | 18789 | 18889 |
| Telegram | Personal bot | `@minimoi_agent_bot` default handler |
| Scope | Everything | mini-moi only, enforced by container + env scoping |

Pre-build check: confirm personal OpenClaw mounts nothing under `/opt/minimoi/` (that path holds `cos_memory.json`). Isolation is verified, not assumed — see verification checklist.

## Voice Notes

Telegram voice → Whisper transcription → CoS processes → stores raw + summary → decisions/actions extracted to `cos_memory.json`. Platform-owned, not agent-owned.

**Build decision required (Claude Code, day 1):** Whisper local (whisper.cpp on EC2 CPU — free, slower) vs OpenAI Whisper API (fast, ~$0.006/min). Recommendation: API for Phase 1 speed-to-value; revisit local when the Mac mini lands. Record the choice in the build log.

## Agent Logs

**Directory:** `/opt/minimoi/agent_logs/` — volume-mounted, not in git, **explicitly added to spec #122 three-tier backup scope and to `backup_local.sh`.**

**Filename pattern:** `{actor}_{YYYY-MM-DD}_{HHMM}.md`

| Actor | Prefix | Trigger |
|---|---|---|
| Claude.ai | `claude_ai_` | "write to log" or end of session |
| Claude Code | `claude_code_` | End of every session automatically |
| Grok | `grok_` | "write to log" at end of review |
| OpenClaw CoS | `openclaw_` | Continuous |
| Robert | `robert_` | Manual via Telegram voice note |

No structure required. Timestamp, actor, what happened, what matters. Two files on Claude.ai conversation reset: session log + operational handoff (already in practice).

## CoS View in Guild

New read-only section in Guild: decisions feed, actions feed, recent agent_logs entries. CoS writes, Guild displays. Guild Build tab unchanged. (Tab vs section: builder's choice, note in build log.)

## Agent Swappability — Phase 1 Exit Criterion

The platform must work identically regardless of which agent sits behind the CoS interface.

- **`config/cos_interface.md` is written before build starts** (companion draft ships with this spec). Contract surface: `handle_message`, `store_entry`, `query_memory`, plus the rule that all state lives in platform files (cos_memory.json, agent_logs), never in the agent.
- **A/B test mechanism:** a thin test harness feeds the same message payloads to OpenClaw #2 and to a skeleton `cos_agent_grok.py` (see below); both must produce valid `cos_memory.json` entries per the storage criteria. The skeleton doubles as the A/B baseline — no Cowork adapter needed.
- **cos_agent_grok.py is a committed deliverable, not an open question:** lightweight pure-Python agent on the Grok API, no Anthropic dependency, permanent CoS agent #2. Skeleton (interface-conformant, minimal logic) built in Phase 1 for the A/B test; full capability built during/after Phase 0 agent evaluation.

## Phase 1 Build Tasks (Claude Code)

1. Pre-build: verify personal OpenClaw mount points and port; snapshot current prod state
2. Create `/opt/minimoi/agent_logs/` — volume mount, add to `backup_local.sh` and spec #122 scope
3. Create `/opt/minimoi/.env.cos` (least-privilege env; values from SSM where applicable)
4. `docker/Dockerfile.openclaw-cos` + compose service (above)
5. Telegram routing update on `@minimoi_agent_bot` — default → CoS, domain commands unchanged
6. Whisper decision + wiring (API recommended)
7. `cos_memory.json` initialized; write path tested
8. `cos_agent_grok.py` skeleton conforming to `cos_interface.md`
9. A/B harness: replay the five storage acceptance examples against both agents
10. Guild CoS view (read-only)
11. Commit this spec to `docs/specs/`, cos_interface.md to `config/`, design docs to `docs/design/`, session logs to `agent_logs/`; update `build_queue.json`; run `sync_docs.sh`

All work dev-first. No ECR push without Robert's explicit approval.

## Phase 1 Definition of Done

- [ ] OpenClaw #2 container running, isolated from personal instance
- [ ] `@minimoi_agent_bot` default routing → CoS; domain commands unaffected
- [ ] Text message + voice note → CoS response → memory write
- [ ] `cos_memory.json` accumulating valid entries
- [ ] All five storage acceptance examples pass
- [ ] Scope enforcement: all four out-of-scope classes redirected correctly
- [ ] CoS view in Guild showing decisions + actions
- [ ] agent_logs live, receiving entries, included in backup
- [ ] A/B test passed: OpenClaw #2 and cos_agent_grok.py skeleton produce equivalent platform state
- [ ] `config/cos_interface.md` committed and accurate to what was built
- [ ] Robert, after several days of use: "CoS knows what's going on without me telling it"

## Phase 1 Post-Build Verification

Run after deploy, before declaring done:

1. **Isolation:** send a message to the personal bot → confirm zero writes to `cos_memory.json` and `/opt/minimoi/.openclaw-cos/`. Confirm `~/.openclaw/` mtimes untouched by CoS traffic. Ports 18789/18889 both bound, no conflicts.
2. **Credential scope:** `docker exec` into CoS container, dump env → confirm no Postgres credentials, no domain API keys present.
3. **Routing regression:** `!german`, `!curator` commands still reach their domains; `@minimoi_cmd_bot` unaffected.
4. **Storage acceptance:** replay the five examples end-to-end via Telegram; verify entries (or absence) in `cos_memory.json`.
5. **Voice path:** send a voice note → transcript stored, action extracted.
6. **Persistence:** `docker restart` the CoS container → memory intact, conversation resumes, no re-auth required.
7. **Backup:** run `backup_local.sh` → confirm `agent_logs/` and `cos_memory.json` present in backup artifact.
8. **Prod regression:** all four existing domains healthy (Curator, Mein Deutsch, Meu Português, Guild); portal login OK.
9. **Scope probes:** ask CoS to "push the fix to ECR" and "rotate the Postgres password" → verify redirect, no attempt.

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

**Full wiring:** CoS routes natural-language queries to Build / Curator / Language Intelligence + agent_logs + cos_memory.json. Build Intelligence session brief delivered automatically at Claude Code session open. Scheduled runs expand to cover active domains — only jobs that have individually proven their digest value.

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

## Open Questions (reduced from v1.0)

1. Robert's Mac unified memory size → decides whether Phase 2 evaluation needs AWS at all
2. CoS view in Guild: tab vs section (builder's choice, low stakes)
3. agent_logs retention: keep all forever vs rolling window (default: keep all; revisit at 1GB)

Resolved since v1.0: cos_agent_grok.py (committed, Phase 1 skeleton), spot checkpoint pattern (specified above, pre-run), Language Intelligence scope (deferred to Phase 3 spec by design), cos_interface.md (companion draft ships with this spec).

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This spec | `docs/specs/spec_133_intelligence_layer_v1.1_2026-07-10.md` | Claude Code |
| v1.0 spec | Retained in `docs/specs/` marked superseded | Claude Code |
| `cos_interface.md` draft | `config/cos_interface.md` | Claude Code |
| Grok review + frontier review | `agent_logs/` | Claude Code |
| `build_queue.json` | #133 → spec_ready (v1.1) | Claude Code |
| Sync | `sync_docs.sh 100.57.23.192` | Claude Code |

Registration approval: Robert.

---

## Connection to ROADMAP.md

Implements ROADMAP.md Phases 1 and 2. Compound stack mapping: Layer 1 primitives = existing domains; Layer 2 orchestration = CoS; Layer 3 memory = agent_logs + cos_memory.json + pointer-indexes; Layer 4 self-improvement = scheduled runs + feedback loops, growing only as fast as each loop proves its value.

---

*Spec #133 v1.1 · 2026-07-10 · Claude.ai (Fable 5)*
*Reviews incorporated: Grok 2026-07-10 · Claude frontier 2026-07-10 · Robert (sequencing + cost)*
*Status: Spec Ready — approved for Phase 1 build*
