#!/bin/bash
# Tier 1 backup: daily local rsync of persistent data paths to a dated
# backup directory on the EC2 host, with 14-day retention.
# Runs daily at 2am UTC via cron (see setup_backup_cron.sh).
# Tier 2 (S3) and Tier 3 (Dropbox) both sync FROM this script's output —
# until this exists, they silently skip the file sync (Postgres dump and
# agent_logs only). See docs/specs/defect_container_persistence_2026-07-14.md.
#
# This file is the installed EC2 copy (sha256 fa6ae387..., 15 Jul) plus the
# #305 additions. Every host path in scripts/operations/curator_state.map,
# and every data/ bind source in docker-compose.prod.yml, must be listed
# below; tests/test_backup_coverage.py checks both.
#
# Retention: the 14-day prune removes only this host's dated copies. Each one
# reaches S3 at 03:00 the same day, and S3 is the kept history (see
# backup_s3.sh). Curator preservation snapshots live outside the dated
# folders, so the prune never touches them, and nothing prunes them.

set -euo pipefail

DATE=$(date +%Y-%m-%d)
DATA_DIR="/opt/minimoi/data"
AUTH_DIR="/opt/minimoi/auth"
BACKUP_ROOT="/opt/minimoi/backups"
LOCAL_BACKUP="${BACKUP_ROOT}/${DATE}"
LOG_FILE="/opt/minimoi/logs/backup_local.log"
TELEGRAM_CHAT_ID="8379221702"
RETENTION_DAYS=14

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
            -d text="❌ Local (Tier 1) backup FAILED — $(date '+%Y-%m-%d %H:%M UTC'). Check /opt/minimoi/logs/backup_local.log on EC2." \
            > /dev/null || true
    fi
}

trap 'notify_failure' ERR

log "=== Local backup starting: ${DATE} ==="

rsync_path() {
    local src="$1"
    local dest_name="$2"
    if [ -e "$src" ]; then
        log "Backing up ${src} -> ${LOCAL_BACKUP}/${dest_name}"
        rsync -a "$src" "${LOCAL_BACKUP}/${dest_name}"
    else
        log "WARNING: ${src} not found — skipping"
    fi
}

rsync_path "${DATA_DIR}/curator_archive/"        "curator_archive/"
rsync_path "${DATA_DIR}/curator_history.json"    "curator_history.json"
rsync_path "${DATA_DIR}/curator_costs.json"      "curator_costs.json"
rsync_path "${DATA_DIR}/curator/"                "curator/"
rsync_path "${DATA_DIR}/interests/"              "interests/"
rsync_path "${DATA_DIR}/research-intelligence/"  "research-intelligence/"
# Formerly container-only Curator state, host-mounted since #305
rsync_path "${DATA_DIR}/annotations/"            "annotations/"
rsync_path "${DATA_DIR}/research-library/"       "research-library/"
rsync_path "${DATA_DIR}/research-config/"        "research-config/"
rsync_path "${DATA_DIR}/curator-config/"         "curator-config/"
# Full article files that curator_history.json points to (#305, #309)
rsync_path "${DATA_DIR}/curator_cache/"          "curator_cache/"
# Guild and CoS state, gateway receipts and research dives (follow-up to #305)
rsync_path "${DATA_DIR}/guild/"                  "guild/"
rsync_path "${DATA_DIR}/model_gateway_receipts.jsonl" "model_gateway_receipts.jsonl"
rsync_path "${DATA_DIR}/research_dives/"         "research_dives/"
rsync_path "${DATA_DIR}/german/"                 "german/"
rsync_path "${DATA_DIR}/portuguese/"             "portuguese/"
rsync_path "${AUTH_DIR}/"                        "auth/"

log "=== Local backup complete: ${LOCAL_BACKUP} ==="

# 14-day retention — remove dated backup dirs older than RETENTION_DAYS
log "Pruning backups older than ${RETENTION_DAYS} days..."
find "$BACKUP_ROOT" -maxdepth 1 -type d -name '20*-*-*' -mtime "+${RETENTION_DAYS}" -print -exec rm -rf {} \; | tee -a "$LOG_FILE"

log "=== Retention prune complete ==="
