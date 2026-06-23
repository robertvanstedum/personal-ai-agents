---
type: decision-record
dr-type: design
domain: platform
status: active
lora-candidate: yes
---

# Decision Record — Monitoring Stack Selection
*2026-06-18 · mini-moi*

---

## Decision

Adopt Sentry + Prometheus + Grafana + CloudWatch as the observability
stack. Sentry handles error tracking. Prometheus collects metrics.
Grafana provides dashboards and alerting. CloudWatch provides native
AWS infrastructure metrics at no extra cost.

Claude Code configures all of it. Robert reads the dashboards and acts
on alerts. No custom monitoring code.

---

## Context

mini-moi is a production system in daily use. It is also being
positioned as enterprise-applicable — the architecture and operational
practices should reflect how a professional engineering team would run
this. The monitoring session came after identifying three distinct needs:

1. **Error tracking** — when something breaks, know immediately with
   full context (what failed, which session, how often)
2. **Application metrics** — performance trends, analysis success rates,
   response times
3. **Infrastructure metrics** — is the EC2 healthy after AWS migration

The question was how to satisfy all three without adding operational
complexity Robert doesn't want to manage or paying for tools not already
in use.

---

## Alternatives considered

### Telegram daily summary

**What it was:** Operations agent sends a daily health digest to
Telegram: services up, disk usage, any errors from logs. Extend the
existing Telegram integration rather than adding new tools.

**Why it was attractive:** Zero new tools. Already have Telegram
infrastructure. Robert already reads Telegram. Low operational overhead.

**Rejected because:** This is notifications, not monitoring. A daily
summary tells you about yesterday's problems, not today's. It has no
drill-down capability — you see "3 analysis failures" but not which
jobs, what errors, what context. It cannot surface trends over time.
It cannot alert in real time when a threshold is crossed.

More importantly: Telegram is CoS's medium for escalation, not
operations monitoring. Mixing the two creates a channel where operations
noise competes with decisions and alerts. Keep the separation — CoS
escalates critical issues, Grafana monitors continuously.

> [FLAG: principle — the right tool for the right concern. Telegram
> is for escalation. Monitoring is a continuous background concern
> that needs its own interface.]

> [FLAG: principle — a daily summary is a lagging indicator. Monitoring
> needs to be a leading indicator — alert before the user notices the
> problem.]

---

### Datadog

**What it was:** SaaS monitoring platform. Full APM, logs, metrics,
alerts, dashboards — all managed.

**Why it was attractive:** Fully managed, production-grade, no
configuration of storage or infrastructure.

**Rejected because:** Not a tool Robert uses or wants to pay for.
Datadog pricing is enterprise-oriented and would significantly exceed
the $60/month AWS budget. It also doesn't build the infrastructure
skills that motivated the AWS migration — reading a Datadog dashboard
teaches Datadog, not observability fundamentals. The goal is to know
how to instrument, collect, store, query, and alert on metrics using
open standards that transfer to any employer context.

> [FLAG: principle — when the goal is to build transferable skills,
> choose open standards over managed SaaS where costs and complexity
> are equivalent.]

---

### Custom monitoring

**What it was:** Build a monitoring endpoint in the Operations agent
or a dedicated service. Collect metrics in Postgres. Build dashboard
views in the Guild portal.

**Why it was attractive:** No new tools, integrated with existing
stack, everything visible in the portal.

**Rejected because:** Engineering effort that doesn't need to be spent.
Prometheus, Grafana, and Sentry exist precisely because this problem
is hard to do well. Custom monitoring would lack alerting ecosystems,
pre-built data source connectors, community dashboards, and the tested
reliability of purpose-built tools. The Operations agent already handles
Tier 1-4 operational actions — extending it to do metrics collection
and dashboarding would exceed its designed scope.

> [FLAG: principle — don't build what open-source tooling already
> solves well. The Operations agent should take actions, not store
> time-series data.]

---

### Sentry + Prometheus + Grafana + CloudWatch (chosen)

**Why this stack is correct:**

**Sentry** — error tracking with full context. When an analysis job
fails, Sentry shows: which persona, which model, transcript length,
full stack trace, how often this error has occurred. Diagnosable in
30 seconds. Free tier covers 5k errors/month — well above mini-moi's
volume. The SDK integration is one `init()` call per Flask app.

**Prometheus** — open-source standard for metrics collection. Flask
apps expose `/metrics`; Prometheus scrapes every 15 seconds. No custom
storage code. The mini-moi-specific metrics (analysis job durations,
WebSocket connection counts, Curator loop durations) are instrumented
once with counters and histograms and automatically available to Grafana.

**Grafana** — reads from Prometheus and CloudWatch. Claude Code builds
the dashboards. Robert opens a URL and reads them. No configuration
knowledge required to use the system. Professional dashboards that
match what any engineering team would use.

**CloudWatch** — AWS native. EC2, RDS, S3 metrics at no extra cost.
The CloudWatch exporter pulls these into Prometheus so they appear
in Grafana alongside application metrics. One unified view.

**The division of responsibility:** Claude Code instruments, configures,
and builds dashboards. Robert reads dashboards and acts on alerts.
This matches how monitoring works in professional engineering teams —
engineers instrument, operators read.

---

## Constraints that shaped this

**$60/month AWS budget.** Rules out managed APM like Datadog.
Prometheus + Grafana run in Docker on the existing t3.small — no
additional infrastructure cost.

**Claude Code configures it.** Robert should not need to know how
to configure Prometheus scrape intervals or Grafana data sources.
The right tool is one Claude Code can fully configure so Robert gets
the dashboards without the setup work.

**Enterprise applicability.** The monitoring stack should be
recognizable to a professional engineering team. Sentry, Prometheus,
and Grafana are industry-standard tools. Being able to say "we use
Prometheus and Grafana" is a different sentence than "we built our
own monitoring."

**AWS migration coming.** Monitoring is scoped to AWS Phase 5
(Hardening). It runs on EC2 after the apps are deployed. This is
the correct sequencing — there is no point monitoring a local dev
environment.

---

## Assumptions made

**t3.small has headroom for monitoring containers.** Prometheus and
Grafana are lightweight (< 256MB each). Three Flask apps + monitoring
on a t3.small (2GB RAM) is expected to be fine. If the instance is
crowded, monitoring moves to a dedicated t3.micro.

**5k Sentry errors/month is sufficient.** mini-moi has one user.
Even with daily analysis sessions, 5k errors/month is well above
realistic failure volume.

**Open-source tooling quality is sufficient.** No SLA requirement.
If Grafana goes down, monitoring is down until restarted. Acceptable
for a personal production system.

---

## Known failure modes

**Dashboard configuration is lost on EC2 termination.** Grafana
dashboard definitions are stored in the `grafana_data` Docker volume.
If the instance is terminated without backup, dashboards need to be
rebuilt. Mitigation: provision dashboards as code in `grafana/provisioning/`
so Claude Code can rebuild them on any instance.

**Prometheus storage grows unbounded.** Time-series data accumulates.
Default retention is 15 days — sufficient, but confirm this in the
configuration. If it grows beyond the EBS volume, metrics are lost.
Mitigation: set `--storage.tsdb.retention.time=15d` explicitly.

---

## Principles this decision reflects

- "The right tool for the right concern. Telegram is for escalation;
  Grafana is for continuous monitoring. Don't mix escalation channels
  with monitoring channels."

- "A daily summary is a lagging indicator. Monitoring needs to be a
  leading indicator — alert before the user notices the problem."

- "Don't build what open-source tooling already solves well."

- "When the goal is to build transferable skills, choose open standards
  over managed SaaS."

- "Claude Code instruments and configures; Robert reads and acts.
  This is the correct division of responsibility for monitoring."

---

## What this is not

**Not real-time alerting for Tier 1-4 ops actions.** That is the
Operations agent's job. Monitoring surfaces trends and anomalies;
the Operations agent takes automated actions within defined tiers.
Both exist; neither replaces the other.

**Not Datadog, not New Relic, not custom.** All three considered
and rejected above.

**Not in scope until AWS Phase 2 is stable.** Monitoring a local
dev environment adds no value. Phase 5 (Hardening) is the correct
gate.

---

## Flags from the session

- [FLAG: principle — Telegram is for escalation, not continuous monitoring]
- [FLAG: principle — leading indicator vs lagging indicator]
- [FLAG: principle — Claude Code configures, Robert reads]
- [FLAG: deferral — monitoring stack gates on AWS Phase 2 stable and
  WebSocket analysis stable on AWS]

---

## Impact / Follow-up

- Spec: `_working/spec_monitoring_stack_2026-06-18.md` — full implementation
  detail (Sentry, Prometheus, Grafana, CloudWatch exporter, four dashboards,
  six alert rules)
- Build order: AWS Phase 2 stable → WebSocket analysis stable → monitoring stack
- Phase placement: AWS Phase 5 (Hardening)
- Supersedes: none — monitoring was previously unspecified

---

*Decision Record · 2026-06-18 · Claude Code (from session transcript)*
*File: `docs/decision-records/dr_monitoring_stack_2026-06-18.md`*
