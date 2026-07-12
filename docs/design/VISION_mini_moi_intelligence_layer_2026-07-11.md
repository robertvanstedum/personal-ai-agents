# mini-moi: A Working System for Multi-Agent Coordination Without Lock-In

**Companion to:** Spec #133 v1.2 — Intelligence Layer
**Date:** 2026-07-11
**Author:** Claude.ai design session (Fable 5), with Robert van Stedum

---

## What mini-moi is

mini-moi is a personal AI platform, built and operated by one person, running four live domains in daily production: geopolitical/financial intelligence (Curator), German and Portuguese language learning, and a Chief of Staff agent that holds conversational memory across all of it. It runs on AWS EC2, has run daily since February 2026, and every design decision behind it — hundreds of them — is captured somewhere in its own history.

That's the surface description. What makes it worth a vision document is what's underneath: mini-moi isn't built around one AI vendor, one agent framework, or one model. It's built around an architecture that treats agents as replaceable parts and proves that replaceability, rather than claiming it.

---

## The problem with how "multi-agent" is usually talked about

Most multi-agent conversation in 2026 is about orchestration: one framework coordinating several models toward a task. That's real, and useful, but it's a narrower claim than it sounds — the models are interchangeable *within* the orchestration layer, but the orchestration layer itself is usually the lock-in point. Swap the framework, and everything built on it moves too.

mini-moi's bet is different: **the thing that shouldn't be swappable is the platform's memory and its interfaces. Everything else — which model, which agent, which cloud, which hardware — should be able to change without the platform noticing.**

That's a harder bar than orchestration, because it means the swappability has to be proven at the point where it's most tempting to cut corners: the component doing the most interesting work.

---

## Three pillars, and what's actually built — not promised — behind each

### 1. Multi-agent, in production, today — not a diagram

This isn't a slide about what multi-agent coordination *could* look like. Here's what's actually running, as of this week:

- **Claude.ai** designs — specs, architecture, review synthesis
- **Claude Code** builds — the agentic coding sessions that turn specs into running infrastructure
- **Grok** does two distinct jobs — external review of designs, *and* it's the model actually running CoS in production (`chief_of_staff.py`), with a working chat loop, tool calls, and scheduled reasoning
- **OpenClaw** runs Robert's personal assistant, isolated from mini-moi entirely — and architecturally, it's one of the possible backends CoS's coordination layer could call someday, on the same footing as any model API, not a special case

Four vendors, four distinct roles, coordinated through shared files and a defined build process — not a single vendor's agent framework deciding how they talk to each other.

### 2. Lock-in avoidance, at the layer that actually turns over

The naive version of "no lock-in" swaps the whole agent for a different one. mini-moi's approach is more precise than that, and the precision matters: **the coordination code — routing, scope enforcement, memory — is mini-moi's own, stays constant, and is never up for replacement. The thing that's genuinely swappable, and swapped often, is the model doing the reasoning underneath it.**

That's a deliberate correction from an earlier framing of this same document, worth stating plainly because getting it right mattered: models rotate — Grok today, whatever's better next quarter, the quarter after — and that rotation should cost nothing structurally. Swapping the *coordination layer itself* for a different framework is a much rarer, heavier decision, and conflating the two would have made the platform harder to reason about for no real benefit. This week's build proves the model-swap boundary directly: the same coordination code, calling two different backends, producing equivalent platform state. That's the test that matches what will actually happen repeatedly, not a hypothetical one.

This extends past agents to the data itself. Every piece of the system's accumulated memory and decision history — design conversations, build sessions, CoS's own conversational memory — is architected to be portable in the most literal sense: a single command (`git clone`, or `aws s3 sync`) reproduces the entire knowledge base on a machine that has never seen mini-moi before. Not a backup in case of failure — a working assumption that the whole system might need to move, to a different cloud, a different agent stack, a different hardware tier, and nothing about its history should be trapped wherever it happens to be running today.

### 3. Learning over time — compounding, not resetting

mini-moi's founding thesis is that it's a process, not a product: the value isn't in any single response, it's in the system getting better at helping because it remembers what happened before. That only works if "remembering" is real, not aspirational.

The architecture built this week draws a careful line between two different kinds of memory, deliberately kept separate rather than collapsed into one convenient store: a *comprehensive* record of every design and build decision (low-filter, store-everything, meant to be mined later by tools that don't exist yet), and CoS's own *curated* conversational memory (high-filter, meant for fluid day-to-day use). Merging them would have been simpler to build and worse to use — a live system needs judgment about what to surface, and a historical record needs to not lose anything to that judgment. Keeping them apart is what lets both jobs be done well.

The next phases build directly on this: a local model that indexes the codebase, the design archive, and the language-learning history — not to replace human or cloud reasoning, but to make it possible to ask "what have we tried, and what did we learn" and get a real answer, grounded in what actually happened, not a plausible-sounding reconstruction.

---

## Why this matters beyond mini-moi

The pattern here — coordinate several vendors' agents deliberately, refuse to let any one of them become structurally necessary, and treat the system's own history as a portable asset rather than platform-locked exhaust — isn't specific to a personal project. It's a bet about what matters as agentic AI tooling multiplies: not depth in any one vendor's stack, but the judgment to combine, sequence, and govern several of them so the whole is resilient to any one part changing.

That bet gets more relevant, not less, as the field does — this week alone, a third major lab (xAI, with Grok Build) entered the terminal-coding-agent market that Claude Code and Codex CLI already occupied, and Cursor is reportedly being absorbed into a fourth. A platform built assuming today's best agent stays best is a platform that has to be rebuilt every time that assumption breaks. mini-moi is a working, running argument that it doesn't have to be rebuilt — it has to be *designed* not to need it.

---

## Status, plainly

This is a showpiece for the architecture and the discipline behind it, not a claim that everything described here is finished. As of today:

- **Built and proven:** four production domains, daily operation since February, CoS running live on Grok with real conversational memory
- **Designed and specified, build starting now:** CoS containerization, the swappability A/B test, the per-agent portable archive, the dual-destination (GitHub + S3) sync
- **Designed, not yet built:** the local-model indexing layer, Build/Curator/Language Intelligence

Keeping that distinction honest is part of the point. A showpiece about avoiding lock-in and proving claims rather than asserting them would undercut itself by overstating its own progress.

---

*Companion to Spec #133 v1.2 — see that document for full technical detail, the Data Flow Model, and the Portable Archive specification.*
