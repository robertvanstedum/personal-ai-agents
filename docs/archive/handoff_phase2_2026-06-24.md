# Handoff — Phase 2: Multi-User + Portuguese Domain
*Created: 2026-06-24 — Claude.ai*
*For: Claude Code, OpenClaw, Grok — read this before starting any Phase 2 work*
*Location: docs/design/handoff_phase2_2026-06-24.md*

---

## What Phase 2 is

Phase 1 was building mini-moi as a personal single-user platform and
migrating it to AWS. That's done. minimoi.ai is live on EC2.

Phase 2 has two goals running in parallel:

**Goal 1 — Operational maturity**
CI/CD pipeline, regression tests, Grafana dashboards. The system
becomes professionally operated, not just running. This is the
gate for Goal 2.

**Goal 2 — Portuguese domain + multi-user**
A second language domain (Brazilian Portuguese) with Robert's three
daughters as the first non-Robert users. This validates the platform
architecture, tests operations under real load, and proves the
system is extensible beyond one person and one language.

These two goals are sequenced, not parallel. Goal 1 must be
substantially complete before Goal 2 build starts.

---

## Two tracks — keep them separate

**Daily operational track (ongoing, don't stop)**
The existing system keeps running. Curator briefings, German sessions,
Guild queue, CoS health checks. Bugs get fixed, small improvements
ship. This is business as usual. Use the normal spec → build → deploy
flow for anything in this track.

**Phase 2 track (new, sequenced)**
The documents below. Don't mix Phase 2 specs with daily operational
work in the build queue. Phase 2 has its own sequence and its own
gates. Register Phase 2 specs separately and clearly.

---

## Phase 2 documents (all six, in reading order)

### 1. Design doc — full thinking
`docs/design/portuguese_domain_2026-06-24.md`
Read this first. Three user profiles, all design decisions,
open questions. The foundation everything else builds on.

### 2. Build plan — sequence and gates
`docs/design/portuguese_domain_build_plan_2026-06-24.md`
The master sequence. Nine specs, what each covers, what gates
each one. Block E (CI/CD) is the gate for all of Phase 2.
Read this to understand the order before touching any spec.

### 3. Block E — CI/CD + regression tests (START HERE)
`docs/specs/spec_cicd_pipeline_2026-06-24.md`
**This is the first thing to build.** Regression suite covering
all existing domains. Deploy pipeline. Automatic defect filing
on test failure. The Definition of Done includes an explicit gate:
"All 12 tests green ← Portuguese build cannot start until this."

### 4. Spec 1 — Auth + multi-user accounts
`docs/specs/spec_1_auth_multiuser_2026-06-24.md`
Full account system. Request → approve → create → login → reset.
AWS SES email. Telegram approval flow. requires_domain() decorator.
**Do not start until Block E Definition of Done is complete.**

### 5. Spec 2 — Portuguese domain shell
`docs/specs/spec_2_portuguese_domain_shell_2026-06-24.md`
Five tabs, Rio design system, natural language chat on every page,
new Docker container. **Do not start until Spec 1 is complete.**
Robert must confirm color palette before CSS is applied.

### 6. Specs 3-9 — TOC only (write as previous spec stabilizes)
Covered in the build plan. Not written yet. Each gets written
when the previous spec is stable and in use.

---

## The gate that matters most

```
Block E Definition of Done — all items checked
    ↓
12 regression tests green on a real pipeline run
    ↓
THEN AND ONLY THEN: start Spec 1
```

If Block E is not done, Spec 1 does not start. No exceptions.
The auth system touches the portal login flow. Without regression
tests running, a broken login could go undetected on production.

---

## Design system rule (important)

Portuguese uses CSS variables, not hardcoded values:

```css
:root {
  --domain-nav-bg: #1A3A2A;
  --domain-accent: #E8A020;
}
```

German will be backported later by changing variable values only.
Do not hardcode colors anywhere in the Portuguese domain.
This is a hard rule — it makes the German backport a one-commit
variable swap.

---

## Robert actions needed before build

These block the build — not Claude Code's job, Robert's job:

| Action | Blocks | Status |
|--------|--------|--------|
| Add 5 GitHub Secrets | Block E pipeline run | Pending |
| Verify minimoi.ai in AWS SES | Spec 1 email delivery | Pending |
| Confirm wife as admin (yes/no) | Spec 1 account creation | Pending |
| Confirm daughters' email addresses | Spec 1 deploy | Pending |
| Confirm Portuguese color palette | Spec 2 CSS | Pending — design session |

---

## Build queue registration

Register these in design_log with Phase 2 label or tag:

| # | Title | Status | Gates on |
|---|-------|--------|---------|
| 102 | CI/CD Pipeline + Regression Tests | spec_ready | Nothing |
| 103 | Auth + Multi-User Account System | spec_ready | #102 done |
| 104 | Portuguese Domain Shell | spec_ready | #103 done |
| 105 | Initial Personas (3) | design | Content session |
| 106 | User Persona Creation | design | #105 in use |
| 107 | Leitura Reading Lists | design | Content decisions |
| 108 | Feedback Analysis + Admin Dashboard | design | #104 live |
| 109 | Grafana Dashboards | spec_ready | Before daughters in |
| 110 | Escrita + Palavras | design | #105 in use |
| 111 | Conversas Full Voice Flow | design | 2 weeks usage |
| 112 | German Design Backport | design | Portuguese stable |

Note: #109 (Grafana) has an existing spec:
`docs/specs/spec_monitoring_prometheus_grafana_2026-06-22.md`
Move it up — build before daughters are invited in.

---

## What OpenClaw should track

OpenClaw manages local files and helps with testing automation.
For Phase 2 specifically:

- Track Block E progress — are tests running? Which are green?
- When Spec 1 ships, confirm auth.* schema created in Postgres
- When Spec 2 ships, confirm portuguese.* schema created
- As daughters create accounts, confirm domain_access table correct
- Monitor EC2 health during Portuguese launch — first real concurrent load

---

## What Grok should review

Before Claude Code builds anything in Phase 2, Grok reviews:
- Design doc (portuguese_domain_2026-06-24.md) — architecture
- Spec 1 (auth) — security review of password handling, token expiry
- Spec 2 (domain shell) — UX and design system approach

Flag anything that would create problems under real multi-user load.

---

## The next two weeks

**Week 1:**
- Block E: CI/CD pipeline + 12 regression tests
- Robert: GitHub Secrets, SES verification, palette session with wife
- Grok: design doc + Spec 1 review

**Week 2 (if Block E is done):**
- Spec 1: Auth system
- Content session: Robert + wife design personas
- Spec 2 starts if Spec 1 is stable

**Not this phase (don't let it creep in):**
- Career Two-Page Redesign — deferred
- Gespräche voice improvements — Block D, separate gate
- Prometheus/Grafana — pulled into Phase 2 as #109, but after Spec 3
- Operations roadmap Phase 2 (approval workflows) — after Portuguese stable

---

*Phase 2 Handoff · 2026-06-24 · Claude.ai*
*Six documents total — all in docs/design/ and docs/specs/*
*Single source of truth for Phase 2 sequence and gates*
