# Build Plan — Portuguese Domain + Multi-User Platform
*Created: 2026-06-24 — Claude.ai*
*Status: Active design — under review before specs*
*Location: docs/design/portuguese_domain_build_plan_2026-06-24.md*
*Design doc: docs/design/portuguese_domain_2026-06-24.md*

---

## Why testing comes first

Spec 1 (Auth) touches the portal login flow, session management,
and route protection — the most central part of the system. If it
breaks, nothing works: not German, not Curator, not Guild.

Building Spec 1 without automated regression tests means a broken
login wouldn't be caught until Robert or a daughter can't get in.
That's not acceptable on a live production system.

CI/CD with a regression suite is the gate for everything in this
plan. Not because of process — because Spec 1 will touch the portal
in ways that need automated verification on every deploy.

---

## Design system approach — Portuguese first, German later

The new design system is built in Portuguese, proven with real users
for 2-3 weeks, then backported to German. Don't touch German until
Portuguese is stable.

All design tokens use CSS variables from day one:

```css
/* Portuguese domain */
:root {
  --domain-nav-bg: #1A3A2A;
  --domain-bg: #F7F2E8;
  --domain-accent: #E8A020;
  --domain-font: 'Georgia', serif;
}

/* German domain (backport — change variables only, not components) */
:root {
  --domain-nav-bg: #2A1F14;
  --domain-bg: #F5F0E8;
  --domain-accent: #C68A5E;
  --domain-font: 'Georgia', serif;
}
```

One component codebase. Two variable sets. Backport is a one-commit
variable swap, not a redesign.

---

## Three users

| User | Age | Level | Notes |
|------|-----|-------|-------|
| Daughter 1 | 14 | Beginner | Wants to learn, early stage |
| Daughter 2 | 20 | Intermediate | Comprehension ahead of production |
| Daughter 3 | 28 | Advanced comprehension | Needs grammar foundation |

Robert and wife (Brazilian, years in Brazil) are content designers
and quality judges. Daughters are test users and feedback source.

---

## Core design decisions

### Immersion-first Portuguese UI
All navigation in Portuguese. Rio de Janeiro aesthetic, not classroom.
Natural language chat is the help system — no tooltips, no modals.

### Account system
Request Access → Robert approves via Telegram → account creation
email (AWS SES) → email + password login → forgot/reset flow.
No Google account required.

### Natural language replaces all legacy UI patterns
No help buttons. No feedback forms. No onboarding modal.
Floating chat on every page. Backend classifies and logs everything.
Robert gets daily Telegram summary of what users asked and reported.

### Persona creation
Robert builds 3 initial personas (beginner/intermediate/advanced).
Users can create 1-3 custom personas with guardrails (name, context,
level required). Admin reviews after the fact.

### Design system
Portuguese-first, shared variables, German backport after 2-3 weeks
of real use proves the design works.

---

## Build sequence

### Gate — must complete before any Portuguese spec starts

**Block E — CI/CD Pipeline + Regression Tests**
Spec: docs/specs/spec_cicd_pipeline_2026-06-24.md

The regression suite runs on every push to main. If tests fail,
deploy is blocked. This is what makes it safe to touch the portal
auth system in Spec 1.

Minimum test coverage before Portuguese build starts:
- Portal: login, logout, protected route redirect, health check
- Curator: health check, loads authenticated
- German: health check, Gespräche loads, personas load
- Guild: queue loads, roadmap loads

These tests catch regressions in existing domains when Portuguese
auth changes are deployed.

**Gate is clear:** CI/CD pipeline running, all existing-domain
tests green, at least one full deploy via pipeline confirmed.

---

### Spec 1 — Auth + Multi-User Account System
**File:** spec_1_auth_multiuser_2026-06-24.md — WRITTEN, spec_ready
**Gates on:** Block E complete (CI/CD + regression tests running)

Full account system. auth.* schema. Flask-Login. bcrypt passwords.
AWS SES email. Telegram approval flow. requires_domain() decorator.
Robert keeps Google OAuth. Admin view in Guild UI.

New regression tests added in this commit:
- New user can request access
- Approved user can create account and log in
- User without domain access gets 403 on protected route
- Password reset flow completes successfully
- Robert's Google OAuth login still works

---

### Spec 2 — Portuguese Domain Shell
**File:** spec_2_portuguese_domain_shell_2026-06-24.md — WRITTEN, spec_ready
**Gates on:** Spec 1 complete and regression tests green

Five tabs (Leitura, Conversas, Escrita, Palavras, Arquivo).
Rio/Brazil design system with CSS variables.
Natural language chat on every page. portuguese.* schema.
New Docker container port 8768. Preview page updated.

**Robert action before build starts:** confirm palette in design
session with wife. Spec 2 has suggested colors — Robert approves
or adjusts before Claude Code applies them.

New regression tests added:
- Portuguese domain loads for authorized user
- Portuguese domain returns 403 for unauthorized user
- Natural language chat route accepts and logs messages
- All five tabs render without errors

---

### Content session — Robert + wife
**Not a spec — a working session**
**Gates on:** Spec 2 shell running and accessible

Before Spec 3 can be written, Robert and wife design:
- Names and backgrounds for 3 initial personas
- Rio settings and situations for each
- Level calibration (beginner/intermediate/advanced)
- Initial reading sources for Leitura

This is a design conversation, not a build task. 1-2 hours.
Output: persona brief document → Claude Code builds persona files.

---

### Spec 3 — Initial Personas (3)
**Status:** TOC only — written after content session
**Gates on:** Spec 2 stable, content session complete

Three personas covering beginner/intermediate/advanced.
Rio settings. Brazilian Portuguese, not European.
Built as .txt files in domains/portuguese/personas/
using German persona format as template.

---

### Daughters invited in
**Gates on:** Spec 3 complete, AWS SES verified, emails ready

Robert approves access requests for three daughters.
Account creation emails sent. First real users on the system.

---

### Spec 4 — User Persona Creation
**Status:** TOC only — written after daughters have used system
**Gates on:** Spec 3 in use, at least 1 week usage data

Users can create 1-3 custom personas. Required: name, context,
level. Optional: personality notes. Admin can view and remove.
Limit enforced per user account.

---

### Spec 5 — Leitura (Reading Lists)
**Status:** TOC only
**Gates on:** Spec 2 stable, reading sources decided

Brazilian Portuguese feeds — Globo, Folha, Agência Brasil,
culture and everyday life. Same RSS ingestion as Mein Deutsch.
Simplified view + full article. Level calibration per content.

---

### Spec 6 — Feedback Analysis + Admin Dashboard
**Status:** TOC only
**Gates on:** Spec 2 live, daughters using, chat_log accumulating

Daily Telegram summary: classified messages from portuguese.chat_log.
AI summarizes help requests, feedback, bugs per day.
Admin dashboard in Guild UI showing raw log and summaries.

---

### Spec 7 — Grafana Dashboards (moved up)
**Status:** TOC only — build before daughters invited in
**Gates on:** Prometheus running

Portuguese domain metrics before launch:
sessions per user, chat volume, analysis job duration, EC2 load.
This validates EC2 t3.small handles real concurrent load.
Operations milestone: real users, real patterns, real visibility.

---

### Spec 8 — Escrita + Palavras
**Status:** TOC only
**Gates on:** Spec 3 personas in use

Writing drills in Portuguese. Brazilian phrasebook.
Drill generation from session errors.
Level-appropriate content per user.

---

### Spec 9 — Conversas Full Voice Flow
**Status:** TOC only
**Gates on:** Spec 3 live, 2 weeks usage data

Full KI-Sitzung voice for Portuguese. MediaRecorder, Whisper
(Portuguese language hint), TTS (OpenAI voices), VAD loop.
Analysis pipeline for Portuguese grammar feedback.
The feature that makes it real.

---

### German design backport
**Status:** TOC only — after Portuguese stable
**Gates on:** 2-3 weeks Portuguese in use, design confirmed

Single commit: update German domain CSS variables to match
refined design system. Components unchanged — variable swap only.
Regression tests confirm German still works after backport.

---

## Full sequence summary

```
Block E — CI/CD + regression tests  ← gate for everything
    ↓
Spec 1 — Auth
    ↓
Spec 2 — Portuguese shell + design system
    ↓
Content session (Robert + wife)
    ↓
Spec 3 — Initial personas
    ↓
Spec 7 — Grafana (before launch)
    ↓
Daughters invited in
    ↓ (parallel tracks)
Spec 4 — User persona creation
Spec 5 — Leitura reading lists
Spec 6 — Feedback analysis
    ↓
Spec 8 — Escrita + Palavras
    ↓
Spec 9 — Full voice flow
    ↓
German design backport
```

---

## Open decisions (resolve before Spec 1 builds)

| Decision | Status | Action |
|----------|--------|--------|
| AWS SES domain verify | Pending | Robert: SES console → verify minimoi.ai |
| Wife as admin | Pending | Robert: yes or no |
| Daughters' email addresses | Pending | Robert provides before Spec 1 deploys |
| Portuguese palette | Pending | Design session with wife |
| AI chat model | Decided | gpt-4o-mini default, configurable |
| Login identifier | Decided | Email as username |
| Persona limit | Decided | 1-3 per user |
| Database isolation | Decided | portuguese.* schema, separate from german.* |

---

*Build Plan · Portuguese Domain · 2026-06-24 · Claude.ai*
*Design doc: docs/design/portuguese_domain_2026-06-24.md*
*Gate: Block E (CI/CD + regression tests) must complete first*
*Specs 1-2 ready after Grok review and Robert approval*
