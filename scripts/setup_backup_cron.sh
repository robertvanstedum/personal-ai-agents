#!/bin/bash
# Install Tier 1 (local), Tier 2 (S3), and Tier 3 (Dropbox) backup cron jobs on EC2.
# Idempotent — safe to re-run.
# Run via SSM after deploying backup_local.sh, backup_s3.sh, and backup_dropbox.sh to EC2.

set -euo pipefail

SCRIPTS_DIR="/opt/minimoi/scripts"
LOG_DIR="/opt/minimoi/logs"

mkdir -p "$SCRIPTS_DIR" "$LOG_DIR"
chmod +x "$SCRIPTS_DIR/backup_local.sh" "$SCRIPTS_DIR/backup_s3.sh" "$SCRIPTS_DIR/backup_dropbox.sh"

LOCAL_CRON="0 2 * * * $SCRIPTS_DIR/backup_local.sh >> $LOG_DIR/backup_local.log 2>&1"
S3_CRON="0 3 * * * $SCRIPTS_DIR/backup_s3.sh >> $LOG_DIR/backup_s3.log 2>&1"
DROPBOX_CRON="0 4 * * 0 $SCRIPTS_DIR/backup_dropbox.sh >> $LOG_DIR/backup_dropbox.log 2>&1"

CURRENT=$(crontab -l 2>/dev/null || true)

install_if_missing() {
    local line="$1"
    local label="$2"
    if echo "$CURRENT" | grep -qF "$label"; then
        echo "Already installed: $label"
    else
        CURRENT=$(printf '%s\n%s' "$CURRENT" "$line")
        echo "Installed: $line"
    fi
}

install_if_missing "$LOCAL_CRON" "backup_local.sh"
install_if_missing "$S3_CRON" "backup_s3.sh"
install_if_missing "$DROPBOX_CRON" "backup_dropbox.sh"

echo "$CURRENT" | crontab -

echo ""
echo "Current crontab:"
crontab -l
