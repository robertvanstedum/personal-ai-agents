# Design-Build — Guild Cabinet

Concept to delivery with persistent memory of why decisions were made.
Formalizes what Robert and the agents already do. Naming it adds the
"why" layer that gets lost between sessions.

**Role definition:** [GUILD_CHARTER.md §8.2](../GUILD_CHARTER.md#82-design-build)

---

## Primary Artifacts

| Location | What it is |
|----------|-----------|
| `career/api-toolkit/` | TMF622/TMF620 specs, patterns, diagrams, interview scenarios |
| `design-decisions.md` (this directory) | Running log of the "why" behind every build choice |

## Sub-Roles

- **Product Owner** — holds the backlog and design decisions across sessions
- **Architect** — patterns, standards (TMF622, OAuth), technical decisions
- **Test + Docs** — test standards (32 German tests as model), documentation standards

## Trigger Phrases

- "Let's design [X]" → Claude.ai architect mode
- "Build [X]" → Claude Code implementation
- "Why did we decide [X]?" → design-decisions.md
- "What's the spec for [X]?" → api-toolkit or domain spec

## Maps To

Claude.ai (architect) + Grok (research) + Claude Code (implementation) + OpenClaw (product memory)
