# mini-moi Learning System — Roadmap
*Created: 2026-06-16 — Claude.ai*
*Location: docs/LEARNING_SYSTEM_ROADMAP.md*
*Status: Active roadmap — foundational steps in progress*

---

## What this is

mini-moi was built on a premise: that an AI system should learn with you
over time, not reset with every session. Most AI tools are stateless —
they know everything in their training data and nothing about you. mini-moi
is built the other way: your context, your goals, your signal.

This roadmap describes how that promise gets delivered technically — how
the system accumulates knowledge, gets better at reasoning about your
specific work, and eventually develops the kind of deep institutional
knowledge that makes it a genuine long-term collaborator rather than a
capable but amnesiac tool.

This is not a speculative vision document. Every phase described here is
technically achievable with your current stack. The question is sequencing —
building the foundation before the structure, the same discipline that held
Neo4j and Postgres until there was sufficient history to justify them.

---

## The core model — institutional memory plus frontier capability

The system is built around two complementary roles that do not replace
each other:

**The local LLM — institutional memory.**
Knows the codebase completely. Knows the decision history. Knows the
failures and why they happened. Knows your intent and objectives, not just
what was built. Knows your working conventions, your voice, your design
principles. Has been here the whole time. Gets better the longer it runs.

**Frontier models (Claude, Grok, OpenAI) — outside consultants.**
Bring current best practices, frontier capability, outside perspective,
and the ability to structure a project from a position of broad knowledge.
Don't know your specific history but bring things the local model can't —
up-to-date capability, broad training, and a perspective unconditioned by
your accumulated assumptions.

Neither replaces the other. The local LLM without frontier models loses
access to current capability and outside perspective. Frontier models
without the local LLM are always starting from scratch — re-explaining
the project, re-establishing context, re-litigating decisions already made.

The goal is not to replace frontier models with a local one. The goal is
to have the local model carry the institutional knowledge so that when
frontier models are brought in, they're consulting on a well-understood
system rather than being handed a session summary and asked to catch up.

**In one line:** Frontier models for capability. Local system for ownership
and continuous learning.

**The long-term employee / consultant analogy:**
The local LLM is the senior employee who has been on the project for years
— knows every corner of the codebase, remembers every decision and why,
knows what was tried and failed. Claude and Grok are consultants brought
in for specific expertise, best practices, and to structure new problems.
The consultant's value depends on the employee being able to brief them
accurately. The employee's value is amplified by the consultant's outside
perspective. The team needs both.

---

## Why start now

Two reasons:

**The data starts accumulating from day one.** Decision records, session
summaries, design rationale — these are more valuable the more history they
contain. Starting late means losing the early decisions, the early failures,
the early reasoning that shaped everything that came after. The cost of
starting now is low. The cost of starting in six months is losing six months
of compounding signal.

**The local LLM evaluation loop is the preparation.** You don't know how
good your local model is for this role until you use it in the actual
design flow. Starting now — as a junior partner, with responses vetted
by Claude or Grok — is how you calibrate what it can do, where it falls
short, and what the LoRA training needs to address. The evaluation IS the
preparation. Waiting until the infrastructure is perfect to start evaluating
means you never start.

---

## Phased plan

### Phase 0 — Foundation (now, no new infrastructure required)

**Goal:** Establish the practices that make everything else possible.
No new tools. No new infrastructure. Changes to workflow only.

**0.1 — Design Session Prompt**
Adopt `docs/DESIGN_SESSION_PROMPT.md` for all significant design
conversations — with Claude, Grok, OpenAI, and the local LLM.
Shared flagging. "Write decision record" trigger. Same format across
all models.

**0.2 — Decision Records**
Produce a Decision Record (DR) for every significant architectural or
product decision going forward. Store in `docs/decision-records/`.
Not retroactively for everything — forward from now.
Format: `dr_[topic]_[YYYY-MM-DD].md`

**0.3 — Local LLM as junior partner**
Start including the local LLM in design conversations now — not as the
primary, but as an additional voice whose responses are evaluated against
Claude and Grok. The goal is calibration: understand where it adds value,
where it falls short, what it needs to know to be more useful.
Treat its responses as a junior team member's — worth hearing, worth
vetting, valuable signal for what the training needs to address.

**0.4 — Flag LoRA candidates in session summaries**
When a session summary or DR contains reasoning, principles, or decisions
that would be valuable to have internalized (not just looked up), mark it:
`[LoRA candidate]`. This is the curation step that makes the eventual
training run high-signal rather than high-noise.

**0.5 — Define evaluation harness**
Before Phase 1 begins, define a small set of concrete questions the local
LLM should be able to answer correctly once RAG is wired. These become
the before/after test at the Phase 0/1 boundary. No infrastructure needed
to define them — just write them down.

Example eval questions:
- "What is the current mobile priority for Gespräche and why?"
- "What was the reasoning behind keeping Meet-specific labeling?"
- "What does `run_chat_turn()` do and why is it separate from `run_review()`?"
- "What was rejected when we considered building RAG without LoRA?"
- "What are the standing principles for Gespräche development?"

Pass/fail against known correct answers. This turns "it feels better"
into measurable progress. The eval set grows as the project grows.

**Completion criteria for Phase 0:**
- Design session prompt in use for at least 5 significant sessions
- At least 10 decision records produced and stored
- Local LLM consulted on at least 3 design questions with responses noted
- LoRA candidates flagged in session summaries going forward
- Evaluation harness defined (minimum 5 questions with known correct answers)

---

### Phase 1 — RAG Layer (next significant build, after Phase 0 habits established)

**Goal:** The local LLM can answer questions about the current state of
the project accurately — from the actual codebase and documents, not
from training data.

**1.1 — Index the repo with metadata**
Set up a local vector store (ChromaDB or SQLite-vec, both run on Mac Mini).
Index the entire `personal-ai-agents` repo:
- All `.py` files chunked at function/class boundaries
- All `.md` files chunked at heading/section boundaries
- Persona `.txt` files
- Session summaries and decision records
- Git commit messages (history awareness)

Index with metadata — not just raw text. Each chunk carries:
- `type`: decision-record / session-summary / code / spec / intent
- `domain`: german / curator / guild / platform
- `recency`: file date or commit date
- `status`: active / superseded (for DRs and specs)

Metadata enables filtered retrieval: "show me only active decision records
for the German domain" rather than naive top-k across everything. This
also prepares for a graph layer later without requiring a rebuild.

**Architecture note:** Keep the memory and retrieval system in its own
module (`memory/` or `learning/`) from day one. Clean interface, domain
logic separate from retrieval logic. This allows the retrieval approach
to evolve (vector → hybrid → agentic) without touching the rest of the
codebase.

**1.2 — Wire to local LLM**
Local LLM (Ollama) receives: query + retrieved chunks.
Answers from the actual project state, not from training data.
Model-agnostic retrieval layer — the vector store works regardless of
which LLM handles generation.

**1.3 — Incremental updates**
Git post-commit hook re-indexes changed files.
Commit new code → RAG knows it within seconds.
OpenClaw writes memory files → RAG indexes them automatically.

**1.4 — OpenClaw integration**
RAG layer makes OpenClaw's memory files queryable by the local LLM.
OpenClaw writes → RAG indexes → local LLM reads.
Three components become one coherent memory system.

**What changes after Phase 1:**
Ask the local LLM "what does `review_router.py` do" → it retrieves
and answers from the actual file. Ask "what specs are in the build queue"
→ it pulls the latest session summary. Ask "what did we decide about
the Google Meet labeling" → it finds the decision record. No more
re-explaining current state.

**1.5 — Run evaluation harness**
At Phase 1 completion, run the eval questions defined in Phase 0 against
the RAG-equipped local LLM. Record results. This is the baseline that
Phase 2 (LoRA) improves on.

**Known evolution path (not Phase 1 scope):**
Pure vector RAG scales well to start but gets retrieval noise as the
corpus grows. The next step is hybrid retrieval: vector search for
semantic similarity plus a lightweight graph layer for entity relationships
(Personas, Decisions, Constraints, their connections). This is the
direction the field is moving (GraphRAG, knowledge graph + vector hybrids).
The `memory/` module architecture makes this evolution possible without
a full rebuild. Don't build it in Phase 1 — note it and keep the door open.

Similarly, agentic retrieval (the model decides what to fetch rather than
naive top-k) is emerging and will be more practical by Phase 2/3 time.
Design for it; don't build it yet.

**Completion criteria:**
- Full repo indexed with metadata, local LLM answering questions from
  actual files
- Incremental update on commit working
- OpenClaw memory files in the index
- Evaluation harness run, results recorded
- At least 2 weeks of daily use before moving to Phase 2

---

### Phase 2 — LoRA Training (after Phase 1 is stable, ~3 months from now)

**Goal:** The local LLM internalizes your design philosophy, working
conventions, and project intent — not as documents it can look up,
but as patterns it has absorbed.

**What RAG cannot do that LoRA can:**
RAG gives the model documents to read. LoRA changes how the model
reasons. A model that has RAG access to your decision records can
look up what was decided. A model with a LoRA adapter trained on
those records has internalized *why* — the principles, the reasoning
patterns, the things that are always true about how you work.

**2.1 — First training run**
Training data: curated LoRA candidates from Phase 0 and Phase 1.
Session summaries, decision records, key design conversations.
Focus: design philosophy, working conventions, project intent.
Not the codebase (that's RAG's job) — the *why* behind the codebase.

Format: instruction-following pairs extracted from design conversations.
Example:
```
Instruction: We are deciding whether to add streaming TTS to Gespräche.
Input: Current TTS uses full audio file generation before playback.
       Latency is 3-5 seconds per turn. This is a daily-use system.
Response: Streaming TTS would meaningfully reduce latency but adds
          complexity to a system Robert uses every day. Per standing
          principle: don't add operational risk to working daily-use
          systems for incremental gains. Measure first, then decide.
          Defer streaming TTS to Phase 2 of Gespräche roadmap.
```

**2.2 — German error pattern adapter**
Separate LoRA adapter trained on accumulated Gespräche session transcripts
and error/correction pairs. After 3-6 months of daily sessions:
thousands of correction pairs, your specific error fingerprint, your
personas' correction style.
Result: local model that corrects your German the way your personas do,
without a cloud API call.

**2.3 — Update cadence**
Not continuous — periodic and milestone-driven.
Suggested cadence: every 6-8 weeks, or at major project milestones.
Each update absorbs the accumulated decisions and learnings since the
last run. The adapter improves with each cycle.

**Completion criteria:**
- First adapter trained and evaluated against Claude/Grok responses
- German error pattern adapter trained on session history
- Update pipeline documented and repeatable

---

### Phase 3 — Integrated local-first operation (6+ months)

**Goal:** The local LLM handles routine design questions, code questions,
and German practice independently. Frontier models consulted for complex
architectural decisions, novel problems, and quality checks.

**What this looks like in practice:**
- Open a design session → local LLM already knows the project, no
  session summary needed
- Ask about current codebase state → RAG answers from actual files
- Raise a design question → local LLM reasons from internalized principles
  plus retrieved context, proposes an answer
- Robert vets with Claude or Grok for significant decisions — the
  consultant check on the employee's recommendation
- German practice → local model handles conversation and correction for
  routine sessions; frontier model consulted for new pattern types

**The team at Phase 3:**
- Local LLM: daily driver, institutional memory, routine decisions
- Claude.ai: complex architecture, new domain problems, spec structure
- Grok: parallel review, second opinion, coverage during Claude limits
- OpenClaw: memory writes, file management
- Claude Code: all implementation and git

**What does not change:**
Robert is still sole decision-maker and approves all pushes. The local
LLM's role is to make his decisions better-informed and faster — not to
make them for him.

---

## What this is not

**Not a replacement for frontier models.**
Frontier models improve faster than local ones. Claude and Grok will
always bring something the local model doesn't have — current capability,
outside perspective, broad training. The goal is complementarity, not
substitution.

**Not a training project for its own sake.**
Every phase must earn its place by making the daily workflow better.
Phase 0 earns its place by capturing reasoning that would otherwise be
lost. Phase 1 earns its place by making the local LLM useful for actual
questions about the project. Phase 2 earns its place when there's enough
accumulated signal to make training worthwhile. Don't build ahead of the
signal.

**Not a fixed plan.**
Local model capability is improving fast. The 6-month picture may look
different from what's described here — better base models, easier LoRA
tooling, new retrieval approaches. This roadmap should be reviewed at
each phase transition and updated to reflect what's actually available.

---

## Relationship to existing roadmap

This learning system is the infrastructure layer that makes everything
else compound. It does not replace the domain roadmaps (Curator, Mein
Deutsch, Guild) — it amplifies them. Every decision made in those domains
becomes part of the institutional knowledge base. Every session transcript
becomes training signal. The domains feed the learning system; the
learning system makes the domains smarter over time.

The foundational steps (Phase 0) are preparation the same way the
database work was preparation for Guild — you don't build the structure
until you have the foundation. Phase 0 starts now. Phase 1 starts when
Phase 0 habits are established. Phase 2 starts when there's enough
signal to make it worthwhile.

---

## Immediate next actions

| Action | Owner | When |
|--------|-------|------|
| Commit `docs/DESIGN_SESSION_PROMPT.md` to repo | Claude Code | Next commit |
| Add DR step to `docs/HANDOFF_PROCESS.md` | Claude Code | Same commit |
| Create `docs/decision-records/` directory | Claude Code | Same commit |
| Start using design session prompt in sessions | Robert | Now |
| Consult local LLM on next design question, note response quality | Robert | This week |
| Flag LoRA candidates in session summaries going forward | Robert + Claude.ai | Now |
| Spec RAG layer architecture (Phase 1) | Claude.ai | When Phase 0 habits established |

---

*Learning System Roadmap · 2026-06-16 · Claude.ai*
*Review at each phase transition and update to reflect current capability*
