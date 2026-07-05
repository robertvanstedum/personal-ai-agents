# Design: OpenClaw as CoS — Phase 1
**File:** `docs/design/design_cos_phase1_2026-07-05.md`
**Status:** Design — not yet build-ready
**Date:** 2026-07-05
**Author:** Claude.ai design session
**References:** ROADMAP.md (Phase 1), CoS_Test_Instance_Setup.md (OpenClaw design doc)

---

## Intent

The Chief of Staff role in mini-moi exists in name but has no dedicated tooling today. I can't talk to CoS like a real partner. OpenClaw gives conversational continuity and memory — but it's currently personal, not platform-scoped. Phase 1 closes that gap: a dedicated mini-moi OpenClaw instance that makes the CoS role functional for the first time.

This is not about building the full memory/intelligence layer (Phase 2). It is about getting a safe, isolated, containerized OpenClaw instance running with tight initial use cases that prove the pattern before expanding scope.

---

## The Two Instances

Personal OpenClaw stays completely separate. This document is only about the mini-moi business instance.

| | Personal OpenClaw | mini-moi OpenClaw (CoS) |
|---|---|---|
| Purpose | Private, home use | Platform coordination, CoS role |
| Scope | Unrestricted | Bounded to mini-moi domains |
| Memory | Personal | Platform — writes to shared stores |
| Telegram | Existing personal bot | mini-moi bot (TBD — see open questions) |
| Data | `~/.openclaw/` | `~/.openclaw-cos/` or container volume |

---

## Why Containerized from Day One

Running the CoS OpenClaw instance in Docker means:
- Dev → EC2 → Mac Mini is the same move every other mini-moi service makes
- No reinstall, no reconfiguration when moving between hosts
- Fits the existing `docker-compose.prod.yml` pattern
- Connectivity setup (Telegram bot token, SSM credentials) is the only variable

The Mac dev test instance is not a throwaway — it becomes the container image that promotes to EC2.

---

## Phase 1 Use Cases (tight scope)

These are the only things CoS does in Phase 1. Scope expands in Phase 2 based on demonstrated reliability.

**1. Mobile note-taking**
Robert says something via Telegram → OpenClaw stores it, timestamps it, tags it with domain if obvious, writes to the actions store. No processing, no summarizing — just capture and confirm.

**2. Bug/defect filing**
Robert identifies a bug on mobile → tells CoS via Telegram → OpenClaw creates a GitHub issue with the right label, confirms to Robert. Robert does not need to open GitHub. This is the single biggest friction point on mobile today.

**3. Document retrieval**
Robert asks for a specific spec, design doc, or handoff → CoS retrieves it from known locations and returns it. Wide read access across `docs/`, `_working/`, and the build queue.

**4. Decision writing**
As design sessions produce decisions, OpenClaw writes them to the shared decisions store. This replaces the decision log that isn't being used because it's manual. Guild surfaces decisions in real time.

**What is explicitly out of Phase 1:**
- Autonomous actions without Robert approval
- Writing to production systems (read-only on prod, read-write on dev only)
- Cross-agent orchestration
- Full memory layer (Phase 2)
- Any action not in the list above

---

## Access Model (Phase 1)

**Read access (wide):**
- All `docs/` directories
- `build_queue.json`
- GitHub issues (read)
- `./openclaw/` md files

**Write access (tight):**
- GitHub issues (create only, no edit/close)
- Decisions store (append only)
- Actions store (append only)
- Own memory (`~/.openclaw-cos/`)

**No access:**
- Production docker-compose or EC2 configuration
- SSM parameters
- Any domain data files
- Personal OpenClaw instance

---

## Memory Writing Obligation (Phase 1)

Phase 1 introduces the two external memory files that Phase 2 builds on:

**Decisions file** (`openclaw/decisions.md` — shared, visible to all actors)
Written by OpenClaw when a decision is made. Format:
```
## 2026-07-05 — Spec naming convention established
**Decision:** All spec files follow numbered convention: spec_<number>_<name>_<date>.md
**Why:** Build queue number in filename makes it immediately clear what spec maps to what queue item.
**Actors:** Robert (decision), Claude.ai (proposed)
**Domain:** cross-domain
```

**Actions file** (`openclaw/actions.md` — shared, visible to all actors)
Written by OpenClaw when a meaningful action is taken. Format:
```
2026-07-05T14:23:00Z | note | cross-domain | "Meeting with SEI Adam Hartstein next week — prep needed"
2026-07-05T14:31:00Z | github_issue | guild | #121 created — "pipeline.items unavailable on Guild dashboard"
```

These two files are the foundation of the memory layer. They are not OpenClaw's internal memory — they are the platform's shared record.

---

## Containerization Design

### Directory structure
```
docker/
  Dockerfile.openclaw-cos    — OpenClaw CoS container image
openclaw/
  decisions.md               — shared decisions store (volume-mounted)
  actions.md                 — shared actions store (volume-mounted)
  .openclaw-cos/             — OpenClaw internal memory (volume-mounted, private)
config/
  openclaw/
    cos_config.yaml          — CoS persona, scope, allowed actions
```

### docker-compose addition
```yaml
openclaw-cos:
  build:
    context: .
    dockerfile: docker/Dockerfile.openclaw-cos
  ports:
    - "18889:18889"
  volumes:
    - /opt/minimoi/openclaw:/app/openclaw
    - /opt/minimoi/.openclaw-cos:/root/.openclaw
  env_file: /opt/minimoi/.env
  restart: unless-stopped
```

### Promotion path
- Phase 1: Mac dev (manual start or LaunchAgent)
- Phase 2: EC2 (same docker-compose pattern)
- Future: Mac Mini (same image, new host, connectivity setup only)

---

## CoS Persona (Phase 1)

The mini-moi OpenClaw instance has a defined scope and persona from day one:

- **Role:** Chief of Staff for the mini-moi platform
- **Tone:** Direct, organized, concise — not a chatbot
- **Scope:** mini-moi domains only. Does not discuss personal matters.
- **Default response to out-of-scope requests:** "That's outside my scope for this instance. Use your personal OpenClaw for that."
- **Proactive behaviors:** None in Phase 1. Responds to requests only.

---

## Open Questions (decide before build)

1. **Telegram bot** — Command prefix on existing `@minimoi_agent_bot` (e.g. `!cos`) or dedicated CoS bot? Lean toward command prefix during Phase 1 — simpler, fewer moving parts. Dedicated bot when scope expands.

2. **Secrets** — Separate SSM prefix (`/minimoi/cos/`) for CoS-specific credentials, or shared `.env` with naming convention (`COS_TELEGRAM_TOKEN` etc.)? Lean toward separate SSM prefix — cleaner separation.

3. **Startup on Mac dev** — Manual start during early testing or separate LaunchAgent? Manual first, LaunchAgent once the pattern is proven stable.

4. **`./openclaw/` directory** — Does this live in the repo root (gitignored sensitive content) or as a separate mounted path? Lean toward volume-mounted outside repo, same pattern as `guests.json`.

---

## Next Steps (in order)

1. Answer the four open questions above
2. Grok review pass on this design doc
3. Claude Code writes the containerization spec (Dockerfile + compose addition)
4. Robert approves and Claude Code builds on dev
5. Robert tests Phase 1 use cases via Telegram for 2+ weeks
6. Phase 2 design session begins only after Phase 1 is stable

---

## Connection to ROADMAP.md

This document implements Phase 1 of the solution roadmap. The memory writing obligation introduced here (decisions.md, actions.md) is the foundation Phase 2 (memory and intelligence layer) builds on. Design for Phase 2 is in `docs/design/design_memory_intelligence_layer_v3_2026-07-05.md`.

---

*Design document · mini-moi · 2026-07-05 · Not build-ready — open questions pending*
*Grok review before build*
