# MiniMoi: private, local and hybrid intelligence

**Research snapshot:** 14 October 2026 (America/Chicago)  
**Version:** 0.1 — owner-directed discussion brief for Claude  
**Topic:** substantial local work, including coding, with selective frontier-model collaboration  
**Author:** Codex, restating Robert’s direction and synthesizing cited research  
**Status:** research and enduring product direction; not an infrastructure purchase, build authorization or change to MiniMoi 1.2 scope

## Abstract

MiniMoi should preserve its owner’s knowledge, working history and artifacts independently of any model vendor, and increasingly perform substantial useful work on privately controlled local infrastructure. “Local” includes real software engineering, not only summarization or administrative chores. Hosted frontier models remain valuable collaborators for demanding tasks, independent review and alternative perspectives. There is no fixed local/cloud quota: 60/40, 40/60 or another allocation is acceptable if actual quality, privacy, reliability and total cost justify it. The Vault provides continuity and controlled access to the particular context; models provide replaceable capabilities. This October 2026 snapshot connects that direction with research on local-first software, model routing, coding evaluation and downloadable multimodal models. Functionality comes first. Hardware, inference engines and automated routing should be evaluated separately after useful workflows exist. This document establishes a dated topic record and reading list, not a claim that MiniMoi already delivers a fully local assistant.

## Five sources to read first

These are **editorial reading abstracts written for Robert**, not copies of the authors’ abstracts. Primary-source claims and MiniMoi implications are distinguished. Paper coverage here is based on published abstracts; full experimental protocols have not been audited.

### 1. Local-first software: You own your data, in spite of the cloud

**Kleppmann, Wiggins, van Hardenberg and McGranaghan; Ink & Switch / Onward!, 2019.** [Read](https://www.inkandswitch.com/essay/local-first/)

The authors argue that ownership, offline use and collaboration can coexist. They describe seven ideals and prototype work on synchronization and data structures. This is about durable software and user agency, rather than LLM capability. It gives MiniMoi a useful foundation: personal knowledge should remain usable even if an external service disappears. **Our implication:** portability and recoverability of the Vault matter independently of which model answers a question. Adopting these ideals would not require adopting their particular synchronization technology.

### 2. RouteLLM: Learning to Route LLMs with Preference Data

**Ong et al.; 2024 preprint, revised February 2025.** [Read](https://arxiv.org/abs/2406.18665v4)

RouteLLM trains inexpensive routers to choose between stronger and weaker language models using preference data. The authors report cost reductions on selected benchmarks while preserving response quality, and investigate transferring routers to different model pairs. This supports making allocation an empirical decision rather than a fixed percentage. **Our implication:** begin with transparent routing rules and a MiniMoi evaluation set; introduce a learned router only if it improves results. This paper does not establish local privacy, reliable long-running coding or a universal savings rate.

### 3. SWE-Bench Pro Verified: A Reliable Benchmark for Software Engineering Agents

**Zheng et al.; September 2026 preprint, revision 9 October 2026.** [Read](https://arxiv.org/abs/2609.08149v3)

The authors identify evaluation leakage and task/test inconsistencies that can inflate coding-agent scores. Their proposed benchmark repairs instances and adds safeguards against exploiting evaluation information; some measured results decline. This is a current warning against buying hardware based on a leaderboard alone. **Our implication:** evaluate local builders on genuine repository tasks with independently held acceptance criteria, actual execution and review. Passing visible tests is useful evidence, not sufficient proof. These are the authors’ findings, not a universal ruling that every prior benchmark result is invalid.

### 4. Qwen3.5-9B model card

**Qwen team; living vendor documentation, accessed 14 October 2026.** [Read](https://huggingface.co/Qwen/Qwen3.5-9B)

This downloadable Apache-2.0 model combines text with a vision encoder and documents serving and agent integrations. The vendor publishes benchmark comparisons across language and visual tasks. It illustrates that local candidates need not be restricted to text or clerical work. **Our implication:** a smaller native multimodal model is a concrete candidate for screenshot interpretation, bounded coding and private document work. Availability and architecture are verifiable; claimed comparative performance remains vendor evidence until reproduced on our workflows. Correct runtime/quantization support is also necessary.

### 5. Kimi K3 open-weights release and model card

**Moonshot AI; release announcement 27 July 2026, model card accessed 14 October 2026.** [Release](https://www.kimi.com/news/kimi-k3-open-source) · [Model card](https://huggingface.co/moonshotai/Kimi-K3)

Moonshot released a much larger open-weight multimodal model aimed at sustained coding, reasoning and knowledge work. Its published architecture has 2.8 trillion total parameters and a million-token context window. The release demonstrates the ambition of downloadable systems, but does not make them inexpensive to operate. **Our implication:** distinguish “weights available” from “practical private deployment.” K3 belongs in the capability watchlist; it is not a hardware recommendation or independently proven replacement for our hosted agents. Its custom license needs its own review.

## 1. What Robert is asking MiniMoi to become

Robert’s direction in the 14 October discussion:

- Private and local operation is one of MiniMoi’s objectives and should remain visible in design decisions.
- Keep a significant portion of work locally, **including building code**. Do not permanently assign local models only easy supporting tasks.
- Retain two or three strong external model relationships—OpenAI, Claude and Grok were the examples—for escalation, collaboration and differing perspectives.
- The share of local work is not the objective. Useful results, affordability and quality are.
- Continue working between models without losing the project’s context or the reasons behind decisions.
- Finish the Vault’s functionality first. Do not turn the vision discussion into an infrastructure project that delays it.
- Use bounded comparisons before committing to a model, vendor or hardware investment.

**Proposed restatement:** MiniMoi owns its continuity and supports privately controlled execution wherever it is capable enough, with deliberate access to external intelligence when appropriate. Models are teammates and replaceable engines; the owner’s context is the lasting asset.

This is consistent with Robert’s recorded September principle of avoiding tight dependence on a product. It does not imply that every component must be on a laptop, that cloud services are forbidden, or that every task should be attempted locally first.

## 2. January to October 2026: continuity, not an invented history

| Checkpoint | What we can responsibly say |
|---|---|
| January 2026 | Robert identifies this as the beginning of the journey. This brief uses that owner-provided date; it does not reconstruct undocumented January implementation, model choices or benchmarks. |
| September 2026 | Existing MiniMoi records discuss product independence and research alignment with agent practice. |
| 2 October 2026 | The intelligence-layer proposal explicitly distinguishes owner-local processing from external routes. It is a design source, not proof that every privacy boundary is deployed. |
| 14 October 2026 | Robert clarifies the substantial-local-work objective, explicitly includes coding, rejects a fixed percentage and keeps infrastructure separate from Vault functionality. |

The enduring theme is learning and doing better together while retaining ownership of the particular context. Today's model availability expands the options; it should not determine the product’s identity.

**Internal continuity sources:**

- `planning-studio/library/sources/mini-moi-collaborative-work-vision/robert-mini-moi-product-independence-principle--2026-09-04.txt` — Robert’s recorded product-independence statement.
- `planning-studio/inbox/agent-field-alignment-research-2026-09-07/README.md` — the existing research topic and its distinction between investigation and adoption.
- `planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/SPEC_INTELLIGENCE_LAYER_v0_3_2026-10-02.md` — local/private routing and disclosure proposals; some technical choices have since evolved.
- The 14 October Robert–Codex conversation is the source for the direction above. This document is an attributed synthesis, not a verbatim transcript. No January transcript was supplied for this brief.

## 3. Separate knowledge, execution and infrastructure

**Knowledge and continuity:** originals, dated conversations, artifacts, decisions, sources, access rules and recoverable history. This is the Vault’s functional concern.

**Execution:** a model’s ability to retrieve authorized context, reason, read images, write code, run tools and contribute evidence. This is where local and hosted agents can be compared.

**Infrastructure:** model files, RAM/VRAM, storage, inference engines, network boundaries and operational support. This is a separate implementation choice.

These layers meet through interfaces. A model should not become the sole holder of project history. A local model should not receive every private record merely because it is local. An encrypted backup in company-controlled cloud storage is different from disclosure of plaintext to a hosted model. Likewise, running a model on a laptop does not make remote tools, logging or telemetry local.

**Privacy precedes model selection.** If material is designated local-only, quality or cost routing must not silently send it to a hosted reviewer. The permitted alternatives are local review, owner review, a disclosed inability to complete the task, or separately authorized sharing of a suitable subset. Summaries and embeddings can also carry sensitive information.

The existing disclosure specification remains its own workstream. This brief does not reassign all personal material to a new class or change capture grants.

## 4. Practical examples for MiniMoi

The following are proposed workflows, **not claims of completed implementation**.

| Work | A substantial local contribution | Selective external contribution | Acceptance evidence |
|---|---|---|---|
| Build a bounded feature | Read relevant code, implement the feature, write meaningful tests and produce a reviewable diff | Challenge the design or independently review an authorized diff | Functional acceptance, appropriate tests, actual diff review and rollback plan |
| Diagnose a bug | Reproduce it, inspect logs, identify the cause and propose or implement a fix | Escalate an ambiguous failure or review a consequential correction | Reproduction before and after; no unresolved regression |
| Private Vault question | Retrieve permitted records, distinguish original evidence from summaries, answer with sources | Receive only a deliberately authorized excerpt, if needed | Supported citations, privacy-route check, honest coverage gaps |
| Screenshot/UI work | Interpret a screenshot, propose changes and implement a bounded UI slice | Provide another design perspective when the image may be shared | Correct image understanding plus desktop/mobile owner acceptance |
| Research or presentation | Gather permitted references, organize findings and draft an artifact | Compare interpretations or improve a difficult argument | Source checks, attribution and review of the actual artifact |
| Operations | Read bounded status, summarize anomalies and prepare a concrete action | Review a high-impact operation or unfamiliar incident | Actual service evidence and existing authorization gates |

A useful coding comparison could ask a local agent and a hosted agent to solve the same small bug independently in isolated checkouts. Give both the same brief, tools and resource limits; keep final acceptance separate. Compare correctness and rework before debating who produced a more convincing explanation. This is a proposed evaluation, not permission to start one.

## 5. What others are doing in the field

**Routing and cascades.** RouteLLM chooses a model before generation; FrugalGPT studies staged calls that can escalate. These are different techniques. Both support measuring the allocation rather than declaring a universal local percentage. Their original experiments do not prove that routing transfers unchanged to coding agents. [B2, B6]

**Coding with local execution.** Aider documents using Ollama models for repository editing. It is a concrete implementation example, not proof that every model works equally well or that a company’s entire workflow is offline. Context settings, tool compatibility and edit reliability matter. [B7]

**Independent perspectives.** Debate research finds that added discussion does not automatically beat simpler ensembles; agreement can conceal error. MiniMoi should preserve initial independent findings, source-based disagreements and the eventual reason for a decision. Different vendors are useful diversity, not guaranteed statistical independence. [B8]

**Downloadable multimodal capability.** Qwen, Google, Mistral and Moonshot publish image-capable models at very different scales. The inference engine must support the selected model’s vision path; a native vision model can become effectively text-only in an unsupported deployment. [B4, B5, B10, B11]

**Portable runtimes.** llama.cpp supports a broad range of quantized inference configurations; MLX-LM provides an Apple-Silicon-oriented route. Neither is a commitment for MiniMoi. Models, quantization and hardware must be tested together. [B12, B13]

## 6. October 2026 examples and corrections

This is a representative watchlist, not a ranking of the world’s strongest models.

| Candidate/example | Why it matters | Important qualification |
|---|---|---|
| OpenAI gpt-oss-20b / 120b | Downloadable reasoning and tool-use models | Text-only; separate from the hosted model in this Codex conversation. |
| Qwen3.5-9B; larger Qwen3.5-397B-A17B | Native vision and language across different sizes | The 9B model is not equivalent to the larger model; vendor benchmark claims require comparison. |
| Google Gemma 3 4B/12B/27B | Downloadable image-and-text options | The 1B model is text-only; terms and runtime support require checking. |
| Mistral Ministral 3 14B | Another downloadable vision-and-language family | Adds a non-Chinese candidate without establishing superior safety or quality. |
| Moonshot Kimi K2.5 | Concrete large-model hybrid CPU/GPU deployment example | Heavy compression and native weights have different resource/quality profiles; some execution paths lack vision. |
| Moonshot Kimi K3 | Newer open-weight multimodal capability, released July 2026 | K2.5 was the conversation’s worked example, **not the latest October release**. Its budget estimates must not be applied to K3. |

The prior $5,000 and $25,000 hardware discussions were exploratory configurations, **not supplier quotes or performance guarantees**. The K2.5 KTransformers guide lists at least 600 GB system RAM and 48 GB total GPU memory for its native-weight hybrid route. Those requirements do not establish end-to-end image acceptance or business readiness. [B14]

The earlier eight-H200 example was one documented deployment, not a universal minimum. Nor can anyone estimate this chat’s undisclosed hosted-model weights accurately enough to specify a private copy.

## 7. How to judge a substantial local workload

Measure **accepted work**, not merely calls or tokens. A local model that produces a patch needing two hours of repair has not necessarily saved money.

For an eventual bounded comparison, record:

- Task class, model/version, quantization, agent tools and disclosure permission.
- Correctness, functional acceptance, review findings and regressions.
- Human intervention and repair time, including hosted review/escalation.
- Elapsed time, first-response latency and reliability under normal resource limits.
- Private-data disclosure and refusal behavior, including accidental cloud fallback.
- Total cost: hardware amortization, electricity, operations, subscriptions and API usage.

Report local shares separately by tasks, accepted coding work, tokens and cost. A 60% token share can coexist with a much smaller share of completed features. No quota should force a privacy violation or acceptance of weaker work.

A future starting set could contain 12–20 representative tasks: several actual bug fixes/features, document questions with gold citations, screenshot interpretation and operations diagnosis. Repeat across more than one task per category and preserve failed attempts. This is a proposed method, not a promised statistical sample size or current regression-suite change.

## 8. Model improvement and boundaries

Fine-tuning and model-specific adapters can improve domain behavior or workflow conventions; distillation can teach selected capabilities where permitted. None guarantees recovery of a proprietary model’s weights or general capability. Retrieval supplies current personal context without putting every private fact into model weights. Adding tools can improve usefulness without changing weights. [B15]

For a private system, the useful comparison is often **model plus tools plus authorized context plus review**, rather than a bare model benchmark. Keep those components separable so improvements do not trap the owner in one vendor.

Downloaded weights also do not remove supply-chain, licensing or jurisdiction questions. Pin and review loaders/dependencies, control outbound traffic, retain license terms and evaluate actual behavior. National origin alone neither proves malicious behavior nor establishes safety. Business procurement needs its own current legal/security review. That discussion should not expand this functional vision into a regulatory work package.

## 9. Keep the Vault work moving

Near term, finish useful capture, preservation, recovery, retrieval with sources and the agreed access behavior. Preserve interfaces that allow the answering model to change. Document what is deployed, what is proposed and where coverage is incomplete.

Later, authorize a bounded local-vs-hosted comparison, including coding and at least one visual task. Use the results to choose models and only then price suitable infrastructure. Do not make local hardware a precondition for finishing the Vault or the MiniMoi 1.2 release.

**For Claude’s discussion:** Where does the Vault design accidentally assume a vendor? Can a new local model use the same permitted knowledge and preserve attribution? What must never leave local control? Which real coding task would make a fair comparison? Which capabilities are missing from the surrounding agent tools rather than from the model itself?

## 10. Maintain this as a dated topic

This brief extends the existing September Agent Field Alignment research record; it does not create a competing Vault spec or a new build initiative.

**Suggested cadence for Robert to adopt:** quarterly, with additional updates when a relevant model, license, runtime or actual MiniMoi comparison materially changes the assessment. Suggested next snapshot: January 2027. No scheduled automation is created by this document.

Preserve this October file unchanged after review; write the next dated snapshot beside it and link its predecessor. Each update should record:

1. What the owner’s objective means now and what has not changed.
2. New primary sources and changed model/runtime/license availability.
3. MiniMoi’s actual deployment and measured task evidence, including failures.
4. Changed understanding, retired assumptions and unresolved questions.
5. Proposed experiments or decisions, kept separate from authorized scope.

Do not rewrite the January origin story from later success. Add original January sources when located, preserving dates and attribution.

## Bibliography

All external sources below were opened on **14 October 2026**. Living documentation can change after this snapshot. Academic entries were reviewed at abstract level unless stated otherwise; vendor and project pages were reviewed directly. This is an annotated reading bibliography, not a systematic literature review. It contains no independent performance reproduction.

### Principles, routing and evaluation

**B1.** Kleppmann, M.; Wiggins, A.; van Hardenberg, P.; McGranaghan, M. (2019). *Local-first software: You own your data, in spite of the cloud.* Ink & Switch / Onward! [Article](https://www.inkandswitch.com/essay/local-first/). Foundational research and prototype experience on ownership and offline collaboration. **Read first.**

**B2.** Ong, I., et al. (2024; revision 2025). *RouteLLM: Learning to Route LLMs with Preference Data.* [arXiv v4](https://arxiv.org/abs/2406.18665v4). Learned model selection; benchmark-bound cost/quality evidence. **Read first.**

**B3.** Zheng, P., et al. (2026; v3, 9 October). *SWE-Bench Pro Verified: A Reliable Benchmark for Software Engineering Agents.* [arXiv v3](https://arxiv.org/abs/2609.08149v3). Evaluation leakage and task-quality concerns; proposed repairs. Recent preprint, not an independently validated MiniMoi acceptance suite. **Read first.**

**B6.** Chen, L.; Zaharia, M.; Zou, J. (2023). *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance.* [arXiv](https://arxiv.org/abs/2305.05176). Cascades and cost-aware model combinations. Historical benchmark findings should not become present-day savings promises.

**B8.** Choi, H. K.; Zhu, X.; Li, S. (2025). *Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models?* NeurIPS 2025 Spotlight. [arXiv v2](https://arxiv.org/abs/2508.17536v2). Tests debate against voting; useful challenge to automatic-consensus assumptions.

**B9.** Deng, X., et al. (2025). *SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?* [arXiv v2](https://arxiv.org/abs/2509.16941v2). Repository-level coding benchmark and failure analysis; background to B3, not interchangeable with it.

### Models and vendor documentation

**B4.** Qwen team. *Qwen3.5-9B model card.* [Official repository](https://huggingface.co/Qwen/Qwen3.5-9B). Native multimodal model, architecture, serving and vendor evaluations. **Read first.** Companion larger-model example: [Qwen3.5-397B-A17B](https://huggingface.co/Qwen/Qwen3.5-397B-A17B).

**B5.** Moonshot AI (27 July 2026). *Kimi K3 open-weights release.* [Official announcement, Chinese](https://www.kimi.com/news/kimi-k3-open-source); [English model card](https://huggingface.co/moonshotai/Kimi-K3); [custom license](https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE). Release availability and vendor capability claims; English summary above is Codex’s interpretation. **Read first.**

**B10.** Google. *Gemma 3 model card.* [Official documentation](https://ai.google.dev/gemma/docs/core/model_card_3). Image-capable model variants and limitations.

**B11.** Mistral AI. *Ministral 3 14B specifications.* [Official documentation](https://docs.mistral.ai/models/ministral-3-14b-25-12). Downloadable vision-and-language candidate.

**B16.** OpenAI. *gpt-oss-120b model specifications* and *Running gpt-oss with Ollama.* [Model](https://developers.openai.com/api/docs/models/gpt-oss-120b); [local guide](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-ollama). Text-only reasoning/tool-use model; published local memory guidance. Separate from hosted Sol/Codex models.

### Practice, deployment and adaptation

**B7.** Aider project. *Ollama integration.* [Documentation](https://aider.chat/docs/llms/ollama.html). Practical local-model repository-editing workflow; configuration guidance rather than a controlled performance study.

**B12.** ggml-org. *llama.cpp.* [Official repository](https://github.com/ggml-org/llama.cpp). Local inference across hardware backends; exact model and vision support is version-dependent.

**B13.** Apple ML Explore. *MLX-LM.* [Official repository](https://github.com/ml-explore/mlx-lm). Apple-Silicon inference and fine-tuning implementation. A platform-specific option, not a MiniMoi hardware decision.

**B14.** KTransformers / KVCache.ai. *Running Kimi K2.5 with SGLang and KT-Kernel.* [Implementation guide](https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/Kimi-K2.5.md). Native-weight hybrid CPU/GPU hardware example; no supplier quote or independent end-to-end vision proof.

**B15.** Google. *Gemma model fine-tuning.* [Official documentation](https://ai.google.dev/gemma/docs/tune). Full-weight and adapter-based customization; specialization is not equivalent to recreating a frontier model.

**B17.** Moonshot AI. *Kimi K2.5 model card and license.* [Model](https://huggingface.co/moonshotai/Kimi-K2.5); [license](https://huggingface.co/moonshotai/Kimi-K2.5/raw/main/LICENSE). The worked example from the originating discussion. Its terms are not K3’s terms.

**B18.** Unsloth. *Kimi K2.5: How to Run Locally.* [Guide](https://unsloth.ai/docs/models/tutorials/kimi-k2.5). Compressed-weight deployment estimates and stated vision limitations; third-party implementation claims, not Moonshot guarantees.

## Snapshot record

- **2026-10-14 — v0.1:** Robert’s substantial-local-work direction recorded, including coding and flexible allocation; functionality/infrastructure separation explicit; October primary-source bibliography and five reading abstracts added; K2.5/latest-model confusion corrected by verifying K3’s July release.
- **Not performed:** hardware purchase, model download, local benchmark, automatic routing, legal clearance, new release scope or public publication.
