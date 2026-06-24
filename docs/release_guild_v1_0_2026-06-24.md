# Guild v1.0 Release Notes
*mini-moi · personal-ai-agents*
*Released: June 2026*

---

## What Guild is

Guild is where the agents and I work together. It's both a coordination
layer and an advisory layer across Curator, Mein Deutsch, and whatever
comes next. The Chief of Staff holds my interests and priorities in
view, coordinates work across domains, and escalates to me when
decisions are needed — not just a command interface, but a partner
that knows what I'm actually trying to do.

v0.9 shipped the concept. v1.0 ships it as a production system.

---

## What changed from v0.9 to v1.0

### Platform moved to AWS

mini-moi now runs on AWS EC2 as primary production. minimoi.ai is
a live, publicly accessible system — not a MacBook behind a Cloudflare
tunnel. The Mac stays as a standby environment, DNS-switchable in
under four hours if needed.

Seven containers running on a t3.small:
- Portal (5001) — unified entry point for all domains
- Curator (8766) — daily intelligence briefing pipeline
- Mein Deutsch (8767) — German language coaching
- System bot — Telegram operational commands and alerts
- CoS bot — Chief of Staff conversational interface
- CoS scheduler (8769) — Operations agent, health checks, periodic tasks
- Postgres — shared data layer

All secrets in AWS SSM Parameter Store. All images in ECR.
No credentials on the server.

### Two-node architecture with role system

Either environment — EC2 or Mac — can hold the production role.
`MINIMOI_ROLE=production` determines which instance runs real bots,
sends real briefings, and fires real cron jobs. The other runs in
standby with test bots, suppressed scheduled sends, and full
web functionality for testing.

Promotion from standby to production: data sync via S3, role swap,
DNS update. Under four hours including validation.

This is the architecture that makes future migrations (Mac Mini,
another cloud) straightforward rather than days of re-testing.

### Chief of Staff + Operations — two bots, clear lanes

Two production Telegram bots on EC2:

**minimoi_system_bot** — operational. Curator briefings, German
drill commands, `!ops` system health, deploy notifications, alerts.

**minimoi_cos_bot** — conversational. Chief of Staff interface.
`!cos` and `!chief` commands. Morning brief. Career interest updates.
Domain health queries.

Both poll on EC2. Mac standby uses test bots. No duplicate messages.
No commands going unanswered if the laptop sleeps.

Operations agent runs health checks every 30 minutes on EC2:
containers, disk, memory, Flask health endpoints. Alerts via
system_bot with the exact command to fix the problem. CoS detects
and informs — doesn't remediate. Robert decides.

### CI/CD pipeline — push to ship

Push to main → 13 regression tests run → 5 Docker images build
→ EC2 updates via SSM (no SSH) → Telegram notification.
3 minutes 25 seconds end to end.

Failed tests block the deploy. GitHub issues filed automatically
for each failure. Rollback is a manual trigger with a previous
SHA — no SSH, no manual steps.

The manual deploy process (push images, SSH to EC2, pull and
restart) is retired.

### Build discipline — specs through the pipeline

Guild v1.0 shipped through its own build pipeline:

```
design session → spec (DoD + Commit) → design_log → build → done
```

The design_log tracks every item from idea to shipped. The Guild
build queue, roadmap, and docs browser show the current state of
everything in flight. This release went through the same process
as every other feature.

---

## What's in Guild v1.0

**Build queue** (`/guild/build`) — specs move from design through
spec_ready, in_build, to done. Automatic classification. 48 items
done at release. 1 active.

**Roadmap** (`/guild/build/roadmap`) — agreed targets by domain,
with status and source document links. Committed, Directional, and
Exploratory tiers. Updated at milestones, not on every build.

**Docs browser** (`/guild/docs`) — all specs and reference docs
browsable from the Guild UI. Specs, design docs, and decision
records all indexed — grows with every build.

**Decision Records** — structured capture of significant decisions
with alternatives considered and reasoning. First-class artifacts
in the repo alongside code.

**Chief of Staff** — Telegram conversational interface that acts
as both coordinator and advisor. `!cos` for domain health, career interest updates, morning brief,
and guidance aligned with my current goals.
`!ops` for system status. CoS maintains context of my priorities
across domains, surfaces what needs attention, and escalates decisions
to me rather than acting on them.

**Operations monitoring** — EC2 health checks every 30 minutes.
Two-layer: AWS CloudWatch for infrastructure metrics, Linux tools
(docker ps, df, free, curl) for application health. Portable to
any future environment.

**Career Focus** — active opportunity pipeline with Aug 3 deadline gate.
Active opportunity tracking. Not the headline feature of v1.0
but running daily.

---

## Why v0.9 was beta and v1.0 is not

v0.9 shipped the Guild concept — build discipline, CoS, the four
background loops — on a MacBook behind a tunnel. Real daily use,
but not production infrastructure.

v1.0 is the same system on AWS with:
- A CI/CD pipeline that protects every deploy
- Automated regression tests that catch regressions before EC2 sees them
- Two agents (system_bot + CoS_bot) that work whether the laptop
  is open or not
- Health monitoring that alerts before problems become outages
- An architecture that can promote any environment to production
  without days of re-testing
- A Chief of Staff that maintains context of my goals across domains,
  not just responds to commands


---

## What's next

**Portuguese language domain** — second language domain with
Robert's daughters as the first non-Robert users. Multi-user
account system, real concurrent load, operations validation
under real usage patterns. mini-moi v1.0 (platform release)
ships when this is live.

**Operations roadmap Phase 2** — approval workflows so CoS can
propose fixes and Robert approves via Telegram. Bounded autonomy
with a policy engine. Months of work, right direction.

**Prometheus + Grafana** — metrics dashboards before real users
arrive. Request rates, session durations, EC2 load under
concurrent usage.

---

## How it's built

Design: Claude.ai
Implementation: Claude Code
Review: Grok
Local memory + files: OpenClaw
Decisions: Robert

One agent active on the repository at a time.
Robert is the decision point between them.

---

*Guild v1.0 · mini-moi · June 2026*
*github.com/robertvanstedum/personal-ai-agents*
