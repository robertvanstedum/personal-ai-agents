# personal-ai-agents — Platform Architecture
**Last updated:** 2026-03-07  
**Location:** `_NewDomains/ARCHITECTURE.md`  
**Status:** Living document — update when principles change, not every session

---

## What This Is

A personal AI operating system built around a single reusable pattern:  
**input → analysis → learned profile → personalized output → feedback loop**

The same architecture runs across multiple life domains. The orchestrator (currently OpenClaw) is pluggable — every component works standalone or agent-driven.

This is not a collection of one-off tools. It is a platform.

---

## Core Pattern (Domain-Agnostic)

```
INPUT SOURCES          ANALYSIS              LEARNED PROFILE
─────────────          ────────              ───────────────
RSS / X bookmarks  →   AI scoring      →   content_preferences.json
Conversations      →   gap detection   →   learner_profile.json
Bank statements    →   categorization  →   spending_profile.json
        │                                          │
        └──────────────────────────────────────────┘
                         ↓
                  PERSONALIZED OUTPUT
                  (briefing / exercises / report)
                         ↓
                    USER FEEDBACK
                  (like / correct / flag)
                         ↓
                  PROFILE UPDATES
                  (loop closes here)
```

Every domain implements this pattern. The domain content differs. The architecture does not.

---

## Platform Structure

```
personal-ai-agents/
├── _NewDomains/                 ← working docs for new domains (not built yet)
│   ├── README.md                ← rules for agents — read first
│   ├── PROJECT_STATE.md         ← single orientation doc, all parties
│   ├── ARCHITECTURE.md          ← this file
│   ├── DOMAIN_SPEC_language_learning.md
│   └── DOMAIN_SPEC_finance.md
│
├── core/                        ← shared infrastructure (extract only when 2+ domains need it)
│   ├── feedback_loop/
│   ├── delivery/                ← Telegram, web UI (channel-agnostic)
│   └── orchestrator/            ← OpenClaw adapter + generic interface
│
├── domains/
│   ├── geopolitics/             ← PUBLIC, active, portfolio piece
│   ├── language_learning/       ← PRIVATE, not started yet
│   ├── finance/                 ← PRIVATE, not started yet
│   └── [future]/
│
├── README.md                    ← PUBLIC PORTFOLIO — do not modify without sign-off
├── CHANGELOG.md                 ← PROTECTED — append only
├── WHITEBOARD.md                ← IDEAS ONLY — do not build without sign-off
└── OPERATIONS.md                ← PROTECTED — edit only when instructed
```

---

## Domain Status

| Domain | Status | Visibility | Primary Output |
|--------|--------|------------|----------------|
| Geopolitics | Active — v0.9-beta | Public GitHub | Daily briefing + deep dives |
| Language Learning | Design phase | Private → Public at milestone | Session analysis + exercises |
| Finance | Design phase | Private indefinitely | Tax reports + household tools |
| Health | Future | TBD | TBD |
| RVSAssociates | Future commercial | Separate repo | Client-facing platform |

---

## Orchestrator Design Principle

OpenClaw is the current orchestrator. It will not always be.

Every domain component must:
- Run standalone via CLI
- Accept inputs and produce outputs without requiring OpenClaw
- Expose a clean interface that any orchestrator can call

OpenClaw adds: memory, multi-step planning, Telegram commands, cost management.

**The platform does not depend on OpenClaw. OpenClaw depends on the platform.**

---

## Storage Strategy

### Current: Flat Files
Structured for future migration. Do not optimize prematurely.

### Future: PostgreSQL
Schema designed for all domains from day one:

```sql
articles     (domain, id, content, score, timestamp)
user_profile (domain, key, value, updated_at)
feedback     (domain, item_id, signal, timestamp)
sessions     (domain, session_id, transcript, analysis)
```

Same tables, `domain` field separates content. Language learning and finance slot in without schema changes.

**Migrate only when flat files become a bottleneck.**

---

## Code Reuse Rules

**Extract to core/ only when:**
1. The same logic exists in 2+ domains, AND
2. The domains have actually diverged and re-converged

**Never extract preemptively.**

Starting a new domain with a copy of the pattern is correct.  
Abstracting before two domains are working is over-engineering.

---

## Delivery Layer Principles

- Telegram is a **channel**, not a data store
- Web UI is the primary read surface
- All feedback channels write to the same profile store
- Adding a new channel requires no changes to domain logic
- Two-bot architecture: `rvsopenbot` for callbacks, `minimoi_cmd_bot` for OpenClaw

---

## Privacy Rules

| Content | Repository | Notes |
|---------|------------|-------|
| Geopolitics | Public GitHub | Portfolio piece |
| Language learning | Private until milestone | Transcripts always gitignored |
| Finance | Private indefinitely | All statements/reports gitignored |
| API keys | Never in repo | Always in macOS keychain |
| Personal data | Never in repo | Always gitignored |

---

## Agent Communication Rules

**PROJECT_STATE.md** — single source of truth. Read first. Update at end of session.  
**WHITEBOARD.md** — ideas only. Nothing built without sign-off in PROJECT_STATE.md.  
**Claude Code** — precise implementation tasks only. Never open-ended design.  
**OpenClaw** — memory and multi-step execution. Never vague instructions.  
**Claude.ai** — design, planning, architecture. Flat rate, no token pressure.

---

## Future Domains (Ideas Only — Not Approved)

- **Health:** activity/food logs → personalized insights
- **RVSAssociates:** commercial platform, Rails, separate repo
- **French:** next language after German B1 — language_learning/ is language-agnostic
