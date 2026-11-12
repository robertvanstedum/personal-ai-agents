#!/bin/bash
# Install MC's GitHub awareness schedule (every 5 minutes) and its log rotation (#535).
# Idempotent: replaces only the marked entry, and the old hand-installed entry that sent its output to
# /dev/null, and keeps every other line. Run as root; the deploy runs it after each release.
#
# The job's output is one JSON line per run (time, state, and a fixed refusal code when held). It never
# carries GitHub text or a credential, so it is safe to keep in /opt/minimoi/logs.
set -euo pipefail

MARKER="# minimoi:mc-github-awareness"
LOG_DIR="${MINIMOI_LOG_DIR:-/opt/minimoi/logs}"
BACKUP_DIR="${MINIMOI_CRONTAB_BACKUP_DIR:-/opt/minimoi/backups/crontab}"
LOGROTATE_FILE="${MINIMOI_LOGROTATE_FILE:-/etc/logrotate.d/minimoi-mc-github-awareness}"
LOG_FILE="${LOG_DIR}/mc_github_awareness.log"
JOB="docker exec -e MINIMOI_JOBS_ROOT=/app/runtime/mc-observations/jobs minimoi-portal python3 /app/scripts/mc/github_awareness.py --sync"
CRON_LINE="*/5 * * * * ${JOB} >> ${LOG_FILE} 2>&1 ${MARKER}"
# Any earlier entry for the same job, marked or not (the 25 Oct hand install ended in >/dev/null 2>&1).
LEGACY="/app/scripts/mc/github_awareness.py --sync"

mkdir -p "${LOG_DIR}" "${BACKUP_DIR}"

CURRENT="$(crontab -l 2>/dev/null || true)"
if [[ "$(printf '%s\n' "${CURRENT}" | grep -cF "${LEGACY}")" == 1 ]] && printf '%s\n' "${CURRENT}" | grep -qxF "${CRON_LINE}"; then
  echo "MC GitHub awareness schedule already current."
else
  TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  printf '%s\n' "${CURRENT}" > "${BACKUP_DIR}/crontab-${TIMESTAMP}.txt"

  TMP="$(mktemp)"
  trap 'rm -f "${TMP}"' EXIT
  printf '%s\n' "${CURRENT}" | grep -vF "${MARKER}" | grep -vF "${LEGACY}" > "${TMP}" || true
  printf '%s\n' "${CRON_LINE}" >> "${TMP}"
  crontab "${TMP}"
  echo "Installed MC GitHub awareness schedule (log: ${LOG_FILE})."
fi

cat > "${LOGROTATE_FILE}" <<EOF
${LOG_FILE} {
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
echo "MC GitHub awareness schedule is active."
