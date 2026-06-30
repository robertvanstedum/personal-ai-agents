# Design Input: Three Areas for Consideration

**Date:** June 30, 2026  
**From:** Grok  
**For:** Claude (design discussion)

---

## 1. AutoResearch – Potential Integration into mini-moi

### Summary

Karpathy’s AutoResearch (March 2026) is an autonomous agent loop that lets an AI agent run repeated experiments (modify code → train → evaluate → keep or revert) with minimal human input. The human primarily controls the system through a structured Markdown file (program.md).

### Key Patterns Worth Considering

- **Markdown as the primary control interface**: The human defines goals, constraints, and behavior in plain text rather than complex orchestration code.
- **Constrained agent scope**: The agent is only allowed to modify one specific file (train.py), while the evaluation harness remains protected.
- **Propose → Test → Commit or Revert pattern**: Changes are tested against a clear metric. Successful changes are kept (via git); unsuccessful ones are cleanly discarded.
- **Fixed evaluation budget**: Experiments run for a consistent time window, making results comparable.

### Potential Applications in mini-moi

| Area                  | Possible Use                                      | Value       | Notes                              |
|-----------------------|---------------------------------------------------|-------------|------------------------------------|
| Intent Layer          | Agent proposes updates to Intent documents for review | High        | Strong alignment with readable control |
| Weekly Synthesis      | Agent runs synthesis loops over recent sessions   | High        | Could generate draft summaries     |
| Exploration Archetype | Help surface connections across threads and topics | Medium-High | Fits divergent nature              |
| Error Pattern Analysis| Detect recurring issues across sessions           | Medium      | Could feed Learning domains        |
| Curator Domain        | Autonomous research and synthesis on topics       | Medium      | Overnight research loops           |

### Recommendation

We should note AutoResearch as a useful reference pattern rather than something to adopt directly. The most transferable ideas are the Markdown-first control layer and the propose → evaluate → keep/revert discipline. These could inform how we design future synthesis agents and the Intent layer.

---

## 2. Memory Layer – Mem0 and Karpathy’s Approach

### Mem0 Overview

Mem0 is currently one of the leading dedicated memory frameworks for AI agents (both open-source and hosted). It focuses on long-term memory across conversations through fact extraction, entity linking, graph-style relationships, and conflict resolution. It supports different memory scopes (user, agent, and session).

**Strengths of Mem0:**
- Mature feature set (entity linking, graph memory, conflict handling).
- Actively maintained and widely adopted in 2026.
- Good retrieval performance.

**Limitations for mini-moi:**
- Adds significant framework complexity.
- Less emphasis on human-readable memory.
- Somewhat opinionated (wants to own the memory layer).
- Likely overkill at current scale.

### Karpathy’s Memory Philosophy

Karpathy has not built a dedicated memory framework, but his work and comments suggest a clear preference for simplicity:

- Views current LLMs as having “anterograde amnesia” (they don’t naturally consolidate long-term knowledge).
- Favors simple, human-readable approaches (e.g., maintaining knowledge in Markdown files — sometimes called an “LLM Wiki” style).
- In AutoResearch, he uses git history + structured logs for memory of what worked, rather than complex databases.
- Strongly prefers minimal, hackable systems over heavy frameworks.

### Recommendation for mini-moi

| Phase       | Recommended Approach                                      | Rationale |
|-------------|-----------------------------------------------------------|-----------|
| Short-term  | Markdown + structured JSON for Intent layer               | Aligns with Karpathy’s simplicity preference and keeps memory human-readable |
| Medium-term | Add lightweight structure (stable IDs, typed relationships) | Prepares for future graph-like capabilities without heavy infrastructure |
| Long-term   | Evaluate Mem0 (or parts of it) only if clear pain points emerge | Avoid premature complexity |

We should aim for a system that starts simple and readable, while leaving room to selectively adopt stronger memory techniques (such as entity linking or conflict resolution) later.

---

## 3. OpenClaw as Central Glue / Orchestrator Layer

### Idea

Instead of building complex, hardcoded orchestration logic across domains and agents, we could use OpenClaw as the central coordination and memory glue. This would make OpenClaw more explicitly responsible for coordination between agents and domains.

This represents a shift from my usual preference for clean, explicit logic. However, Karpathy’s emphasis on human-readable control (via program.md) made me reconsider whether a more explicit, inspectable orchestration layer could be valuable.

### Proposed Concept: “Chief of Staff” Role

- OpenClaw could act as a persistent Chief of Staff layer.
- It would handle coordination, routing, memory access, and high-level workflow management between domains (Guild, Curator, German, Portuguese, Exploration).
- Other agents would remain more specialized and focused, while OpenClaw manages the “org chart” and information flow.
- Because OpenClaw is swappable, we could later replace or augment it with a different agent (or even a small team of agents) without rewriting core logic.

### Potential Benefits

- Creates a single, inspectable point for coordination and memory.
- Makes the overall agent “organization” more explicit and understandable.
- Aligns with Karpathy’s idea of controlling complex behavior through readable instructions rather than hidden logic.
- Provides a clean separation between specialized agents and orchestration/coordination.
- Supports future evolution (e.g., replacing OpenClaw with a more advanced orchestrator later).

### Potential Downsides / Risks

- Could centralize too much responsibility in one place.
- Might reduce clarity if too much logic moves into OpenClaw prompts instead of explicit code.
- Requires careful design of the interface between OpenClaw and domain-specific agents.

### Questions for Discussion

- Should OpenClaw act purely as memory + coordination glue, or should it also hold high-level decision-making authority?
- How much orchestration logic should live in prompts vs. code?
- Would we still want some lightweight explicit orchestration code, or lean fully into OpenClaw as the coordinator?
- How would this affect the current working model (OpenClaw manages memory/files)?

---

## Summary of the Three Areas

| Area                  | Core Idea                                                      | Alignment with Current Thinking      | Priority for Discussion |
|-----------------------|----------------------------------------------------------------|--------------------------------------|-------------------------|
| AutoResearch          | Markdown-first agent control + propose/test loops              | High                                 | Medium                  |
| Memory Layer          | Simple + readable first, selective adoption of advanced tools later | High                            | High                    |
| OpenClaw as Glue      | Use OpenClaw as explicit Chief of Staff / orchestrator layer   | Medium (shift in approach)           | High                    |

These three areas are interconnected. The human-readable control pattern from AutoResearch strengthens the case for making orchestration more explicit (via OpenClaw), while both ideas should inform how we design the memory layer.

---

*This document is ready to share with Claude. Let me know if you want any section adjusted or expanded before sending it.*