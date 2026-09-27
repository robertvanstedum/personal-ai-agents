#!/bin/bash
# jobs.sh — run a background job on staging by hand (Spec 159: dev jobs run
# only when triggered). Each mirrors its EC2 cron command, minus the Telegram
# send, and logs to $STAGING_ROOT/logs/<job>.log.
#
# Usage: jobs.sh lesen|curator|intelligence|leitura
#   lesen         German Lesen refresh (EC2 cron: hourly)
#   curator       RSS curation run (EC2: run_curator_cron_ec2.sh; costs ~$0.3)
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
    run docker exec minimoi-curator python domains/curator/curator_rss_v2.py \
      --model=grok-4.3 --fallback --temperature=0.7 ;;
  intelligence)
    run docker exec minimoi-curator python domains/curator/curator_intelligence.py \
      --date "$(date -u +%Y-%m-%d)" ;;
  leitura)
    run docker exec minimoi-portuguese python /app/domains/portuguese/leitura_rss.py ;;
  *)
    sed -n '2,11p' "$0"; exit 2 ;;
esac
