# Decision Record — AWS Migration Day 1: Two Live Systems
*Created: 2026-06-20 — Claude.ai*
*Domain: Platform / Infrastructure / Operations*
*Type: decision-record*
*Status: active*
*LoRA candidate: yes*

---

## frontmatter
```yaml
type: decision-record
domain: platform/infrastructure
status: active
lora-candidate: yes
date: 2026-06-20
```

---

## Context

AWS Phase 2 completed successfully — portal live on EC2 with SSL at
`https://app.mini-moi.ai`. The goal for the day was to validate EC2
end to end and prepare for the production DNS flip.

What actually happened was significantly more complex. Running two live
systems revealed architectural gaps the migration plan didn't fully
anticipate: Telegram was designed for a single environment, the DNS
configuration was backwards from the target state, the German and
Curator domains returned 404 due to Docker networking issues, and the
PostgreSQL schema hadn't been migrated to EC2 at all.

The day surfaced real operational complexity and produced decisions
that now shape the architecture going forward.

---

## What was planned vs what was found

**Planned:** validate EC2 features one by one, flip DNS when ready.

**Found:**
- German and Curator 404 — two separate bugs:
  1. Nginx location blocks passing wrong paths (no trailing slash)
  2. Portal proxy defaulting to `localhost:8766/8767` which fails
     inside Docker (localhost = container, not EC2 host)
- PostgreSQL on EC2 is fresh — no schema, no data. Guild Queue and
  Build Log show 0 items. All data is on Mac.
- Neo4j: empty on Mac too. `graph_seed.py` was never successfully
  run. Nothing to migrate.
- Telegram: runs on Mac only via launchd. If Mac is off, no bot
  polls for commands. EC2 sends outbound messages but receives
  nothing.
- The two live domains (`app.mini-moi.ai` → EC2,
  `app.minimoi.ai` → Mac tunnel) are reversed from the target.

---

## Decisions made

### 1. Two live systems — not staging/dev, both production-equivalent

**Decision:** Treat both instances as production-equivalent during
validation. Do not demote Mac to staging until EC2 is fully proven.

**Rationale:** EC2 had too many gaps to call production today. Mac is
more reliable and has all the data. Forcing a staging/production split
before EC2 is validated creates operational risk with no benefit.

**Rejected:** Immediately demoting Mac to staging and treating EC2 as
production. Rejected because EC2 was missing credentials, had 404s on
two domains, and had no PostgreSQL data.

**Implication:** Two systems need to coexist intelligently. This
requires a role system to prevent duplicate Telegram messages and
double-running of scheduled jobs.

---

### 2. Production/standby role model — swappable, not fixed

**Decision:** Either environment can be production. `MINIMOI_ROLE`
environment variable designates which instance is currently active.
Default is `production` on EC2. Mac defaults to `standby`. Either
can be promoted with ~12 hour switchover including testing.

**Rationale:** AWS is the target production home, but we want the
ability to promote Mac (or a future Mac Mini) to production without
days of re-validation. This was surfaced by the realization that
getting EC2 fully working took much longer than expected — the same
thing will happen with a Mac Mini migration if the architecture
doesn't support easy promotion.

**Rejected:** Hard-coding EC2 as permanent primary. Rejected because
it creates lock-in and doesn't match the reality of how the system
will be operated.

**Rejected:** Full automated failover / zero-downtime switchover.
Rejected as over-engineering for a single-developer system. 12-hour
manual switchover is acceptable.

---

### 3. Telegram consolidation: three bots → two bots

**Decision:** Collapse `minimoi_cmd_bot`, `rvsopenbot`, and
`minimoi_agent_bot` into two bots:
- `minimoi_agent_bot` — conversational (CoS, OpenClaw)
- `minimoi_system_bot` — content and commands (briefings, alerts,
  drills, `!ops`)

**Rationale:** The three-bot model had accumulated design debt (`!ops`
on the wrong bot). The original distinction between inbound commands
and outbound alerts no longer cleanly maps to how the bots are used.
Two bots — one conversational, one operational — is cleaner and easier
to reason about in a dual-environment setup.

**Rejected:** Keeping the three-bot model and cleaning it up in place.
Rejected because it adds a third lane that doesn't add clarity, and
the consolidation was the natural moment to simplify.

**Rejected:** Single bot for everything. Rejected because the
conversational and operational use cases genuinely benefit from
separation — different interaction styles, different expectations.

---

### 4. Test bots for standby environment

**Decision:** Create `minimoi_agent_bot_test` and
`minimoi_system_bot_test`. Standby instance always uses test bots.
Same developer receives both — bot name in header distinguishes which
instance sent what.

**Rationale:** Without test bots, there is no safe way to test
Telegram flows on the Mac. Running production bots on both instances
creates duplicate messages. Test bots enable full Telegram testing
on the standby instance without any risk of polluting the production
bot stream.

**Rejected:** Suppressing all Telegram activity on standby. Rejected
because Robert needs to test Telegram flows, including the copy-paste
workflow between Grok and minimoi, on the Mac. Silent standby removes
this capability.

---

### 5. PostgreSQL migration approach

**Decision:** Migrate Mac PostgreSQL data to EC2 using
`pg_dump → S3 → pg_restore` with `--no-owner` flag. Run the
migration after all Docker networking issues are resolved and
EC2 is functionally working. Neo4j: defer, nothing to migrate.

**Rationale:** Data migration before functional validation is the
wrong order. Migrate after you've confirmed the app actually works
on EC2. Neo4j was confirmed empty on Mac — no migration needed,
seed fresh from Postgres on EC2 after migration.

**Rejected:** Starting with data migration (migrate first, debug
second). Rejected because a non-working app with data is harder
to debug than a working app without data.

---

### 6. DNS target state clarified

**Decision:**
- `app.minimoi.ai` → EC2 (production)
- `app.mini-moi.ai` → Mac tunnel (standby/test)

This is the reverse of the current state. The swap happens after
EC2 is fully validated with all credentials and features confirmed
working.

**Rejected:** Flipping DNS before validation is complete. Rejected
because this would make `app.minimoi.ai` production before the system
is ready — visible to anyone who has the URL.

---

## What emerged organically (not planned)

**Primary/secondary became production/standby.** The naming shift
came from recognizing that "primary/secondary" implied a permanent
hierarchy, while "production/standby" better reflects that either
instance can be promoted. This changes the mental model for future
Mac Mini and other environment additions.

**Mac Mini migration path.** The difficulty of this EC2 migration
surfaced a direct benefit: if the architecture supports graceful
environment promotion, the eventual Mac Mini migration will be
significantly faster. The work done today is the template.

**Two-bot model.** The Telegram architecture cleanup was always
planned, but the simplification to two bots emerged from the
operational reality of needing clear separation between conversational
and system use cases in a dual-environment setup.

**Data as the hard problem.** The plan underestimated data migration.
PostgreSQL, curator signals, German session data, OpenClaw memory —
all of these diverge between environments the moment you run both.
The long-term answer is S3 as shared data layer (Phase 4), but the
short-term answer is: Mac is source of truth, migrate to EC2 when
ready, accept drift during the validation period.

---

## Doubts and open questions

**Is the data sync strategy sufficient?** The current plan is manual
pg_dump → S3 → pg_restore at promotion time. If there are frequent
promotions or if data divergence becomes significant, this will feel
painful. S3 as a live shared data layer (Phase 4) is the right
answer but requires application changes.

**Will the role system actually be enforced?** `is_production()` as
a Python function is only as good as the discipline of calling it
everywhere. One missed guard means duplicate briefings or missing
commands. A decorator pattern would make this more robust.

**Does the two-bot model hold up?** The `!ops` command going to the
system bot feels right, but there may be ambiguous cases as the CoS
page is built out. If CoS becomes the primary interface for commands,
the system/agent distinction may blur.

**12-hour switchover — is it actually achievable?** Today's session
revealed that "get EC2 working" took longer than anticipated. A
switchover in 12 hours assumes everything is already working on the
target environment and only role + DNS changes are needed. The first
time it's done will likely take longer.

---

## Impact / Follow-up

**Implemented:**
- Docker networking bug identified and fix committed (Nginx + env vars)
- Production/standby naming adopted in new Telegram spec
- Two-bot model locked in new Telegram spec
- Test bot approach confirmed

**Not yet implemented:**
- Telegram spec v2 awaiting Claude Code build
- Test bots not yet created in BotFather (Robert action)
- PostgreSQL migration not yet run
- DNS not yet swapped

**Follow-up decisions needed:**
- Is_production decorator vs explicit guards — decide before build
- S3 data sync strategy — Phase 4, but design decision needed
- CoS page scope relative to agent_bot vs system_bot command handling

---

*Decision Record · 2026-06-20 · Claude.ai*
*Produced from: AWS Migration Day 1 session*
*Save to: docs/decision-records/drafts/dr_aws_migration_day1_2026-06-20.md*
