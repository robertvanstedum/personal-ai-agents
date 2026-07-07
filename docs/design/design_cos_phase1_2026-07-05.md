# Design: Chief of Staff — Phase 1
**File:** `docs/design/design_cos_phase1_2026-07-05.md`
**Status:** Design — not yet build-ready
**Date:** 2026-07-05 (v3 — Grok refinements applied)
**Author:** Claude.ai design session + Grok review (two passes)
**References:** ROADMAP.md (Phase 1), CoS_Test_Instance_Setup.md

---

## Intent

The Chief of Staff is Robert's personal Cabinet leader and strategic thinking partner. It is not a note-taker, not a task tracker, not a scrum master. It is the central nervous system of the mini-moi operation — watching across all domains, maintaining memory of what has happened and why, questioning what needs questioning, and handing off to the right actor at the right time.

Phase 1 gets this capability live for the first time. The current Guild CoS agent is a placeholder. This replaces it with a real CoS — containerized, isolated from personal OpenClaw, with cross-domain read access, autonomous (within limits) decisions/actions writing, and proactive watching behavior.

**Critical design requirement:** Agent swappability is a Phase 1 exit criterion. Before Phase 1 is complete, a parallel A/B test with a second agent must prove the platform works identically regardless of which agent implements the CoS interface. Memory and decisions belong to the platform, not to OpenClaw.

---

## Role Definition

**Chief of Staff** — Cabinet leader, executive board member, personal strategic thinking partner.

| Dimension | Description |
|---|---|
| Level | Strategic / Executive |
| Scope | All domains + personal conversations + Robert's overall thinking |
| Nature | High-level advisor, cross-domain coordinator, proactive watcher |
| Memory | Writes decisions and actions naturally as part of its own flow — not prompted |
| Autonomy | Watches, questions, flags, and proposes handoffs within defined Phase 1 limits |
| Access | Read access to all domains. Full access to CoS domain. |
| Visibility | No other agent sees the CoS domain. Master Craftsman can notify CoS but cannot read it. |

**What CoS is not:**
- Not a TPM, scrum master, or ops director (that's the Master Craftsman's territory)
- Not a chatbot that waits to be asked
- Not a note-taker that only acts when told
- Not deeply embedded in any one tool — agent-agnostic by design

---

## The Two Roles — CoS vs Master Craftsman

| Role | Level | Focus | Domain | Access to other |
|---|---|---|---|---|
| **Chief of Staff** | Strategic | Thinking partner, cross-domain watching, decisions/memory | CoS Domain (private) | Reads all domains |
| **Master Craftsman** | Tactical | Build quality, standards, domain template compliance | Build Domain | Can notify CoS only — cannot read CoS domain |

CoS is senior. Master Craftsman reports up. One-way visibility.

---

## Access Model

| Actor | CoS Domain | Build Domain | All other domains |
|---|---|---|---|
| CoS | Full | Read | Read + can propose handoff |
| Master Craftsman | Notify only (no read) | Full | Read (build-relevant only) |
| Other agents | None | Via interface only | Own domain only |
| Robert | Full | Full | Full |

---

## Memory — Natural Flow, Not Prompted

CoS writes to the shared memory stores as part of its natural operation. Robert does not need to tell it to log something.

**Decisions store** (`openclaw/decisions.md` — platform-owned, all actors can read):
Written when a strategic, cross-domain, or architectural decision is made. Not every tactical action.
```
## 2026-07-05 — CoS and Build domains to be split from Guild
**Decision:** Guild evolves into three distinct roles: CoS domain (private, strategic),
Build domain (shareable, execution), Master Craftsman (quality guardian in Build).
**Why:** Guild is doing too many jobs. Clean separation improves shareability and focus.
**Actors:** Robert (decision), Claude.ai + Grok (design)
**Domain:** cross-domain
```

**Actions store** (`openclaw/actions.md` — platform-owned, all actors can read):
Written when a meaningful action occurs. Terse, timestamped.
```
2026-07-05T22:14:00Z | cross-domain | cos | Flagged: leanings.json not volume-mounted — data loss risk
2026-07-05T22:31:00Z | guild | cos | Handoff proposed to Claude Code: register three idea-phase items
```

**All strategic decisions go to `decisions.md`** — no separate private decisions layer in Phase 1. Platform-owned, queryable. Revisit in Phase 2 only if genuinely sensitive content requires separation.

**CoS private memory** (`~/.openclaw-cos/` — CoS internal working memory, not shared):
OpenClaw's own context. Survives agent swap because the platform stores what matters externally.

---

## Autonomous Behaviors (Phase 1 — within limits)

**Permitted autonomously:**
- Write to decisions.md and actions.md
- Flag issues noticed across any domain (Telegram notification to Robert)
- Propose a handoff to any agent (write to actions.md + Telegram message to Robert — Robert approves)
- Retrieve any document from any domain on request
- Create GitHub issues for bugs or defects Robert identifies on mobile

**Requires Robert approval before acting:**
- Any write to domain data files
- Any deployment or infrastructure change
- Executing any proposed handoff
- Any action not in the permitted list above

**Not in Phase 1:**
- Executing code or running scripts autonomously
- Writing to production systems without approval
- Cross-agent orchestration beyond handoff proposals
- Full memory layer ingestion (Phase 2)

---

## Proactive Watching

CoS monitors across domains and surfaces what needs attention without being asked.

**Phase 1 observation sources (explicit):**
- `build_queue.json` — spec status, age, blocked items, items without progress
- GitHub issues — open/unresolved, items missing build queue entries
- `decisions.md` — gap detection (decision made in session but not logged)
- `actions.md` — pattern detection across actions
- Domain health endpoints — is everything up and responding?
- EC2 disk and backup status — trending issues before crisis

**Example watching behaviors:**
- Spec in `in_build` for 10+ days with no commit → flags to Robert
- Disk usage trending up → flags before crisis
- Decision made in design session not yet in decisions.md → writes it
- GitHub issue open with no build queue entry → proposes adding it
- German and Portuguese domains diverging from domain template → flags to Master Craftsman

**Watching cadence:** On-demand plus hourly scheduled scans. Not continuous — adds complexity without proportional value in Phase 1.

**Handoff mechanism:** CoS writes to `actions.md` (the record), then sends a Telegram message to Robert with the proposed handoff and rationale. Robert approves or redirects. CoS does not execute the handoff autonomously in Phase 1.

---

## Telegram Interface

**Bot:** Command prefix `!cos` on existing `@minimoi_agent_bot`. Simpler than a dedicated bot in Phase 1 — fewer moving parts, easier to manage. Dedicated CoS bot when scope expands in Phase 2.

**Example interactions:**
```
Robert: !cos what's blocked in the build queue?
Robert: !cos file a bug — German lesen scroll broken on mobile
Robert: !cos what did we decide about the backup system?
```

CoS also sends proactive Telegram messages to Robert when it flags something — without being asked.

---

## The Two OpenClaw Instances

| | Personal OpenClaw | mini-moi CoS (OpenClaw Phase 1) |
|---|---|---|
| Purpose | Private, home use | Platform CoS role |
| Scope | Unrestricted personal | Bounded to mini-moi domains |
| Memory | Personal | Platform — writes to shared stores |
| Telegram | Existing personal bot | `!cos` prefix on `@minimoi_agent_bot` |
| Data | `~/.openclaw/` | `~/.openclaw-cos/` or container volume |
| Swappable | N/A | Yes — by design from day one |

---

## Agent Interface Contract

The platform depends on the CoS interface, not on OpenClaw specifically.

**Any CoS agent must:**
- Accept `!cos` commands via Telegram
- Write decisions to `openclaw/decisions.md` naturally as part of operation
- Write actions to `openclaw/actions.md` naturally as part of operation
- Maintain read access to all domain data sources
- Run hourly watching scans across Phase 1 observation sources
- Propose handoffs via actions.md + Telegram — never execute autonomously
- Scope all activity to mini-moi domains
- Reject out-of-scope requests explicitly

**The platform must NOT:**
- Call OpenClaw APIs directly from Guild or domain servers
- Hard-code OpenClaw-specific behavior anywhere in mini-moi code
- Store memory in a format only OpenClaw can read

Interface documented in: `config/openclaw/cos_interface.md` (to be written before build)

---

## Containerization

```yaml
openclaw-cos:
  build:
    context: .
    dockerfile: docker/Dockerfile.openclaw-cos
  ports:
    - "18889:18889"
  volumes:
    - /opt/minimoi/openclaw:/app/openclaw        # shared decisions + actions
    - /opt/minimoi/.openclaw-cos:/root/.openclaw  # CoS private memory
  env_file: /opt/minimoi/.env
  restart: unless-stopped
```

Promotion path: Mac dev → EC2 → Mac Mini. Same image, different host, connectivity setup only.
Agent swap: replace `Dockerfile.openclaw-cos`, keep everything else.

---

## Open Questions (decide before build)

1. **Secrets** — Separate SSM prefix `/minimoi/cos/` or shared `.env` with naming convention?
2. **Startup on Mac dev** — Manual start during early testing or LaunchAgent?
3. **`./openclaw/` directory** — In repo (gitignored) or volume-mounted outside repo?
4. **A/B test agent** — Cowork is the current candidate. Confirm before Phase 1 exit.
5. **`cos_interface.md`** — Written before build starts. Who writes first draft — Claude.ai or OpenClaw?

*Previously open questions now resolved:*
- Telegram bot: `!cos` prefix on `@minimoi_agent_bot` ✅
- Watching cadence: on-demand + hourly scheduled ✅
- Private decisions layer: no — all strategic decisions to decisions.md ✅

---

## Phase 1 Exit Criteria

All must be true before Phase 2 begins:

- [ ] CoS running reliably for 2+ weeks on Mac dev
- [ ] Robert using CoS daily via Telegram — natural conversation, not just commands
- [ ] decisions.md writing automatically — no prompting required
- [ ] actions.md writing automatically across all observed events
- [ ] At least 3 proactive flags generated without Robert asking
- [ ] At least 1 cross-domain handoff proposed by CoS
- [ ] Hourly watching scans confirmed running
- [ ] Guild surfaces decisions and actions in real time
- [ ] A/B test with second agent completed — swappability confirmed
- [ ] `config/openclaw/cos_interface.md` written and accurate
- [ ] No incidents involving personal OpenClaw instance
- [ ] Robert can say: "CoS knows what's going on without me telling it"

---

## Connection to ROADMAP.md and Future Phases

**Phase 2 (memory layer):** CoS's natural decisions/actions writing in Phase 1 becomes the foundation. Raw data in decisions.md, actions.md, and `~/.openclaw-cos/` feeds the intelligence layer.

**Guild split (Ideas #2 + #3):** CoS moves to its own domain. Master Craftsman takes the tactical build quality role in Build domain. CoS retains cross-domain read access and strategic oversight.

**Phase 3+ (bounded autonomy):** Watching behaviors expand. Proactive flags become proposals. Proposals become autonomous actions within policy. Mirrors the ROADMAP.md phase map.

---

*Design document · mini-moi · 2026-07-05 · v3*
*Full rewrite from note-taker framing — Cabinet leader definition throughout*
*Two Grok review passes completed — open questions resolved*
*Next: answer 5 remaining open questions → write cos_interface.md → build spec*
