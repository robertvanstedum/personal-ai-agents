---
title: Private, local and hybrid intelligence in mini-moi
snapshot: October 2026
version: 1.0, revised 14 October 2026 after Codex review (first dated snapshot; next due January 2027)
authors: Claude Chat, Codex
editor in chief: Robert van Stedum
status: standing direction and dated research record; not a purchase, build authorization or change to any release scope
supersedes: MiniMoi-Private-Local-Hybrid-Intelligence-2026-10-14.md (Codex v0.1 brief) and its companion, both folded in
---

# Private, local and hybrid intelligence in mini-moi

*October 2026 snapshot*

## Abstract

mini-moi has had a local model in it on purpose since the beginning: the 2025 experiment ran entirely on a local model, the production architecture keeps a local tier with no external dependency, and the public documents say local capability is retained and expected to matter more as open models and hardware improve. This snapshot restates that direction as it stands in October 2026 and sets it against the field. The direction is: keep the owner's knowledge, working history and artifacts independent of any model vendor; do a substantial share of useful work, including coding, on privately controlled hardware where quality allows; keep two or three strong hosted models for escalation, independent review and a second opinion; set no fixed local/cloud quota; judge by accepted work, privacy, reliability and total cost. In October 2026 the field makes that direction more credible than it was a year ago and still not free: the best open-weight models trail the hosted frontier by about four months on composite measures and by a few points on coding, the gap persists on long-horizon agent work, the one published cost case for self-hosting depends on utilization and is a single confounded case study rather than a law, and the practical limit is memory, not model availability. No order of local workloads is decided, and the editor in chief has said none is needed now: mini-moi already has tasks throughout it that are first candidates, and coding and testing are candidates too. The strongest single candidate this snapshot can name is a local pass over the Vault, the owner's own records, judged against a gold set the owner confirms, run on hardware already owned, with nothing private leaving the machine. Hardware is priced only after evidence exists from whichever candidates run first.

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

**Privacy precedes model selection.** If material is designated local-only, no quality or cost router may send it to a hosted model. The permitted alternatives are local review, owner review, a disclosed inability to finish, or separately authorized sharing of a suitable excerpt. Summaries and embeddings carry the information too, and are covered by the same rule. This must be enforced at the retrieval and routing boundaries, with verification recorded. The model gateway exists and is where the routing half belongs; its existence does not establish that the full disclosure path, from retrieval through routing to the hosted call, is implemented and verified. Until that verification is recorded, the rule is a requirement, not a delivered property.

## 3. Where the field stands, October 2026

This section is new research for this snapshot. Sources are in the bibliography; the figures are the sources' own and have not been reproduced here.

### 3.1 The gap to the hosted frontier

Epoch AI's estimate (C0) is that open-weight models have lagged the closed frontier by about four months on average since January 2026, roughly eight points on its composite index. That is an aggregate historical estimate, not a release cycle and not a forecast of the future gap. The per-benchmark figures below are from Epoch's June 2026 snapshot as reported by WhatLLM in July (C1); they are frozen at that date here, because the secondary source mixes live tables with a June fallback. On that snapshot the largest measured gap was on a general intelligence index (about eleven points); on coding indexes the gap was about five points; on agentic indexes about four; on fresh code generation and graduate-level science the two were near parity. The same analysis notes that public benchmarks may flatter open models because public test sets are easier to optimize against, and that near-parity on a score does not make products interchangeable: reliability, tool schemas, safety controls and uptime still differ.

The gap persists where mini-moi's build loop lives: long-horizon agent work, error recovery, multi-step tasks in a terminal or repository. The analysis also makes a point this document agrees with: results on those tasks depend on the harness (search, edits, tests, retries) as much as the model, so scores mix the two.

### 3.2 What is available

Open-weight models in October 2026 range from very large mixture-of-experts systems that need multi-GPU clusters (Kimi K3 at 2.8 trillion total parameters; DeepSeek V4 under MIT; GLM-5.2 under MIT; Mistral Large 3 under Apache 2.0) down to models that fit one consumer GPU or a laptop (the Qwen3.5 and 3.6 families, Gemma 3, Mistral Small 4, Ministral 3, gpt-oss-20b). Half of the twelve models one tracker ranks carry an OSI-approved license; the rest have custom or modified terms that need reading. The strongest model in that tracker's single-GPU class (at most 32 GB of GPU or unified memory) scored 48 of 100 on its benchmark composite, against the large models' scores in the seventies: the single-GPU class is usable, not frontier.

The practical conclusion from every source consulted is the same: the limiting factor for private deployment is memory, not weights. Weights are available; running the good ones is a hardware decision.

### 3.3 What runs on what we have, and what we might buy

- **An 8 GB Mac** (the current workshop machine) runs an embedding model and a model in the 1 to 4 billion parameter class, quantized, one heavy process at a time. That is enough for tagging, summaries and embeddings. It is not enough for a coding model worth comparing.
- **A Mac with more memory** runs a coding model worth comparing. Ollama's MLX preview (March 2026) sets more than 32 GB of unified memory as its own requirement, for one supported model so far, a 35-billion-total, 3-billion-active mixture-of-experts coding model; the throughput figure it publishes (about 134 tokens per second decode in 4-bit on an M5-class chip) is specific to that runtime, version and model, not a general expectation for the chip. That 32 GB floor is the preview's, not the minimum for every worthwhile coding comparison; smaller quantized coding models run under llama.cpp or MLX-LM on less, with correspondingly less to compare.
- **64 to 128 GB of unified memory** (the Strix Halo machines and the MacBook Pro M5 Max discussed on 8 October) runs many models in the 27 to 120 billion class, and the larger mixture-of-experts models with heavy quantization. Memory alone does not make a given model comfortable: quantization level, context length and whatever else is running on the machine decide what fits and how fast it goes. The hardware options and prices gathered then are unchanged and remain deferred.

### 3.4 The cost case, honestly

One 2026 case study compared a hosted frontier coding agent with a self-hosted quantized open model for a single developer on a real monorepo over two 28-day periods. With prompt caching the hosted API's effective cost fell to under a dollar per million tokens; per token, the cached API was cheaper than the on-premise slice at single-developer utilization. On total cost of ownership, on-premise came out about 40% cheaper under shared hardware allocation and about 44% more expensive under dedicated reservation. The quality finding is the one to keep, stated precisely: the open model's fix-commit ratio was 75% against the hosted model's 46%. That ratio measures the share of commits that were repairs, not the percentage of tasks that failed; it is an odds ratio of about 3.6 for a commit being a fix, with more API and dependency misuse and logical defects on the open side. Different models, harnesses, periods and quantization levels prevent attributing the difference solely to local versus hosted. The authors say so; this document keeps that qualification wherever the figure is quoted.

The lesson for mini-moi is the one Codex's brief already stated: measure accepted work, not calls or tokens. A local patch that costs two hours of repair saved nothing. And utilization matters: hardware that sits idle is the expensive kind.

### 3.5 Routing and cascades

RouteLLM trains cheap routers to send prompts to a stronger or weaker model using preference data and reports cost reductions on selected benchmarks at preserved quality; FrugalGPT studies staged calls that escalate. Both support treating the local/hosted split as an empirical decision, not a quota. Neither establishes that routing transfers unchanged to long-running coding agents, and neither addresses privacy. mini-moi starts with transparent routing rules and its own evaluation set; a learned router comes only if it beats the rules on that set, and never overrides a local-only designation.

### 3.6 Debate and second opinions

Research on multi-agent debate finds that added discussion does not reliably beat simpler ensembles, and that agreement can hide error. mini-moi's practice of preserving each agent's initial independent finding, the source-based disagreements and the eventual reason for a decision is the right response. Different vendors are useful diversity, not guaranteed independence.

## 4. What local can do in mini-moi: candidates, not an order

### 4.1 A pass over the Vault

The editor in chief's answer (14 October) is that no first workload needs choosing now: candidates exist throughout mini-moi, coding and testing included, and whichever is ready and bounded runs first. The Vault pass is recorded here as the strongest candidate this snapshot can name, not as the required first. It is the cheapest bounded comparison available, it runs on hardware already owned, it never sends private material anywhere, and it produces the evidence that decides every later question. Four workloads, in order of how little they risk and how much they teach:

| Workload | The local model does | Judged against | Teaches |
|---|---|---|---|
| **Embeddings** | Produce the vectors for the Vault's semantic index | A gold set; recall of known passages | Whether a local embedding model is good enough for "what do I know about X" on the owner's material |
| **Tagging and classification** | Propose topic, domain, kind and time-validity tags on raw-tier records | The owner's confirmed tags on a sample; the hosted model's tags on the same sample | Whether the storage and tags are right (retrieval teaches storage) |
| **Reflection drafts** | Draft the weekly "what happened, what recurred, what is open" and candidate promotions from raw to curated, with citations | The owner's review; nothing becomes curated without approval | Whether a local model can do consolidation with provenance intact |
| **Gold-set answering** | Answer about twenty owner-written questions with citations to exact passages and explicit gaps | Owner-confirmed answers; for the shareable subset only, the hosted model's answers on the same questions | The direct local-versus-hosted comparison on the one metric that measures the promise |

Every run records model, quantization, runtime, prompt version, date and result. Repeated monthly, this becomes the dataset that answers the only version of the question that matters: how far behind is local on this person's material, and is the distance closing.

The gold set is split in two. Local-only cases cover records that must never leave locally controlled processing; for those, the owner's confirmed answer is the only baseline, and no hosted answer is produced. Shareable comparison cases cover records already within a hosted model's permitted scope; for those, the hosted answer is a second reference beside the owner's. No capture grant changes. The existing Ask path stays the baseline for the shareable cases.

### 4.2 A bounded coding or testing comparison

When a machine with enough memory exists, one coding comparison, with testing work (writing or running a test suite against a known defect) as an equally good candidate: a task already done once with a hosted agent, with acceptance held separately. A good candidate is a bounded Workshop journal fix of the kind reproduced in the October 8 review (a torn-tail defect with a known test), or a domain export adapter with a dry-run contract. Same brief, isolated checkouts, same tools and resource limits; correctness and rework measured before anyone judges the explanation. Not anything on the career path, which has a deadline and an audience.

### 4.3 Already local, kept

Curator's research triage and Mein Deutsch's last-tier translation fallback stay local. They are small, proven, and the public documents already describe them. They are also the first places to try a newer small model when one is worth trying, because the acceptance is already defined.

### 4.4 Stays hosted, for now

Escalation on hard problems, independent review of consequential diffs, a second opinion from a different vendor, and any task where the quality gap in long-horizon agent work would cost more than the hosted call. Two or three hosted relationships, chosen for difference, not one. None of this is permanent: a local model qualifies for consequential review the same way it qualifies for anything else, through recorded evidence on mini-moi's own tasks, consistent with the objective in section 1.2.

## 5. How we judge

Accepted work, not calls or tokens. For every comparison, record: task class; model, version, quantization, runtime, tools and disclosure permission; correctness, functional acceptance, review findings and regressions; human intervention and repair time, including hosted escalation; elapsed time, first-response latency and reliability under normal resource limits; private-data disclosure and refusal behaviour, including any accidental hosted fallback; total cost (hardware amortization, electricity, operations, subscriptions, API).

Report local shares separately by tasks, by accepted coding work, by tokens and by cost. A 60% token share can coexist with a far smaller share of completed features. No quota may force a privacy violation or acceptance of weaker work.

The gold set is the instrument for the Vault; a 12 to 20 task set (bug fixes, features, document questions with gold citations, a screenshot interpretation, an operations diagnosis) is the instrument for execution. Failed attempts are preserved.

## 6. Hardware posture

**A local server was part of the plan from the beginning, and it still is.** mini-moi went to AWS for production and everything outward-facing; that was a hosting decision for availability and reach, not a decision against local compute. A privately controlled server at home remains in scope for when the evidence justifies it. It is the natural host for the larger local models, for the Vault pass at scale, for a second Workshop, and for the always-on local jobs that a laptop cannot promise. The laptop is where the owner works; the server is where local intelligence runs when it is worth running continuously.

The decision is deferred, and the reason is now evidence-based rather than reluctance. The current laptop can run the Vault pass. A coding comparison worth running needs more memory than it has (the Ollama MLX preview sets 32 GB as its own floor; other runtimes go lower with smaller models). Buying for the 64 to 128 GB class is buying for a model's successor as much as for the model: open models have historically trailed the frontier and keep being replaced, and a machine outlives whichever one it was bought for. Three shapes are on the table, none chosen:

- **A laptop upgrade** for the workshop (the 8 October options: a 128 GB Strix Halo laptop at about $3,000; a MacBook Pro M5 Pro at 64 GB at about $3,000; an M5 Max at 128 GB from about $4,500). Portable, café-capable, limited to one heavy job.
- **A local server** at home: a Strix Halo mini PC or small tower in the 64 to 128 GB unified-memory class (the Framework Desktop's 128 GB configuration launched at about $2,000), or a used workstation with one or two consumer GPUs. Always on, runs the local models and jobs continuously, lets the laptop stay light. It also answers the single-host concentration that the October reviews named as a risk.
- **Both, in sequence:** the server first, because it serves the Vault pass and the always-on jobs, and the laptop replaced only if the workshop itself needs more than the current machine.

The decision waits for two things: the Vault pass results, which say what size of local model earns its keep on the owner's material, and the clean-machine restore drill that the Vault's custody work requires anyway, so that a new machine, laptop or server, is a restore rather than a migration.

**History and current leaning (editor's note, October 2026).** A Mac mini as the local server was planned for March 2026 and deferred. The laptop's limits are now visible. The current leaning is a Linux server rather than a Mac, with Q1 or Q2 2027 as the likely window, if the evidence by then justifies it. This is a leaning, not a decision; it belongs in the January snapshot as either a purchase plan or a further deferral with reasons.

Hardware figures circulated in earlier discussions ($5,000 and $25,000 configurations; a 600 GB RAM hybrid deployment for a trillion-parameter model) were exploratory, not quotes, and none applies to the current plan.

## 7. What never leaves local control

The Vault's originals and retained editions. The keys and the recovery sheet. Anything designated Private or local-only. Embeddings and summaries of those records. Hosted models receive deliberately authorized excerpts, never the corpus, and the gateway must refuse a hosted route for a local-only designation rather than falling back; that refusal is verified and recorded, not assumed (section 2).

"Local control" means owner-controlled processing, not a physical location. An encrypted backup of the Vault in off-site storage the owner controls, with keys that never leave the owner, is permitted and expected; it is the restore drill's premise. What is forbidden is plaintext, embeddings or summaries of local-only records reaching any processing the owner does not control, hosted models included. The gold set follows the same line: local-only cases are judged against the owner's answers alone; hosted answers can be a baseline only for the shareable cases (section 4.1).

Downloaded weights do not remove supply-chain, licence or jurisdiction questions: pin and review loaders and dependencies, control outbound traffic, keep the licence terms with the model, and evaluate actual behaviour. National origin alone neither proves malicious behaviour nor establishes safety. This stays a functional vision; it does not become a regulatory work package.

## 8. Risks and counterpoints

- **"Open models have caught up."** Partly, and the benchmarks that say so are the ones easiest to optimize against. On the work mini-moi cares about most (long-horizon agent tasks in a repository) the gap persists. The four-month figure is Epoch's historical average, not a schedule; plan for a lag of unknown future size and measure it on mini-moi's own tasks.
- **"Local is cheaper."** Only at utilization. For one person, cached hosted calls can be cheaper per token than an on-premise slice; the total-cost case flips with shared hardware and flips back with dedicated hardware. And quality defects are a cost.
- **"The harness is the model's problem."** It is not. Most of what makes a build frictionless (a verified native route per agent, a deterministic dispatcher, the inbox in daily use, the Vault consumer wired to the assistants, a coverage matrix) is independent of which model answers. A local model dropped into today's harness inherits today's friction. Finish the harness before judging any model.
- **"A local model is private."** Only if its tools, logging and telemetry are too, and only if the router cannot route around the designation.
- **Vendor assumptions in the Vault design.** Three open and one already closed: provenance fields must record quantization and runtime, not only provider and model; capture adapters depend on vendor session formats (mitigated by thin adapters and exports); attribution rules were tested on hosted-model output and need the same tests on local output. Embedding identity is handled: the integrated index already pins the embedding model's digest and dimensions and refuses a mismatch, so a mixed index cannot happen by accident.

## 9. What this snapshot does not do

No model downloaded, no benchmark run, no job scheduled, no hardware priced beyond the October 8 figures, no legal clearance, no change to capture grants, release scope or the Vault's custody order. MiniMoi 1.2 and the career track proceed as planned.

## 10. Open for the editor in chief

1. ~~Confirm, or change, the proposed order: the Vault pass first.~~ Answered 14 October: no order is decided now; candidates exist throughout mini-moi, coding and testing included. Section 4 is a list of candidates.
2. The first small model for tagging and reflection drafts on the 8 GB machine (a 4-billion-class quantized model is the realistic ceiling; the embedding model already running is the first candidate for embeddings).
3. Whether the monthly local-versus-hosted gold-set result appears in the morning brief or only in the quarterly snapshot.
4. Hardware: deferred until the Vault pass and the restore drill; confirm. When the time comes, the first question is server or laptop, and the document's suggestion is server first.

## 11. Cadence

Quarterly, with an extra update when a model, licence, runtime or a real mini-moi comparison changes the assessment. Next snapshot: January 2027. This file is preserved unchanged after review; the next snapshot is written beside it and links back. Each update records what the objective means now and what has not changed; new primary sources and changed availability; mini-moi's actual deployment and measured evidence, including failures; retired assumptions and unresolved questions; proposed experiments kept separate from authorized scope. The January 2026 origin is not rewritten from later success; original sources are added as they are located, with their dates.

## Bibliography

All external sources were opened in October 2026. Academic entries were reviewed at abstract or summary level unless stated; vendor and project pages directly. This is an annotated reading list, not a systematic review, and contains no independent reproduction of any figure. Entries B1 to B18 carry over from the Codex brief; C0 to C6 are new to this snapshot.

### Read first: five reading abstracts

Editorial abstracts written for the editor in chief, not the authors' own. Each gives the finding, its limits, and what it means for mini-moi. Papers were read at abstract or summary level; protocols were not audited.

**B1. Local-first software: you own your data, in spite of the cloud.** Kleppmann, Wiggins, van Hardenberg, McGranaghan; Ink & Switch / Onward!, 2019. https://www.inkandswitch.com/essay/local-first/ — The authors argue that ownership, offline use and collaboration can coexist, and set out seven ideals with prototype work on synchronization and data structures. It is about durable software and user agency, not model capability. *Limits:* their synchronization technology is one way to meet the ideals, not the only one. *For mini-moi:* the Vault's portability and recoverability matter independently of which model answers a question; the ideals can be adopted without the particular technology.

**B2. RouteLLM: learning to route LLMs with preference data.** Ong et al.; 2024 preprint, revised February 2025. https://arxiv.org/abs/2406.18665v4 — Trains inexpensive routers to choose between a stronger and a weaker model from preference data, reporting cost reductions on selected benchmarks at preserved quality, and studies transfer between model pairs. *Limits:* benchmark-bound; says nothing about privacy, long-running coding agents or a universal savings rate. *For mini-moi:* allocation is an empirical decision, not a quota; start with transparent rules and our own evaluation set, and add a learned router only if it beats the rules there, never over a local-only designation.

**B3. SWE-Bench Pro Verified: a reliable benchmark for software engineering agents.** Zheng et al.; September 2026 preprint, revised 9 October 2026. https://arxiv.org/abs/2609.08149v3 — Identifies evaluation leakage and task and test inconsistencies that inflate coding-agent scores, repairs instances and adds safeguards; some measured results fall. *Limits:* a recent preprint and the authors' own findings, not a ruling that every prior result is invalid, and not a mini-moi acceptance suite. *For mini-moi:* judge local builders on real repository tasks with independently held acceptance, real execution and review; passing visible tests is evidence, not proof; do not buy hardware from a leaderboard.

**C2. Inference economics of enterprise coding agents: a case study of cloud vs. on-premise LLMs.** 2026. https://arxiv.org/abs/2607.13080 — One developer, one real monorepo, two 28-day periods: with caching the hosted API was cheaper per token than the on-premise slice; on total cost of ownership on-premise was 40% lower under shared allocation and 44% higher under dedicated; the open model's fix-commit ratio was 75% against 46%, an odds ratio of about 3.6 for a commit being a repair. *Limits:* the ratio counts repair commits, not failed tasks; different models, harnesses, periods and quantization mean the difference cannot be attributed solely to local versus hosted; a case, not a law. *For mini-moi:* the clearest published argument for measuring accepted work and repair time rather than tokens, and for utilization as the hinge of the cost case.

**B4. Qwen3.5-9B model card.** Qwen team; living vendor documentation, accessed October 2026. https://huggingface.co/Qwen/Qwen3.5-9B — A downloadable Apache-2.0 model that combines text with a vision encoder and documents serving and agent integrations; the vendor publishes comparisons across language and visual tasks. *Limits:* comparative performance is vendor evidence until reproduced on our workflows; runtime and quantization support must be checked. *For mini-moi:* a concrete candidate for screenshot interpretation, bounded coding and private document work on a mid-memory machine, showing that local candidates need not be restricted to text or clerical work.

**C0.** Epoch AI (2026). *The gap between open and closed models.* https://epoch.ai/data-insights/open-closed-eci-gap — The primary source for the four-month, roughly eight-point historical lag on Epoch's composite index since January 2026. An aggregate historical estimate; not a release cycle and not a forecast.

**C1.** WhatLLM (July 2026). *Open-source vs proprietary LLMs 2026.* https://whatllm.org/blog/open-source-vs-proprietary-llms-2026 — Secondary report of Epoch's estimate (C0) with per-benchmark gaps from Epoch's June 2026 snapshot (the page mixes live tables with that June fallback; the figures in section 3.1 are frozen at June); where the gap persists (long-horizon agent work) and where it has closed; the harness confound; cost per completed task over cost per token.


### Field state, October 2026

**C3.** Thunder Compute (updated 1 October 2026). *Best Open Source LLMs (October 2026).* https://www.thundercompute.com/blog/best-open-source-llms — Current open-weight families, sizes, licences, vision support, and benchmark figures against hosted models; the practical gap is hardware.

**C4.** BenchLM (10 October 2026). *Local LLM Hardware Statistics.* https://benchlm.ai/stats/local-llm-hardware — Twelve ranked open-weight models with reference hardware; the single-GPU class (at most 32 GB) and its best score; licence share.

**C5.** Ollama (30 March 2026). *Ollama is now powered by MLX on Apple Silicon (preview).* https://ollama.com/blog/mlx — Unified-memory inference on Apple Silicon, one supported coding model, a throughput figure specific to that runtime and version, and the preview's own more-than-32 GB requirement.

**C6.** Shop ZimaSpace (2026). *Open models are catching frontier AI: is 2026 the year local AI becomes good enough?* https://shop.zimaspace.com/blogs/tech-ai-hub/open-models-vs-frontier-ai-local-ai-good-enough-2026 — A practitioner's framing of the "good enough" question; consulted for context, not figures.

### Models and vendor documentation

**B4.** Qwen3.5-9B — see Read first; companion larger model https://huggingface.co/Qwen/Qwen3.5-397B-A17B.
**B5.** Moonshot AI (27 July 2026). *Kimi K3 open-weights release* https://www.kimi.com/news/kimi-k3-open-source and model card https://huggingface.co/moonshotai/Kimi-K3 — Largest open-weight multimodal model (2.8 trillion total parameters, million-token context); custom licence needing its own review. Shows that "weights available" and "practical private deployment" are different things; on the watchlist, not a hardware recommendation.
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

- **October 2026, v1.0:** first standing snapshot. Direction restated from the public repository and the owner's 14 October clarification; field state researched (gap, availability, hardware classes, cost case, routing, debate); Vault pass named as the strongest candidate local workload, with no order decided (editor in chief, 14 October: candidates exist throughout mini-moi, coding and testing included); hardware deferred on evidence; Codex brief and companion folded in.
- **14 October 2026, Codex review applied before v1.0 is treated as settled:** privacy enforcement restated as a requirement verified at the retrieval and routing boundaries, not a delivered property of the gateway; embedding-identity gap closed (index pins digest and dimensions); Vault-first downgraded from decision to candidate and "stays hosted" made evidence-qualified; Epoch cited directly and the four-month lag kept historical, benchmark dates frozen at June 2026; hardware thresholds and throughput figures scoped to the runtime that published them; cost study's fix-commit ratio described as repair commits, qualification carried into the abstract; owner-controlled encrypted off-site backups permitted and the gold set split into local-only and shareable cases; five reading abstracts restored; C5 link corrected.
- **Not performed:** hardware purchase, model download, local benchmark, automatic routing, legal clearance, new release scope, public publication.
