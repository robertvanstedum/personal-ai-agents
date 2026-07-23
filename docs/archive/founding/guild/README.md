# Guild

*A working model of a human + AI team.*

Guild is the operating model and intelligence layer for **mini-moi** — the connective tissue that turns separate functional domains (Curator, Mein Deutsch, and future domains) into a coherent, learning team.

Where the other domains do specific jobs, Guild defines *how the work itself gets done*: how intent is set, labor is divided, feedback is captured, and the system gets measurably better over time.

## Core Idea

A good team has roles, shared memory, and a tight loop between intent → action → real-world feedback → adjustment. Guild builds exactly that with AI agents in the team seats.

The human sets intent and makes decisions. The **Chief of Staff** carries the strategy, keeps domains healthy, scans for relevant change, and surfaces recommendations — never deciding on its own.

## Documents in This Domain

- **CHARTER.md** — The vision, roles, and why Guild exists (the "what" and "why")
- **TECHNICAL_PLAN.md** — Architecture, the shared spine (Postgres + graph + temporal memory), model gateway, MCP/A2A, build roadmap, and investigation plan (the "how")

## Founding Step: mini-moi Portal (minimoi.ai)

The live portal at [minimoi.ai](https://minimoi.ai) is the first visible integration point. It provides a unified, authenticated entry point to Curator and Mein Deutsch behind a clean reverse proxy + auth layer (owner / family / guest tiers).

This portal demonstrates the "Hub" concept that Guild will eventually deepen with cross-domain memory, health monitoring, and the Chief-of-Staff function.

## Status

- **Phase 0** (functional domains in daily use): Complete
- **Phase 1** (Guild front door + portal as integration surface): In progress
- **Phase 2** (the spine): Next focus

Guild is part of mini-moi — not a general intelligence, a specific one.