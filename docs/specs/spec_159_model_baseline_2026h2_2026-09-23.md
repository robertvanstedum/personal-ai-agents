# Spec 159 — Model Baseline 2026 H2: tiers, house models, local-first

- **Prepared:** v1.0, 23 September 2026 (US Central). v0.1 (earlier the same day) was the working draft; v1.0 adds the multi-agent context in §1 and is the version published for review.
- **Decision owner:** Robert van Stedum. **Prepared by:** Claude Code.
- **Status:** Design, v1.0 published for review by Claude.ai, OpenAI (Codex/ChatGPT) and Grok. Robert's decisions D1–D7 (§11) follow the reviews.
- **Build queue:** #159, `design`, high priority.
- **Supersedes:** Spec 125 (Model Name Centralization, 2026-07-05). Spec 125's file inventory, config-file mechanism and regression plan are carried forward here (§9, §10) and updated. Spec 125 is closed as superseded at Robert's direction, 23 Sep 2026.
- **Builds on, does not replace:** [Shared LiteLLM Model Gateway](spec_shared_litellm_model_gateway_2026-08-15.md) (routing, fallback, telemetry), [Spec 146](spec_146_openclaw_cos_gateway_2026-08-09.md) (OpenClaw CoS gateway), and the unmerged Track B branch `feat/cos-guild-model-roles` @ `021a1bb` (model roles for CoS and Guild).
- **Target:** final design over the weekend of 26–27 Sep 2026, implementation the week of 28 Sep, sequenced with the existing backlog. Phase 0 (stopping the personal OpenClaw spend) this week.
- **Companion report:** `_working/model-rebalance_2026-09-23.html` (rate card and matrices, same numbers as §3).

---

## 1. Why a new baseline now

**Context: mini-moi has become a multi-agent system.** Over the past months the platform has grown from single-domain features into coordinated agents:

- multi-agent coordination between Claude Code, Codex, OpenClaw and Grok (sentinels, handoffs, hourly checks, Telegram);
- the Chief of Staff (Agent A, in production beta);
- the Master Craftsman;
- the Guild;
- new local Workshop developments;
- Collaboration Rooms (Spec 158).

Each of these adds model calls: background loops, heartbeats, handoff checks, reviews and second opinions. Many carry large contexts and run whether or not Robert is at the keyboard. Cost now grows with the number of agents and how often they talk to each other, not with Robert's own use. The model choices behind all this were made when xAI's cheap tier existed. They need resetting **now**, before Rooms and CoS scale further, not after.

The model market has also moved faster than mini-moi's configuration. Evidence gathered on 23 Sep 2026:

1. **xAI's cheap tier no longer exists.** `grok-4-1-fast-reasoning`, `grok-4-1-fast-non-reasoning`, `grok-4-0709`, `grok-3` and other models were retired on 15 May 2026. Their IDs still resolve, but they redirect to `grok-4.3` and bill at $1.25 / $2.50 per 1M tokens, about 6× the old $0.20 / $0.50. Most mini-moi call sites (§10) still use those IDs, so costs rose without any code change or warning.
2. **Unattributed xAI spend.** Robert saw xAI charges while not using mini-moi. They came from his personal OpenClaw: about $25.70 of `grok-4.6` usage logged 18–23 Sep, plus about 3.5M `grok-4-1-fast` tokens that OpenClaw logged at **$0** because it has no price for a retired ID. The main recurring cause is scheduled jobs (hourly Claude/Codex check, 30-minute heartbeat) that wake the *main* chat session. Each wake sends a 150–180k-token context to `grok-4.6` at extra-high effort, about $0.30–0.45 per wake, and a failed call can fall back to Opus 4.5.
3. **Hidden model overrides.** The prod curator cron passes `--model=grok-4.3`, but `curator_rss_v2.py` doesn't accept it and silently uses the hardcoded default.
4. **New cheapest option.** OpenAI's `gpt-6-luna` ($0.10 / $0.50) is 10–12× cheaper than grok-4.3 or Claude Haiku 4.5 for small tasks.
5. **Local models run, but aren't used deliberately.** Ollama is running on the Mac with four small models. The dev CoS local route uses `qwen3:4b`, which ignores the no-thinking setting (700 tokens and 55 s for a five-item classification in testing), while `llama3.2:3b` got it right in about 6 s once loaded.
6. **The architecture principle is still unmet.** "Model names never hardcoded in domain functions" (Spec 125) is still broken in about 25 files. Swapping a model means editing code.

This spec sets which models mini-moi uses for the rest of 2026, how those choices are expressed (configuration, not code), and how the local-first direction is measured.

## 2. Principles

- **P1 — Five tiers, each with a job.** Every model call site is assigned a tier (§4). A tier's model can change in configuration without touching domain code.
- **P2 — Local first for the smallest work.** Anything the local tier can do reliably runs locally, on the Mac and in prod (§7). Escalation to cloud is automatic and counted.
- **P3 — One house model family per domain, plus a counterpoint.** When candidate models cost about the same, a domain uses one provider family so its outputs are consistent. Where a second opinion adds value (Curator/Research challenger, Guild review, language correction review), a model from a *different* provider acts as the **counterpoint** (§5).
- **P4 — Configuration, not code.** Domain code asks for a *role* (e.g. `curator.rank`). The role maps to a gateway logical name, and the gateway maps that to a provider model, effort and fallbacks. Provider model IDs appear only in gateway configuration.
- **P5 — No top-tier model in loops or fallbacks.** Scheduled jobs, polling and heartbeats never run on a premium model, and no fallback list escalates to one. Premium models are chosen by hand for a task.
- **P6 — Small, fresh context for background jobs.** Scheduled and background calls start from a fresh, minimal context and never reuse a conversation session.
- **P7 — Know who spent what.** Each tool or runtime has its own provider key (OpenClaw personal, Grok CLI, mini-moi prod, mini-moi dev). Every provider account has a monthly spend cap. The gateway records cost per role.

## 3. Rate card (23 Sep 2026)

Standard tier, USD per 1M tokens, prompts under 200k tokens. xAI roughly doubles its rates at 200k tokens and above. Estimated cost per call for three typical calls:

- **Scheduled check:** 8k in, 0.5k out.
- **Chat turn:** 40k context, 80% cached, 1.5k out.
- **Curator run:** 65k in, 4k out.

| Tier | Model | Provider | Input | Cached | Output | Scheduled check | Chat turn | Curator run |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Local | `llama3.2:3b` (Ollama) | Local | $0 | $0 | $0 | $0 | $0 | not suited |
| Economy | `gpt-6-luna` | OpenAI | 0.10 | 0.01 | 0.50 | 0.0010 | 0.0019 | 0.009 |
| Economy | `claude-haiku-4-5` | Anthropic | 1.00 | 0.10 | 5.00 | 0.0105 | 0.019 | 0.085 |
| Workhorse | `grok-4.3` | xAI | 1.25 | 0.20 | 2.50 | 0.0112 | 0.020 | 0.091 |
| Workhorse | `claude-sonnet-5` | Anthropic | 2.00 | 0.20 | 10.00 | 0.0210 | 0.037 | 0.170 |
| Workhorse | `gpt-6-sol` | OpenAI | 2.00 | 0.20 | 10.00 | 0.0210 | 0.037 | 0.170 |
| Premium | `grok-4.7` | xAI | 2.00 | 0.50 | 6.00 | 0.0190 | 0.041 | 0.154 |
| Premium | `claude-opus-5-5` | Anthropic | 4.00 | 0.20 | 20.00 | 0.0420 | 0.068 | 0.340 |
| Premium | `claude-opus-5` | Anthropic | 5.00 | 0.50 | 25.00 | 0.0525 | 0.094 | 0.425 |
| Premium | `gpt-6-astra` | OpenAI | 10.00 | 1.00 | 50.00 | 0.1050 | 0.187 | 0.850 |
| Premium | `claude-fable-5-1` | Anthropic | 10.00 | 0.25 | 50.00 | 0.1050 | 0.163 | 0.850 |

`grok-4.5` and `grok-4.6` are priced like `grok-4.7` ($2 / $6) and add nothing, so this baseline leaves them out. Reasoning effort adds billed output tokens on every provider. Build and design work in Claude Code runs on Robert's Max plan at no per-token cost and is not covered by this rate card.

## 4. Tiers and gateway logical names

These gateway logical names are what roles point to. Existing route names (`minimoi-cos-agent`, `minimoi-cos-agent-xai-fast`, `minimoi-cos-web-search`, `minimoi-cos-agent-anthropic`, `minimoi-cos-agent-local`) remain as aliases until every consumer has moved.

| Tier | Logical name | Primary | Effort | Fallback | Allowed callers |
|---|---|---|---|---|---|
| T0 No model | — | code check (hash, timestamp, diff) | — | — | Polling, sentinels, file watchers |
| L Local | `mm-local` | Ollama `llama3.2:3b` | no thinking | `mm-economy` | Scheduled jobs, classification, yes/no, short summaries |
| T1 Economy | `mm-economy` | `openai/gpt-6-luna` | none | `xai/grok-4.3` (none) | Scheduled jobs, quick routes, bulk processing |
| T2 Workhorse, xAI | `mm-work-xai` | `xai/grok-4.3` | medium (`-low` variant: low) | `anthropic/claude-sonnet-5` | xAI-house domains |
| T2 Workhorse, Claude | `mm-work-claude` | `anthropic/claude-sonnet-5` | adaptive, medium | `xai/grok-4.3` | Claude-house domains |
| T2 Counterpoint | `mm-counter-xai` / `mm-counter-claude` | the other house's workhorse | medium | none (a failed counterpoint is reported, not substituted) | Challenger and review roles |
| T2 Search | `mm-search` | `xai/grok-4.3` + xAI search | low | `anthropic/claude-sonnet-5` + web search | CoS web search |
| T3 Premium | `mm-premium` | `xai/grok-4.7` | high | none | Manual, interactive only. Refused for scheduled callers. |

Rules:

- No fallback chain contains `mm-premium`.
- The gateway refuses `mm-premium` for any caller whose key is tagged `scheduled`.
- Each call's effort is set explicitly in gateway configuration and never left to a provider default or a retired-ID redirect.

## 5. House models and counterpoint by domain

**How to choose.** If the cost difference between two workhorse models is small at a domain's real volume, consistency and quality decide. If it's large (long, output-heavy conversation), cost decides.

Rough volume check:

- **Curator and Research:** a few large calls a day. Moving between grok-4.3 and Sonnet 5 changes monthly cost by a few dollars, so the choice is about consistency.
- **Language conversation:** many output-heavy turns. At about 50 turns a day, Sonnet 5 would cost roughly $20+/month more than grok-4.3, so cost decides.

| Domain | House family | House roles | Counterpoint | Why |
|---|---|---|---|---|
| **Curator + Research** | **Claude** (`mm-work-claude`) | deep dive, intelligence, feedback, research, observations | **xAI** `mm-counter-xai` (challenger) | Most Curator call sites are already Claude (§10) and the output is written analysis. The challenger pattern already exists (`challenger_config.json`: Claude primary, xAI challenger). Its retired challenger ID becomes grok-4.3. |
| Curator ranking | *decision D2* | daily RSS ranking | — | Today grok (retired ID → 4.3). Option A: keep `mm-work-xai-low` (≈$0.09/run) as an intentional exception. Option B: move to the house `mm-work-claude` (≈$0.17/run, about +$2.50/month) for consistency. Either way, A/B test `gpt-6-luna` first. |
| **German + Portuguese** (parity) | **xAI** (`mm-work-xai`) | conversation, personas, domain features, review router default | **Claude** `mm-counter-claude` (challenger for writing and transcript review) | Output-heavy chat, where grok-4.3's $2.50 output price matters. Keeps the existing challenger shape. Portuguese mirrors German. |
| **CoS** (Agent A, backend, bot) | **xAI** (`mm-work-xai`) | agent, briefings | Claude, via gateway fallback only | Agent A is built on the xAI path, and conversation volume favours grok-4.3. |
| **Guild** | **xAI** (`mm-work-xai`) | dev agent, operations | **Claude** `mm-counter-claude` (challenger, review) | Same house as CoS, since they share agents. Review work gets the counterpoint. |
| Guild watch loops | Local then economy | curator / German / novelty watch | — | T0 check first, then `mm-local`, then `mm-economy`. |
| **All domains, economy work** | Local, then OpenAI `gpt-6-luna` | pre-filters, classification, signal store, quick routes | — | Cheapest by an order of magnitude. There is no economy model in the xAI or Claude houses that competes. |
| **Voice** | unchanged | `gpt-realtime-2.1`, `gpt-4o-transcribe`, `gpt-4o-mini-tts`, `grok-voice-latest`, `grok-transcribe` | — | Priced per minute or per character. Out of scope (§12). |

OpenAI appears here only as the economy tier and in voice. `gpt-6-sol` is priced like Sonnet 5 and isn't a house model anywhere, but it is available as a third opinion if a review panel ever needs one.

## 6. Role map (configuration)

This replaces Spec 125's `config/models.json` design. Roles map to **gateway logical names**, never to provider IDs. The file is volume-mounted on EC2 (as Spec 125 intended), so a model change needs only a config edit and a gateway reload, with no image rebuild.

```json
{
  "_comment": "Spec 159 baseline. Roles -> gateway logical names. Provider model IDs live only in services/model_gateway/litellm*.yaml.",
  "_baseline": "2026-H2",
  "curator":    { "rank": "mm-work-xai-low", "prefilter": "mm-local", "signal": "mm-local",
                  "synthesize": "mm-work-claude", "deep_dive": "mm-work-claude",
                  "feedback": "mm-work-claude", "research": "mm-work-claude",
                  "challenger": "mm-counter-xai" },
  "german":     { "converse": "mm-work-xai", "fast": "mm-economy", "review": "mm-work-xai",
                  "review_panel": ["mm-work-xai", "mm-work-claude", "mm-economy"],
                  "challenger": "mm-counter-claude" },
  "portuguese": { "converse": "mm-work-xai", "fast": "mm-economy", "review": "mm-work-xai",
                  "review_panel": ["mm-work-xai", "mm-work-claude", "mm-economy"],
                  "challenger": "mm-counter-claude" },
  "cos":        { "agent": "mm-work-xai", "quick": "mm-economy", "search": "mm-search",
                  "local": "mm-local" },
  "guild":      { "agent": "mm-work-xai", "watch": "mm-local", "challenger": "mm-counter-claude",
                  "dev": "mm-work-xai" }
}
```

`mm-local` falls back to `mm-economy` in the gateway, so `curator.prefilter` and `curator.signal` work whether or not the prod local tier (§7, D1) is approved. Until an A/B test confirms the local or economy tier matches Haiku's quality, the Curator pre-filter keeps `claude-haiku-4-5` as a temporary named override.

The German/Portuguese review router currently lets the user choose "grok / gpt / claude". The choices map to `review_panel`, so the retired `gpt-4o` and `gpt-4o-mini` and the old Claude IDs disappear from code.

## 7. Local tier: Mac and prod

### 7.1 Mac (dev and personal) — measured 23 Sep

- **Hardware and software:** Ollama 0.34.2, running as a login item. MacBook Air M3, 8 GB RAM, about 12 GB free disk.
- **Installed models:** `qwen3:4b`, `llama3.2:3b`, `qwen3:1.7b`, `gemma3:1b`.
- **Baseline model:** `llama3.2:3b`. It was correct on the classification test, generates at about 33 tokens/s, and takes about 20 s for the first load.
- **Not qwen3:4b** until its thinking output can be reliably turned off.
- **Memory:** set `OLLAMA_KEEP_ALIVE` (for example 30m) so scheduled jobs don't reload the model each time. Keep one model loaded; the machine can't hold more than about 4B parameters.
- **Track B dependency:** Mac launchd Guild agents must reach the gateway. This spec recommends publishing the gateway on a fixed **localhost-only** port for Mac dev. Robert decides (D6).

### 7.2 Prod (EC2) — measured 23 Sep via read-only SSM

- **Instance:** the prod EC2 instance, t3a.medium: 2 vCPU (burstable), 3.85 GB RAM, no GPU.
- **Memory:** about 1.2 GB available with 13 containers running. The model gateway uses about 660 MB and Agent A about 380 MB. 37 GB disk free.
- **Consequence:** even a 1B model (about 1 GB loaded) would push the instance into swap. The prod local tier needs more memory.

| Option | Change | Local model | Approx. added cost | Notes |
|---|---|---|---|---|
| P0 | none | none. `mm-local` → `mm-economy` in prod | $0 | The prod economy work this baseline expects costs cents a day on luna. |
| **P1 (recommended)** | resize to **t3a.large** (8 GB, 2 vCPU) | `gemma3:1b` or `qwen3:1.7b` in an `ollama` container, memory-capped at 2.5 GB | ≈ +$27/month on-demand | CPU-only. Throughput not yet measured; expect well below the Mac. Burstable CPU credits limit sustained use, so this is for small, infrequent tasks only. The resize needs a stop/start (a few minutes of downtime) and is a production change requiring Robert's approval. |
| P2 | resize to t3a.xlarge (16 GB, 4 vCPU) | up to ~4B (`llama3.2:3b`) | ≈ +$82/month | Only if P1's scorecard shows the 1–2B models aren't good enough. |

Instance prices are on-demand us-east-1 list rates and must be confirmed in the AWS console before resizing.

**Honest framing:** P1 costs more per month than the prod cloud economy calls it would replace. The reason to do it is **strategic**: proving local-first in production under real load with real numbers. P1 is a measured trial with an exit: after 30 days, keep it, move to P2, or roll back to P0 based on the scorecard.

**Guardrails for prod local:**

- Memory-capped container.
- Pull only the models this spec names.
- The gateway health check routes around a failed Ollama instance automatically.
- No domain may *require* local; a local failure is never a user-facing error.

### 7.3 Local-first scorecard

The gateway already records usage. Add a weekly summary for every `mm-local` call:

- calls served locally;
- escalations to economy, with the reason (invalid output, timeout, unavailable);
- median latency;
- the cloud cost that was avoided.

Example: "Week 39: 1,480 local, 41 escalated (2.8%), p50 3.1 s, $0 local vs $1.60 luna-equivalent." This is the evidence for moving more work local, or for better hardware later.

## 8. Personal tools

These are outside the repo but part of the baseline, so the spend picture is complete.

| Tool | Setting | Baseline |
|---|---|---|
| OpenClaw (personal) | default model | `xai/grok-4.3`, low effort. Today it's `grok-4.6` at extra high. |
| | fallbacks | `grok-4.20-0309-reasoning` → `claude-sonnet-5` → `ollama/llama3.2:3b`. **Remove Opus 4.5.** |
| | scheduled jobs `d632ed24` (hourly), `4a2aab2a` (heartbeat) | `--session isolated`, `ollama/llama3.2:3b` with luna fallback. Code check first where possible. |
| | model list | Remove retired IDs (`grok-4-1-fast*`, `grok-4`, `grok-4.20-beta-*`). |
| | habit | Start a new main session daily and after each design session. |
| Grok CLI | model / effort | `grok-4.5` medium for routine work, `grok-4.7` high for hard work. Own API key. |
| Claude Code | — | Max plan. First choice for builds, architecture and reviews. |
| All providers | accounts | Monthly spend caps set in each console. One key per tool or runtime. |

The OpenClaw items are local, reversible and outside the repo, so Robert may approve them ahead of this spec (Phase 0).

## 9. Implementation phases (configuration first)

| Phase | Scope | Kind | Gate |
|---|---|---|---|
| 0 | OpenClaw personal config (§8), provider spend caps, per-tool keys | Mac config, consoles | Robert, may run before spec approval |
| 1 | Gateway: add logical names (§4), OpenAI + Ollama providers, explicit effort, premium refusal for scheduled callers, aliases for existing route names. Dev and prod YAML kept identical apart from the local endpoint. | Config | Dev test → reviewed diff → prod |
| 2 | Role map `config/models.json` (§6) + a small shared loader. Replace every provider ID in domain code (§10) with a role lookup. Fix `--model` being ignored in the curator cron. Fix OpenClaw's $0 logging for redirected IDs if configurable. | Code (mechanical) + config | Spec 125's regression plan (below) |
| 3 | Local tier: Mac keep-alive, dev CoS local route → `llama3.2:3b`. Prod P1 only if D1 = P1: resize, `ollama` container, memory cap, health check. | Config + infra | Robert approves prod resize |
| 4 | Guild watch loops: T0 code check before any model call | Code | Short design note first |
| 5 | Scorecard (§7.3) and per-role cost view | Config + small code | — |
| 6 | Quality checks: A/B `gpt-6-luna` vs current for curator ranking, pre-filter and signal store; Sonnet 5 vs Sonnet 4.5/4.6 for Curator synthesis | Evaluation | Results reviewed before switching defaults |

**Regression plan (from Spec 125, still in force for Phase 2):**

1. Record a baseline per domain on dev before changing anything.
2. Apply the changes on dev and re-run the domain checklist: Curator (briefing, observations, deep dive, feedback, enrichment, signal store); German and Portuguese (read, converse, write-correction, drills, reviewer); Guild and CoS (agent query, watch loops).
3. Any error or clear quality drop stops the rollout.
4. An independent review (non-author agent) of the actual diff.
5. Robert approves the prod deploy.
6. After deploy, check that every domain is healthy and the role map is mounted and readable.

Dev follows prod's model map. Dev background jobs are disabled and run only when triggered, which is part of the cost reduction.

## 10. Inventory: hardcoded model IDs (23 Sep 2026)

Found with `grep` over `domains core scripts services minimoi_portal config`, tests excluded. **R** = retired ID, which now bills as grok-4.3. **O** = older generation, which moves to the current equivalent.

| File | Lines | IDs | Flag |
|---|---|---|---|
| `domains/cos/backends/grok_backend.py` | 19, 117 | grok-4-1-fast-reasoning | R |
| `domains/curator/curator_rss_v2.py` | 954, 1699, 1711, 3349, 3356–3358, 3594 | grok-4-1-fast-reasoning, grok-4-1 | R |
| `domains/curator/curator_rss_v2.py` | 584, 629, 775, 824, 903, 942, 2104, 2105 | claude-haiku-4-5, claude-sonnet-4-5, grok-3-mini, claude-sonnet-4 | O |
| `domains/curator/curator_deepdive.py` | 210 | claude-sonnet-4-5 | O |
| `domains/curator/curator_feedback.py` | 299, 1243 | claude-sonnet-4-5 | O |
| `domains/curator/curator_intelligence.py` | 54, 55 | claude-haiku-4-5, claude-sonnet-4-5 | O |
| `domains/curator/curator_utils.py` | 255 | claude-haiku-4-5-20251001 | O |
| `domains/curator/deep_dive.py` | 197 | claude-sonnet-4-6 | O |
| `domains/curator/research_routes.py` | 3095 | claude-sonnet-4-5 | O |
| `domains/curator/signal_store.py` | 347 | grok-3-mini | O |
| `domains/curator/curator_server.py` | 132 | "grok-4-1" display label | cosmetic |
| `domains/german/german_domain.py` | 120, 121, 2247 | grok-4-1-fast, claude-haiku-4-5-20251001, claude-sonnet-4-6 | R/O |
| `domains/german/get_german_session.py` | 85 | claude-haiku-4-5-20251001 | O |
| `domains/german/providers/review_router.py` | 91, 113, 139, 228, 243, 258 | grok-4.3, gpt-4o, claude-sonnet-4-6, gpt-4o-mini | O |
| `domains/german/reviewer.py` | 518 | claude-sonnet-4-6 | O |
| `domains/german/data/config/domain.json` | 9 | claude-sonnet-4-6 | O (config) |
| `domains/portuguese/html_server.py` | 526, 567 | claude-haiku-4-5-20251001 | O |
| `domains/portuguese/review_router.py` | 152, 174, 196, 276, 291, 306 | grok-4.3, gpt-4o, claude-haiku-4-5-20251001, grok-3-mini, gpt-4o-mini | O |
| `domains/guild/agents/dev_agent.py` | 219 | grok-4-1-fast-reasoning | R |
| `domains/guild/agents/loops/cos_curator_watch.py` | 117 | grok-4-1-fast-reasoning | R |
| `domains/guild/agents/loops/cos_german_watch.py` | 168 | grok-4-1-fast-reasoning | R |
| `domains/guild/agents/loops/cos_novelty_watch.py` | 144 | grok-4-1-fast-reasoning | R |
| `domains/guild/agents/loops/cos_job_search.py` | 422, 474, 494 | grok-4-1-fast-reasoning | R (loop disabled) |
| `domains/guild/config/challenger_config.json` | 3, 5 | claude-sonnet-4-6, grok-4-1-fast-reasoning | R/O (config) |
| `domains/guild/services/challenger.py` | 150, 151 | claude-sonnet-4-6, grok-4-1-fast-reasoning | R/O |
| `services/model_gateway/litellm.yaml`, `litellm.prod.yaml` | routes | xai/grok-4, xai/grok-4-1-fast, claude-sonnet-4-6 | R/O (gateway config, allowed) |
| `scripts/operations/run_curator_cron.sh`, `scripts/run_curator_cron_ec2.sh` | curator call | `--model=grok-4.3`, silently ignored | bug |
| `core/realtime_voice/*` | various | voice models | out of scope |

Track B (`021a1bb`) already moves the challenger and CoS/Guild agents onto gateway routes. Phase 2 must rebase onto Track B once it merges, not redo that work.

## 11. Decisions for Robert

| # | Decision | Recommendation |
|---|---|---|
| D1 | Local model in prod: P0 / P1 / P2 (§7.2) | **P1** as a 30-day measured trial |
| D2 | Curator ranking: keep xAI as an exception, or move to the Claude house | Keep grok-4.3 low for now; decide after the Phase 6 A/B |
| D3 | Adopt OpenAI `gpt-6-luna` as the cloud economy tier | Yes, after the Phase 6 check on three real workloads |
| D4 | House families (§5): Curator = Claude; languages, CoS, Guild = xAI; counterpoint = the other house | Adopt |
| D5 | Monthly spend caps per provider account | Set them. Amounts are Robert's call. |
| D6 | Gateway on a fixed localhost-only port for Mac launchd agents (unblocks Track B) | Adopt |
| D7 | Retire `grok-4.5`/`4.6` from the baseline and keep `grok-4.7` as the only xAI premium | Adopt |

## 12. Out of scope

- Voice model selection and pricing (realtime, transcription, TTS): separate review.
- Automatic budget enforcement in the gateway (deferred in the gateway spec). This spec asks only for provider-console caps and premium refusal for scheduled callers.
- Changes to model *behaviour* (prompts, personas). This spec changes which model runs, not what it's asked.
- The interactive model pickers in Claude Code and Codex.

## 13. Questions for reviewers (Claude.ai, OpenAI, Grok)

1. Are the house assignments in §5 sound? Is there a domain where the counterpoint should be the house instead?
2. Is `gpt-6-luna` suitable for the economy roles listed, or should any stay on Haiku or grok-4.3 with no reasoning?
3. Is P1 (t3a.large + a 1–2B CPU model) a credible production local tier, or is the strategic value better proven on the Mac first?
4. Anything in §4's gateway rules (premium refusal, counterpoint without fallback) that conflicts with the gateway spec or Track B?
5. Is anything in the §3 rate card wrong or out of date? Please cite the provider page.

## 14. Sources

- xAI models and pricing — https://docs.x.ai/docs/models (read 23 Sep 2026)
- xAI 15 May 2026 retirement — https://docs.x.ai/developers/migration/may-15-retirement
- OpenAI pricing — https://developers.openai.com/api/docs/pricing ; models — https://developers.openai.com/api/docs/models
- Anthropic pricing — https://www.anthropic.com/pricing (Claude model table cached 2026-06-24 in Claude Code's API reference)
- Measurements: OpenClaw `~/.openclaw/agents/main/agent/openclaw-agent.sqlite` transcript usage (18–23 Sep); Ollama benchmark on the Mac (23 Sep); EC2 `free`/`docker stats` via SSM (23 Sep, read-only).
