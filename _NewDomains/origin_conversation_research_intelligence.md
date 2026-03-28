# Origin Conversation: Research Intelligence Agent
**Date:** March 20, 2026  
**Participants:** Robert (owner), Claude (design collaborator)  
**Status:** Historical artifact — how the agentic phase of personal-ai-agents began  
**Repo:** personal-ai-agents / mini-moi  

---

## Context

This conversation took place in a Claude.ai web chat session on March 20, 2026. Robert had just spent a frustrating session doing back-and-forth UI fixes with Claude Code and OpenClaw, which led to a discussion about better tooling for UI work — and then pivoted into something more substantive: designing the first genuinely agentic task for the personal-ai-agents project.

The conversation moved through three phases:
1. UI tooling frustration → screenshot-based fix workflow → saved UI fix plan
2. "What real agentic work could OpenClaw do?" → four candidate directions
3. Designing the Research Intelligence Agent from scratch, iteratively, in a single session

What follows is a summary reconstruction of the key exchanges, with the reasoning preserved. The full direction document (`openclaw_direction_research_intelligence_v2.md`) is the output artifact.

---

## The Starting Point

Robert raised the question: what kind of work could OpenClaw do that is genuinely agentic — open intent with guardrails, where it figures out what to do itself — rather than just executing tasks?

Four candidate directions were proposed:

- **Source Quality Auditor** — evaluate RSS sources by signal yield against like/dislike data
- **Investigation Thread Builder** — find emerging stories not yet tracked as Priorities
- **Weekly Signal Review** — calibrate scoring model against actual feedback signals
- **RSS Gap Hunter** — find 3 sources worth adding based on category performance

Robert chose none of these directly. Instead he proposed something bigger.

---

## The Core Idea Emerges

Robert's framing (paraphrased from the conversation):

> "Find new sources — including in Chinese, Russian, and other languages — translate examples, send them to me from time to time, and we build some more trusted sources. It can confirm against what others are recommending, what scholars are saying, especially in developing countries with strong universities. A few weeks, see if it works."

This was the seed. What followed was a design conversation that expanded the scope considerably while keeping the core intent: **a slow-burn research intelligence that surfaces what the Anglophone briefing system misses.**

---

## Key Design Decisions and How They Were Made

### Languages
Started with Chinese, Russian, Portuguese, Spanish, German. Robert added Japanese, confirmed Arabic and French, dropped Hindi. The principle: languages where serious institutional scholarship exists and is underrepresented in Western feeds.

Portuguese and German got special treatment: Robert reads both, so the agent should flag rather than translate — don't waste budget on what he can read himself.

### Beyond RSS
Robert pushed to include books, academic papers, and abstracts — not just feeds. The insight: an abstract is enough to decide whether something matters. A book from 1963 that predicted the current configuration is worth finding even if you can only get the introduction.

### Budget and Model Routing
$10/week, $3/day, $20 total stop. Robert's instinct: route cheap tasks to Ollama and Haiku, reserve Sonnet for synthesis only. The rule that emerged: **no Sonnet call on content that hasn't passed a Haiku triage first.** This is a hard constraint, not a guideline.

### Scheduling Autonomy
Robert: "It can run whenever it wants, just not all at once. Could be a 2-minute burst or a periodic longer session. It can decide."

This was a significant design choice — the agent self-schedules rather than being triggered. It reads its own session log to know where it is, continues open threads before starting new ones, and spreads work over time.

### The Long Time Horizon
The key intellectual framing came from Robert citing Kotkin: the argument that Russia and China swapped geopolitical positions over 70 years. This became the pilot thread — and established the agent's core behavior: **every historical frame needs a current relevance connection, every current event needs a historical precedent connection.**

Robert's formulation: "Should go back in time. No limit on that really. This is a free-form search on my interests that should bubble up to current events but with a very long time horizon."

### The Citation Chase
"If a reputable source suggests old parallels, the agent should go find them. There might be one famous old author but there are other less-cited ones who might have a better view."

This became a core behavior: chase the citation graph backward. The less-cited author with the better frame is the target.

### Collaboration, Not Tool Execution
Robert: "This is a collaboration. It can ask me for advice. I can put things into its web chat and not just Telegram, since I prefer MacBook. I will talk to it or chat at times and it can use that and give me feedback, informally."

This reframed the entire relationship. The agent isn't executing a static direction — it's building a working model of Robert's intellectual interests over time, updating from every interaction, pushing back when the evidence warrants it.

### The Local Library
Robert: "OpenClaw should build a library or reference on my local disk where I can read easily. I don't need a UI but a directory or simple interface. Everything should be able to easily inform the Curator."

Design decision: simple directory structure, everything findable with grep, no database until functional completeness is proven. The library and the agent are loosely coupled — agent writes to library, library can inform Curator, but neither depends on the other being finished.

### Essays
Robert: "It can also write an essay itself summarizing some findings. 5-page limit for now."

The agent writes synthesis essays on its own initiative when it has accumulated enough validated material. Argument-first, cited, ends with "what to watch."

---

## The Project Narrative

The last phase of the conversation established the broader context:

Robert wants this documented as a case study — win or lose — and published on GitHub as part of the personal-ai-agents public repo. The framing:

**Phase 1 (current):** Intelligent Curator with feedback loop. Ranked briefings, like/dislike/save signals, Telegram delivery, four-tier model stack, learning from user behavior.

**Proof of Concept (this):** First agentic extension. Takes the signal base from Phase 1, deploys an autonomous research agent on longer time horizons, across languages, with minimal supervision.

**Phase 2 (future):** Formalized agentic extension. Secure, extensible, documented architecture. The jump from "works for Robert" to "works as a pattern."

Robert's framing: "The experiment might fail, but our goal is to make this part of the personal-ai-project. It is the first agentic attempt. A successful end result would be to show on GitHub how the project stepped up from an intelligent curator with feedback into something that used the data from that base but added the agentic part."

---

## What Was Produced

Three files from this session:

1. **`curator_ui_fix_plan.md`** — Six prioritized UI consistency fixes for the Curator interface, saved for a future Claude Code session. Not the focus today.

2. **`openclaw_direction_research_intelligence_v2.md`** — The full direction document for the Research Intelligence Agent. This is the primary output. It covers mission, autonomy, budget, model routing, languages, source types, library structure, communication format, essay standards, the Kotkin pilot thread, guardrails, and success criteria for the 3-week pilot.

3. **`origin_conversation.md`** (this file) — The historical artifact documenting how the agentic phase began.

---

## What Happens Next

1. Robert saves the direction document to OpenClaw
2. Budget enforcement mechanism is confirmed or built (cost ledger in session-log.md)
3. Library directory structure is initialized on local disk
4. Agent activates and begins the Kotkin pilot thread
5. Robert and the agent interact informally over the following weeks via Telegram and web chat
6. At week 3, evaluate against success criteria
7. If 3 of 5 pass: continue, expand, begin formalizing Phase 2
8. Either way: publish to GitHub with session logs as the case study record

---

## A Note on How This Was Designed

The direction document was not written from a brief. It was built iteratively in conversation, with Robert adding constraints and expansions as the design emerged. The final document reflects about 45 minutes of back-and-forth, with each exchange either deepening an existing element or adding a new one.

The collaboration model baked into the direction document — the agent asks questions, Robert redirects, informal chat is a valid input, the agent updates its model of Robert's interests over time — mirrors the way the direction document itself was produced.

That wasn't accidental.

---
*Saved: March 20, 2026*  
*For: personal-ai-agents GitHub repo, agentic phase origin documentation*
