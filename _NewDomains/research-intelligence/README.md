# Research Intelligence Agent
**Project:** personal-ai-agents — agentic extension  
**Status:** Proof of Concept — Pilot active  
**Started:** March 20, 2026  
**Owner:** Robert  

---

## What This Is

The first agentic extension of the Curator project.

Curator (Phase 1) is an intelligent daily briefing system: it ranks articles, learns from like/dislike/save signals, and delivers a personalized geopolitics + finance briefing every morning. It is good at the Anglophone news layer.

This agent works the layer underneath. It finds the analytical frames, historical parallels, and non-Western perspectives that daily briefings miss. It operates on long time horizons, across multiple languages, with minimal supervision. It is a research collaborator, not a task executor.

**This is a proof of concept.** It may fail. The experiment — including the failures — is being documented and will be published here as a case study in agentic AI research workflow.

---

## Project Narrative

```
Phase 1 (current):     Curator — intelligent briefing with feedback loop
                       Ranked daily, like/dislike/save signals, Telegram delivery
                       Four-tier model stack, learns from user behavior
                            ↓
Proof of Concept:      Research Intelligence Agent (this repo)
                       Takes Curator's signal base, adds autonomous research
                       Long time horizons, multi-language, minimal supervision
                       3-week pilot — documents success or failure
                            ↓
Phase 2 (if PoC works): Formalized agentic extension
                        Secure, extensible to other domains
                        "Works for Robert" → "Works as a pattern"
```

---

## Quick Start

```bash
# Check agent status
cat library/session-log.md | tail -5

# Search the library
grep -r "Kotkin" library/

# Browse the reading interface (after HTML reader is built)
open web/index.html

# Check budget
grep "Cumulative" library/session-log.md | tail -1
```

---

## Repository Structure

```
research-intelligence/
├── README.md                        # This file
├── CHANGELOG.md                     # Every meaningful change
├── BUGS.md                          # Bug log — open and closed
├── WAY_OF_WORKING.md                # Collaboration protocol for all tools
├── docs/
│   ├── direction/
│   │   ├── openclaw_direction_v2.md       # Primary agent direction document
│   │   ├── openclaw_approach.md           # OpenClaw's planned approach (PoC phase)
│   │   ├── origin_conversation.md         # How this started — historical artifact
│   │   └── curator_ui_fix_plan.md         # Separate: Curator UI fixes (future session)
│   └── specs/
│       └── [feature]-spec-[date].md       # One per Claude Code build task
├── library/                         # Research library (agent writes here)
│   ├── README.md                    # Master index table
│   ├── session-log.md               # Session ledger + budget tracking
│   ├── reading-list.md              # Books flagged for Robert
│   ├── sources/
│   │   ├── validated/
│   │   └── candidates/
│   ├── topics/
│   │   ├── empire-landpower/        # Kotkin pilot thread
│   │   ├── monetary-systems/
│   │   ├── eurasian-order/
│   │   └── latin-america/
│   ├── translations/
│   │   └── [language]/
│   └── essays/
├── web/                             # HTML reading interface (built by Claude Code)
│   ├── generate.js                  # Static site generator
│   └── index.html                   # Generated — do not edit manually
└── curator-candidates/              # Validated sources ready to propose to Curator
```

---

## The Tools

| Tool | Role |
|------|------|
| Claude.ai | Architecture, direction documents, design review. No code execution. |
| OpenClaw | Research agent, orchestrator, triggers Claude Code, maintains logs |
| Claude Code | Implementation only, per spec, waits for review before writing files |

**Robert approves all specs before build. Robert merges all PRs. No exceptions.**

See `WAY_OF_WORKING.md` for the full protocol.

---

## Pilot Thread: Kotkin

**The question:** Stephen Kotkin argues Russia and China swapped geopolitical positions over 70 years — China from peripheral/revolutionary to core/status quo, Russia the reverse. Is this right? What does non-Anglophone scholarship say? Who said it first, or better?

This is the first open research thread. It tests the full workflow:
find → triage → translate → validate → synthesize → deliver

Expected output: one 3–5 page synthesis essay in `library/essays/`  
Expected cost: ~$1.35

---

## Budget

| Limit | Amount |
|-------|--------|
| Daily | $3.00 (warn at $2.50) |
| Weekly | $10.00 |
| Pilot total | $20.00 (warn at $18.00, hard stop at $20.00) |

---

## Success Criteria (3-Week Pilot)

1. Found something genuinely new — non-Anglophone source or frame that surprised Robert
2. Library is usable — grep works, README is coherent
3. Kotkin essay written — quality over length
4. On budget — under $30 total for 3 weeks
5. Interrupted appropriately — not too often, not too rarely

3 of 5 → continue and expand toward Phase 2.

---

## Origin

This project was designed in a single conversation on March 20, 2026, between Robert and Claude.ai. The full conversation is documented in `docs/direction/origin_conversation.md`. The design moved from UI tooling frustration → agentic task design → a full research intelligence agent spec in one session.

The collaboration model baked into the agent — it asks questions, Robert redirects, informal chat is a valid input — mirrors how the direction document itself was produced. That wasn't accidental.

---

*PoC phase — document everything, including failures*  
*Next review: April 10, 2026*
