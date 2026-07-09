#!/bin/bash
# Tier 3 backup: sync latest local backup to Dropbox via rclone.
# Disaster recovery layer — completely independent of AWS.
# Runs weekly Sunday at 4am UTC via cron (see setup_backup_cron.sh).
# Requires: rclone installed on EC2, Dropbox remote named "dropbox" configured.

set -euo pipefail

DATE=$(date +%Y-%m-%d)
LOCAL_BACKUP="/opt/minimoi/backups/${DATE}"
LOG_FILE="/opt/minimoi/logs/backup_dropbox.log"

mkdir -p /opt/minimoi/logs

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "=== Dropbox backup starting: ${DATE} ==="

if [ -d "$LOCAL_BACKUP" ]; then
    # Overwrite latest — single recovery snapshot
    log "Syncing ${LOCAL_BACKUP} → dropbox:minimoi-backups/latest/"
    rclone sync "${LOCAL_BACKUP}" dropbox:minimoi-backups/latest/ --quiet
    log "Latest sync complete"

    # Dated weekly copy for history
    log "Copying to dropbox:minimoi-backups/weekly/${DATE}/"
    rclone sync "${LOCAL_BACKUP}" "dropbox:minimoi-backups/weekly/${DATE}/" --quiet
    log "Weekly copy complete"
else
    log "WARNING: Local backup dir ${LOCAL_BACKUP} not found — skipping Dropbox sync"
    exit 1
fi

log "=== Dropbox backup complete ==="
