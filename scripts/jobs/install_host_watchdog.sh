#!/bin/sh
# Install the scheduled-jobs watchdog and the daily health check on a Linux host (FF-19, #537). Run as root.
#
#   install_host_watchdog.sh ec2                 production: /etc/cron.d/minimoi-jobs-health. The deploy ships the tools to
#                                                /opt/minimoi/jobs-tools (config/host_scripts.json) and runs this through
#                                                host_script_parity.py install-cron, so every deploy keeps it in place.
#   install_host_watchdog.sh dev --from DIR      AWS staging: copies the tools from DIR (a checkout or an extracted bundle
#                                                at the release commit) to /opt/minimoi-staging/jobs-tools and installs two
#                                                systemd timers (Dev has no cron).
#   install_host_watchdog.sh ec2|dev --remove    removes the schedule; status files, reports and tools stay.
#
# Schedules: watch hourly (judging is cheap and MC's answer stays fresh); daily at 09:10 Chicago (the report for MC and,
# on production only, Telegram for "needs action"). Test hooks: MINIMOI_ETC (default /etc), SYSTEMCTL.
set -eu

host="${1:-}"; shift || true
etc="${MINIMOI_ETC:-/etc}"
systemctl_bin="${SYSTEMCTL:-systemctl}"
FILES="core/__init__.py core/jobs/__init__.py core/jobs/alert.py core/jobs/health.py core/jobs/judge.py
core/jobs/registry.py core/jobs/schedule.py core/jobs/status.py scripts/jobs/watchdog.py scripts/jobs/daily_health.py
scripts/jobs/host_watchdog.sh config/scheduled_jobs.json"

case "$host" in
  ec2)
    cron="$etc/cron.d/minimoi-jobs-health"
    if [ "${1:-}" = --remove ]; then rm -f "$cron"; echo "removed $cron"; exit 0; fi
    w=/opt/minimoi/jobs-tools/scripts/jobs/host_watchdog.sh
    [ -x "$w" ] || { echo "missing $w (the deploy ships it)" >&2; exit 1; }
    tmp="$cron.tmp"
    ( umask 022
      printf '%s\n' "# minimoi-jobs-health (FF-19 #537): installed by scripts/jobs/install_host_watchdog.sh; edit the repo, not this file" \
        "7 * * * * root $w ec2 watch >> /opt/minimoi/logs/jobs-watchdog.log 2>&1" \
        "CRON_TZ=America/Chicago" \
        "10 9 * * * root $w ec2 daily >> /opt/minimoi/logs/jobs-daily-health.log 2>&1" > "$tmp" )
    mv "$tmp" "$cron"
    echo "installed $cron"
    ;;
  dev)
    units="$etc/systemd/system"
    if [ "${1:-}" = --remove ]; then
      for t in minimoi-jobs-watchdog minimoi-jobs-daily-health; do
        "$systemctl_bin" disable --now "$t.timer" >/dev/null 2>&1 || true
        rm -f "$units/$t.timer" "$units/$t.service"
      done
      "$systemctl_bin" daemon-reload
      echo "removed the Dev jobs timers"; exit 0
    fi
    [ "${1:-}" = --from ] && [ -n "${2:-}" ] || { echo "usage: $0 dev --from DIR" >&2; exit 2; }
    src="$2"; tools=/opt/minimoi-staging/jobs-tools
    for f in $FILES; do
      [ -f "$src/$f" ] || { echo "missing $src/$f" >&2; exit 1; }
    done
    for f in $FILES; do
      mkdir -p "$tools/$(dirname "$f")"
      cp "$src/$f" "$tools/$f.tmp" && mv "$tools/$f.tmp" "$tools/$f"
    done
    chmod 755 "$tools/scripts/jobs/host_watchdog.sh"
    w="$tools/scripts/jobs/host_watchdog.sh"
    mkdir -p "$units"
    printf '[Unit]\nDescription=MiniMoi scheduled-jobs watchdog (Dev)\nAfter=docker.service\n[Service]\nType=oneshot\nUser=root\nExecStart=%s dev watch\n' "$w" > "$units/minimoi-jobs-watchdog.service"
    printf '[Unit]\nDescription=MiniMoi scheduled-jobs watchdog, hourly\n[Timer]\nOnCalendar=*-*-* *:07:00\nPersistent=true\n[Install]\nWantedBy=timers.target\n' > "$units/minimoi-jobs-watchdog.timer"
    printf '[Unit]\nDescription=MiniMoi daily health check (Dev)\nAfter=docker.service\n[Service]\nType=oneshot\nUser=root\nExecStart=%s dev daily\n' "$w" > "$units/minimoi-jobs-daily-health.service"
    printf '[Unit]\nDescription=MiniMoi daily health check, 09:10 Chicago\n[Timer]\nOnCalendar=*-*-* 09:10:00 America/Chicago\nPersistent=true\n[Install]\nWantedBy=timers.target\n' > "$units/minimoi-jobs-daily-health.timer"
    "$systemctl_bin" daemon-reload
    "$systemctl_bin" enable --now minimoi-jobs-watchdog.timer minimoi-jobs-daily-health.timer
    echo "installed the Dev jobs timers"
    ;;
  *) echo "usage: $0 ec2 | dev --from DIR | ec2|dev --remove" >&2; exit 2 ;;
esac
