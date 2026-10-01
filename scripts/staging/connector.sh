#!/bin/bash
# connector.sh — the Rooms R2 Mac connector (docs/specs/minimoi-connected-work/ROOMS_R2.md §3.2-3.4).
#
# Claude Code answers in Rooms through this Mac: a launchd agent runs the
# connector from the pinned release worktree with a private virtualenv. It
# reaches Records only through the records-door sidecar on 127.0.0.1:18881.
# No secret is ever written into the plist or printed.
#
# Usage:
#   connector.sh install     create the virtualenv (requests only), write and load
#                            the launchd agent com.vanstedum.minimoi-rooms-connector
#   connector.sh uninstall   unload and remove the agent; journal, proof record and
#                            secrets stay (revocation is a separate owner step)
#   connector.sh status      launchd state and the last log lines (ids and outcomes only)
#   connector.sh preflight   no model request: CLI present and signed in through
#                            claude.ai, startup inputs absent, the door answers the
#                            connector's own credential and refuses none

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

LABEL="com.vanstedum.minimoi-rooms-connector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
VENV="$STAGING_ROOT/connector-venv"
SECRETS="$STAGING_ROOT/secrets/rooms-connector"
STATE="$STAGING_ROOT/data/rooms-connector"
LOG="$STAGING_ROOT/logs/rooms-connector.log"
CLI="${CLAUDE_CLI:-/opt/homebrew/bin/claude}"

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<< "$1"; }

cmd="${1:-}"
case "$cmd" in
  install)
    require_absolute_root
    require_release
    [[ -f "$RELEASE_DIR/services/rooms_connector/connector.py" ]] || die "the pinned release has no Rooms connector"
    [[ -d "$SECRETS" ]] || die "missing $SECRETS (records.sh provision-connector)"
    py=$(command -v python3.13 || command -v python3) || die "no python3"
    [[ -x "$VENV/bin/python" ]] || "$py" -m venv "$VENV"
    "$VENV/bin/pip" install -q "requests>=2.32,<3" >/dev/null
    ( umask 077; mkdir -p "$STATE" "$(dirname "$LOG")" )
    tmp=$(mktemp "$PLIST.XXXX")
    cat > "$tmp" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$(xml "$VENV/bin/python")</string><string>-m</string><string>services.rooms_connector.connector</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml "$STATE")</string>
  <key>EnvironmentVariables</key><dict>
    <key>PYTHONPATH</key><string>$(xml "$RELEASE_DIR")</string>
    <key>RECORDS_URL</key><string>http://127.0.0.1:18881</string>
    <key>ROOMS_CONNECTOR_SECRETS</key><string>$(xml "$SECRETS")</string>
    <key>ROOMS_CONNECTOR_STATE</key><string>$(xml "$STATE")</string>
    <key>CLAUDE_CLI</key><string>$(xml "$CLI")</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$(xml "$LOG")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOG")</string>
</dict></plist>
EOF
    chmod 600 "$tmp"
    mv "$tmp" "$PLIST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    note "installed and loaded $LABEL (code: $RELEASE_DIR; log: $LOG)" ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    note "unloaded and removed $LABEL; $STATE and $SECRETS are kept" ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state =|pid =|last exit code" || echo "$LABEL: not loaded"
    [[ -f "$LOG" ]] && tail -n 5 "$LOG" ;;
  preflight)
    bad=0
    check() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; else echo "FAIL $1: expected [$3], found [$2]"; bad=1; fi; }
    [[ -x "$VENV/bin/python" ]] || die "no virtualenv yet (connector.sh install)"
    out=$(PYTHONPATH="$RELEASE_DIR" "$VENV/bin/python" -c "
from services.rooms_connector.claude_runner import ClaudeRunner
r = ClaudeRunner(cli='$CLI', state_dir='$STATE')
ok, reason = r.check(allow_unproven=True)
print('ok' if ok else reason)
" 2>/dev/null || echo check_failed)
    check "Claude Code CLI present, signed in through claude.ai, startup inputs absent (no model request)" "$out" "ok"
    code=$(PYTHONPATH="$RELEASE_DIR" "$VENV/bin/python" -c "
from services.rooms_worker.clients import Records, read_secret
c = Records('http://127.0.0.1:18881', read_secret('$SECRETS/rooms-connector-mac.token'))
print(c.call('GET', '/hosted-teammates')[0])
" 2>/dev/null || echo call_failed)
    check "door answers the connector's own credential" "$code" "200"
    check "door refuses a request without one" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18881/api/v1/me || echo curl_failed)" "401"
    [[ "$bad" == 0 ]] || die "connector preflight failed" ;;
  *)
    sed -n '2,19p' "$0"; exit 2 ;;
esac
