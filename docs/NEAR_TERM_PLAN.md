# Near-Term Plan — mini-moi
*Version 1.0 · 2026-06-21 — Claude.ai*
*Incorporates: Claude Code state investigation, Grok review, DR from Day 1*
*For: Claude Code, Claude.ai, Grok, OpenClaw — open this at the start of any session*

---

## Target Operating Model

mini-moi runs across two production-capable environments. Either can
hold the production role at any time. Only one is production at once.

```
production role                standby role
────────────────               ────────────────
Real Telegram bots             Test Telegram bots
Cron jobs run                  Cron jobs suppressed
Outbound alerts active         Outbound alerts suppressed
Primary DNS (minimoi.ai)       dev.minimoi.ai
```

**Default assignment:**
- EC2 (t3.small) → production (AWS, always-on, cloud skills story)
- Mac → standby (development, testing, emergency backup)
- Mac Mini (future) → promoted via same pattern, no rework needed

**Promotion contract:**
Either environment can become production in ~2–4 hours:
1. Data sync (S3 bridge)
2. Role variable swap
3. DNS update (~2 min propagation)
4. Feature validation
5. Old environment → standby

Downtime acceptable (~12 hours max including testing). Solo operator.
No automated failover needed. Manual is correct at this scale.

**The role, not the machine, determines behavior.**
This principle is built into Block A and carries forward permanently.

---

## Current state (verified 2026-06-21 — Block A complete)

| Node | URL | Role today | State |
|------|-----|-----------|-------|
| EC2 | minimoi.ai | Production | All services running. Cron active. system-bot + cos-bot containers live. |
| Mac | dev.minimoi.ai | Standby | All services running. MINIMOI_ROLE=standby. Test bots registered (system_test_bot validated; cos_test_bot flood-wait resolving). |

**Block A — COMPLETE as of 2026-06-21:**
- ✅ A1: MINIMOI_ROLE system — utils/telegram.py role-aware token helpers
- ✅ A2: Cron on EC2 — scripts/setup_ec2_cron.sh deployed
- ✅ A3: Telegram SSM token helpers — SSM params set on EC2
- ✅ A5: DNS flip — minimoi.ai → EC2 Elastic IP, dev.minimoi.ai → Mac tunnel, mini-moi.ai → 301 redirect
- ✅ A6: Two-bot containers — minimoi/system-bot + minimoi/cos-bot on ECR, running on EC2

**Validation still pending:**
- ⏳ minimoi_cos_test_bot: Telegram flood-wait (~15h) from rapid restarts during A6 setup. Auto-resolves.
- German commands in system_bot: deferred to Block B (per Robert, 2026-06-21)

---

## Domain decision (locked)

Both minimoi.ai and mini-moi.ai point to production.
Two domains exist as mistype safety — both always land correctly.

| Domain | Points to | Purpose |
|--------|-----------|---------|
| minimoi.ai | EC2 Elastic IP 100.57.23.192 | Production |
| mini-moi.ai | EC2 Elastic IP (same) | Production — mistype safety |
| dev.minimoi.ai | Mac Cloudflare Tunnel | Standby/dev — unambiguously not production |

OpenClaw produces a short DR confirming this before A5.

Current DNS (wrong — fixed in A5):
- app.mini-moi.ai → EC2 (wrong subdomain, right target)
- minimoi.ai → Mac tunnel (wrong target)
- mini-moi.ai → Mac tunnel (wrong target)

---

## Data synchronization strategy

**During transition (now):** Mac is source of truth. EC2 data came
from a pg_restore and will drift as Mac cron runs and sessions
accumulate. Accept the drift during validation — it ends when
EC2 becomes primary after A5.

**At promotion time (A5 and all future promotions):**
S3 is the bridge. Same procedure every time:

```bash
# Step 1 — dump from current production
docker exec postgres-ai-agents pg_dump -U postgres personal_agents \
  > /tmp/minimoi_full.sql
aws s3 cp /tmp/minimoi_full.sql \
  s3://minimoi-data-332704997792/sync/minimoi_$(date +%Y%m%d_%H%M).sql

# Step 2 — restore on incoming production
aws s3 cp s3://minimoi-data-332704997792/sync/minimoi_[timestamp].sql \
  /tmp/minimoi_full.sql
docker exec -i postgres-ai-agents psql -U postgres \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" personal_agents
docker exec -i postgres-ai-agents psql -U postgres personal_agents \
  --no-owner < /tmp/minimoi_full.sql
```

**Other data at promotion time:**
- Curator signals JSON → already in Postgres or S3 (confirm)
- German session data → in Postgres (covered above)
- OpenClaw memory files → Mac-local; OpenClaw to confirm if in repo

**Not attempting real-time sync.** S3 bridge at promotion time is
correct for v1. Automated live sync is Phase 4 (S3 as shared data
layer). Don't build it now.

**After EC2 becomes primary (post-A5):** Mac standby data will drift
again as production writes accumulate on EC2. Monthly or on-demand
sync from EC2 → Mac keeps standby reasonably current.

---

## Rollback procedure

If something goes wrong after a DNS flip:

```
Time    Action
0:00    Problem detected on new production
0:05    Flip DNS back (Cloudflare, ~2 min propagation)
        minimoi.ai + mini-moi.ai → previous production IP
0:10    Set new production env to MINIMOI_ROLE=standby, restart
0:10    Set previous production env to MINIMOI_ROLE=production, restart
0:15    Verify previous production is receiving traffic
0:30    Investigate root cause on standby (no traffic pressure)
```

Estimated rollback time: 15–30 minutes.
Data loss risk: minimal — writes during the failed window go to the
new production; rollback loses those writes. Acceptable at solo scale.

---

## What is safe from loss

| Asset | Location | Safe? |
|-------|---------|-------|
| Code | GitHub | ✅ |
| Specs, docs, DRs | GitHub (docs/, _working/) | ✅ |
| cos_context.json | GitHub (domains/guild/config/) | ✅ |
| Telegram spec v2 | GitHub (docs/specs/) | ✅ |
| PostgreSQL data | EC2 + Mac + nightly backup | ✅ |
| Curator/German session data | Postgres + nightly backup | ✅ |
| OpenClaw memory files | Mac — OpenClaw to confirm if committed | ⚠️ |

---

## Block A — Establish two production-capable environments

Build in order. Each is a gate for the next.
Goal: both environments are fully capable of being production.
EC2 becomes primary at A5. Mac becomes standby with all
capability intact for future promotion.

### A1 — MINIMOI_ROLE system

Establishes which node is production. Every other block depends on this.

New utils/role.py:
```python
import os

def is_production() -> bool:
    """
    True if this instance holds the production role.
    Default is production — standby must be explicitly set.
    Either environment can hold this role.
    """
    return os.environ.get('MINIMOI_ROLE', 'production') == 'production'

def get_telegram_env() -> str:
    """Returns SSM path segment: 'production' or 'test'."""
    return 'production' if is_production() else 'test'
```

Startup log on every service — non-negotiable, makes debugging instant:
```python
role = os.environ.get('MINIMOI_ROLE', 'production')
app.logger.info(
    f"[{app.name}] Starting as {role} — "
    f"Telegram: {get_telegram_env()} bots"
)
```

Set in environment:
- EC2 /opt/minimoi/.env: MINIMOI_ROLE=production
- Mac .env or launchd plists: MINIMOI_ROLE=standby

Guards — if is_production(): before every:
- Outbound Telegram alert or briefing
- Scheduled job trigger in: curator_rss_v2.py, x_pull_incremental.py,
  x_to_article.py, chief_of_staff.py, operations.py

Standby suppresses: outbound briefings, scoring loops, cron triggers
Standby still runs: web UI, Telegram bots (test tokens), all design
work, manual commands — fully testable at any time

### A2 — Cron on EC2

Mac cron still active. EC2 has none. Most significant operational gap.

Before writing EC2 crontab: Claude Code reads Mac launchd plists
to confirm exact current timing:
- ~/Library/LaunchAgents/com.user.curator-cron.plist
- ~/Library/LaunchAgents/com.user.x-pull-cron.plist

New scripts/setup_ec2_cron.sh installs /etc/cron.d/minimoi on EC2.
Times must match Mac schedule (verify before deploying):
```
0  5 * * * ec2-user docker exec minimoi-curator python x_pull_incremental.py
15 5 * * * ec2-user docker exec minimoi-curator python curator_rss_v2.py
```

Mac launchd plists unchanged. A1 guards cause Mac scripts to exit
early when MINIMOI_ROLE=standby. No double-running.

### A3 — Telegram architecture (2-bot model)

Design log id 94, spec_ready.
Spec: docs/specs/spec_telegram_architecture_v2_2026-06-20.md
Two bots — confirmed, locked:

| Bot | Purpose | Direction |
|-----|---------|-----------|
| minimoi_agent_bot | CoS + OpenClaw conversational | Bidirectional |
| minimoi_system_bot | Briefings, alerts, drills, !ops, deploy notifications | Mixed |

Gate — Robert actions before Claude Code builds:
1. Create minimoi_agent_bot_test and minimoi_system_bot_test in BotFather
2. Add SSM /minimoi/test/: telegram_agent_bot_token,
   telegram_system_bot_token, telegram_chat_id
3. Add/rename SSM /minimoi/production/: same three keys

Also before A3: SSM credential audit. Confirmed working on EC2:
anthropic_api_key, xai_api_key, database_url. Not yet verified:
deepl_api_key, tavily_api_key, X OAuth2. Test each feature on EC2.

What Claude Code builds:
- get_telegram_env() selects production or test tokens based on role
- telegram-bot container added to docker-compose.prod.yml
- Same container in docker-compose.dev.yml (picks up standby role automatically)
- docs/TELEGRAM_ARCHITECTURE.md committed

### A4 — Full feature validation on EC2

Every feature confirmed working before DNS flip.
Test on app.mini-moi.ai (current EC2 URL).

- [ ] Portal login (Google OAuth)
- [ ] Guild Queue and Build Log show correct data
- [ ] Curator briefing generates (requires A2 cron to have run)
- [ ] Telegram notifications arrive (requires A3)
- [ ] German Gespräche voice session works
- [ ] German analysis pipeline runs
- [ ] Lesen / reading lists load
- [ ] Schreiben saves correctly
- [ ] Wörter / vocabulary loads
- [ ] Archiv shows session history
- [ ] DeepL translation works (verify SSM param)
- [ ] Tavily web search works in deep dive (verify SSM param)
- [ ] Standby Mac still functional (dev.minimoi.ai accessible)

### A5 — DNS flip + promote EC2 to production

Gate: A1–A4 complete. OpenClaw DR on domain naming approved.
Data sync from Mac → EC2 immediately before flip (see sync procedure above).

Claude Code steps:
1. Run data sync (pg_dump → S3 → pg_restore on EC2)
2. EC2 nginx: add minimoi.ai and mini-moi.ai to server_name
3. Certbot: add both domains to SSL cert
4. Cloudflare: A records minimoi.ai + mini-moi.ai → 100.57.23.192
5. Cloudflare Tunnel: update hostname to dev.minimoi.ai
6. Validate both production domains load EC2 correctly
7. Set Mac .env to MINIMOI_ROLE=standby, restart Mac services
8. Confirm Mac still accessible at dev.minimoi.ai
9. Retire app.mini-moi.ai and app.minimoi.ai (30-day redirect, then remove)

Rollback procedure documented above — 15–30 min if needed.

---

## Block B — German batch
After Block A stable.

| Item | What |
|------|------|
| T1-B | Safe area + font audit (inputs ≥ 16px) |
| T2-C | Post-session summary card (no LLM call) |
| T2-A | iOS Share Sheet (navigator.share) |
| T2-B | Schreiben save toast ("Gespeichert ✓") |

Order: T1-B → T2-C → T2-A → T2-B.
Reference: docs/GESPRACHE_FORWARD_SPEC.md

---

## Block C — CoS page
Gate: Block A complete (needs stable Telegram).
Design session with Claude.ai → spec → Claude Code builds.
See docs/COS_PAGE_ROADMAP.md.

---

## Block D — Gespräche Phase 1
Gate: 2 weeks stable daily use on EC2 as primary.
Design session needed before spec.

---

## Future environment promotion path

With the role system in place, any environment can be promoted:

1. Prepare: Docker + ECR images + MINIMOI_ROLE=production in .env
2. Data sync: pg_dump → S3 → pg_restore (same procedure as A5)
3. Validate: full A4-style feature checklist
4. DNS flip: update A records (~2 min Cloudflare propagation)
5. Previous production: set MINIMOI_ROLE=standby, restart

Estimated time: 2–4 hours including testing.
Rollback: reverse steps 4–5 (15–30 min).

**Mac Mini path:** When purchased, promotion follows this exact
procedure. No architectural rework. Block A builds the template
that makes future promotions straightforward.

---

## Open items for OpenClaw

1. Domain naming DR — confirm minimoi.ai + mini-moi.ai → EC2,
   dev.minimoi.ai → Mac tunnel. Short DR before A5.
2. Cron timing — pull exact times from Mac launchd plists before A2.
3. OpenClaw memory files — confirm whether _working/memory/ is
   committed or gitignored. Document for data sync clarity.
4. DR stale correction — add note to dr_aws_migration_day1_2026-06-20.md:
   PostgreSQL migration completed same session, not pending.

---

## Parked — not near-term

| Item | Why |
|------|-----|
| CI/CD (GitHub Actions) | Phase 3 — after two-node stable |
| S3 as live shared data layer | Phase 4 — real-time sync not needed yet |
| Security architecture / guest access | Low urgency |
| Career focus editor | No interview dependency |
| Voice command routing | Needs 2–4 weeks daily use data |
| Decisions view UI | Needs ~15 DRs first |
| PWA wrapper | After mobile loop polished |
| Neo4j seeding | Gate: 20+ sources in Postgres |

---

## Key reference documents

| Doc | What it covers |
|-----|---------------|
| docs/AWS_MIGRATION_PLAN.md | Full 6-phase migration plan |
| docs/PRODUCTION_CUTOVER_PLAN.md | Phases A-D, validation checklist |
| docs/PHASE3_CICD_PLAN.md | CI/CD vision and MVP scope |
| docs/specs/spec_telegram_architecture_v2_2026-06-20.md | Telegram 2-bot spec (id 94) |
| docs/decision-records/drafts/dr_aws_migration_day1_2026-06-20.md | Day 1 decisions (Postgres item stale) |
| docs/GESPRACHE_FORWARD_SPEC.md | German mobile roadmap |
| docs/COS_PAGE_ROADMAP.md | CoS page design questions |
| _working/handoff_near_term_plan_state_2026-06-21.md | Claude Code state investigation |

---

*Near-Term Plan v1.0 · 2026-06-21 · Claude.ai*
*File: _working/NEAR_TERM_PLAN.md — replaces all previous versions*
*Update when steps complete or priorities shift*
