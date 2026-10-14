---
title: Private, local and hybrid intelligence in mini-moi
snapshot: October 2026
version: 1.0 (first dated snapshot; next due January 2027)
authors: Claude Chat, Codex
editor in chief: Robert van Stedum
status: standing direction and dated research record; not a purchase, build authorization or change to any release scope
supersedes: MiniMoi-Private-Local-Hybrid-Intelligence-2026-10-14.md (Codex v0.1 brief) and its companion, both folded in
---

# Private, local and hybrid intelligence in mini-moi

*October 2026 snapshot*

## Abstract

mini-moi has had a local model in it on purpose since the beginning: the 2025 experiment ran entirely on a local model, the production architecture keeps a local tier with no external dependency, and the public documents say local capability is retained and expected to matter more as open models and hardware improve. This snapshot restates that direction as it stands in October 2026 and sets it against the field. The direction is: keep the owner's knowledge, working history and artifacts independent of any model vendor; do a substantial share of useful work, including coding, on privately controlled hardware where quality allows; keep two or three strong hosted models for escalation, independent review and a second opinion; set no fixed local/cloud quota; judge by accepted work, privacy, reliability and total cost. In October 2026 the field makes that direction more credible than it was a year ago and still not free: the best open-weight models trail the hosted frontier by about four months on composite measures and by a few points on coding, the gap persists on long-horizon agent work, the cost case for self-hosting depends on utilization, and the practical limit is memory, not model availability. The first substantial local workload in mini-moi is therefore the Vault itself, not coding: a local pass over the owner's own records, judged against a gold set the owner confirms, run on hardware already owned, with nothing private leaving the machine. Coding comparisons follow once that runtime is proven. Hardware is priced only after the evidence exists.

## 1. The direction, restated

### 1.1 What the public documents have said since the start

The repository describes mini-moi as local-first and model-agnostic. The architecture document's first design principle is local-first data: preferences and history remain with the person, not with any one model, provider or machine, in files the application owns. The second principle is model-agnostic operation: model choice rests on cost, task quality and vendor independence, and a test makes a real call to the configured local model on the development Mac so the local path stays exercised.

Local inference is not a slogan there. The 2025 experiment ran entirely on a local Ollama model, for privacy and to test what a local model could do. Curator's research triage runs on a local Gemma 3 1B with a hosted fallback, at zero per-call cost. Mein Deutsch's translation has a three-tier fallback whose last tier is a local model with no external dependency. Curator's article scoring moved to hosted models when their per-run cost became negligible, and the architecture document is explicit that the local option was deliberately retained rather than removed, to be re-verified on suitable hardware. Its own forward statement: local capability is likely to matter more as open models and local hardware improve, including in physical AI. Extending local inference to production needs suitable compute and an operational design; it is a roadmap direction, not a current guarantee.

So the baseline is already set in public: data stays with the owner; models are swappable; local is real, small today, and kept on purpose. The plan also always included a local server, not only a laptop: production and everything outward-facing went to AWS, and a privately controlled server at home remains in scope for the day the evidence justifies it (section 6).

### 1.2 What the owner added in October 2026

Robert's direction of 14 October, restated:

- Private and local operation is one of mini-moi's objectives and stays visible in design decisions.
- A significant share of work should run locally, **including building code**. Local models are not permanently assigned only easy supporting tasks.
- Two or three strong hosted relationships are kept (OpenAI, Anthropic and xAI were the examples) for escalation, collaboration and differing perspectives.
- The share of local work is not the objective. Useful results, affordability and quality are. 60/40, 40/60 or another split is acceptable if the evidence justifies it.
- Work continues across models without losing the project's context or the reasons behind decisions. The Vault provides that continuity; models are replaceable engines.
- Finish the Vault's functionality first. The vision does not become an infrastructure project that delays it.
- Bounded comparisons precede any commitment to a model, vendor or hardware.

In one sentence: mini-moi owns its continuity and supports privately controlled execution wherever it is capable enough, with deliberate access to hosted intelligence when that is better. The owner's context is the lasting asset; models are teammates.

## 2. Three layers that must stay separate

**Knowledge and continuity.** Originals, dated conversations, artifacts, decisions, sources, access rules and recoverable history. This is the Vault, and it is the functional concern that comes first. Its design is in the companion vision document ("Where things live"); nothing here changes it.

**Execution.** A model's ability to retrieve authorized context, reason, read an image, write code, run tools and contribute evidence. This is where local and hosted models are compared, and the only place a comparison means anything.

**Infrastructure.** Model files, memory, storage, inference engines, network boundaries and operations. A separate choice, made after execution evidence exists.

The layers meet through interfaces, and three rules keep them apart. No model is the sole holder of project history. No local model receives every private record merely because it is local. Running a model on a laptop does not make its tools, logging or telemetry local; an encrypted backup in owner-controlled cloud storage is a different thing from disclosing plaintext to a hosted model.

**Privacy precedes model selection.** If material is designated local-only, no quality or cost router may send it to a hosted model. The permitted alternatives are local review, owner review, a disclosed inability to finish, or separately authorized sharing of a suitable excerpt. Summaries and embeddings carry the information too, and are covered by the same rule. The model gateway already exists and is where this is enforced, not in prompts.

## 3. Where the field stands, October 2026

This section is new research for this snapshot. Sources are in the bibliography; the figures are the sources' own and have not been reproduced here.

### 3.1 The gap to the hosted frontier

Epoch AI's estimate, as reported in mid-2026, is that open-weight models have lagged the closed frontier by about four months on average since January 2026, roughly eight points on its composite index. On the June 2026 snapshot the largest measured gap was on a general intelligence index (about eleven points); on coding indexes the gap was about five points; on agentic indexes about four; on fresh code generation and graduate-level science the two were near parity. The same analysis notes that public benchmarks may flatter open models because public test sets are easier to optimize against, and that near-parity on a score does not make products interchangeable: reliability, tool schemas, safety controls and uptime still differ.

The gap persists where mini-moi's build loop lives: long-horizon agent work, error recovery, multi-step tasks in a terminal or repository. The analysis also makes a point this document agrees with: results on those tasks depend on the harness (search, edits, tests, retries) as much as the model, so scores mix the two.

### 3.2 What is available

Open-weight models in October 2026 range from very large mixture-of-experts systems that need multi-GPU clusters (Kimi K3 at 2.8 trillion total parameters; DeepSeek V4 under MIT; GLM-5.2 under MIT; Mistral Large 3 under Apache 2.0) down to models that fit one consumer GPU or a laptop (the Qwen3.5 and 3.6 families, Gemma 3, Mistral Small 4, Ministral 3, gpt-oss-20b). Half of the twelve models one tracker ranks carry an OSI-approved license; the rest have custom or modified terms that need reading. The strongest model in that tracker's single-GPU class (at most 32 GB of GPU or unified memory) scored 48 of 100 on its benchmark composite, against the large models' scores in the seventies: the single-GPU class is usable, not frontier.

The practical conclusion from every source consulted is the same: the limiting factor for private deployment is memory, not weights. Weights are available; running the good ones is a hardware decision.

### 3.3 What runs on what we have, and what we might buy

- **An 8 GB Mac** (the current workshop machine) runs an embedding model and a model in the 1 to 4 billion parameter class, quantized, one heavy process at a time. That is enough for tagging, summaries and embeddings. It is not enough for a coding model worth comparing.
- **A Mac with more than 32 GB of unified memory** now runs Ollama on Apple's MLX framework (preview since March 2026), with one supported model so far, a 35-billion-total, 3-billion-active mixture-of-experts coding model, at about 134 tokens per second decode in 4-bit on an M5-class chip. The M5 Pro and Max chips' neural accelerators improve time to first token. This is the first class of machine on which a local coding comparison is worth running.
- **64 to 128 GB of unified memory** (the Strix Halo machines and the MacBook Pro M5 Max discussed on 8 October) runs the 27 to 120 billion class comfortably and the larger mixture-of-experts models with heavy quantization. The hardware options and prices gathered then are unchanged and remain deferred.

### 3.4 The cost case, honestly

One 2026 case study compared a hosted frontier coding agent with a self-hosted quantized open model for a single developer on a real monorepo over two 28-day periods. With prompt caching the hosted API's effective cost fell to under a dollar per million tokens; per token, the cached API was cheaper than the on-premise slice at single-developer utilization. On total cost of ownership, on-premise came out about 40% cheaper under shared hardware allocation and about 44% more expensive under dedicated reservation. The quality finding is the one to keep: the open model's fix-commit ratio was 75% against the hosted model's 46%, an odds ratio of about 3.6 for needing a repair, with more API and dependency misuse and logical defects. The authors flag that one developer, a confounded harness and quantization make this a case, not a law.

The lesson for mini-moi is the one Codex's brief already stated: measure accepted work, not calls or tokens. A local patch that costs two hours of repair saved nothing. And utilization matters: hardware that sits idle is the expensive kind.

### 3.5 Routing and cascades

RouteLLM trains cheap routers to send prompts to a stronger or weaker model using preference data and reports cost reductions on selected benchmarks at preserved quality; FrugalGPT studies staged calls that escalate. Both support treating the local/hosted split as an empirical decision, not a quota. Neither establishes that routing transfers unchanged to long-running coding agents, and neither addresses privacy. mini-moi starts with transparent routing rules and its own evaluation set; a learned router comes only if it beats the rules on that set, and never overrides a local-only designation.

### 3.6 Debate and second opinions

Research on multi-agent debate finds that added discussion does not reliably beat simpler ensembles, and that agreement can hide error. mini-moi's practice of preserving each agent's initial independent finding, the source-based disagreements and the eventual reason for a decision is the right response. Different vendors are useful diversity, not guaranteed independence.

## 4. What local does in mini-moi, in order

### 4.1 First: a pass over the Vault

The first substantial local workload is the Vault, not coding. It is the cheapest bounded comparison available, it runs on hardware already owned, it never sends private material anywhere, and it produces the evidence that decides every later question. Four workloads, in order of how little they risk and how much they teach:

| Workload | The local model does | Judged against | Teaches |
|---|---|---|---|
| **Embeddings** | Produce the vectors for the Vault's semantic index | A gold set; recall of known passages | Whether a local embedding model is good enough for "what do I know about X" on the owner's material |
| **Tagging and classification** | Propose topic, domain, kind and time-validity tags on raw-tier records | The owner's confirmed tags on a sample; the hosted model's tags on the same sample | Whether the storage and tags are right (retrieval teaches storage) |
| **Reflection drafts** | Draft the weekly "what happened, what recurred, what is open" and candidate promotions from raw to curated, with citations | The owner's review; nothing becomes curated without approval | Whether a local model can do consolidation with provenance intact |
| **Gold-set answering** | Answer about twenty owner-written questions with citations to exact passages and explicit gaps | Owner-confirmed answers; the hosted model's answers on the same questions | The direct local-versus-hosted comparison on the one metric that measures the promise |

Every run records model, quantization, runtime, prompt version, date and result. Repeated monthly, this becomes the dataset that answers the only version of the question that matters: how far behind is local on this person's material, and is the distance closing.

The hosted side of each comparison runs only on records already within that model's permitted scope. No capture grant changes. The existing Ask path stays the baseline.

### 4.2 Second: a bounded coding comparison

Once a local runtime is proven on the Vault pass and a machine with enough memory exists, one coding comparison: a task already done once with a hosted agent, with acceptance held separately. A good candidate is a bounded Workshop journal fix of the kind reproduced in the October 8 review (a torn-tail defect with a known test), or a domain export adapter with a dry-run contract. Same brief, isolated checkouts, same tools and resource limits; correctness and rework measured before anyone judges the explanation. Not anything on the career path, which has a deadline and an audience.

### 4.3 Already local, kept

Curator's research triage and Mein Deutsch's last-tier translation fallback stay local. They are small, proven, and the public documents already describe them. They are also the first places to try a newer small model when one is worth trying, because the acceptance is already defined.

### 4.4 Stays hosted, by design

Escalation on hard problems, independent review of consequential diffs, a second opinion from a different vendor, and any task where the quality gap in long-horizon agent work would cost more than the hosted call. Two or three hosted relationships, chosen for difference, not one.

## 5. How we judge

Accepted work, not calls or tokens. For every comparison, record: task class; model, version, quantization, runtime, tools and disclosure permission; correctness, functional acceptance, review findings and regressions; human intervention and repair time, including hosted escalation; elapsed time, first-response latency and reliability under normal resource limits; private-data disclosure and refusal behaviour, including any accidental hosted fallback; total cost (hardware amortization, electricity, operations, subscriptions, API).

Report local shares separately by tasks, by accepted coding work, by tokens and by cost. A 60% token share can coexist with a far smaller share of completed features. No quota may force a privacy violation or acceptance of weaker work.

The gold set is the instrument for the Vault; a 12 to 20 task set (bug fixes, features, document questions with gold citations, a screenshot interpretation, an operations diagnosis) is the instrument for execution. Failed attempts are preserved.

## 6. Hardware posture

**A local server was part of the plan from the beginning, and it still is.** mini-moi went to AWS for production and everything outward-facing; that was a hosting decision for availability and reach, not a decision against local compute. A privately controlled server at home remains in scope for when the evidence justifies it. It is the natural host for the larger local models, for the Vault pass at scale, for a second Workshop, and for the always-on local jobs that a laptop cannot promise. The laptop is where the owner works; the server is where local intelligence runs when it is worth running continuously.

The decision is deferred, and the reason is now evidence-based rather than reluctance. The current laptop can run the Vault pass. A coding comparison needs the more-than-32 GB class. Buying for the 64 to 128 GB class is buying for a model's successor as much as for the model, since the open frontier moves roughly every four months. Three shapes are on the table, none chosen:

- **A laptop upgrade** for the workshop (the 8 October options: a 128 GB Strix Halo laptop at about $3,000; a MacBook Pro M5 Pro at 64 GB at about $3,000; an M5 Max at 128 GB from about $4,500). Portable, café-capable, limited to one heavy job.
- **A local server** at home: a Strix Halo mini PC or small tower in the 64 to 128 GB unified-memory class (the Framework Desktop's 128 GB configuration launched at about $2,000), or a used workstation with one or two consumer GPUs. Always on, runs the local models and jobs continuously, lets the laptop stay light. It also answers the single-host concentration that the October reviews named as a risk.
- **Both, in sequence:** the server first, because it serves the Vault pass and the always-on jobs, and the laptop replaced only if the workshop itself needs more than the current machine.

The decision waits for two things: the Vault pass results, which say what size of local model earns its keep on the owner's material, and the clean-machine restore drill that the Vault's custody work requires anyway, so that a new machine, laptop or server, is a restore rather than a migration.

**History and current leaning (editor's note, October 2026).** A Mac mini as the local server was planned for March 2026 and deferred. The laptop's limits are now visible. The current leaning is a Linux server rather than a Mac, with Q1 or Q2 2027 as the likely window, if the evidence by then justifies it. This is a leaning, not a decision; it belongs in the January snapshot as either a purchase plan or a further deferral with reasons.

Hardware figures circulated in earlier discussions ($5,000 and $25,000 configurations; a 600 GB RAM hybrid deployment for a trillion-parameter model) were exploratory, not quotes, and none applies to the current plan.

## 7. What never leaves local control

The Vault's originals and retained editions. The keys and the recovery sheet. Anything designated Private or local-only. Embeddings and summaries of those records. Hosted models receive deliberately authorized excerpts, never the corpus, and the gateway refuses a hosted route for a local-only designation rather than falling back.

Downloaded weights do not remove supply-chain, licence or jurisdiction questions: pin and review loaders and dependencies, control outbound traffic, keep the licence terms with the model, and evaluate actual behaviour. National origin alone neither proves malicious behaviour nor establishes safety. This stays a functional vision; it does not become a regulatory work package.

## 8. Risks and counterpoints

- **"Open models have caught up."** Partly, and the benchmarks that say so are the ones easiest to optimize against. On the work mini-moi cares about most (long-horizon agent tasks in a repository) the gap persists. Plan for a four-month lag that may not shrink on schedule.
- **"Local is cheaper."** Only at utilization. For one person, cached hosted calls can be cheaper per token than an on-premise slice; the total-cost case flips with shared hardware and flips back with dedicated hardware. And quality defects are a cost.
- **"The harness is the model's problem."** It is not. Most of what makes a build frictionless (a verified native route per agent, a deterministic dispatcher, the inbox in daily use, the Vault consumer wired to the assistants, a coverage matrix) is independent of which model answers. A local model dropped into today's harness inherits today's friction. Finish the harness before judging any model.
- **"A local model is private."** Only if its tools, logging and telemetry are too, and only if the router cannot route around the designation.
- **Vendor assumptions in the Vault design.** Four small ones, all fixable: provenance fields must record quantization and runtime, not only provider and model; the semantic index must carry its embedding model's identity so a mixed index is impossible by accident; capture adapters depend on vendor session formats (mitigated by thin adapters and exports); attribution rules were tested on hosted-model output and need the same tests on local output.

## 9. What this snapshot does not do

No model downloaded, no benchmark run, no job scheduled, no hardware priced beyond the October 8 figures, no legal clearance, no change to capture grants, release scope or the Vault's custody order. MiniMoi 1.2 and the career track proceed as planned.

## 10. Open for the editor in chief

1. Confirm the Vault pass as the first local workload, ahead of any coding comparison.
2. The first small model for tagging and reflection drafts on the 8 GB machine (a 4-billion-class quantized model is the realistic ceiling; the embedding model already running is the first candidate for embeddings).
3. Whether the monthly local-versus-hosted gold-set result appears in the morning brief or only in the quarterly snapshot.
4. Hardware: deferred until the Vault pass and the restore drill; confirm. When the time comes, the first question is server or laptop, and the document's suggestion is server first.

## 11. Cadence

Quarterly, with an extra update when a model, licence, runtime or a real mini-moi comparison changes the assessment. Next snapshot: January 2027. This file is preserved unchanged after review; the next snapshot is written beside it and links back. Each update records what the objective means now and what has not changed; new primary sources and changed availability; mini-moi's actual deployment and measured evidence, including failures; retired assumptions and unresolved questions; proposed experiments kept separate from authorized scope. The January 2026 origin is not rewritten from later success; original sources are added as they are located, with their dates.

## Bibliography

All external sources were opened in October 2026. Academic entries were reviewed at abstract or summary level unless stated; vendor and project pages directly. This is an annotated reading list, not a systematic review, and contains no independent reproduction of any figure. Entries B1 to B18 carry over from the Codex brief; C1 to C6 are new to this snapshot.

### Read first

**B1.** Kleppmann, Wiggins, van Hardenberg, McGranaghan (2019). *Local-first software: You own your data, in spite of the cloud.* Ink & Switch. https://www.inkandswitch.com/essay/local-first/ — Ownership, offline use and collaboration can coexist; seven ideals. The foundation for keeping the owner's knowledge usable if any service disappears.

**C1.** WhatLLM (July 2026). *Open-source vs proprietary LLMs 2026.* https://whatllm.org/blog/open-source-vs-proprietary-llms-2026 — Reports Epoch AI's estimate of a four-month, roughly eight-point lag; gaps by benchmark; where the gap persists (long-horizon agent work) and where it has closed; the harness confound; cost per completed task over cost per token.

**C2.** *Inference Economics of Enterprise Coding Agents: A Case Study of Cloud vs. On-Premise LLMs* (2026). https://arxiv.org/pdf/2607.13080 — Single-developer, two-period case study: cached hosted API cheaper per token; on-premise TCO 40% lower under shared allocation and 44% higher under dedicated; open model's defect odds about 3.6 times higher. Confounded; one developer; the clearest published statement of "measure accepted work."

**B2.** Ong et al. (2024, rev. 2025). *RouteLLM: Learning to Route LLMs with Preference Data.* https://arxiv.org/abs/2406.18665v4 — Learned routing between stronger and weaker models; benchmark-bound cost and quality evidence.

**B3.** Zheng et al. (2026). *SWE-Bench Pro Verified.* https://arxiv.org/abs/2609.08149v3 — Evaluation leakage and task inconsistencies that inflate coding-agent scores; a warning against buying hardware from a leaderboard.

### Field state, October 2026

**C3.** Thunder Compute (updated 1 October 2026). *Best Open Source LLMs (October 2026).* https://www.thundercompute.com/blog/best-open-source-llms — Current open-weight families, sizes, licences, vision support, and benchmark figures against hosted models; the practical gap is hardware.

**C4.** BenchLM (10 October 2026). *Local LLM Hardware Statistics.* https://benchlm.ai/stats/local-llm-hardware — Twelve ranked open-weight models with reference hardware; the single-GPU class (at most 32 GB) and its best score; licence share.

**C5.** Ollama (30 March 2026). *Ollama is now powered by MLX on Apple Silicon (preview).* https://registry.ollama.ai/blog/mlx — Unified-memory inference on Apple Silicon, one supported coding model, throughput figures, the more-than-32 GB requirement.

**C6.** Shop ZimaSpace (2026). *Open models are catching frontier AI: is 2026 the year local AI becomes good enough?* https://shop.zimaspace.com/blogs/tech-ai-hub/open-models-vs-frontier-ai-local-ai-good-enough-2026 — A practitioner's framing of the "good enough" question; consulted for context, not figures.

### Models and vendor documentation

**B4.** Qwen team. *Qwen3.5-9B model card.* https://huggingface.co/Qwen/Qwen3.5-9B — Native multimodal small model; companion larger model https://huggingface.co/Qwen/Qwen3.5-397B-A17B.
**B5.** Moonshot AI (27 July 2026). *Kimi K3 open-weights release* https://www.kimi.com/news/kimi-k3-open-source and model card https://huggingface.co/moonshotai/Kimi-K3 — Largest open-weight multimodal model; custom licence.
**B10.** Google. *Gemma 3 model card.* https://ai.google.dev/gemma/docs/core/model_card_3
**B11.** Mistral AI. *Ministral 3 14B.* https://docs.mistral.ai/models/ministral-3-14b-25-12
**B16.** OpenAI. *gpt-oss-120b* https://developers.openai.com/api/docs/models/gpt-oss-120b and *Running gpt-oss with Ollama* https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-ollama
**B17.** Moonshot AI. *Kimi K2.5 model card and licence.* https://huggingface.co/moonshotai/Kimi-K2.5

### Practice, deployment, adaptation, evaluation

**B6.** Chen, Zaharia, Zou (2023). *FrugalGPT.* https://arxiv.org/abs/2305.05176 — Cascades and cost-aware combinations.
**B7.** Aider. *Ollama integration.* https://aider.chat/docs/llms/ollama.html — Repository editing with local models in practice.
**B8.** Choi, Zhu, Li (2025). *Debate or Vote.* https://arxiv.org/abs/2508.17536v2 — Debate does not automatically beat simpler ensembles.
**B9.** Deng et al. (2025). *SWE-Bench Pro.* https://arxiv.org/abs/2509.16941v2 — Repository-level coding benchmark; background to B3.
**B12.** ggml-org. *llama.cpp.* https://github.com/ggml-org/llama.cpp
**B13.** Apple. *MLX-LM.* https://github.com/ml-explore/mlx-lm
**B14.** KTransformers. *Running Kimi K2.5 with SGLang and KT-Kernel.* https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/Kimi-K2.5.md — Hybrid CPU/GPU deployment example; hardware figures are not quotes.
**B15.** Google. *Gemma model fine-tuning.* https://ai.google.dev/gemma/docs/tune
**B18.** Unsloth. *Kimi K2.5: How to run locally.* https://unsloth.ai/docs/models/tutorials/kimi-k2.5

### mini-moi's own record

- README.md, ARCHITECTURE.md (maintained baseline, reviewed through 2026-08-16), ROADMAP.md in https://github.com/robertvanstedum/personal-ai-agents — the local-first data principle, the model-agnostic principle, the local test call, Curator's local triage, the German local fallback tier, and the statement that local capability is retained and expected to matter more.
- *Where things live* (vision, October 2026) — the Vault, the archive, retrieval and insight, the capability-public/instance-private rule.
- *mini-moi and the field* (whitepaper, 9 October 2026) — harness engineering, portable memory, governed shared memory.
- Codex, *MiniMoi: private, local and hybrid intelligence*, v0.1 brief (14 October 2026), and Claude Chat's companion of the same date — folded into this document.

## Snapshot record

- **October 2026, v1.0:** first standing snapshot. Direction restated from the public repository and the owner's 14 October clarification; field state researched (gap, availability, hardware classes, cost case, routing, debate); Vault pass set as the first local workload; hardware deferred on evidence; Codex brief and companion folded in.
- **Not performed:** hardware purchase, model download, local benchmark, automatic routing, legal clearance, new release scope, public publication.
