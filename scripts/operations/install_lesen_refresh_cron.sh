#!/bin/bash
# Install the production Lesen refresh schedule and log rotation.
# Idempotent: replaces only the marked mini-moi entry and preserves all others.
set -euo pipefail

MARKER="# minimoi:lesen-refresh"
LOG_DIR="/opt/minimoi/logs"
BACKUP_DIR="/opt/minimoi/backups/crontab"
CRON_LINE="5 * * * * docker exec minimoi-german python /app/domains/german/lesen_refresh_cli.py >> ${LOG_DIR}/lesen_refresh.log 2>&1 ${MARKER}"

mkdir -p "${LOG_DIR}" "${BACKUP_DIR}"

CURRENT="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "${CURRENT}" | grep -qF "${CRON_LINE}"; then
  echo "Lesen refresh schedule already current."
else
  TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  printf '%s\n' "${CURRENT}" > "${BACKUP_DIR}/crontab-${TIMESTAMP}.txt"

  TMP="$(mktemp)"
  trap 'rm -f "${TMP}"' EXIT
  printf '%s\n' "${CURRENT}" | grep -vF "${MARKER}" > "${TMP}" || true
  printf '%s\n' "${CRON_LINE}" >> "${TMP}"
  crontab "${TMP}"
  echo "Installed Lesen refresh schedule."
fi

cat > /etc/logrotate.d/minimoi-lesen-refresh <<'EOF'
/opt/minimoi/logs/lesen_refresh.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF

crontab -l | grep -F "${MARKER}"
echo "Lesen refresh deployment contract is active."
