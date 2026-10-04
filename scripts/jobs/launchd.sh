#!/bin/sh
# Install, remove or inspect the two scheduled-jobs launchd agents on this Mac.
#
#   scripts/jobs/launchd.sh install     copy both plists to ~/Library/LaunchAgents and load them
#   scripts/jobs/launchd.sh uninstall   unload them and remove the copies (status files and logs stay)
#   scripts/jobs/launchd.sh status      what launchd says about each, and the last status file state
#
# Robert runs this. Nothing in the repo runs it. The plists name the main checkout
# (/Users/vanstedum/Projects/personal-ai-agents); to point them at another checkout (for a trial from a worktree),
# set MINIMOI_REPO=/path before `install`. Test hooks: LAUNCHCTL, MINIMOI_LAUNCH_AGENTS_DIR.
set -eu

here="$(cd "$(dirname "$0")/../.." && pwd)"
src="$here/infrastructure/launchd"
default_repo="/Users/vanstedum/Projects/personal-ai-agents"
repo="${MINIMOI_REPO:-$default_repo}"
agents="${MINIMOI_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
launchctl_bin="${LAUNCHCTL:-launchctl}"
uid="$(id -u)"
staging="${MINIMOI_STAGING_ROOT:-$HOME/minimoi-staging}"
labels="com.vanstedum.minimoi-memory-watch com.vanstedum.minimoi-jobs-watchdog"

usage() { echo "usage: $0 install|uninstall|status" >&2; exit 2; }

install_one() {
  label="$1"
  plist="$src/$label.plist"
  target="$agents/$label.plist"
  [ -f "$plist" ] || { echo "missing $plist" >&2; exit 1; }
  sed "s|$default_repo|$repo|g" "$plist" > "$target.tmp"
  if command -v plutil >/dev/null 2>&1; then plutil -lint "$target.tmp" >/dev/null || { rm -f "$target.tmp"; echo "invalid plist for $label" >&2; exit 1; }; fi
  mv "$target.tmp" "$target"
  chmod 644 "$target"
  "$launchctl_bin" bootout "gui/$uid/$label" >/dev/null 2>&1 || true     # replace a loaded copy
  "$launchctl_bin" bootstrap "gui/$uid" "$target"
  echo "installed $label"
}

case "${1:-}" in
  install)
    mkdir -p "$agents" "$staging/logs"
    mkdir -p "$staging/data/jobs" && chmod 700 "$staging/data/jobs"
    for label in $labels; do install_one "$label"; done
    ;;
  uninstall)
    for label in $labels; do
      "$launchctl_bin" bootout "gui/$uid/$label" >/dev/null 2>&1 || true
      rm -f "$agents/$label.plist"
      echo "removed $label"
    done
    ;;
  status)
    for label in $labels; do
      if "$launchctl_bin" print "gui/$uid/$label" >/dev/null 2>&1; then echo "$label: loaded"; else echo "$label: not loaded"; fi
    done
    for f in memory-watch jobs-watchdog; do
      p="$staging/data/jobs/$f.json"
      if [ -f "$p" ]; then
        printf '%s: ' "$f"
        python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("state"), d.get("finished_at") or d.get("started_at"), d.get("results"))' "$p" 2>/dev/null || echo "unreadable"
      else
        echo "$f: no status file yet"
      fi
    done
    ;;
  *) usage ;;
esac
