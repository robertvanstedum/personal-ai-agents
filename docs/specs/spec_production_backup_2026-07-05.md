# Spec: Production Backup System
**File:** `spec_production_backup_2026-07-05.md`
**Status:** Backlog — build this week
**Date:** 2026-07-05
**Author:** Claude.ai design session
**Motivated by:** Data loss incident 2026-07-05 — curator_archive and research_dives wiped by force-recreate without volume mounts. Mac was implicit backup pre-EC2. No production backup existed post-migration.

---

## Intent

mini-moi's core value compounds over time. Curator builds a reading history. Guild tracks every decision and spec. Portuguese and German accumulate session data and vocabulary. Losing that history — as happened today — directly undermines the platform's purpose.

This spec establishes a three-tier backup system designed around one principle: **no single vendor dependency**. If AWS goes away tomorrow — billing issue, account problem, anything — data survives. If Dropbox goes away, data survives. If EC2 is terminated, data survives. Each tier is independent. Together they provide redundancy across vendors, geographies, and failure modes.

Future tiers will include local Mac Mini storage as a fourth independent layer — planned but not in scope for this build.

---

## What Needs Backing Up

| Data | Location | Why critical |
|---|---|---|
| Curator archive | `/opt/minimoi/data/curator_archive/` | Core history — briefing editions accumulate daily |
| Research dives | `/opt/minimoi/data/research_dives/` | Deeper dive outputs — not regenerable |
| Curator history | `/opt/minimoi/data/curator_history.json` | Saved/liked articles |
| Observations | `/opt/minimoi/data/curator/intelligence_*.json` | AI observations on articles |
| Guests | `/opt/minimoi/guests.json` | Active guest accounts |
| Build queue | `/opt/minimoi/build_queue.json` | Current build state |
| Decisions store | `/opt/minimoi/openclaw/decisions.md` | Platform decision log (once CoS live) |
| Actions store | `/opt/minimoi/openclaw/actions.md` | Platform action log (once CoS live) |
| Postgres | minimoi database | Portuguese sessions, vocabulary, persona progress |
| Specs and design docs | `/opt/minimoi/docs/` | Already in git — lowest priority |

**Not backed up here:**
- Docker images — rebuilt from git, not data
- Code — in git
- `.env` and secrets — in SSM, not on disk

---

## Options Considered

### Option 1: Local EC2 backup only
Daily rsync to `/opt/minimoi/backups/YYYY-MM-DD/`. Keep 14 days.

**Verdict:** Necessary but not sufficient. If EC2 instance or volume fails, backup goes with it. Layer 1 only.

### Option 2: AWS S3
Daily sync from EC2 to S3 using AWS CLI + IAM instance role. No credentials to manage.

**Verdict:** Recommended as Layer 2. Already in AWS ecosystem, 11-nine durability, negligible cost (~$0.50-2/month).

### Option 3: Dropbox
Weekly sync from EC2 to Dropbox via rclone. Completely independent of AWS.

**Verdict:** Recommended as Layer 3. The disaster recovery layer — if AWS account goes away entirely, data survives in a completely separate vendor. Weekly cadence is sufficient for this tier.

### Option 4: Mac Mini local storage (future)
Direct rsync or rclone from EC2 to Mac Mini on local network or via Tailscale.

**Verdict:** Planned for future. Mac Mini as a fourth independent layer — local, physical, no cloud vendor dependency at all. Not in scope for this build but designed for in the architecture.

### Option 5: AWS S3 + Glacier
S3 for recent backups, Glacier for long-term archive.

**Verdict:** Defer. Add Glacier lifecycle rule in 6 months if data volume grows significantly.

---

## Recommended Architecture: Three-Tier

```
Tier 1 — Local EC2          Tier 2 — AWS S3             Tier 3 — Dropbox
Daily at 2am UTC            Daily at 3am UTC             Weekly Sunday 4am UTC
14-day retention            90-day retention             Latest snapshot only
Fast recovery (minutes)     Durable recovery (minutes)   Disaster recovery
Same host                   Same AWS account             Independent vendor
Already live (hotfix)       Build this week              Build this week
```

**Future Tier 4 — Mac Mini (planned, not in scope)**
- Direct sync to local Mac Mini storage
- Independent of all cloud vendors
- Physical possession of data
- Requires Mac Mini setup + network connectivity (Tailscale or local)
- Schedule: weekly, same day as Dropbox but offset by 2 hours

**The independence principle:**
- Tier 1 fails if EC2 disk fails → Tier 2 and 3 survive
- Tier 2 fails if AWS account suspended → Tier 1 and 3 survive
- Tier 3 fails if Dropbox account suspended → Tier 1 and 2 survive
- Future Tier 4: if all cloud fails → local Mac Mini survives

---

## Build Plan

### Phase 1 — Local EC2 backup (already live as hotfix)
`/opt/minimoi/scripts/backup_local.sh` running daily at 2am UTC.
14-day retention. Confirmed working 2026-07-05.

### Phase 2 — AWS S3 setup (Robert + Claude Code)
1. Create S3 bucket `minimoi-backups` in us-east-1 (same region as EC2)
2. Enable versioning
3. Enable SSE-S3 encryption at rest (free, one checkbox)
4. Add lifecycle rule: delete objects older than 90 days
5. Add S3 policy to EC2 IAM instance role:
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:PutObject",
    "s3:GetObject",
    "s3:ListBucket",
    "s3:DeleteObject"
  ],
  "Resource": [
    "arn:aws:s3:::minimoi-backups",
    "arn:aws:s3:::minimoi-backups/*"
  ]
}
```
6. Test: `aws s3 ls` from EC2 — confirm access

Create `/opt/minimoi/scripts/backup_s3.sh`:
```bash
#!/bin/bash
DATE=$(date +%Y-%m-%d)
S3_BUCKET="s3://minimoi-backups"
LOCAL_BACKUP="/opt/minimoi/backups/${DATE}"

# Sync local backup to S3
aws s3 sync ${LOCAL_BACKUP} ${S3_BUCKET}/${DATE}/

# Postgres dump direct to S3 (avoids writing large file to EC2 disk)
PGPASSWORD=$(aws ssm get-parameter \
  --name /minimoi/production/postgres_password \
  --with-decryption --query Parameter.Value --output text) \
  pg_dump -h localhost -U postgres minimoi | \
  aws s3 cp - ${S3_BUCKET}/${DATE}/postgres.dump

echo "S3 backup complete: ${S3_BUCKET}/${DATE}/"
```
Install cron: `0 3 * * * /opt/minimoi/scripts/backup_s3.sh`

### Phase 3 — Dropbox via rclone (Claude Code, dev-first)
1. Install rclone on EC2
2. Configure rclone Dropbox remote — OAuth token stored in SSM (`/minimoi/production/rclone_dropbox_token`)
3. Test rclone access from EC2

Create `/opt/minimoi/scripts/backup_dropbox.sh`:
```bash
#!/bin/bash
DATE=$(date +%Y-%m-%d)
LOCAL_BACKUP="/opt/minimoi/backups/${DATE}"

# Sync latest local backup to Dropbox
# Only keeps latest — this is disaster recovery, not operational recovery
rclone sync ${LOCAL_BACKUP} dropbox:minimoi-backups/latest/

# Also keep a dated weekly copy
rclone sync ${LOCAL_BACKUP} dropbox:minimoi-backups/weekly/${DATE}/

echo "Dropbox backup complete: minimoi-backups/latest/ and weekly/${DATE}/"
```
Install cron: `0 4 * * 0 /opt/minimoi/scripts/backup_dropbox.sh` (Sunday 4am UTC)

### Phase 4 — Monitoring and alerting
- Backup success/failure check added to cos-scheduler after #75 fix
- Alert via Telegram if any backup hasn't run in expected window
- Guild dashboard backup status card (future)

### Phase 5 — Restore procedure documented and tested
Write `docs/RESTORE_PROCEDURE.md`:
- Restore from local EC2 backup (fastest)
- Restore from S3 (if EC2 volume lost)
- Restore from Dropbox (if AWS account lost)
- Restore Postgres from dump
- Future: restore from Mac Mini
- **Must be tested end-to-end before this spec is marked done**

### Future Phase — Mac Mini Tier 4
When Mac Mini is available and networked:
- Add rclone or rsync target for Mac Mini
- Weekly schedule, offset from Dropbox by 2 hours
- Document as fourth independent recovery path
- Update RESTORE_PROCEDURE.md

---

## Definition of Done

- [ ] Tier 1: `backup_local.sh` running daily at 2am UTC — confirmed (already live)
- [ ] Tier 2: S3 bucket created, versioned, encrypted, lifecycle rule set
- [ ] Tier 2: EC2 IAM role has S3 write access confirmed
- [ ] Tier 2: `backup_s3.sh` tested on dev, deployed to EC2, running daily at 3am UTC
- [ ] Tier 2: First successful S3 backup confirmed — files visible in AWS console
- [ ] Tier 3: rclone installed and configured on EC2
- [ ] Tier 3: Dropbox OAuth token in SSM
- [ ] Tier 3: `backup_dropbox.sh` tested, deployed, running weekly Sunday 4am UTC
- [ ] Tier 3: First successful Dropbox backup confirmed
- [ ] `docs/RESTORE_PROCEDURE.md` written and restore tested end-to-end from each tier
- [ ] Backup failure alerting wired (at minimum: Telegram notification)
- [ ] Robert has confirmed: "I could restore the platform from scratch in under 30 minutes using any one of the three tiers"

---

## Commit

No ECR push needed for backup scripts — EC2-only changes. S3 and IAM setup via AWS console.

```
feat: three-tier production backup — local EC2 + S3 + Dropbox (#TBD)

- backup_local.sh: daily at 2am UTC, 14-day retention (already live)
- backup_s3.sh: daily at 3am UTC, 90-day retention, S3 us-east-1
- backup_dropbox.sh: weekly Sunday 4am UTC, latest + dated weekly copy
- RESTORE_PROCEDURE.md: tested restore paths for all three tiers
- Future: Mac Mini as Tier 4 local physical backup
```

---

## Notes for Grok Review

- S3 bucket in same region as EC2 — avoid cross-region transfer costs
- IAM instance role preferred over access keys — no credentials to rotate
- Postgres dump piped directly to S3 in Tier 2 — avoids EC2 disk pressure
- SSE-S3 encryption on S3 bucket — free, no performance impact, should be on
- rclone Dropbox token in SSM — same pattern as all other credentials
- Dropbox tier is disaster recovery only — weekly cadence is intentional
- Restore procedure must be tested before spec is marked done — untested backup is not a backup
- Mac Mini Tier 4 is explicitly planned — future-proof the architecture now

---

## Incident Record (motivating this spec)

**2026-07-05:** Multiple `--force-recreate` calls on minimoi-curator without volume mounts wiped:
- `curator_archive/` — all briefing editions since EC2 migration (Jun 22 — Jul 5)
- `research_dives/` — deeper dive outputs
- `curator_history.json` at risk on next rebuild

Partially recovered from Mac iCloud backup (pre-EC2 data intact, ~2 weeks of EC2-generated editions unrecoverable). Volume mounts applied as immediate fix. This incident is the direct motivation for this spec.

**Root cause:** No production backup system existed after EC2 migration. Mac was the implicit backup pre-migration. That implicit dependency was never made explicit or replaced.

---

*Spec · 2026-07-05 · Claude.ai design session · Status: Backlog — build this week*
*Grok review before build*
*Future: Mac Mini Tier 4 added when hardware available*
