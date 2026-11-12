#!/bin/sh
# The scheduled-jobs watchdog on a Linux host (FF-19, #537): production (ec2) or AWS staging (dev).
#
#   host_watchdog.sh ec2|dev watch     judge this host's jobs, write health-<host>.json (hourly)
#   host_watchdog.sh ec2|dev daily     the daily check for MC (scripts/jobs/daily_health.py; ec2 only sends Telegram)
#
# Installed by scripts/jobs/install_host_watchdog.sh, which the deploy runs (ec2: host_script_parity.py install-cron;
# dev: the lead's deploy step). Runs as root from the tools folder the deploy fills, with the host's own python3 and the
# standard library only. Status files and reports carry job ids, fixed codes and times, never content.
#
# Where the status lives:
#   ec2  /opt/minimoi/jobs (vault-apply, the exports, the refresh) and, for MC's GitHub feed, jobs/ inside the portal's
#        mc-observations volume (found from the running portal's mounts, so a renamed volume still resolves).
#   dev  /opt/minimoi-staging/data/jobs, and the same volume inside Dev's portal.
# Reports for MC go to health/ inside that volume (the portal reads /app/runtime/mc-observations/health).
set -eu

host="${1:-}"; mode="${2:-watch}"
case "$host" in
  ec2) tools=/opt/minimoi/jobs-tools; jobs=/opt/minimoi/jobs; logs=/opt/minimoi/logs ;;
  dev) tools=/opt/minimoi-staging/jobs-tools; jobs=/opt/minimoi-staging/data/jobs; logs=/opt/minimoi-staging/logs ;;
  *) echo "usage: $0 ec2|dev watch|daily" >&2; exit 2 ;;
esac

volume_source() {
  docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/runtime/mc-observations"}}{{.Source}}{{end}}{{end}}' \
    minimoi-portal 2>/dev/null || true
}

obs="$(volume_source)"
export MINIMOI_JOBS_HOST="$host" MINIMOI_JOBS_ROOT="$jobs" MINIMOI_JOBS_REGISTRY="$tools/config/scheduled_jobs.json"
export MINIMOI_JOBS_ALERT=off            # the daily check is the one that sends, and only for "needs action"
if [ -n "$obs" ]; then
  export MINIMOI_JOBS_ROOT_MC_OBSERVATIONS="$obs/jobs" MINIMOI_JOBS_HEALTH_DIR="$obs/health"
else
  export MINIMOI_JOBS_HEALTH_DIR="$jobs/health"    # portal down: still judged; MC sees a stale report, which is itself reported
fi
if [ "$host" = ec2 ]; then
  export MINIMOI_TELEGRAM_SSM_PARAM=/minimoi/production/telegram_bot_token TELEGRAM_CHAT_ID=8379221702
fi
mkdir -p "$logs"
case "$mode" in
  watch) exec /usr/bin/python3 -I -B "$tools/scripts/jobs/watchdog.py" ;;
  daily)
    # Dev reports for Dev's MC only; Telegram stays production's (its token is production's)
    [ "$host" = ec2 ] || export MINIMOI_JOBS_NOTIFY=off
    exec /usr/bin/python3 -I -B "$tools/scripts/jobs/daily_health.py" ;;
  *) echo "usage: $0 ec2|dev watch|daily" >&2; exit 2 ;;
esac
