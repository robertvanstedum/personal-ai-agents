# Design — Portuguese Language Domain + Multi-User Platform
*Created: 2026-06-24 — Claude.ai*
*Status: Active design — under review before specs*
*Location: docs/design/portuguese_domain_2026-06-24.md*

---

## What this is

Adding Portuguese as the second language domain to mini-moi, with
three of Robert's daughters as the first non-Robert users. This is
not just a language addition — it's the first time the platform has
real users with real accounts, real feedback, and real unpredictable
load. The Portuguese domain is the vehicle. Multi-user production
readiness is the actual milestone.

Robert and his wife (Brazilian, years in Brazil) are the content
designers and quality judges. The daughters are the test users and
the feedback source.

---

## The three users

| User | Age | Level | Notes |
|------|-----|-------|-------|
| Daughter 1 | 14 | Beginner — knows a bit, not confident | Wants to learn, early stage |
| Daughter 2 | 20 | Intermediate — understands more, weak on writing/speaking | Comprehension ahead of production |
| Daughter 3 | 28 | Advanced comprehension — lived in Brazil, fluent receptively | Needs foundational grammar, written correctness |

These three users represent a real range. The platform needs to work
for all three without requiring admin intervention for every session.

---

## Core design decisions

### 1. Immersion-first, Portuguese UI

All tab names, navigation, and interface elements are in Portuguese.
This is an immersion program, not a classroom. The user is in Rio de
Janeiro, not studying Portuguese from the outside.

Tab equivalents:
- Leitura (reading — Lesen)
- Conversas (conversations — Gespräche)
- Escrita (writing — Schreiben)
- Palavras (vocabulary — Wörter)
- Arquivo (archive — Archiv)

The Rio/Brazil aesthetic replaces Vienna/Austria throughout — imagery,
persona settings, reading sources, cultural references.

### 2. Natural language as the help system

No help buttons. No tooltips. No onboarding modal.

Every page has a floating chat input. Users type anything:
- "O que é esta página?" → explanation of the page
- "Como crio uma persona?" → instructions for creating a persona
- "O áudio não funciona no meu celular" → bug report, extracted automatically
- "Não entendo como começar" → help request + implicit feedback

The backend AI classifies each message: help request, feedback,
bug report, or general conversation. Everything is logged. Robert
gets a daily summary via Telegram: what people asked, what confused
them, what broke. No forms, no feedback button — just talk.

This is the right pattern for 2026. Dedicated help UIs are legacy.
The natural language interface IS the help system.

### 3. Account system — request → approve → create

No Google OAuth dependency. Full account system:

```
User lands on preview
    ↓
"Request Access" for Portuguese domain
    ↓
Robert gets Telegram notification (system_bot)
    ↓
Robert approves (reply /approve or via Guild UI)
    ↓
User gets email with account creation link
    ↓
User sets username + password
    ↓
Forgot password / reset flow (standard email link)
```

Robert controls who gets in before they even create an account.
The approval gate is important — this isn't a public signup.

Admin role: Robert (and wife if desired) — can see all users,
all sessions, all feedback summaries, all personas.

User role: daughters — can access their domain, create personas
within limits, see their own history.

### 4. Persona system — personal and bounded

Robert creates 3 initial personas covering the full range:
- Beginner-friendly persona — patient, simple vocabulary, Rio everyday life
- Intermediate persona — conversational, confidence-building, social situations  
- Advanced persona — grammar-focused, written correctness, professional contexts

Users can create 1-3 additional personas with guardrails:
- Required: name, context (where do you know this person, what do you talk about)
- Required: level setting (iniciante / intermediário / avançado)
- Optional: personality notes

Examples of what this enables naturally:
- "Ana — minha companheira de vôlei, conversamos sobre treinos e fin de semana"
- "Carlos — meu colega de trabalho, reuniões e projetos"

Robert can see all user-created personas as admin. No pre-approval
needed — trust the users, review after the fact, remove anything
that doesn't make sense.

Limit: 3 user-created personas per user to start. Enough to make
it personal, not enough to become a distraction.

### 5. Preview update

Current preview: Curator + German.
After Portuguese launch: Curator + German + Portuguese.

German and Portuguese both show "Request Access — separate access
required for each domain." Clear that these are distinct domains
with separate approval, not one login for everything.

---

## What stays the same from Mein Deutsch

- KI-Sitzung architecture (voice sessions with AI personas)
- Analysis pipeline (same review engine, Portuguese instead of German)
- Lesen article ingestion (new sources: Globo, Folha, Brazilian Portuguese feeds)
- Schreiben writing drills
- Palavras phrasebook
- Arquivo session history
- Anki-style drill generation from session errors

The infrastructure is proven. The content changes. The architecture
does not.

---

## What's new vs Mein Deutsch

| Feature | Mein Deutsch | Portuguese domain |
|---------|-------------|-------------------|
| Auth | Single user (Robert) | Multi-user accounts |
| Persona creation | Admin only | Users can create (1-3, with guardrails) |
| Help system | None (Robert knows the system) | Natural language chat on every page |
| Feedback | None | Extracted from natural language, logged |
| UI language | German (with some English) | Portuguese (immersion-first) |
| Cultural context | Vienna, Austria | Rio de Janeiro, Brazil |
| User base | Expert user | Range from beginner to advanced |

---

## Operations implications

Three users hitting EC2 at random times from phones is the first
real production stress test. This validates everything built in
Block A:
- EC2 t3.small capacity under real concurrent load
- CoS health checks catching real incidents
- CI/CD pipeline handling deploys without taking down active sessions
- Grafana showing real usage patterns (this is why Grafana moves up)
- Telegram alerts firing for real problems not just test scenarios

The feedback loop from daughters → natural language → AI extraction
→ Robert Telegram summary is also the first real test of the CoS
coordination layer under real-world conditions.

---

## Open design questions (resolve before Spec 1)

1. **Email provider for account creation/reset:** Need a transactional
   email service. Options: SendGrid (free tier 100/day), AWS SES
   (very cheap, already on AWS), Resend (modern, simple API).
   Recommend AWS SES for consistency with existing infrastructure.

2. **Username or email as login?** Email as username is simpler
   (one field, easy to remember, reset flow is obvious). Username
   adds complexity with no clear benefit at this scale. Recommend
   email as login.

3. **Domain isolation in database:** Does each user's Portuguese
   session data live in the same Postgres schema as Robert's German
   data, or a separate schema? Recommend separate schema:
   `portuguese.*` parallel to `guild.*`. Cleaner, easier to manage,
   safer for user data separation.

4. **Natural language chat — which model?** The in-page chat needs
   to be fast and cheap since it's user-facing and potentially high
   volume. gpt-4o-mini or Claude Haiku. Recommend configurable per
   domain with a sensible default.

5. **Feedback summary cadence:** Daily Telegram summary to Robert,
   or on-demand only? Recommend daily at 8am — same as curator
   briefing pattern. Robert reviews with morning coffee.

6. **Wife as admin?** If yes, she gets an admin account and can
   review feedback, personas, and session quality directly. Recommend
   yes — she's the content expert for Brazilian Portuguese.

---

## Build sequence overview

Full table of contents — Specs 1-2 written in full below,
Specs 3-9 as TOC only.

| Spec | Title | Gates on | Priority |
|------|-------|---------|---------|
| 1 | Auth + account system | Nothing — start here | High |
| 2 | Portuguese domain shell | Spec 1 complete | High |
| 3 | Initial personas (3) | Content from Robert + wife | High |
| 4 | Natural language help + feedback | Spec 2 stable | Medium |
| 5 | User persona creation | Spec 3 in use | Medium |
| 6 | Preview update | Spec 1 (access request flow) | Medium |
| 7 | Grafana dashboards | Move up — before launch | High |
| 8 | Admin dashboard (feedback review) | Spec 4 live | Lower |
| 9 | Reading list (Leitura) sources | Content decisions | Lower |

---

## Content decisions needed from Robert + wife

Before Spec 3 (personas) can be written:
- Names and backgrounds for 3 initial personas
- Rio/Brazil settings and situations for each
- Level calibration per persona
- Initial reading sources for Leitura (Brazilian Portuguese news,
  culture, everyday topics)
- Any specific vocabulary or grammar areas to emphasize per level

This is a design session, not a build task. Robert and wife decide.
Claude.ai facilitates. Claude Code builds after.

---

*Design doc · 2026-06-24 · Claude.ai*
*Review with Grok before writing specs*
*Spec 1 and Spec 2 follow in separate files*
*Location: docs/design/portuguese_domain_2026-06-24.md*
