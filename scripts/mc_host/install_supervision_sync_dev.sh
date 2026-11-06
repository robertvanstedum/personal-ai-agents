#!/bin/bash
# The Mac's relay for Dev: Robert's requests through MC on Dev reach the agents' shared ledger here, and their replies go
# back. Same sync as production's, with its own state folder, its own Keychain token (made here, never printed) and relay
# only. Prints ONLY the token's SHA-256, which Dev's portal needs. `--remove` stops it and deletes its job, not its token.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HOME/.minimoi/supervision-dev"
LABEL=ai.minimoi.supervision-sync.dev
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi
if ! security find-generic-password -s minimoi_supervision -a sync_token_dev >/dev/null 2>&1; then
  security add-generic-password -s minimoi_supervision -a sync_token_dev -w "$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48)"
fi
install -d -m 0700 "$ROOT" "$ROOT/bin"
install -m 0755 "$HERE/supervision_sync.py" "$ROOT/bin/supervision_sync.py"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-I</string><string>$ROOT/bin/supervision_sync.py</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>MINIMOI_SUPERVISION_URL</key><string>https://dev.minimoi.ai/internal/supervision/v1/worker</string>
    <key>MINIMOI_SUPERVISION_DIR</key><string>$ROOT</string>
    <key>MINIMOI_SUPERVISION_KEYCHAIN_ACCOUNT</key><string>sync_token_dev</string>
    <key>MINIMOI_SUPERVISION_RELAY_ONLY</key><string>1</string>
    <key>MINIMOI_AGENT_REQUESTS_DIR</key><string>$HOME/minimoi-staging/data/guild/agent_requests</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
  </dict>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ROOT/sync.log</string>
  <key>StandardErrorPath</key><string>$ROOT/sync.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL (every 30 s; log $ROOT/sync.log)"
security find-generic-password -s minimoi_supervision -a sync_token_dev -w | tr -d '\n' | shasum -a 256 | cut -d' ' -f1
