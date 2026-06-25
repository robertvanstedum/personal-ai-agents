# mini-moi Roadmap
*Living document — versioned in GitHub*
*As of: June 2026*
*Next review: August 2026 or at major milestone*

---

## Where we are

mini-moi is a personal AI agent platform in daily production on AWS
since June 2026. Three domains live: Curator (intelligence briefing),
Mein Deutsch (German coaching), Guild (agent coordination). Two-node
architecture: EC2 primary, Mac standby, DNS-switchable.

The core infrastructure is stable. The work ahead is about making
the system more maintainable, more capable, and genuinely useful
as a long-term personal platform — not just something that runs,
but something that gets better over time.

---

## Committed — doing this, matter of when

### DevOps Pipeline
Replace the current manual deploy process with a CI/CD pipeline.
Push to main, tests run, images build, EC2 updates. Routine deploys
require no manual steps. Emergency access via Instance Connect stays
for diagnosis — just not for normal operation.

Target: this week.
Spec: docs/PHASE3_CICD_PLAN.md

### German — Portuguese as second language
Mein Deutsch proved the pattern works. Portuguese is the natural
next language — I'm fluent, have real use for it, and the
infrastructure (personas, reading lists, analysis pipeline) is
already built. Adding Portuguese is mostly configuration and
content, not new architecture.

Target: after German mobile batch is stable.
Gate: design session to confirm approach → docs/design/

### German — multi-user readiness
German currently assumes a single user (Robert). The auth
infrastructure already supports multiple users — `auth.domain_access`
can grant German access the same way it grants Portuguese. But the
content and data layer isn't ready:

1. Base personas (2-3) suitable for new learners — the current
   Vienna personas are Robert-specific and not appropriate for a
   general learner starting from scratch
2. Per-user session data separation — sessions, progress, and
   personas need to be tied to `auth.users.id`, not a single
   global store
3. User persona creation — same pattern as Portuguese Spec 4
4. Starter reading list — separate from Robert's curated Lesen config

This is a design session before any spec. The Portuguese multi-user
patterns (per-user DB rows keyed on user ID, domain_access grant)
carry over directly. The content (personas, reading lists) needs
fresh design for a general learner.

Gate: Portuguese Spec 2 stable.
Status: design needed — roll into Portuguese Spec 3 planning session.

### CoS — structured build methodology
CoS responds to commands well but doesn't yet have a consistent
way to track what it's working on, surface priorities, or close
loops. The CoS page design session will address this — the goal
is a cleaner operational loop so CoS becomes a more effective
coordination partner, not just a command responder.

Target: next month.
Gate: design session first → docs/design/cos_page_[date].md

### Career focus active through August
Job search is the near-term forcing function. Career Two-Page
Redesign (#7) is spec_ready. The Guild career pipeline needs to
surface the right things at the right time through August 3.

Target: before Aug 3.
Spec: docs/specs/spec_career_two_page_2026-06-11.md

---

## Directional — clear intent, timeline not locked

### Operations — toward bounded agent autonomy
Phase 1 (detection + alerts) is live. Phase 2 adds approval
workflows so CoS can propose fixes and I approve via Telegram.
Phase 3 adds bounded auto-execution for lowest-risk actions.
Each phase requires demonstrated reliability from the previous.

This is months of work, not weeks. The reason it matters: reducing
the operational load on me while keeping meaningful oversight in
place. Right now every incident requires manual action. That should
change gradually, as the patterns become clear.

Reference: docs/OPERATIONS_ROADMAP.md

### Learning System — local LLM that knows the project
Decision Records are the foundation. The direction is a local model
that understands the codebase, the decisions, and the intent — so
I don't have to re-explain context every session. RAG layer first,
then LoRA adapters for domain-specific patterns.

The reason it matters: the system should get smarter about itself
over time, not just accumulate more features.

Reference: docs/LEARNING_SYSTEM_ROADMAP.md

### Gespräche — closing the real conversation loop
Phase 1 is voice sessions with AI personas. The next phase is
automatic transcript handoff so a real conversation (with a human)
feeds the same analysis pipeline as a KI-Sitzung. The pattern is
already designed. The gate is 2 weeks of stable daily use on EC2
to understand what actually needs fixing first.

Gate: 2 weeks stable daily use on EC2.

### Curator — Neo4j intelligence layer
The briefing pipeline scores articles but doesn't yet build a
knowledge graph of sources, entities, and relationships. Neo4j
is provisioned and waiting.

Gate: 20+ tagged sources in Postgres.

### Prometheus + Grafana metrics
Sentry is wired (needs DSN). Metrics dashboards are the next
monitoring layer — request rates, analysis job durations, cron
timing. Two Docker containers added to the EC2 stack.

Spec: docs/specs/spec_monitoring_prometheus_grafana_2026-06-22.md

---

## Exploratory — worth tracking, not committed

- **On-device inference for voice** — Gespräche voice quality
  depends on hosted models today. A local model fast enough for
  real-time conversation would change the privacy and latency story.
  Hardware (Mac Mini 48GB) is the prerequisite.

- **Portuguese and German sources in Curator** — adding sources
  in both languages to the briefing pipeline. Natural extension
  once the Portuguese language domain is live.

- **Agent-to-agent coordination** — CoS, OpenClaw, and the system
  bot currently operate in defined lanes. More fluid coordination
  between agents is a longer-term architectural direction.

- **Model merging / adapter routing** — as LoRA adapters accumulate,
  routing between them dynamically becomes interesting. Not near-term.

---

## Done — June 2026

| Item | Shipped | Notes |
|------|---------|-------|
| AWS Block A — two-node hardening | 2026-06-21 | Role system, cron, DNS, bots |
| Operations monitoring Phase 1 | 2026-06-24 | CoS health checks, system_bot alerts |
| Telegram 3-bot → 2-bot | 2026-06-21 | system_bot + cos_bot on EC2 |
| DNS flip | 2026-06-21 | minimoi.ai → EC2, dev.minimoi.ai → Mac |
| Postgres migration Mac → EC2 | 2026-06-21 | All schemas migrated |
| Spec triage | 2026-06-24 | 10 cancelled, queue cleaned |
| Decision Record practice | 2026-06-17 | Foundation established |
| Mobile audit fixes (11) | 2026-06-16 | All German pages rendering |
| Mein Deutsch v1.1 | 2026-06-16 | mein-deutsch-v1.1 tag |
| Guild v0.9 | 2026-06-13 | |

---

*Roadmap · mini-moi · June 2026*
*Near-term sprint: docs/NEAR_TERM_PLAN.md*
*Build queue: design_log.json*
*Active design: docs/design/*
