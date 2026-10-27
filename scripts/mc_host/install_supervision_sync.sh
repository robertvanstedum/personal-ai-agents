#!/bin/bash
# Install the Mac side of build supervision (#365): a token in the login Keychain (made here, never printed), a copy of
# supervision_sync.py outside any checkout, and a launchd job running it every 30 s. Prints ONLY the token's SHA-256,
# which production needs (`deploy_mc_container_ec2.sh supervision on <sha256>`). Re-run to update the copy; the token
# is kept. `--rotate` makes a new token (then run `supervision on` again with the new hash).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.minimoi/supervision/bin"
LABEL=ai.minimoi.supervision-sync
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/.minimoi/supervision/sync.log"

if [[ "${1:-}" == "--rotate" ]]; then
  security delete-generic-password -s minimoi_supervision -a sync_token >/dev/null 2>&1 || true
fi
if ! security find-generic-password -s minimoi_supervision -a sync_token >/dev/null 2>&1; then
  security add-generic-password -s minimoi_supervision -a sync_token -w "$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48)"
fi

install -d -m 0700 "$BIN"
install -m 0755 "$HERE/supervision_sync.py" "$BIN/supervision_sync.py"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>-I</string><string>$BIN/supervision_sync.py</string></array>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL (every 30 s; log $LOG)"
printf 'sync token sha256: '
security find-generic-password -s minimoi_supervision -a sync_token -w | tr -d '\n' | shasum -a 256 | cut -d' ' -f1
