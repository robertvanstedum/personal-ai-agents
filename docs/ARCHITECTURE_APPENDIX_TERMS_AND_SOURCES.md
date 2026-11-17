# Appendix: What mini-moi does, in the industry's terms

*1.2 beta, November 2026. Companion to [ARCHITECTURE.md](../ARCHITECTURE.md). MCP and A2A are vocabulary
borrowed here, not protocols mini-moi runs.*

## Why this exists

ARCHITECTURE.md describes mini-moi in my own words. This appendix maps those words to the terms the field uses in
2026, so that someone scanning the repository recognizes what is here. It is also my reading list: every term carries a source I have
found worth reading, with a one-line note on what it adds.

Rules I hold myself to here. A term is claimed only where the code earns it; the fit column says **exact**, **close**
or **stretch**, and a stretch is a term to learn from, not to use. Sources are marked **[V]** when the page was
opened and the definition read on 12 November 2026, **[E]** when the page exists but could not be opened (paywall or
block) and the definition came from the publisher's own summary, **[U]** when only secondary. Definitions are mine,
not quotations.

Radar status comes from the Thoughtworks Technology Radar, volumes 34 and 35 (2026,
https://www.thoughtworks.com/radar/techniques) [V]. **▲radar** means Adopt or Trial on the Radar.

---

## 1. The learning loops in the three learning domains

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Reactions and comments update a learned preference profile that is injected into the next scoring run | **Preference learning from implicit feedback**; user profile memory, personalization, learned user model | Inferring what a person likes from behaviour rather than explicit ratings, kept as a profile used at inference | Google ML course, explicit vs implicit feedback https://developers.google.com/machine-learning/recommendation/collaborative/basics [V]; LangChain memory concepts, profile vs collection https://docs.langchain.com/oss/python/concepts/memory [V] | exact |
| The same, as a property of the system | **Data flywheel**; feedback loop, self-improving system | Data produced by using the system is fed back to improve it | NVIDIA glossary https://www.nvidia.com/en-us/glossary/data-flywheel/ [V] | close: our loop updates a profile and a prompt, not model weights |
| The profile is placed in the scoring prompt | **Context engineering**; procedural memory, prompt personalization | Curating the tokens the model sees each call, including learned instructions and memories | Anthropic, Effective context engineering (Sep 2025) https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents [V]; LangChain write/select/compress/isolate https://www.langchain.com/blog/context-engineering-for-agents [V] | exact ▲radar |
| Weekly synthesis over 30 days of observations: "what has been thought about this before" | **Reflection** (Generative Agents); memory consolidation, structured note-taking | Periodically synthesizing low-level memories into higher-level insights that are stored and retrieved later | Park et al. 2023 https://arxiv.org/abs/2304.03442 [V]; Mem0 consolidation https://arxiv.org/abs/2504.19413 [V] | exact |
| The serendipity pool | **Serendipity recommendation**; filter-bubble mitigation, exploration vs exploitation, diversity re-ranking | Deliberately surfacing unexpected but relevant items so the recommender does not narrow to its own past | Bursting Filter Bubble (2025) https://arxiv.org/abs/2502.13539 [V]; SOLAR, EMNLP 2025 findings https://aclanthology.org/2025.findings-emnlp.538.pdf [E] | exact |
| The thinking record: question, leaning, evidence | **Decision record (ADR-style)**; reasoning trace, evidence log, sensemaking "build case" | A durable record of a question, the current leaning and the evidence, so later work builds on it | ADR https://adr.github.io/ [V]; Pirolli and Card sensemaking loop, via NATO proceedings https://www.sto.nato.int/publications/STO%20Meeting%20Proceedings/RTO-MP-SCI-174/MP-SCI-174-03.pdf [U] | close |
| Ranking a few hundred candidates with a model | **LLM-as-a-judge**; model-based grader, LLM reranking | A strong model scores or ranks candidates against a rubric instead of hand-built heuristics | Zheng et al. 2023 https://arxiv.org/abs/2306.05685 [V] | exact |

## 2. Language immersion

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Unscripted voice conversation with AI personas in real scenes | **Dialogue-based CALL** (computer-assisted language learning); conversational agent for L2 speaking, AI language tutor, role-play practice | Systems that hold natural conversations with learners for speaking practice, often in role-play with corrective feedback | Hou and Min, ReCALL 38(1) 2026, meta-analysis https://www.cambridge.org/core/services/aop-cambridge-core/content/view/31847710516602398819C5E594038E7B/S0958344025100268a.pdf/dialogue-based-computer-assisted-language-learning-systems-for-second-language-speaking-development-a-three-level-meta-analysis.pdf [V]; role-play prompting, CSEDU 2025 https://scitepress.org/PublishedPapers/2025/132354 [E] | exact |
| Personas | **AI persona / role-play agent**; character agent | A model instructed to behave as a consistent interlocutor | CSEDU 2025 above [E] | exact |
| Transcript review shapes the next session | **Reflection / self-critique loop**; episodic memory, corrective feedback, adaptive learning path | Reviewing a session's record to produce guidance that changes the next one | Reflexion https://arxiv.org/abs/2303.11366 [V]; LangChain episodic memory [V] | close |
| A separate learning record per person | **Learner model / user profile**; long-term memory, personalization | A persistent per-user store of progress, errors and preferences | AWS AgentCore Memory https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html [V] | close |

## 3. Memory

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| The Vault: records with front matter and immutable editions | **Agent memory / long-term memory**; semantic memory, agent-maintained knowledge base, file-based memory | Durable structured records outside the context window that agents read and write across sessions | LangChain memory concepts [V]; CoALA taxonomy https://arxiv.org/abs/2309.02427 [V]; Thoughtworks Vol. 35 "Agent-maintained knowledge bases" (Trial) [V] | exact ▲radar |
| Keyword plus vector index | **Hybrid search** (BM25 plus vector, RRF fusion) | One query runs full-text and vector search and merges the ranked results | Azure AI Search hybrid overview https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview [V] | exact |
| The agent-neutral "ask" with a citation rule | **RAG with citations / grounded generation**; retrieval-augmented generation, source attribution | Answering from retrieved records and returning the passages that support each claim | Lewis et al. 2020 https://arxiv.org/abs/2005.11401 [V]; Anthropic Citations https://platform.claude.com/docs/en/build-with-claude/citations [V] | close: the record is people-maintained, current by design (a start-here page beats a search over June), and cited; production retrieval is keyword over a published snapshot, not a vector index |
| Immutable editions: a correction is a new record | **Event sourcing / immutable snapshots**; content versioning | Records are never edited in place; each change is a new edition | Fowler, Event Sourcing https://martinfowler.com/eaaDev/EventSourcing.html [V] | close |
| The Chief of Staff store: goals, plans, obligations, decisions, one command writer with receipts | **Persistent goals / goal tracking**; system of record for the owner, single-writer pattern, decision records | Objectives and commitments stored outside the conversation so agents re-ground on them each turn | OpenAI Codex Goals https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex [V]; a16z, system of record to system of intelligence (May 2026) https://a16z.com/from-system-of-record-to-system-of-intelligence/ [V] | close |
| The lessons ledger: append-only, hash-chained, adopted by the owner | **Tamper-evident audit log**; hash-chained log, procedural memory (adopted rules), lessons learned | Each entry hashes its predecessor so edits are detectable; adoption turns entries into standing rules | Microsoft Agent Governance Toolkit, audit logging https://microsoft.github.io/agent-governance-toolkit/tutorials/04-audit-and-compliance/ [V]; LangChain procedural memory [V] | exact |
| Append-only turn logs, usage records, receipts | **Audit trail / traceability**; observability, record-keeping (EU AI Act Art. 12), provenance | Automatic logging sufficient to reconstruct and attribute what happened | EU AI Act Art. 12 https://artificialintelligenceact.eu/article/12/ [V]; OpenTelemetry GenAI conventions https://github.com/open-telemetry/semantic-conventions-genai [V] | exact |

## 4. The two partners and their runtimes

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| The Master Craftsman: build and operations front door that coordinates the coding agents | **Orchestrator / supervisor agent** (orchestrator-workers); manager pattern, lead agent plus subagents, team of coding agents | A central agent that decomposes work, delegates to workers and judges results | Anthropic, Building effective agents https://www.anthropic.com/engineering/building-effective-agents [V]; Anthropic multi-agent research system https://www.anthropic.com/engineering/multi-agent-research-system [V]; OpenAI, A practical guide to building agents https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf [V]; LangChain multi-agent https://docs.langchain.com/oss/python/langchain/multi-agent [V] | exact |
| The Chief of Staff: cross-domain partner | **Personal agent**; "brilliant friend", decision-support agent, copilot (weaker) | An agent whose value is judgment and context across domains, not task execution | Claude's Constitution https://www.anthropic.com/constitution [V] | close: "thinking partner" is my phrase, not yet a standard term |
| Bounded agent runtimes in containers behind a stable interface | **Agent runtime**; managed agent runtime, session isolation, ports-and-adapters architecture | A hosting layer that runs agent code in an isolated session behind a fixed contract, independent of model and framework | AWS AgentCore Runtime https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html [V]; Thoughtworks Vol. 35 "Managed agents runtime" (Trial) [V]; Cockburn, Hexagonal architecture https://alistair.cockburn.us/hexagonal-architecture/ [V] | close: two runtimes behind one interface, not a managed runtime service ▲radar |
| The runtime can be swapped | **Model-agnostic / framework-agnostic**; optionality, pluggable runtime | Memory and contract do not depend on one model or framework | AgentCore Runtime [V]; Thoughtworks, harness engineering https://www.thoughtworks.com/en-us/insights/podcasts/technology-podcasts/what-harness-engineering [V] | exact |
| The instructions, hooks and checks around the agents | **Agent harness / harness engineering**; enterprise harness, Agent Skills | The tools, instructions, permissions, checks and memory wrapped around a model to make it reliable | Thoughtworks Vol. 34 themes https://www.thoughtworks.com/about-us/news/2026/combat-ai-cognitive-debt-radar-v34 [V]; Enterprise harness (Assess) https://www.thoughtworks.com/radar/techniques/enterprise-harness [V]; Agent Skills https://agentskills.io/ [V] | exact ▲radar |

### 4-word check: skill, tool, record, memory (added 13 Nov)

A public explainer (IBM Technology, Martin Keen, "Skills vs MCP vs RAG vs Memory", 3 Sep 2026,
https://www.youtube.com/watch?v=X4FVEEegCbk [V for existence]) sorts agent knowledge into four kinds. mini-moi
already has all four under its own names, and keeps the persona outside them.

| Explainer's word | mini-moi's word | What it is here | What it is not |
|---|---|---|---|
| Skill | Skill (`SKILL.md`) | When and how to use a tool that already exists; read only when the turn needs it | A new power; standing text in the persona |
| MCP | Tool, and an effect when it leaves mini-moi | The typed door: GitHub, delivery record, host reads, notes. Effects that leave mini-moi propose, I approve, a receipt follows. A note needs no approval card: it is shown back as "Noted: …" (decided 13 Nov) | The MCP protocol or a plugin bus |
| RAG | The record | Specs, planning repo, issues, artifacts; people maintain it, agents retrieve and cite it. Today the Master Craftsman cannot read the planning repo and its repo copy lags; both are in flight (#567) | A vector index over everything; currency matters more than recall |
| Memory | Note, then lesson | A settlement I made, written and shown back; a lesson is a note that repeated and that I promoted | The agent writing what it thinks it learned |
| (outside the four) | Persona | The stable voice: a view, disagreement from evidence, cite or say there is no record | A place for procedures |

Helpers return evidence and never write notes or lessons. Coding agents are an effect with a receipt, not residents
of mini-moi's memory. The note line is specified and not yet wired to chat at the pinned commit.

## 5. Multi-agent coordination

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| The request ledger: immutable request, append-only events | **Task lifecycle (A2A Task)**; event sourcing, work item, durable execution | A trackable unit of work with an id and state transitions; terminal tasks never restart | A2A, Life of a Task https://a2a-protocol.org/latest/topics/life-of-a-task/ [V]; key concepts https://a2a-protocol.org/latest/topics/key-concepts/ [V] | close: the same lifecycle in A2A's vocabulary; mini-moi does not implement the A2A protocol |
| Receipt means pickup, not completion; a result is a report, not verification | **Acknowledgement vs completion**; task states; outcome vs transcript | Accepting work is not finishing it; an agent's report is not a verified outcome | A2A task states [V]; Anthropic, Demystifying evals (outcome vs transcript) https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents [V] | close |
| Guildhall rooms: turn-taking, addressing by name, owner corrections supersede pending work | **Multi-agent group chat**; speaker selection, conversation orchestration, handoffs | Several agents share one conversation under selection and override rules | AutoGen SelectorGroupChat https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html [V]; OpenAI Agents SDK handoffs https://openai.github.io/openai-agents-python/handoffs/ [V] | exact |
| Chartered helper seats with per-task and per-day caps | **Budget-capped agents**; per-key budgets, token budgets, rate limits | Each agent has an identity whose spend and rate are capped per period | LiteLLM budgets https://docs.litellm.ai/docs/proxy/users [V]; Anthropic task budgets https://platform.claude.com/docs/en/build-with-claude/task-budgets [V]; Claude Code `--max-budget-usd` https://code.claude.com/docs/en/cli-reference [V] | exact |
| Delegation cards: a coding agent runs in a clean clone with a CLI budget after my exact approval, supervised | **Sandboxed coding agent / delegated task in an isolated workspace**; subagent, cloud agent task, unattended agentic builds | Authorizing an agent to work on a fresh copy under explicit tool, network and spend limits | Claude Code subagents https://code.claude.com/docs/en/sub-agents [V]; Codex cloud https://learn.chatgpt.com/docs/cloud [V]; Thoughtworks Vol. 35 "Agent sandboxing" (Adopt), "Unattended overnight agentic builds" (Trial) [V] | close: each task needs my exact approval and runs supervised; no standing runner ▲radar |
| Review by a different model family | **Cross-model review**; LLM council, independent reviewer, model diversity | A reviewer from another family does not share the author's blind spots | Song 2026, When Does a Second Model Help? https://arxiv.org/abs/2610.01471 [V]; Claude Code best practices https://code.claude.com/docs/en/best-practices [V] | exact |
| Agents ask each other through the ledger, not by polling | **Handoff**; agent-to-agent protocol, asynchronous delegation | Passing work between agents through a defined contract | A2A https://a2a-protocol.org/latest/ [V] | exact for handoff; A2A named as the protocol whose vocabulary we borrow, not one we speak |

## 6. Control

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| An owner-approved, digest-pinned policy with approval tiers: read, standing-rule writes, exact per-effect approval | **Risk-tiered human oversight**; policy-as-code, tool risk ratings, human-in-the-loop approval, least agency | Classify actions by impact and reversibility; require approval only above a threshold, in machine-readable policy | Thoughtworks Vol. 35 "Risk-tiered human oversight" (Trial) [V]; OpenAI practical guide, tool safeguards [V]; Open Policy Agent https://www.openpolicyagent.org/docs/latest/ [V]; OWASP Top 10 for Agentic Applications 2026 https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ [V] | close: the policy does not cover web search, the runtime's read tools or recall ▲radar |
| Single-use grants | **Just-in-time, single-use authorization**; task-scoped credentials, tool approval | Approval valid for one action, then consumed | OpenAI Agents SDK human-in-the-loop https://openai.github.io/openai-agents-python/human_in_the_loop/ [V]; LangGraph interrupts https://docs.langchain.com/oss/python/langgraph/interrupts [V] | exact |
| Fenced receipts and read-back verifiers | **Verification / outcome checking**; code-based graders, post-condition checks | Deterministic checks that the intended effect happened before the action counts | Anthropic, Demystifying evals [V]; Claude Code Stop hooks [V] | close: "read-back" is aviation, not AI vocabulary |
| The owner Stop fence | **Human oversight with stop control**; kill switch, interrupt | The overseer can halt the system safely at any time | EU AI Act Art. 14 https://artificialintelligenceact.eu/article/14/ [V] | exact |
| A gateway key per agent with a cap and a rate limit | **Virtual keys / per-agent credentials**; agent identity, non-human identity, least privilege | Each agent authenticates with its own key, scoped and capped at the gateway | LiteLLM virtual keys [V]; Microsoft Entra Agent ID https://learn.microsoft.com/en-us/entra/agent-id/ [V]; NIST least privilege https://csrc.nist.gov/glossary/term/least_privilege [V] | exact |
| The voice budget reserves before the call and fails closed | **Fail-closed budget enforcement**; reserve-then-commit, hard cap | If the reservation fails, the call does not happen | NIST fail secure https://csrc.nist.gov/glossary/term/fail_secure [V] | exact |
| The supervisor relays decisions to me and approves nothing | **Human-in-the-loop with separation of duties**; human-on-the-loop, guardian agent (monitor) | The system pauses for a human decision; the relay cannot itself approve | Arize, HITL vs HOTL https://arize.com/glossary/human-on-the-loop/ [V]; NIST separation of duty https://csrc.nist.gov/glossary/term/separation_of_duty [V]; Gartner guardian agents (Jun 2025) https://www.gartner.com/en/newsroom/press-releases/2025-06-11-gartner-predicts-that-guardian-agents-will-capture-10-to-15-percent-of-the-agentic-ai-market-by-2030 [E] | exact |
| Platform-signed assertions for one subject and one request | **Agent identity / workload identity**; signed tokens, zero trust for agents | Short-lived signed identities issued by the platform rather than shared secrets | NIST AI Agent Standards Initiative https://www.nist.gov/artificial-intelligence/ai-agent-standards-initiative [V]; NIST SP 800-207 https://csrc.nist.gov/pubs/sp/800/207/final [V] | exact |
| Code floors on policy tiers | **Deterministic guardrails / defence in depth**; rules-based guardrails | Limits enforced by code regardless of what the model decides | OpenAI Agents SDK guardrails https://openai.github.io/openai-agents-python/guardrails/ [V]; Claude Code, deterministic vs non-deterministic controls [V] | exact ▲radar |
| Container isolation for the agents | **Agent sandboxing**; filesystem and network isolation, blast-radius containment | OS-level boundaries on what an agent can read, write and reach | Anthropic, Claude Code sandboxing https://www.anthropic.com/engineering/claude-code-sandboxing [V]; Thoughtworks Vol. 35 (Adopt) [V] | exact for the Master Craftsman (no published port, no socket, egress only to model hosts); close for the Chief of Staff agent, whose main configuration allows exec, write, edit and any-URL fetch inside its mounts ▲radar |

## 7. The model layer

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| A gateway with nine tier routes, three providers by strong, mid, low | **LLM gateway / AI gateway**; model routing, model tiers | A proxy that routes, authenticates, meters and limits model calls | LiteLLM proxy docs https://docs.litellm.ai/docs/proxy/users [V]; Anthropic routing pattern [V] | exact |
| One-line role switching | **Model routing by alias**; config-driven model selection | Swap the model behind a role by changing a route, not code | LiteLLM routing [V] | exact |
| Declared failover, one level | **Fallbacks / failover** | Retry on a declared alternative when the primary fails | LiteLLM fallbacks [V] | exact |
| Per-call sanitized receipts, usage records, cost reporting against a policy | **FinOps for AI**; AI cost observability, usage metering, unit economics | Cost per call, user or outcome, reported against budgets | FinOps Foundation, FinOps for AI https://www.finops.org/wg/finops-for-ai-overview/ [V]; OpenTelemetry GenAI [V] | exact |
| The route catalogue: plan access vs metered API, one failover | **Cost-aware routing**; subscription vs API billing | Choosing the cheapest valid access path for a call class | no standard term; FinOps unit economics is nearest [V] | stretch |
| Personal context kept above the model layer | **Model-agnostic; context layer**; system of intelligence, memory portability | Memory and policy live in the platform, so any model can be plugged in beneath | AgentCore model flexibility [V]; a16z system of intelligence [V] | close |
| The local-model option, kept for development; no local model runs in production | **Open-weight / local models** | Running weights on your own hardware | Thoughtworks Vol. 35 "Open-weight models" (Trial) [V] | close: retained as an option, not in production |

## 8. Delivery

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Specification before build, with reviews | **Spec-driven development (SDD)**; specification-first | Start from a structured spec, derive plan and tasks, implement against it | Thoughtworks Radar https://www.thoughtworks.com/radar/techniques/spec-driven-development [V]; GitHub Spec-Kit https://github.com/github/spec-kit [V] | exact ▲radar |
| The intended spec-led AI delivery lifecycle | **AI-DLC**; AI-native SDLC, Inception/Construction/Operations, bolts | AI drafts and humans validate at every phase | AWS DevOps blog https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle [V]; awslabs/aidlc-workflows https://github.com/awslabs/aidlc-workflows [E] | exact (in design) |
| Release classification: documents, scoped, full | **Risk-based change classification**; path-to-production mapping | Route changes to lighter or heavier release processes by risk | Thoughtworks Vol. 35 "Path-to-production mapping" (Adopt) [V] | close |
| Immutable images, scoped restarts | **Immutable infrastructure** | Replace rather than modify running artifacts | AWS Well-Architected https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_tracking_changes_immutable_infrastructure.html [E] | exact |
| Nightly full suite | **Continuous integration / nightly regression** | Full test run on a schedule, separate from per-change checks | Anthropic, regression vs capability evals [V] | exact |
| A delivery record only after a verified deploy | **Provenance / deployment record**; SLSA provenance | Verifiable metadata about what was produced, when and how | SLSA provenance https://slsa.dev/spec/v1.0/provenance [V] | close |
| Guarded swap with rollback | **Blue/green deployment**; canary release | Switch traffic between two environments so rollback is a switch | AWS blue/green https://docs.aws.amazon.com/whitepapers/latest/overview-deployment-options/bluegreen-deployments.html [V]; Fowler canary https://martinfowler.com/bliki/CanaryRelease.html [V] | exact |
| Claude Code, Codex and Grok as contributors under my approval | **Agentic coding under human review**; team of coding agents, agent pipeline responsibility | Coding agents open changes; humans gate merge and deploy | Thoughtworks Vol. 35 "Agent pipeline responsibility" (Assess), Vol. 34 "Team of coding agents" [V] | exact |
| Lessons fed back into the harness after a build | **Feedback flywheel**; self-improving harness | A retrospective step that updates shared agent instructions and checks | Thoughtworks "Feedback flywheel" (Trial, Nov 2026) https://www.thoughtworks.com/en-us/radar/techniques/feedback-flywheel [V] | exact ▲radar |

## 9. What the whole thing is

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| A personal, longitudinal thinking record | **Personal knowledge management with AI**; "second brain", personal agent, longitudinal memory | A personal, continuously maintained store an AI can read, write and reason over | practitioner usage, e.g. https://www.dsebastien.net/ai-ready-second-brain/ [U] | close |
| Persistent context that compounds | **System of intelligence**; compounding context, institutional memory | Accumulated context raises the value of every later interaction | a16z (May 2026) https://a16z.com/from-system-of-record-to-system-of-intelligence/ [V] | close |
| Feedback that compounds instead of resetting | **Data flywheel / feedback flywheel** | See sections 1 and 8 | NVIDIA [V]; Thoughtworks [V] | close: "continual learning" strictly means weight updates; Google's definition https://research.google/blog/introducing-nested-learning-a-new-ml-paradigm-for-continual-learning/ [V]; say "learning loop" or "flywheel" |
| "Platform over product" | **Compound AI system**; agent platform, harness | Many interacting components rather than one model or one app | BAIR, Compound AI Systems https://bair.berkeley.edu/blog/2024/02/18/compound-ai-systems/ [V] | close: the slogan is mine |
| The same pattern for a team or an organization | **Enterprise harness / organizational memory**; AgentOps, agent governance | An organization-wide layer of skills, policies, records and tools that agents consume | Thoughtworks Enterprise harness [V]; IBM AgentOps https://www.ibm.com/think/topics/agentops [V] | close |

## 10. Intent and judgment: what the foundations are for

| What we do | Industry term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Carrying my intent across sessions and domains | **Persistent goals / goal persistence**; goal drift (the failure mode), long-horizon agents, intent-grounded memory | Objectives, constraints and completion conditions stored outside the conversation so agents re-ground on them each turn | OpenAI Codex Goals [V]; STITCH, Grounding Agent Memory in Contextual Intent (2026) https://arxiv.org/abs/2601.10702 [V]; Microsoft Agent Framework https://learn.microsoft.com/agent-framework/overview/ [V] | close: "intent-driven agent" is not standardized; "persistent goals" and "goal drift" are current |
| Goals, plans and obligations tracked by the Chief of Staff | **Goal tracking / decision intelligence** | Modelling decisions and commitments explicitly so they can be executed, governed and audited | TechTarget on Gartner's decision intelligence https://www.techtarget.com/searchbusinessanalytics/definition/decision-intelligence [V]; Gartner market overview https://www.gartner.com/en/documents/8240993 [E] | close |
| Agents exercise judgment within bounds, disagree, push back | **Sycophancy avoidance / honest pushback**; "diplomatically honest rather than dishonestly diplomatic", critique | The model gives its genuine assessment and disagrees when warranted instead of validating the user | Claude's Constitution (Jan 2026) https://www.anthropic.com/constitution [V]; Sharma et al., Towards Understanding Sycophancy https://arxiv.org/abs/2310.13548 [V]; Anthropic personal-guidance study (Apr 2026) https://www.anthropic.com/research/claude-personal-guidance [V]; OpenAI Model Spec https://model-spec.openai.com/2026-10-29.html [V]; sycophancy taxonomy (2026) https://arxiv.org/abs/2605.21778 [V] | exact |
| Saying how sure it is | **Calibration / calibrated uncertainty**; models that know what they know | Confidence that matches the evidence | Kadavath et al. 2022 https://arxiv.org/abs/2207.05221 [V]; Constitution [V] | exact |
| Partner, not tool; bounded judgment | **Bounded autonomy / least agency**; evaluator-optimizer, generator-critic, guardian agent (reviewer) | The agent decides within explicit limits and escalates beyond them; a second role critiques | Anthropic evaluator-optimizer [V]; OWASP agentic 2026 [V]; Gartner guardian reviewers [E] | close |
| Improving my knowledge over time | **Knowledge compounding / institutional memory**; sensemaking (foraging loop to sensemaking loop), agent-maintained knowledge base | Evidence gathered, schematized and built into a case that persists and is reused | Pirolli and Card 2005 [U]; a16z institutional memory [V]; Thoughtworks "Agent-maintained knowledge bases" [V] | close |
| Better decisions: leanings, evidence, options | **Decision records (ADR)**; decision support, decision log, options analysis | Each significant decision captured with context, options, rationale and consequences | ADR https://adr.github.io/ [V]; Thoughtworks enterprise harness lists ADRs as agent inputs [V] | exact (ADR); close (decision intelligence) |
| Asking what would make a plan fail before committing | **Premortem**; prospective hindsight | Assume the plan has failed; list the reasons first | Klein, HBR 2007 https://hbr.org/2007/09/performing-a-project-premortem [V] | exact |
| Risk calculation; the challenger pass; "what could go wrong" reviews | **AI red-teaming / adversarial role-playing**; risk assessment (NIST AI RMF Map and Measure), threat modelling, scenario analysis, FMEA | Structured exercises to find how a system or a plan could fail, before and after deployment | NIST AI 600-1 GenAI profile https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf [V]; NIST AI RMF https://www.nist.gov/itl/ai-risk-management-framework [V]; Thoughtworks Vol. 35 "Threat modeling" (Adopt) [V]; FMEA, ASQ https://asq.org/quality-resources/fmea [E] | exact (red-team, threat model); close (scenario analysis, FMEA) |


## 11. Three practices that are distinctive, and what to call them

These three are where the platform does something the common products do not, and a term
map can flatten them. Each is checked against the literature and the nearest product precedent, so the claim of
distinctiveness is itself verifiable.

### 11a. Personas I author, in a scene I set and iterate, with improvisation inside the boundary

The user creates the persona and sets the scene (place, situation, relationship); both persist across sessions and
the scene is iterated; within its boundary the conversation is improvised, so it stays dynamic and keeps the stress
and tension of a real interpersonal exchange in a foreign language: getting stuck, recovering, filler, repair.

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| A scene with a boundary and a goal inside it; language is the means | **Task-based language teaching (TBLT)**; task-based role-play | A task focuses on meaning, contains a gap, makes the learner rely on their own resources, and has an outcome other than language use (Ellis's four criteria) | Ellis, in Tomlinson (ed.), Materials Development in Language Teaching, CUP https://cambridge.org/core/services/aop-cambridge-core/content/view/C65EF4A6A41CD294E763457590FA4EA2/stamped-9780521157049c11_p212-235_CBO.pdf/macro_and_microevaluations_of_taskbased_teaching.pdf [V] | close |
| The same, in the LLM era | **Game-master LLM / task-based role-play with LLMs** | An LLM guides learners through an open-ended spoken narrative with characters so the target language arises in meaningful interaction, not drills; 21-27% retained gains at one week | Tahmasbi et al., Game-Master LLMs for Task-Based Role-Play (Nov 2025, rev. Sep 2026) https://arxiv.org/abs/2511.15504 [V] | close |
| I create and refine the persona; it persists | **Collaborative persona development; role-playing scenarios; contextual depth enhancement** (persona pattern language) | Prompt patterns in which the user co-creates and refines a persona, enriches its background and constraints, and runs it in simulated situations | Schreiber, White, Schmidt (Vanderbilt), A Pattern Language for Persona-based Interactions with LLMs https://www.dre.vanderbilt.edu/~schmidt/PDF/Persona-Pattern-Language.pdf [V] | close |
| The persona stays in character across sessions | **Persona consistency / role consistency** | Keeping a role-play aligned with the defined character across turns and contexts | Ji et al., Enhancing Persona Consistency (Mar 2025) https://arxiv.org/abs/2503.17662 [V] | close |
| Getting stuck, recovering, filler, repair | **Interactional competence**: turn-taking, sequencing, **repair**; "interactional fluency" | The conversation-analytic practices that make interaction competent; current AI partners mostly produce "supported monologue" with little repair | Waring, PASAA 74 (2026) https://www.culi.chula.ac.th/Images/asset/pasaa_journal/file-79-1056-evxat2567518.pdf [V]; Dombi, Sydorenko, Timpe-Laughlin, LLT 28(2) 2024 https://www.lltjournal.org/item-detail/1129/ [V]; Scheinberg et al., EDM 2026 https://educationaldatamining.org/edm2026/proceedings/2026.EDM.full-papers.210/index.html [V] | exact for the goal |
| The productive tension | **Willingness to communicate (WTC); foreign-language speaking anxiety (FLSA)** | The standard pair of constructs for the emotional side of speaking practice | Wang, Zou, Du, Wang, System 127 (2024) https://scholar.xjtlu.edu.cn/en/publications/the-impact-of-different-conversational-generative-ai-chatbots-on-/ [V] | close: the field frames anxiety as something to reduce; we keep some of it on purpose |
| Keeping the stress on purpose | **Desirable difficulties** (Bjork) | Conditions that feel hard and depress practice performance but improve long-term retention | Bjork Learning and Forgetting Lab, UCLA https://bjorklab.psych.ucla.edu/research/ [V] | stretch: memory research, borrowed framing, label it as borrowed |
| Improvised speech inside a frame | **Improvisation activities for spontaneous speech** (applied improvisation, "a playful pressure to speak") | Drama-derived unscripted activities that elicit spontaneous speech and raise confidence | Zondag, TESOL Journal 2026 https://onlinelibrary.wiley.com/doi/10.1002/tesj.70108 [E]; Zondag, Language Teaching Research 2021 https://journals.sagepub.com/doi/10.1177/13621688211044725 [E] | close |
| Product precedent | **Duolingo Video Call / Roleplay** | Free-flowing conversation with a fixed character who adapts to level; Roleplay uses human-written scenarios | https://blog.duolingo.com/video-call/ [V]; https://blog.duolingo.com/duolingo-max/ [V] | close for the conversation; no user-authored personas, no persistent iterated scenes |

No established term for "bounded improvisation" or "interactional authenticity" was found. The nearest named idea for
deliberately keeping friction is **Antagonistic AI** (Cai, Arawjo, Glassman, Feb 2024, https://arxiv.org/abs/2402.07350
[V]), systems that are challenging by design to build resilience; a stretch.

**Is it distinctive?** Yes. Commercial speaking tools ship human-authored scenarios, or let the user author a
character but not a pedagogically bounded scene that persists and is iterated. The research has the pieces (TBLT's
task criteria, persona patterns including collaborative development, interactional competence with repair as the
target) but no paper found combines user-authored persistent personas, scene iteration, and deliberately preserved
interactional stress. The evidence cuts the other way: the 2026 studies find AI partners produce supported monologue
with little repair, and the WTC/FLSA literature treats anxiety as something to lower. The honest vocabulary is TBLT
plus interactional competence and repair plus persona consistency, with "desirable difficulties" as a labelled
borrowing. Nearest precedent: Game-Master task-based role-play plus Duolingo Video Call's adaptivity and memory.

### 11b. A depth ladder with a challenge at every rung, and Observations that look for what I missed

A selected article can become a scan, a deep dive, a deeper dive, or an ongoing thread. Every level includes a
challenge pass and alternative views. Separately, periodic AI Observations read the accumulated record and suggest
gaps and things I might also want to consider.

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Deep dive and deeper dive on an article | **Deep research (agent)** | An agent plans, runs search-read-search cycles, and returns a cited report the user refines | Google, Gemini Deep Research (Dec 2024) https://blog.google/products/gemini/google-gemini-deep-research/ [V]; Perplexity Deep Research (Feb 2025) https://www.perplexity.ai/hub/blog/introducing-perplexity-deep-research [V]; OpenAI, Introducing deep research https://openai.com/index/introducing-deep-research/ [E] | close: one depth level, no standing challenge pass |
| The ongoing thread | **Living review / dynamic survey** | A review maintained as a document: new material triaged for scope and folded in while structure is preserved | Mumcu et al., Agentic AI-Empowered Dynamic Survey Framework (Feb 2026) https://arxiv.org/abs/2602.04071 [V] | close |
| Challenge pass and alternative views | **Multi-agent debate** | Several model instances propose and critique over rounds before converging | Du et al. (2023, ICML 2024) https://arxiv.org/abs/2305.14325 [V]; caution: Zhang et al., Stop Overvaluing Multi-Agent Debate (Feb 2025) https://arxiv.org/abs/2502.08788 [V]: debate helps mainly when the debaters are heterogeneous | close |
| The challenge pass as a role | **Devil's advocate / constructive agitator**; red-team pass, steelmanning | A model role that interrogates an output with alternative interpretations, uncertainty, biases and failure cases | Suh et al., Don't Just Translate, Agitate (CHI 2025 workshop) https://arxiv.org/abs/2504.12424 [V] | exact for the role |
| Critique, then revise | **Critique-and-revise (self-critique)** | Critiques against stated principles, then revisions; the Constitutional AI mechanism | Bai et al., Constitutional AI (2022) https://arxiv.org/abs/2212.08073 [V] | close |
| Pushing my assumptions | **Socratic questioning** | Structured questions that lead the user to examine viewpoints and probe assumptions | Favero et al. (Sep 2024) https://arxiv.org/abs/2409.05511 [V] | close |
| Periodic AI Observations over the record | **Proactive agents; proactive information gathering; knowledge-gap navigation** | An agent identifies gaps and unstated dimensions of the user's problem and raises them unprompted, judged by initiative appropriateness | Huang et al., Teaching Language Models To Gather Information Proactively (Jul 2025) https://arxiv.org/abs/2507.21389 [V]; Kaur et al., PROPER Agents, Knowledge Gap Navigation (ACL 2026) https://arxiv.org/abs/2601.09926 [V]; ProactiveEval (Aug 2025) https://arxiv.org/abs/2508.20973 [V] | close: per-task in the literature; periodic review over an accumulated personal corpus is not covered |

No research-tool term for "progressive depth" or "depth-on-demand" was found; "blind-spot detection" is used loosely.

**Is it distinctive?** Yes, as a combination. Deep-research products give one depth level, no standing challenge
pass, and no proactive cross-record observations; alternative views come only if asked. The research exists
piecewise, and the 2025 debate critique is useful: debate helps when the debaters are heterogeneous, which argues
for the challenge pass being a different model (section 11c). The ladder with a challenge at every rung plus
periodic Observations over the accumulated record has no single precedent. Nearest: Gemini Deep Research for the
depth side, the dynamic-survey framework for the thread side, and PROPER-style knowledge-gap navigation applied to a
personal corpus for the Observations.

### 11c. Cross-provider review as a standing rule

Every non-trivial change or artifact is reviewed by a model from a different provider than the one that produced it.
This is a rule, not an option, and it routinely surfaces issues and insights.

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| A different-provider reviewer for every non-trivial change | **Cross-model (cross-vendor) code review**; "model inversion" | Routing review of an agent's code to a model from a different developer | Xiang et al., Cross-Model LLM Code Review (Jul 2026, KDD'26 workshop) https://arxiv.org/abs/2607.21656 [V]; Greptile, Models are worse at reviewing their own code (Jul 2026) https://www.greptile.com/blog/model-inversion [V] | exact |
| The same for prose and artifacts | **Cross-model review in LLM verification**; second-opinion review, reviewer independence | Controlled test of whether a reviewer from another family adds anything over a fresh same-model review | Song, When Does a Second Model Help? (Oct 2026) https://arxiv.org/abs/2610.01471 [V] | exact |
| Several providers judge, anonymised | **LLM council** (jury, panel) | Several models answer; each ranks the others' anonymised answers; a chairman synthesizes | Karpathy, llm-council (Nov 2025) https://github.com/karpathy/llm-council [V] | close |
| Judges from different families to cut self-bias | **Panel of LLM evaluators (PoLL)** | A panel of smaller models from disjoint families beats one large judge with less intra-model bias | Verga et al., Replacing Judges with Juries (Apr 2024) https://arxiv.org/abs/2404.18796 [V] | close |
| Why the reviewer must be another family | **Self-preference / same-family lift** | Judges favour candidates from their own family: +3.4 to 8.4 points once quality is controlled | Awuni et al., Who Judges Matters (Sep 2026) https://arxiv.org/abs/2609.17857 [V] | close |
| Diversity in multi-agent setups | **Model heterogeneity**; heterogeneous ensembles, Mixture-of-Agents | Debates and ensembles among different models rather than copies of one; "a universal antidote" for debate failure | Zhang et al. https://arxiv.org/abs/2502.08788 [V]; Nilayam et al., Heterogeneous LLM Debate Under Adversarial Peers (Jun 2026) https://arxiv.org/abs/2606.19826 [V]; Wang et al., Mixture-of-Agents https://arxiv.org/abs/2406.04692 [V] | close |
| Analogy | **N-version programming with coding agents** | Independently generated versions voted to mask faults; common-mode failures persist and trace to spec ambiguity | Ron, Baudry, Monperrus (Jun 2026) https://arxiv.org/abs/2606.20158 [V] | stretch |
| Counter-evidence to weigh | **Correlated errors in judge panels** | Nine frontier judges from seven families gave about two independent votes' worth of information | Kohli, Nine Judges, Two Effective Votes (May 2026, NeurIPS 2026) https://arxiv.org/abs/2605.29800 [V] | close, caution |

**Does the evidence support the rule?** Mostly. Greptile, on 1,000 pull requests: cross-model recall of serious bugs
about 62% against 50% for same-model on Claude-authored PRs, 60% against 54% on Codex-authored. Song: one same-model
plus one cross-model review caught 57% of planted errors against 43% for two same-model reviews, with only 41%
overlap between what the two reviewer types find; but a lightweight cross-model reviewer added nothing, and
model-bias false positives (flagging the other model's idioms as wrong) are a cost. Xiang: the benefit is asymmetric,
Claude reviewing Codex +18 points, Codex reviewing Claude −9, Claude self-review zero, so reviewer strength may matter
more than vendor difference. Kohli: family diversity alone does not buy independent errors.

**Is it distinctive?** The concept is now named and measured, so not unique. What is uncommon is applying it as a
standing rule to every non-trivial change and artifact across a personal platform, and treating the reviewer's
insights, not only its bug catches, as first-class output. Two provisos worth stating in our own docs: pick a strong
reviewer, not merely a different one; and expect correlated blind spots, which is the argument for human and
specification-level review on top. Nearest precedents: Greptile model inversion and Karpathy's council.


## 12. Infrastructure and operations: how it is run, in the industry's terms

Companion to OPERATIONS.md. Same rules: a term is claimed only where the practice earns it. Three phrasings to
avoid as claims: "origin cloaking" (not a Cloudflare term; say "outbound-only tunnel, origin with no public IP");
"read-only observability agent" (my phrase, no source); "AgentOps" in the literature means operating agents, not
agents that operate, so it is close, not exact.

### 12a. Hosting

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| One production EC2 host and one Dev/staging host, same compose file plus an override | **Compose file merging / override file** | Compose combines a base file with an override; later files add to or change earlier ones | https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/ [V] | exact |
| Separate production and Dev hosts | **Environment separation** | Distinct controls and resources for non-production and production | AWS, organizing your environment https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/benefits-of-using-multiple-aws-accounts.html [V] | close: AWS means separate accounts; we separate hosts in one account |
| Docker Compose as the runtime, not Kubernetes | **Docker Compose** (single-host multi-container orchestration) | One YAML file to configure and run several containers on one host | https://docs.docker.com/compose/ [V] | exact |
| Images built in GitHub Actions | **CI/CD pipeline** | Automated build, test and deploy on repository events | https://docs.github.com/en/actions/get-started/understand-github-actions [V] | exact |
| Pushed to a private ECR registry, pulled by commit tag | **Private container registry; image tag immutability** | A managed registry; with tag immutability on, a tag can never be overwritten | https://docs.aws.amazon.com/AmazonECR/latest/userguide/what-is-ecr.html [V]; https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-tag-mutability.html [V] | exact (if the repository setting is on; confirm) |
| Deploy over SSM, no inbound SSH needed | **SSM Session Manager (bastion-less access) and Run Command** | Node access without open inbound ports, bastions or SSH keys; remote command execution | https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html [V]; https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html [V] | exact |
| nginx in front of the portal | **Reverse proxy** | Receives client requests, forwards them to a proxied server, returns the response | https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/ [V] | exact |
| Let's Encrypt certificates | **Automated certificate issuance (ACME)** | A free automated CA; the web server obtains trusted certificates without manual steps | https://letsencrypt.org/how-it-works/ [V] | exact |
| Cloudflare Tunnel carries public traffic; no port exposed | **Cloudflare Tunnel** (outbound-only connector; origin without a public IP; part of Cloudflare Zero Trust) | Links private infrastructure to Cloudflare through outbound-only connections; no inbound listener on the origin | https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/ [V]; https://developers.cloudflare.com/learning-paths/get-started/security/secure-origin/ [V] | exact |
| Container ports bound to 127.0.0.1 | **Loopback-only port publishing** | Only the host can reach the container port | https://docs.docker.com/engine/network/port-publishing/ [V] | exact |
| gp3 root volume | **General Purpose SSD gp3** | Baseline 3,000 IOPS and 125 MiB/s, performance provisioned independently of size | https://docs.aws.amazon.com/ebs/latest/userguide/general-purpose.html [V] | exact |
| Swap file added after the OOM | **Swap file on EC2** | Memory overcommit relief | https://repost.aws/knowledge-center/ec2-memory-swap-file [E] | exact |
| t3.small → t3a.medium → t3a.large | **Rightsizing** | Matching instance size to measured usage | https://docs.aws.amazon.com/cost-management/latest/userguide/ce-rightsizing.html [V] | close: we up-sized by hand after incidents; same vocabulary |

### 12b. Delivery

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Release classification by changed paths | **Path-filtered workflows** (monorepo path filtering) | Run or skip a workflow by the paths a push touched | https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax [V] | close: we classify into three release classes; GitHub only filters |
| Scoped restarts with `--no-deps` | **Scoped service restart** | Recreate selected services without their dependencies | https://docs.docker.com/reference/cli/docker/compose/up/ [V] | exact |
| Health-gated deploys | **Container healthcheck; `service_healthy`**; deployment circuit breaker (AWS) | A command decides whether a container is healthy; dependents wait on it; a breaker rolls back a failing deployment | https://docs.docker.com/reference/compose-file/services/ [V]; https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html [V] | exact (Compose); close (breaker) |
| Nightly full suite | **Secondary pipeline stage / nightly build** | Slower suites run in a second stage, not on every commit | Fowler, Continuous Integration https://martinfowler.com/articles/continuousIntegration.html [V] | exact |
| Dev delivery record written only after a verified deploy | **Deployment record / release evidence** (no canonical term); nearest: lightweight ADRs | A record with context and consequences, in source control | https://www.thoughtworks.com/radar/techniques/lightweight-architecture-decision-records [V] | stretch |
| Guarded swap with automatic restore | **Blue-green deployment with automatic rollback** | Switch traffic to the new environment; rollback is a switch back | https://martinfowler.com/bliki/BlueGreenDeployment.html [V] | close: we swap in place on one host |
| Immutable image per commit | **Immutable server / immutable infrastructure** | Never change a deployed artifact; build a new one and replace | https://martinfowler.com/bliki/ImmutableServer.html [V] | exact at image level |
| Rollback by `git revert` through the pipeline, or by recreating a prior tag | **Roll-forward revert; redeploy previous artifact** | Undo by a new commit that reverses the change | https://git-scm.com/docs/git-revert [V] | exact |
| The gate is my go, not a pipeline stage | **Continuous delivery** (not continuous deployment) with a **manual approval gate** outside the pipeline | Always releasable, release when a person says so; GitHub's required reviewers are the in-pipeline form | https://martinfowler.com/bliki/ContinuousDelivery.html [V]; https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments [V] | exact (CD); close (gate) |
| Comparison | **Change advisory board** (ITIL) | The heavyweight form of the same gate | https://www.atlassian.com/itsm/change-management/change-advisory-board [E] | stretch at this scale |

### 12c. Operations by agents

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| The Master Craftsman reports health on request, reads release observations, keeps the Build Log, all in the chat | **ChatOps** (conversation-driven operations) | Operational work done and coordinated inside the chat tool | PagerDuty https://www.pagerduty.com/resources/learn/what-is-chatops/ [V]; Atlassian https://www.atlassian.com/incident-management/devops/chatops [E] | exact |
| The Chief of Staff's health loop and daily cost checkpoint | **AIOps** | AI applied to IT operations and service management | IBM https://www.ibm.com/think/topics/aiops [V]; Gartner glossary [E] | close: industry AIOps means ML over telemetry at scale |
| Agents taking part in running the platform | **AgentOps** | DevOps-style observability and lifecycle practice for LLM agents | Dong, Lu, Zhu, AgentOps (2024) https://arxiv.org/abs/2411.05285 [V] | close: the literature means operating agents; we also use agents to operate |
| A helper seat runs hygiene checks | **Runbook automation** | Scripted maintenance and remediation steps run on a schedule or request | SSM Automation https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-automation.html [V] | close |
| The supervisor relays stops and decisions over Telegram | **On-call paging via chat** | Being reachable; the page summons the responder | Google SRE workbook, On-call https://sre.google/workbook/on-call/ [V] | close: Telegram means "urgent action waiting" |
| Agents absorb routine checks | **Toil reduction** | Removing manual, repetitive, automatable ops work with no lasting value | Google SRE book, Eliminating toil https://sre.google/sre-book/eliminating-toil/ [V] | exact |
| Vocabulary to grow into | **Error budget; SLI, SLO** | The tolerated shortfall between a target and measured reliability | https://sre.google/sre-book/embracing-risk/ [V]; https://sre.google/sre-book/service-level-objectives/ [V] | stretch: no formal SLO yet |

### 12d. Cost

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Per-agent capped gateway keys | **Per-key budgets, spend caps** | A budget and duration on a key; the proxy blocks over-budget calls | LiteLLM https://docs.litellm.ai/docs/proxy/users [V] | exact |
| Usage records per call | **Metering; FinOps Inform phase** | Visibility of cost, usage and efficiency for allocation and forecasting | FinOps Foundation phases https://www.finops.org/framework/phases/ [V] | exact |
| Threshold warnings (fallback rate, unpriced requests) | **Budget alerts; cost anomaly detection** | Alert on thresholds or anomalous spend | https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html [V]; https://docs.aws.amazon.com/cost-management/latest/userguide/manage-ad.html [V] | close: ours are rule-based, not ML |
| The daily cost checkpoint | **FinOps Optimize and Operate phases** | Find efficiency and rate improvements; implement with shared accountability; loop | FinOps phases [V] | exact |
| Cost by agent and role | **Showback** (vs chargeback) | Report costs by scope without booking them | https://www.finops.org/framework/capabilities/invoicing-chargeback/ [V] | exact |
| Cost per run, per article, per turn | **Unit economics** | Tie spend to the activity that creates value | https://www.finops.org/framework/capabilities/unit-economics/ [V] | exact |
| Spot vs on-demand in the June decision | **Spot Instances vs On-Demand** | Spare capacity at a discount with two minutes' notice, vs a static price | https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-instances.html [V] | exact |
| AWS Budgets email to me; Cost Explorer | **AWS Budgets; Cost Explorer** | Threshold alerts; analysis with 13 months of history | https://docs.aws.amazon.com/cost-management/latest/userguide/ce-what-is.html [V] | exact; the gap is routing alerts to the agents |

### 12e. Observability and incidents

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Sentry in the web apps | **Error tracking, issue grouping** | Similar events grouped into one issue with frequency and users affected | https://docs.sentry.io/product/issues/ [V] | exact |
| CloudWatch basic metrics | **Basic monitoring** (5-minute metrics) | EC2 metrics to CloudWatch at 5-minute resolution | https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/viewing_metrics_with_cloudwatch.html [V] | exact |
| Logs and metrics, no traces | **Observability** (logs, metrics, traces) | Investigating a system from the outside through its signals | OpenTelemetry primer https://opentelemetry.io/docs/concepts/observability-primer/ [V] | close: two of three pillars |
| `/health` endpoints and container healthchecks | **Health endpoints; liveness vs readiness** | When to restart a container vs when it may take traffic | https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/ [V] | close: Compose has one healthcheck |
| The August OOM and console reboot | **Incident response; break-glass access** | Structured handling of an urgent failure; an audited emergency access path | https://sre.google/workbook/incident-response/ [V]; https://docs.aws.amazon.com/wellarchitected/latest/framework/sec_permissions_emergency_process.html [V] | exact |
| Decision records after incidents | **Blameless postmortem; ADR** | Causes without blame, assuming good faith | https://sre.google/sre-book/postmortem-culture/ [V] | close |
| Telegram only when I am needed | **Alert fatigue** (the thing avoided) | Pages so frequent they get skimmed or ignored | https://sre.google/sre-book/monitoring-distributed-systems/ [V] | close |

### 12f. Data and backups

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Host-mounted data folders | **Bind mounts** | A host directory attached into a container | https://docs.docker.com/engine/storage/bind-mounts/ [V] | exact |
| One PostgreSQL instance, dumps | **SQL dump (pg_dump)** | One of PostgreSQL's three backup approaches | https://www.postgresql.org/docs/current/backup.html [V] | exact |
| Local nightly, S3 nightly, a third tier that never worked | **3-2-1 backup rule** | Three copies, two media, one off-site | CISA https://www.cisa.gov/resources-tools/resources/level-your-defenses-four-cybersecurity-best-practices-businesses [V] | close: intended 3-2-1, two tiers live |
| 14-day retention; sync, not snapshot | **Retention policy; snapshot vs sync** | A snapshot is a point in time; a sync overwrites | https://docs.aws.amazon.com/ebs/latest/userguide/ebs-snapshots.html [V] | exact |
| age-encrypted Vault backup | **Client-side encryption** | Encrypt before upload; the store never sees plaintext | https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingClientSideEncryption.html [V]; https://github.com/FiloSottile/age [V] | exact |
| No restore test yet | **Restore testing** | Periodic proof that a backup restores, and how long it takes | https://docs.aws.amazon.com/aws-backup/latest/devguide/restore-testing.html [V] | the term is exact; the practice is missing |
| Target | **Immutable backups** (Vault Lock, WORM) | Recovery points that cannot be deleted until their lifecycle ends | https://docs.aws.amazon.com/aws-backup/latest/devguide/vault-lock.html [V] | stretch: not implemented |
| Vocabulary | **RPO, RTO** | Acceptable data loss window; acceptable restoration delay | https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/disaster-recovery-dr-objectives.html [V] | exact: ours is RPO about 24 h, RTO undefined |

### 12g. Secrets and access

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| SSM Parameter Store SecureStrings as the source of truth | **Secrets management** (centralized secret store) | One encrypted store, provisioning and audit | https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html [V]; OWASP https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html [V] | exact |
| Secrets projected into root-owned 0600 files, mounted into the one container that needs each | **Secret injection via mounted files, not environment variables** | OWASP prefers mounted files or in-memory fetch; environment variables are not recommended | OWASP cheat sheet [V] | exact |
| Per-actor tokens stored as SHA-256 hashes | **Hashed API tokens** | Store the digest of a high-entropy token, not the token | OWASP password storage (does not address random tokens) https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html [V] | close |
| The secret chain refuses when the role is unset | **Fail-closed configuration; environment separation** | A missing role is an error, not a default | https://docs.aws.amazon.com/wellarchitected/latest/framework/sec_permissions_least_privileges.html [V] | close |
| Keychain on the workstation | **OS credential store (Keychain)** | The system's secure container for passwords and keys | https://support.apple.com/guide/keychain-access/what-is-keychain-access-kyca1083/mac [V] | exact |
| Least-privilege operator role vs root console | **Principle of least privilege; break-glass account** | Only the permissions a task needs; a documented, audited emergency path | https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html [V]; emergency process [V] | exact |
| No inbound ports, identity-based access | **Zero trust** | No implicit trust from network position | NIST SP 800-207 https://csrc.nist.gov/pubs/sp/800/207/final [V] | close |

### 12h. Scheduling

| What we do | Term (synonyms) | What it means | Source | Fit |
|---|---|---|---|---|
| Host cron split across users | **cron, per-user crontabs** | Time-based job daemon | https://man7.org/linux/man-pages/man8/cron.8.html [V] | exact |
| Loops inside the Chief of Staff container | **In-process scheduler (APScheduler)** | A job runner inside the application | https://apscheduler.readthedocs.io/en/3.x/ [V] | exact |
| launchd on the workstation | **launchd agents and daemons** | Background jobs defined by a plist | https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html [V] | exact |
| systemd timer on Dev | **systemd timer units** (`Persistent=` catch-up) | A timer unit schedules another unit; missed runs can catch up | https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html [V] | exact |
| Daily jobs gated by role, window and a stamp file | **Idempotency; at-least-once execution with an idempotent consumer** | A retry has no extra effect; delivery may repeat, the consumer tolerates it | AWS Builders' Library https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/ [V]; SQS standard queues [V] | exact; close |
| Gap | **Watchdog** (`WatchdogSec=`) | A service must ping or be marked failed | https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html [V] | stretch: not implemented |

---

## Glossary: the terms people search for, with what they mean here

1. **Agentic AI**: systems that plan, use tools and act with some autonomy; Anthropic separates workflows (predefined paths) from agents (model-directed). https://www.anthropic.com/engineering/building-effective-agents [V].
2. **AI agent**: an LLM running a workflow with tools inside guardrails to accomplish a task. OpenAI practical guide [V].
3. **Agentic workflow**: the model, not fixed code, decides the next step. Microsoft Agent Framework [V].
4. **Compound AI system**: many interacting components (models, retrievers, tools). BAIR [V].
5. **Agent harness / harness engineering**: the tools, prompts, permissions, memory and checks around a model. Thoughtworks [V]. ▲radar.
6. **Tool use / function calling**: model-invoked software interfaces. https://www.anthropic.com/engineering/writing-tools-for-agents [V].
7. **MCP (Model Context Protocol)**: open standard connecting AI apps to tools and data. https://modelcontextprotocol.io/docs/getting-started/intro [V]. **Not used by mini-moi:** the platform's typed tool door does the job MCP names (tool use behind tiers and receipts) without the protocol; say "tool use / function calling" for what we have, and MCP only as the standard we would adopt if the engine ever needed it.
8. **A2A (Agent2Agent)**: open protocol for interoperability between opaque agents. https://a2a-protocol.org/latest/ [V]. **Not implemented:** the request ledger uses A2A's task vocabulary (task, states, artifact) as a description; there is no A2A endpoint or Agent Card.
9. **Agent Skills**: folder-based SKILL.md format loaded on demand. https://agentskills.io/ [V]. ▲radar.
10. **Agent memory (working, episodic, semantic, procedural)**: thread state vs long-term facts, events and learned rules. LangChain [V]; CoALA [V].
11. **RAG**: generate from retrieved documents. Lewis et al. [V].
12. **Hybrid search**: keyword plus vector, merged. Azure [V].
13. **Context engineering**: curating the tokens the model sees each step. Anthropic [V]; LangChain [V]. ▲radar.
14. **Continual learning**: a model acquiring knowledge over time without forgetting; strictly weight-level. Google Nested Learning [V]. Not a claim we make.
15. **Data flywheel / feedback flywheel**: usage data or lessons fed back to improve the system. NVIDIA [V]; Thoughtworks [V]. ▲radar.
16. **Preference learning / personalization**: learning what a person likes from feedback. Ouyang et al. (RLHF) https://arxiv.org/abs/2203.02155 [V]; Google implicit feedback [V].
17. **LLM-as-a-judge**: a strong model grades against a rubric. Zheng et al. [V].
18. **Evals**: tests with graders over transcripts and outcomes; capability vs regression. Anthropic [V]..
19. **Guardrails**: input, output and tool checks with tripwires. OpenAI Agents SDK [V]. ▲radar.
20. **Human-in-the-loop / human-on-the-loop**: blocking approval vs monitoring with intervention. Arize [V]. ▲radar ("risk-tiered human oversight" Trial; "human-in-the-loop theater" Caution).
21. **Policy-as-code**: machine-evaluated declarative policy. OPA [V].
22. **Least privilege / least agency**: minimum access; for agents, minimum unsupervised decision scope. NIST [V]; OWASP [V].
23. **Agent sandboxing**: OS-level filesystem and network isolation. Anthropic [V]. ▲radar (Adopt).
24. **Agent identity / non-human identity / zero trust**: per-agent signed identities, continuous verification. Entra Agent ID [V]; NIST [V].
25. **Audit trail / provenance / tamper-evident log**: append-only, hash-chained records; SLSA for builds; EU AI Act Art. 12. [V].
26. **Event sourcing / append-only logs / idempotency**: state as a sequence of events; safe retries. Fowler [V].
27. **Observability / tracing**: OpenTelemetry GenAI conventions. [V].
28. **AgentOps**: lifecycle management of agents, like DevOps for agents. IBM [V].
29. **LLM gateway / model routing**: proxy for auth, routing, failover, budgets, metering. LiteLLM [V].
30. **FinOps for AI / token budgets**: cost and value management of AI. FinOps Foundation [V].
31. **Multi-agent orchestration; orchestrator-workers; handoffs; group chat**: patterns for coordinating agents. Anthropic, OpenAI, LangChain, AutoGen [V].
32. **Reflection / self-critique**: an agent writes feedback on its own attempts and keeps it. Reflexion [V]; Generative Agents [V].
33. **Sycophancy / calibration / honesty**: model specs now require pushback and calibrated confidence. Claude's Constitution [V]; OpenAI Model Spec [V].
34. **Spec-driven development**: spec to plan to tasks to implementation with agents. Thoughtworks [V]; Spec-Kit [V]. ▲radar.
35. **AI-DLC**: AWS AI-Driven Development Life Cycle. AWS [V].
36. **Guardian agents**: Gartner's reviewers, monitors and protectors over other agents. [E].
37. **AI governance: NIST AI RMF** (Govern, Map, Measure, Manage) [V]; **ISO/IEC 42001:2023** https://www.iso.org/standard/42001 [E]; **EU AI Act Art. 14 human oversight** [V].
38. **Decision intelligence / ADR / premortem**: engineering how decisions are made; durable rationale; failure-first planning. [E]/[V]/[V].
39. **AI red-teaming / threat modelling**: structured adversarial probing. NIST AI 600-1 [V]; Thoughtworks (Adopt) [V].
40. **Persistent goals / goal drift**: goals stored outside the thread with completion conditions; drift is the failure mode. Codex Goals [V]; STITCH [V].
41. **System of intelligence**: a16z's reasoning layer above systems of record that accumulates context. [V].

## How I use these terms

Lead with the ones that are current, vendor-backed, and that the code earns exactly: agent harness,
context engineering, agent memory, tool use and function calling, guardrails, risk-tiered human oversight, agent sandboxing, agent
identity, LLM gateway, FinOps for AI, evals and LLM-as-judge, spec-driven development, AI-DLC, audit trail and
provenance. Keep my own phrases next to them, not instead of them: "thinking partner", "platform over product",
"intent" are how I think about it, and the standard term is how a reader finds it. Do not claim "continual learning"
(weight updates), "intent-driven agent" (not standardized), or MCP and A2A as protocols we run: they are
the vocabulary we borrow for the tool door and the ledger.
