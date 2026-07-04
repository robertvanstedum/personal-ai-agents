# Build Table of Contents — Portuguese Domain + Multi-User Platform
*Created: 2026-06-24 — Claude.ai*
*Specs 1-2 written in full. Specs 3-9 are TOC only.*
*Each spec written when the previous is stable.*

---

## Spec 1 — Auth + Multi-User Account System
**File:** spec_1_auth_multiuser_2026-06-24.md — WRITTEN
**Status:** spec_ready
**Gates on:** Nothing — start here

Request Access → Robert approves via Telegram → account creation
email (AWS SES) → user sets password → forgot/reset flow.
auth.* schema. requires_domain() decorator. Robert keeps Google OAuth.
Admin view in Guild UI.

---

## Spec 2 — Portuguese Domain Shell
**File:** spec_2_portuguese_domain_shell_2026-06-24.md — WRITTEN
**Status:** spec_ready (after Spec 1)
**Gates on:** Spec 1 complete

Five tabs (Leitura, Conversas, Escrita, Palavras, Arquivo).
Rio/Brazil design system — palette confirmed by Robert in design
session first. Natural language chat on every page (replaces all
help modals and feedback forms). portuguese.* schema. New Docker
container on port 8768. Preview page updated.

---

## Spec 3 — Initial Personas (3)
**Status:** Needs content session first
**Gates on:** Spec 2 shell stable

Content design session with Robert + wife before this spec is
written. Three personas covering beginner / intermediate / advanced.
Rio settings, Brazilian Portuguese, real situations (café, praia,
trabalho). Robert builds persona .txt files using existing German
persona format as template. Claude Code adds them to the domain.

**Robert action before this spec:** design session to name, describe,
and calibrate the three personas. 1-2 hours, can do over Telegram
or in person with wife.

---

## Spec 4 — User Persona Creation
**Status:** TOC only
**Gates on:** Spec 3 in use, daughters have logged in

Allow each user to create 1-3 custom personas. Required fields:
name, context (where do you know this person, what do you talk about),
level (iniciante/intermediário/avançado). Optional: personality notes.
Persona creation UI in Conversas tab. Admin can view and remove
any persona. Limit enforced per user.

---

## Spec 5 — Leitura (Reading Lists)
**Status:** TOC only
**Gates on:** Spec 2 stable, content decisions made

Brazilian Portuguese reading sources — Globo, Folha de São Paulo,
Agência Brasil, culture and everyday life feeds. Same RSS ingestion
pattern as Mein Deutsch Lesen. Simplified view + full article.
Reading difficulty calibration per level. Robert + wife select
initial sources.

---

## Spec 6 — Feedback Analysis + Admin Dashboard
**Status:** TOC only
**Gates on:** Spec 2 live, daughters using it

Daily Telegram summary to Robert: classified messages from
`portuguese.chat_log` — help requests, feedback, bug reports.
AI summarizes: "3 daughters used it today. 2 help requests about
Conversas. 1 bug: audio not working on iOS. 1 piece of feedback:
wants more beginner content." Admin dashboard in Guild UI showing
raw chat log and summaries.

---

## Spec 7 — Grafana Dashboards (moved up)
**Status:** TOC only — MOVE UP before Portuguese launch
**Gates on:** Prometheus running (Spec from ops monitoring track)

Dashboards showing real usage before daughters start using. Portuguese
domain metrics: sessions per user, chat volume, analysis job duration.
Validate EC2 t3.small handles concurrent load. This is the operations
validation milestone — real users, real patterns, real visibility.

---

## Spec 8 — Escrita + Palavras (Writing + Vocabulary)
**Status:** TOC only
**Gates on:** Spec 3 personas in use

Writing drills in Portuguese. Phrasebook with Brazilian Portuguese
phrases, not European Portuguese. Drill generation from session errors
(same pattern as German). Level-appropriate content per user.

---

## Spec 9 — Conversas Full Flow (Voice + Analysis)
**Status:** TOC only
**Gates on:** Spec 3 personas live, 2 weeks usage data

Full KI-Sitzung voice flow for Portuguese. Same architecture as
Mein Deutsch Gespräche — MediaRecorder, Whisper (Portuguese language
hint), TTS (OpenAI, female/male voices per persona), VAD loop.
Analysis pipeline adapted for Portuguese grammar feedback.
This is the feature that makes it real.

---

## Sequence summary

```
Spec 1 — Auth (build now)
    ↓
Spec 2 — Domain shell (build after Spec 1)
    ↓
Content session with Robert + wife (persona design)
    ↓
Spec 3 — Initial personas
    ↓
Daughters invited in
    ↓ (parallel)
Spec 4 — User persona creation
Spec 5 — Leitura reading lists
Spec 7 — Grafana (operations visibility)
    ↓
Spec 6 — Feedback analysis + admin dashboard
    ↓
Spec 8 — Escrita + Palavras
    ↓
Spec 9 — Full voice flow
```

---

## Open design questions (resolve before Spec 1 builds)

1. **AWS SES:** Verify minimoi.ai domain in SES for sending email.
   Robert action: SES console → verify domain → add DNS records.

2. **Portuguese domain palette:** Robert + wife confirm colors in
   design session. Suggested starting point in Spec 2.

3. **Wife as admin:** Yes or no? If yes, she gets admin account
   created directly alongside Robert.

4. **Daughters' email addresses:** Needed to pre-approve accounts.
   Robert provides before Spec 1 is deployed.

5. **AI chat model:** gpt-4o-mini as default? Or Claude Haiku?
   Both fast and cheap. Recommend configurable with gpt-4o-mini default.

---

*Build TOC · Portuguese Domain · 2026-06-24 · Claude.ai*
*Full design: docs/design/portuguese_domain_2026-06-24.md*
*Specs 1-2 ready to hand to Claude Code after review*
