#!/bin/bash
# Tier 2 backup: sync today's local EC2 backup + Postgres dump to S3.
# Runs daily at 3am UTC via cron (see setup_backup_cron.sh).
# Requires: EC2 IAM instance role with s3:PutObject/GetObject/ListBucket on minimoi-backups.
#
# This file is the installed EC2 copy (sha256 c2aa82d5..., 9 Jul) plus the
# #305 off-host copy of Curator preservation snapshots.
#
# Retention: Robert keeps all production content and history. This script
# never deletes in S3 (no --delete). The bucket is versioned. Its lifecycle
# rule must not expire backups; see the #305 spec, section 3 C.

set -euo pipefail

DATE=$(date +%Y-%m-%d)
S3_BUCKET="s3://minimoi-backups"
LOCAL_BACKUP="/opt/minimoi/backups/${DATE}"
LOG_FILE="/opt/minimoi/logs/backup_s3.log"
TELEGRAM_CHAT_ID="8379221702"

mkdir -p /opt/minimoi/logs

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

notify_failure() {
    local token
    token=$(aws ssm get-parameter \
        --name /minimoi/production/telegram_bot_token \
        --with-decryption \
        --query Parameter.Value \
        --output text \
        --region us-east-1 2>/dev/null || true)
    if [ -n "$token" ]; then
        curl -sf "https://api.telegram.org/bot${token}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="❌ S3 backup FAILED — $(date '+%Y-%m-%d %H:%M UTC'). Check /opt/minimoi/logs/backup_s3.log on EC2." \
            > /dev/null || true
    fi
}

trap 'notify_failure' ERR

log "=== S3 backup starting: ${DATE} ==="

# Sync today's local backup directory to S3
if [ -d "$LOCAL_BACKUP" ]; then
    log "Syncing ${LOCAL_BACKUP} → ${S3_BUCKET}/${DATE}/"
    aws s3 sync "${LOCAL_BACKUP}" "${S3_BUCKET}/${DATE}/" --quiet
    log "File sync complete"
else
    log "WARNING: Local backup dir ${LOCAL_BACKUP} not found — skipping file sync"
fi

# Curator preservation snapshots (#305): copy every completed snapshot off
# the host. Additive only; an in-progress *.partial folder is skipped.
PRESERVE_DIR="/opt/minimoi/backups/curator-preserve"
if [ -d "$PRESERVE_DIR" ]; then
    log "Syncing ${PRESERVE_DIR} → ${S3_BUCKET}/curator-preserve/"
    aws s3 sync "${PRESERVE_DIR}/" "${S3_BUCKET}/curator-preserve/" --exclude "*.partial/*" --quiet
    log "Curator preservation sync complete"
else
    log "WARNING: ${PRESERVE_DIR} not found — skipping Curator preservation sync"
fi

# Postgres dumps piped directly to S3 (no EC2 disk pressure), encrypted in the pipe (#508, spec 1.2 D13): S3 only ever
# holds ciphertext of the databases. Restore: age -d -i <Vault backup identity> postgres.dump.age | psql ...
AGE="/opt/minimoi-tools/age-v1.3.2/age"
RECIPIENTS="/opt/minimoi/backup.recipients"
for DB in personal_agents litellm_keys; do
    NAME="postgres.dump"; [ "$DB" = "personal_agents" ] || NAME="postgres-${DB}.dump"
    log "Dumping Postgres ${DB} → ${S3_BUCKET}/${DATE}/${NAME}.age"
    docker exec postgres-ai-agents pg_dump -U postgres "$DB" | "$AGE" -R "$RECIPIENTS" | \
        aws s3 cp - "${S3_BUCKET}/${DATE}/${NAME}.age"
done
log "Postgres dumps complete"

log "=== S3 backup complete: ${S3_BUCKET}/${DATE}/ ==="
