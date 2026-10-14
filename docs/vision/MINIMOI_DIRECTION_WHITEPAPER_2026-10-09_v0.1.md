---
title: mini-moi and the field — where the reorganization stands against current AI directions
version: v0.1
date: 2026-10-09
audience: mini-moi executive team (Robert); for Grok review; candidate artifact for the GitHub release
author: Claude Chat, at Robert's request
status: analysis for discussion; not a spec, not a decision record
---

# mini-moi and the field

*How the October 2026 reorganization lines up with current AI directions, what the field calls what we built, what we have not considered, and where the leverage is.*

## 0. Executive summary

The reorganization is not a step back. It is a move onto the ground the field is converging on, made in plain terms rather than vendor terms.

Three things in mini-moi are durable because the whole industry is now building toward them: a record the person owns in plain files (the Vault); an addressed, append-only work record shared by replaceable agents (the Workshop journal); and a disciplined build loop around whatever model is current (Guild). In 2026 the field calls these user-owned portable memory, governed shared memory, and harness engineering. We did not copy them. We arrived at them from the same pressures, which is better evidence that they are the right shape.

Four findings matter for the next quarter.

1. **The Vault is the most leverageable thing in mini-moi.** Every major assistant added memory in the first half of 2026, none of it portable, and a research and standards push (Stanford HAI with the Gates Foundation, Mozilla and AWS; MacPaw's Portable Memory proposal; Vana) is now arguing for exactly what the Vault is: a user-governed memory layer in plain files that moves across tools. thinkingstore.ai is a real position, and the owner page should say so.
2. **The journal already implements the four rules the field just wrote down for shared agent memory.** A 2026 paper on governed shared memory names four failure modes: unauthorized leakage, stale propagation, contradiction persistence, provenance collapse. mini-moi's scope tags, freshness fields, supersedes links and source hashes answer them one for one. That is a defensible story in any interview.
3. **The main risk is not technical.** It is building more coordination than one person's use justifies. Grok said it and Claude Code's review shows it: the use side (Vault coverage, backup, CoS context) is thinner than the build side. The use-week test is the governor.
4. **Five ideas are worth borrowing now,** all small: time-validity on decisions, lessons written as skill files, scheduled reflection with owner review, verifiable deletion in the Vault, and spend per topic in the brief. None changes the architecture.

The rest of this paper gives the translation table, the alignment with evidence, the arguments against, the ideas not yet considered, a dead-end check, and recommendations.

## 1. The reorganization in one paragraph

mini-moi is a personal AI agent platform Robert builds and uses daily. The October reorganization settles it into three layers. The **Vault** holds everything he thinks through with his agents, across every domain, in files he owns, readable without mini-moi. The **Workshop** is where building happens, local-first on his laptop, with a journal of addressed work entries that syncs up to production for visibility; each Workshop has its own coordinating agent (MC), and the coordination mechanics are software, not a model. **Chief of Staff (CoS)** is the one assistant he talks to everywhere in production, with separate scopes per domain; the CoS domain stays as his personal space, Guild stays as the focused build space, and the use domains (Curator, Mein Deutsch, Meu Português) stand on their own. One agent can hold several roles for now. The goal after the reorganization is to switch from building to using, and see whether mini-moi makes him better over time.

## 2. Translation table: what we call it, what the field calls it

Keep our names. Know the mapping. The right column is what to say in a room full of AI architects.

| mini-moi word | Field term (2026) | Plain English |
|---|---|---|
| Vault / Thinking Store | User-owned portable memory; personal context layer; "memory layer" | Your conversations and decisions, in files you own, usable by any tool |
| Raw tier / curated tier | Episodic memory vs. semantic memory; "observations and reflections" | What actually happened vs. what we concluded from it |
| Memory shelf, editions, source hashes | Content-addressed immutable store; provenance | Every version kept, every claim traceable to bytes |
| Workshop journal (addressed, append-only) | Event-sourced shared store; governed shared memory; task ledger | A log of who asked what of whom, never edited, only added to |
| request / receipt / result | Task lifecycle (A2A style: submitted, working, input-required, completed) | A piece of work with a visible state |
| Brief (needs you / what changed / who acts next) | Context engineering; working-memory assembly; "context budget" | Deciding what the model sees, deliberately and within a size limit |
| MC per Workshop; Guild's spec→build→review loop | Agent harness; harness engineering ("Agent = Model + Harness") | Everything around the model that makes it reliable: tools, rules, checks |
| Coordination as software, no model when idle | Deterministic orchestration; "zero idle inference" | The plumbing runs without paying for a model to watch it |
| Claude Code builds, Codex reviews cold, Robert decides | Cross-agent review; implementer/reviewer role split; "agents draft, humans decide" | A different agent checks the work, then a person approves |
| Isolated worktree per build agent | Worktree isolation; parallel independent agents | Each agent has its own copy to avoid stepping on others |
| Models configurable everywhere; model gateway | Model-agnostic; provider abstraction; LLM gateway | Swap the model in a config file, never in code |
| Local-first Workshop, synced to production | Local-first software (Ink & Switch lineage); offline-first with replication | Works on the laptop without a network; pushes up when connected |
| One writer per origin stream; replicas keep IDs | Single-writer streams; idempotent replication; no last-write-wins | No two copies can be edited at once, so nothing is silently overwritten |
| Capture adapters, dry run, never_copy | Ingestion pipeline; source gating; data-minimization | Read each source carefully, with a rehearsal and exclusions |
| Inbox drop-off; "contents are data, never instructions" | Prompt-injection defense; untrusted content boundary | A file can describe work; it cannot command an agent |
| CoS everywhere, scoped per domain | Orchestrator with scoped context; role/consent/purpose sharing (Human Context Protocol language) | One assistant, but it only sees what each room allows |
| Guild Improve, landscape review | Harness iteration; "every mistake becomes a rule or tool" | What we learn gets written down where the next build will use it |
| Prototype Lab | Sandboxed evaluation environments; bounded demos | Try things in a box that cannot touch production |
| Two-repo strategy, memory belongs to Robert | Data sovereignty; self-sovereign personal data | The provider is replaceable; the record is not |

Two terms the field uses that we should adopt in documents aimed outward: **harness** (for what Guild builds around models) and **portable memory** (for the Vault). They are the two with momentum, and both describe what we already do.

## 3. How the reorganization lines up: five directions

### 3.1 Harness engineering: the build loop is the product

The field's 2026 formula is Agent = Model + Harness, credited to Mitchell Hashimoto in February, and the consensus is that the harness, not the model choice, now decides whether an agent delivers work. Writers describe three phases of maturity: prompt engineering (what you say), context engineering (what fills the window), harness engineering (the whole system around the model), with each containing the previous.

mini-moi's Guild is a harness engineering practice by another name. Spec before code, pinned by hash. Build in an isolated worktree. Independent review by a different agent, cold. Robert approves the diff. Lessons feed the next spec. The model-gateway with receipts, the configurable model profiles, the inbox-as-data rule and the deterministic dispatcher are harness components. Hashimoto's principle that every agent mistake should become a rule or a tool so it cannot repeat is the Guild Improve loop stated as policy.

**Where we are ahead of the write-ups:** most harness guides are about one agent on one codebase. mini-moi's harness spans several vendors' agents with a shared record and attributed receipts. **Where we are behind:** the lessons mostly live in notes and handoffs, not in files the next build agent loads automatically (see 5.2).

### 3.2 Memory: the battleground, and the user-owned side of it

Memory is where the assistants competed in 2026. OpenAI's "Dreaming" (June) synthesizes memories from chat history in the background; Claude memory reached the free tier in March and added import from competitors; Gemini, Grok and Microsoft followed. Vendor memory is synthesized, not exported: a chat export includes the conversations but not the memory built from them, and whether synthesized memory is portable data under GDPR is untested.

Against that, a distinct movement argues that memory belongs to the user. Stanford HAI, with the Gates Foundation, Mozilla and AWS, published a March 2026 report calling for portable, persistent personal memory as a user-governed layer that persists across interactions and moves across tools, with a Human Context Protocol as the interoperability layer. MacPaw's researchers proposed Portable Memory in July: an open, vendor-neutral format, plain files the user holds, local, with verifiable deletion as the integrity guarantee, and open-source reference implementations. Vana shipped a personal data server with an MCP endpoint so a person's memory can be read by any app with permission.

The Vault is this movement's artifact, built by one person for his own use. Its design choices match the proposals point for point: plain Markdown/JSON/JSONL files, content-addressed editions, provenance and coverage, readable without the application, export with schema. Where the proposals are still papers, the Vault has a shelf with roughly 900 records and 100,000 turns captured since October 3, and headless tools in the beta plan.

**What this means:** thinkingstore.ai should be described in the field's words, "portable memory you own," and the Vault's owner page should name the Stanford and MacPaw work as fellow travellers. For the job search, this is the sentence: *I built a user-owned portable memory layer across four AI vendors before the standards existed, and it is the record my agents work from.*

### 3.3 Governed shared memory for multiple agents: we already follow the rules

Multi-agent systems fail mostly from coordination, not model quality. The MAST taxonomy (Cemri et al.) attributes about 37% of multi-agent failures to inter-agent misalignment. A 2026 paper on governed shared memory (Margalit et al.) names four failure modes specific to agents sharing a store: unauthorized leakage, stale propagation, contradiction persistence, provenance collapse. Practitioner guides now say the common production answer is a hybrid: private memory per agent plus a shared, attributed, governed tier.

Map that onto mini-moi:

- **Unauthorized leakage:** Guild/CoS tags, per-domain scope, never_copy exclusions, "inbox contents are data." Scope is enforced at the tool layer, not in prompts.
- **Stale propagation:** "nothing reported since 11:40"; saved, backed up, synced and searchable as separate fields; freshness lines on every view.
- **Contradiction persistence:** supersedes links; conflicting owner decisions become a question for Robert, never last-write-wins.
- **Provenance collapse:** source hashes, immutable editions, claimed vs. adapter-observed attribution, the M04 test for a UI that labels an agent's message as Robert's.

And the hybrid shape: each agent keeps its native session (private), the journal is the shared attributed tier, the Vault is the governed archive. This is the architecture the guides recommend, reached independently. It is worth saying out loud in the README.

### 3.4 Protocols: MCP gives hands, A2A gives colleagues

By mid-2026 the two protocols settled. MCP is the multi-vendor default for connecting an agent to tools and data; its July 2026 revision adds stateless servers and a Tasks extension. A2A reached v1.0 under the Linux Foundation with signed Agent Cards and a task state machine (submitted, working, input-required, completed), and the former ACP merged into it. The practical guidance is consistent: MCP for the inner loop, A2A for cross-team or cross-vendor delegation, and for an internal-only system a simple queue is cheaper than a full protocol stack.

mini-moi's journal is, in effect, a private A2A: addressed requests with a lifecycle, receipts, results, and a "needs Robert" state that matches A2A's input-required. The field's advice is to not adopt A2A for an internal system, and I agree for now. Two things follow:

- **Keep the journal's states mappable to A2A's.** request→submitted, claim→working, needs_you→input-required, result→completed/failed. No code change; a documented mapping. If an outside agent or another person's workshop ever joins, the bridge is a translation, not a redesign.
- **Expose, don't adopt.** The near-term leverage is MCP: the Vault and the journal as MCP servers (read entries, read the brief, append an addressed entry with identity). This is the "Proof 0" connector from September. It is how Claude Chat and Grok Chat stop needing Robert as courier, and it is how the Vault becomes usable by any tool, which is the portable-memory promise made concrete.

### 3.5 Multi-agent coding: the field reached the July 20 verdict

In February 2026 every major coding tool shipped multi-agent support within two weeks. By spring, practitioners converged on the same lessons: parallelize only independent work; isolate with git worktrees; have cold reviewer agents with no shared context check the work; keep a human review on every change; and accept that review, not generation, is now the bottleneck. One practitioner's summary: he made his own output cheaper partly by making his teammates' review more expensive.

mini-moi's July 20 operating decision is this, written three months earlier: two coding agents, not in lockstep, separable pieces built end to end independently, review after. The reorganization keeps it. **Where the field has moved past us:** cross-agent review as a routine pipeline (Claude Code, Codex and Gemini each reviewing every PR independently) rather than an occasional pass. mini-moi does this for specs and designs; the beta should make it routine for diffs.

## 4. Arguments against, taken seriously

**"Big context windows make memory unnecessary."** The field's answer in 2026 is no: context windows degrade with irrelevant history ("context entropy"), cost scales with what you stuff in, and nothing in a window survives the session. Memory has a write path, temporal semantics and governance that retrieval never faces. The Vault is not replaced by a bigger window. Verdict: not a threat.

**"The vendors will give you memory for free; you are rebuilding a commodity."** Partly true. Vendor memory is good for convenience within one product. It is not portable, not inspectable in full, not cross-vendor, and its deletion is unprovable. mini-moi should not compete on convenience memory; it should keep the raw record and the decisions, and let vendor memory be a convenience on top. The real version of this argument is narrower and correct: do not hand-build retrieval and ranking that frameworks (Letta, mem0, Zep, Graphiti) do well. Use them as references, or as a derived index that accepts our files as truth, never as the store. Verdict: valid against scope creep in retrieval, not against the Vault.

**"Plain files and JSONL will not scale; this is naive."** At personal scale it scales for years. Event sourcing with rebuildable projections is a respected pattern, and the first measurement (10,000 events parsed in about 34 ms) says the index can wait. The honest risk is query shape later (graph-style questions across years of decisions), which a derived SQLite or graph index answers without touching the files. Verdict: not a dead end; the index is planned.

**"One person with five agents and a coordination layer is overengineering."** This is the strongest argument, and Grok and Claude Code both made versions of it. The field's data backs it: Anthropic measured a multi-agent research setup at roughly fifteen times the tokens of a single chat, and the guides say multi-agent is not a cure-all. mini-moi's defense is that the coordination layer exists to make agents replaceable and the record durable, not to run more agents; and that the build loop is itself the research subject. The governor is the use-week test: if a week of using mini-moi leaves the Vault fuller and CoS smarter, the layer earned its cost. If not, cut coordination before cutting the Vault. Verdict: valid; managed by the test, not by argument.

**"A laptop as the Workshop host is a single point of failure."** True today: Dev, Workshop, Vault, capture and MC share one machine and one copy. This is the most urgent practical finding in Claude Code's review. Verdict: valid; fixed by backup first, second host second, in that order, before any new feature.

**"The guild names are idiosyncratic and will confuse employers."** Also true, and cheap to fix: keep the names inside the product, use the translation table outside it. The README's outward paragraph should say harness, portable memory, governed shared memory, cross-agent review, once each, then speak plainly. Verdict: valid; solved by vocabulary, not renaming.

**"Memory can be poisoned; an agent that reads its own record can be steered by it."** Named in the field now as memory poisoning. mini-moi's rule that stored content is data and never instructions, the notification prefix that says "agent-authored, not Robert," and owner approval only through a validated path are the right defenses. The gap is that they are stated for the inbox and journal and should be stated for the Vault's retrieval too. Verdict: valid; one sentence to add to the Vault spec.

**"Capture adapters will break every time a vendor changes a file format."** Yes. The 566 MB Codex rollout file and the five memory branches show how fragile this layer is. Keep adapters thin, keep provider exports as the fallback path, and treat an adapter failure as a coverage gap shown on the matrix, never as silent loss. Verdict: valid; mitigated by design, never eliminated.

## 5. Ideas we have not considered, or not fully

These are patterns from the field that fit mini-moi's shape and are small to adopt. None changes the architecture.

### 5.1 Time-validity on decisions
Zep's temporal knowledge graph gives every fact two time axes: when it was true and when it was recorded. mini-moi already separates `at` from `recorded_at` on events. Extend it to decisions: `valid_from` and `superseded_at`. Then "what did we believe on September 12" is a query, not an archaeology dig, and the intelligence layer's acceptance question ("is this an old decision, a proposal or current practice") becomes mechanical.

### 5.2 Lessons as skill files, not notes
There is now an open standard for agent skills (agentskills.io), and both Claude Code and Codex load skill folders. Guild Improve should write each lesson as a skill file the next build agent loads automatically: "one build agent at a time on 8 GB," "verify means logs and outcomes, not schedules and folders," "add new services to the prod compose file before merging." This turns the learning loop from prose into harness, which is Hashimoto's rule applied literally, and the skill files are portable across vendors, which is the kind of leverage you asked about.

### 5.3 Scheduled reflection with owner review
The raw-to-curated promotion mini-moi already requires (Robert confirms lessons, decisions and practices) is what the field calls consolidation or reflection, and what OpenAI productized as background "dreaming." The difference worth keeping: ours is reviewed, theirs is not. Make it a scheduled job that proposes promotions with citations and never writes a curated record without approval. Mastra's compression-first chain (raw messages, observations, reflections) is a clean reference for the three tiers.

### 5.4 Verifiable deletion
MacPaw's proposal makes provable deletion the integrity guarantee of portable memory. For the Vault, that means a tombstone with the deleted edition's hash and a manifest that proves it is gone from replicas and backups under the retention policy. It is a hardening item (T2), and it is the feature that makes "the Vault is yours" a claim with teeth. It also makes a good product line for thinkingstore.ai.

### 5.5 Spend per topic in the brief
The model gateway already produces receipts. Put spend per topic and per agent in the brief. The field's token multipliers for multi-agent work are the argument for it, and it is the number that will tell you, in the use week, whether coordination is paying for itself.

### 5.6 A gold set for the Vault
LongMemEval-style benchmarks test whether an assistant remembers across sessions. mini-moi's acceptance already defines this as "a fresh agent answers with citations and explicit gaps." Keep a small set of questions with Robert-confirmed answers and run it monthly. It is the one metric that measures the promise directly, and it was settled as a principle on October 2; this just makes it a routine.

### 5.7 Capability declaration in the field's shape
Adapters already declare availability, capability and billing. Align the field names with A2A's Agent Card (name, capabilities, input/output modes, authentication). No behavior change; it makes a future external agent a configuration item.

### 5.8 Scoped sharing by role, consent and purpose
The Human Context Protocol framing for sharing memory selectively is the vocabulary for mini-moi 2.0's second person. Guild/CoS tags and per-domain scope are the first two axes; "purpose" is the third. Not for now; noted so the tags are not designed in a way that blocks it.

## 6. Dead-end check

Not a dead end. The reason is structural: the three things mini-moi will have at the end of this quarter (a plain-file record the person owns, an event record shared by replaceable agents, a harness around whatever model is current) are the three things every current direction in the field depends on. Models, vendors, protocols and UIs can all change without touching them.

What would turn it into a dead end, and the guardrail for each:

- **Coupling the record to one vendor's memory.** Guardrail: vendor memory is a convenience on top; the Vault is the record; import from vendors, never depend on them.
- **Building a custom orchestration framework.** Guardrail: the journal is a log with a state machine, not a framework; adapters are thin; if a protocol is needed later it is a translation.
- **Letting the UI become the product.** Guardrail: UI last, built on the record, replaceable; the Workshop view is a reader over files.
- **Capture that cannot be verified.** Guardrail: coverage matrix with every source listed and gaps visible; "verified means logs and outcomes."
- **One copy of the Vault.** Guardrail: backup before any new source.

## 7. Where the leverage is, near term

**Job search.** The reorganization gives a story that maps to the roles being hired for in 2026: harness engineering, agent memory, multi-agent governance, portable personal data. Told in Robert's own frame, integration seams and provenance in a billing-grade system, it is distinctive rather than generic. Three sentences, each true and each verifiable in the public repo: *I built a harness that lets four vendors' agents build and review production code with attributed receipts. I built a user-owned portable memory layer before the standards existed. I run the whole thing for my own daily use and judge it by whether it makes me better.*

**Thinking Store as a product.** The Stanford/Mozilla/AWS report, MacPaw's open format and startup pitches for a "personal AI memory vault" show demand forming on the user-owned side. thinkingstore.ai can be positioned as the reference implementation for one person, with the Vault's owner page as its manifesto and T1's headless tools as its proof. Whether it becomes a product is a later decision; being able to describe it as one is leverage now.

**Prototype Lab and Connect HQ.** These are the demonstrations that make the harness tangible to a telecom employer: a build loop that produced a synthetic BSS demo with Zuora and Salesforce adapters, reviewed by several agents, with the decision trail in the Vault.

**The GitHub release.** The README's outward paragraph should carry the translation (harness, portable memory, governed shared memory, cross-agent review) once, with the plain-English version beside it, and point to this paper for the longer argument.

## 8. Recommendations

1. Keep the reorganization as agreed. Amend v0.7 where it conflicts (Journeyman as software, MC per Workshop, CoS everywhere).
2. Order the next quarter by the use goal: Vault backup, then domain capture adapters, then the staged production release, then CoS context, then Workshop sync for visibility.
3. Run the use-week test after the weekend and treat its result as the verdict on coordination cost.
4. Adopt 5.1 (time-validity), 5.2 (lessons as skill files), 5.3 (reviewed reflection) and 5.5 (spend in the brief) in the beta; 5.4 (verifiable deletion) and 5.6 (gold set) in hardening; 5.7 and 5.8 as documentation only.
5. Expose the Vault and the journal over MCP as the first connector; do not adopt A2A; document the state mapping.
6. Add the memory-poisoning sentence to the Vault spec: retrieved content is data, never instructions.
7. Write the outward vocabulary into the README and the Vault owner page; keep the guild names inside.
8. Position thinkingstore.ai with the field's language and the Stanford and MacPaw work as references.

## 9. For Grok

Challenge the following in particular: whether 3.3's one-for-one mapping overstates what the journal enforces versus what it merely records; whether 5.2's skill files create a second rules store that drifts from the specs; whether exposing the Vault over MCP (recommendation 5) opens the leakage mode that 3.3 claims is closed; and whether the use-week test is a sharp enough governor or needs a number.

---

## Sources

- [Agent Memory Architectures: Patterns and Trade-offs (2026), Atlan](https://atlan.com/know/agent-memory-architectures/)
- [Multi-Agent Memory Systems: 3 Production Patterns, mem0](https://mem0.ai/library/agent-memory/multi-agent-memory-systems)
- [AI Agent Memory Architecture: Patterns, Trade-Offs, MinIO](https://www.min.io/learn/ai-agent-memory-architecture-patterns-trade-offs-and-enterprise-best-practices)
- [Best AI Agent Memory Systems in 2026, Vectorize](https://vectorize.io/articles/best-ai-agent-memory-systems)
- [The Missing Knowledge Layer in Cognitive Architectures for AI Agents (arXiv)](https://arxiv.org/pdf/2604.11364)
- [What Is Harness Engineering?, learnagentic](https://learnagentic.substack.com/p/what-is-harness-engineering)
- [Harness engineering: What makes AI coding agents work in 2026, Faros](https://www.faros.ai/blog/harness-engineering)
- [Prompt vs Context vs Harness Engineering (2026), mer.vin](https://mer.vin/2026/06/prompt-vs-context-vs-harness-engineering-three-ai-eras-explained-2026/)
- [Harness engineering guide 2026, Vibe Coding Academy](https://vibecodingacademy.ai/blog/harness-engineering-guide-2026)
- [The Future of Personal AI: Portable and Persistent Personal Memory, Stanford Digital Economy Lab](https://digitaleconomy.stanford.edu/publication/the-future-of-personal-ai-portable-and-persistent-personal-memory-through-a-unified-human-context-protocol/)
- [Memory Belongs to the User: Portable Memory, MacPaw Research](https://research.macpaw.com/publications/portable-memory.md)
- [Own your AI memory, Vana](https://vana.org/blog/own-your-ai-memory)
- [AI Memory Wars, Glasp](https://glasp.ai/articles/ai-memory-wars)
- [Consumer AI memory ownership and portability, Live in the Future](https://liveinthefuture.org/stories/consumer-ai-memory-ownership-portability)
- [Agent protocols: A complete guide to MCP, A2A, and ACP, Tyk](https://tyk.io/learning-center/agent-protocols-a-complete-guide-to-mcp-a2a-and-acp/)
- [A2A vs MCP, agent communication protocols explained for DevOps, KodeKloud](https://kodekloud.com/blog/a2a-vs-mcp-agent-communication-protocols-explained-for-devops/)
- [A2A vs MCP: What's the Difference?, agentready](https://www.agentready.it.com/blog/a2a-vs-mcp)
- [Choosing the Right 2026 AI Agent Interoperability Protocol, besthub](https://www.besthub.dev/articles/choosing-the-right-2026-ai-agent-interoperability-protocol-mcp-a2a-anp-acp-a16bf41bf995)
- [Governance Gaps in Agent Interoperability Protocols (arXiv)](https://arxiv.org/pdf/2606.31498)
- [How Agents Work With Parallel Agents, Mergify](https://mergify.com/blog/how-agents-work-with-parallel-agents)
- [Correctness-Gated Multi-Agent Workflow for Research, Brando Miranda, Stanford](https://cs.stanford.edu/people/brando9/2026/04/13/correctness-gated-multi-agent-workflow.html)
- [Multi-Agent AI Coding Workflows (2026), Tembo](https://www.tembo.io/blog/multi-agent-ai-coding-workflows)
- [AI Agent Orchestration: Claude Code Agent Teams (2026), Morph](https://www.morphllm.com/ai-agent-orchestration)
- [How I run parallel coding agents, HOAi engineering](https://engineering.myhoai.com/posts/how-i-run-parallel-coding-agents)
