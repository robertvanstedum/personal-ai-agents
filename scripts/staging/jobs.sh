#!/bin/bash
# jobs.sh — run a background job on staging by hand (Spec 159: dev jobs run
# only when triggered). Each mirrors its EC2 cron command, minus the Telegram
# send, and logs to $STAGING_ROOT/logs/<job>.log.
#
# Usage: jobs.sh lesen|curator|intelligence|leitura
#   lesen         German Lesen refresh (EC2 cron: hourly)
#   curator       REFUSED on staging, exit 3: curator_rss_v2.py skips every run
#                 unless MINIMOI_ROLE=production, and staging is standby on
#                 purpose (production would also switch Telegram to the
#                 production bot and SSM path). There is no override.
#   intelligence  AI Observations for today (EC2: run_intelligence_cron_ec2.sh)
#   leitura       Portuguese Leitura RSS import (no EC2 schedule exists)

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_absolute_root
JOB="${1:-}"
LOG="$STAGING_ROOT/logs/${JOB:-none}.log"
mkdir -p "$STAGING_ROOT/logs"

run() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] jobs.sh $JOB: $*" >> "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
  return "${PIPESTATUS[0]}"
}

case "$JOB" in
  lesen)
    run docker exec minimoi-german python /app/domains/german/lesen_refresh_cli.py ;;
  curator)
    # Refuse instead of running: the script would print "STANDBY — scheduled
    # run suppressed" and exit 0, which reads as a successful job.
    echo "jobs.sh curator: refused. On staging (MINIMOI_ROLE=standby) curator_rss_v2.py skips the run and" >&2
    echo "exits 0 without scoring anything. Running it as production would switch Telegram to the production" >&2
    echo "bot and SSM path, so staging never does that. The curator's data here stays as seeded." >&2
    exit 3 ;;
  intelligence)
    run docker exec minimoi-curator python domains/curator/curator_intelligence.py \
      --date "$(date -u +%Y-%m-%d)" ;;
  leitura)
    run docker exec minimoi-portuguese python /app/domains/portuguese/leitura_rss.py ;;
  *)
    sed -n '2,13p' "$0"; exit 2 ;;
esac
