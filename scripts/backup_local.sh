#!/bin/bash
# Tier 1 backup: daily local rsync of persistent data + local Postgres dump
# to a dated backup directory on the EC2 host, with 14-day retention.
# Runs daily at 2am UTC via cron (see setup_backup_cron.sh).
# Tier 2 (S3) and Tier 3 (Dropbox) both sync FROM this script's output.
#
# Reconstructed 2026-07-15 from /opt/minimoi/logs/backup.log after an
# earlier, non-git-tracked version of this script (present directly on
# the EC2 host, never committed) was unintentionally overwritten during
# deployment. Behavior below matches what that version's log output
# showed it was actually doing — whole-data-dir rsync, auth copy, local
# Postgres dump, agent_logs rsync, retention prune — plus a Telegram
# failure alert as a genuine addition. See
# docs/specs/defect_container_persistence_2026-07-14.md for background.

set -euo pipefail

DATE=$(date +%Y-%m-%d)
DATA_DIR="/opt/minimoi/data"
AUTH_DIR="/opt/minimoi/auth"
AGENT_LOGS_DIR="/opt/minimoi/agent_logs"
BACKUP_ROOT="/opt/minimoi/backups"
LOCAL_BACKUP="${BACKUP_ROOT}/${DATE}"
LOG_FILE="/opt/minimoi/logs/backup.log"
TELEGRAM_CHAT_ID="8379221702"
RETENTION_DAYS=14
POSTGRES_CONTAINER="postgres-ai-agents"
POSTGRES_DB="personal_agents"

mkdir -p /opt/minimoi/logs "$LOCAL_BACKUP"

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
            -d text="❌ Local (Tier 1) backup FAILED — $(date '+%Y-%m-%d %H:%M UTC'). Check /opt/minimoi/logs/backup.log on EC2." \
            > /dev/null || true
    fi
}

trap 'notify_failure' ERR

log "Starting backup to ${LOCAL_BACKUP}"

rsync -a "${DATA_DIR}/" "${LOCAL_BACKUP}/data/"
log "data/ synced"

rsync -a "${AUTH_DIR}/" "${LOCAL_BACKUP}/auth/"
log "auth files copied"

docker exec "$POSTGRES_CONTAINER" pg_dump -U postgres "$POSTGRES_DB" > "${LOCAL_BACKUP}/postgres.dump"
log "postgres dump complete"

if [ -d "$AGENT_LOGS_DIR" ]; then
    rsync -a "${AGENT_LOGS_DIR}/" "${LOCAL_BACKUP}/agent_logs/"
    log "agent_logs synced"
else
    log "WARNING: ${AGENT_LOGS_DIR} not found — skipping"
fi

# 14-day retention — remove dated backup dirs older than RETENTION_DAYS
find "$BACKUP_ROOT" -maxdepth 1 -type d -name '20*-*-*' -mtime "+${RETENTION_DAYS}" -exec rm -rf {} \;
log "old backups pruned"

log "Backup complete: ${LOCAL_BACKUP}"
