#!/bin/bash
# Tier 2 backup: sync today's local EC2 backup + Postgres dump to S3.
# Runs daily at 3am UTC via cron (see setup_backup_cron.sh).
# Requires: EC2 IAM instance role with s3:PutObject/GetObject/ListBucket on minimoi-backups.

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

# Postgres dump piped directly to S3 (no EC2 disk pressure)
log "Dumping Postgres personal_agents → ${S3_BUCKET}/${DATE}/postgres.dump"
docker exec postgres-ai-agents pg_dump -U postgres personal_agents | \
    aws s3 cp - "${S3_BUCKET}/${DATE}/postgres.dump"
log "Postgres dump complete"

log "=== S3 backup complete: ${S3_BUCKET}/${DATE}/ ==="
