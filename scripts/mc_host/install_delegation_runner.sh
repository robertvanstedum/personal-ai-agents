#!/bin/bash
# Install the Mac delegation runner for PRODUCTION (#365): MC on minimoi.ai hands research to Claude Code or Codex
# here. Outbound HTTPS only (core/mc_delegation_dispatcher/door.py); production keeps only the token's SHA-256.
#
#   bash scripts/mc_host/install_delegation_runner.sh [--rotate]
#
# Makes (once) a runner token in the login Keychain (service minimoi_delegation_runner, account prod), never printed.
# Copies the runner's code from origin/main into ~/minimoi-runtime/runner-prod/bundle (outside any checkout, so a
# branch switch never changes what runs), makes a bare mirror of the public repository, and loads the launchd job
# ai.minimoi.delegation-runner.prod. Research only (read-only checkouts). Claude Code: 2 USD cap per job; Codex: 30
# minutes per job. One job at a time. Prints ONLY the token's SHA-256, for `deploy_mc_container_ec2.sh runner on <sha256>`.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
BASE="$HOME/minimoi-runtime/runner-prod"
BUNDLE="$BASE/bundle"
MIRROR="$BASE/mirrors/personal-ai-agents.git"
LABEL=ai.minimoi.delegation-runner.prod
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOOR=https://minimoi.ai/internal/mc/v1/delegation-runner

if [[ "${1:-}" == "--rotate" ]]; then
  security delete-generic-password -s minimoi_delegation_runner -a prod >/dev/null 2>&1 || true
fi
if ! security find-generic-password -s minimoi_delegation_runner -a prod >/dev/null 2>&1; then
  security add-generic-password -s minimoi_delegation_runner -a prod -w "$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48)"
fi

install -d -m 0700 "$BASE" "$BASE/work" "$BASE/artifacts" "$BASE/mirrors"
git -C "$REPO_DIR" fetch --quiet origin main
rm -rf "$BUNDLE.new" && mkdir -p "$BUNDLE.new"
git -C "$REPO_DIR" archive origin/main core/__init__.py core/mc_delegation_runner core/mc_delegation_dispatcher \
  | tar -x -C "$BUNDLE.new"
cat > "$BUNDLE.new/run_runner.py" <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.mc_delegation_runner.cli import main
sys.exit(main(sys.argv[1:]))
EOF
rm -rf "$BUNDLE" && mv "$BUNDLE.new" "$BUNDLE"
[[ -d "$MIRROR" ]] || git clone --quiet --bare https://github.com/robertvanstedum/personal-ai-agents.git "$MIRROR"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/python3</string><string>-I</string><string>$BUNDLE/run_runner.py</string>
    <string>--door</string><string>$DOOR</string>
    <string>--principal</string><string>robert</string><string>--environment</string><string>prod</string>
    <string>--runner-id</string><string>mac-prod-1</string>
    <string>--mirror</string><string>personal-ai-agents=$MIRROR</string>
    <string>--workspace-root</string><string>$BASE/work</string><string>--artifact-root</string><string>$BASE/artifacts</string>
    <string>--max-cost-usd</string><string>2</string><string>--scopes</string><string>research</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$BASE/runner.log</string>
  <key>StandardErrorPath</key><string>$BASE/runner.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL from origin/main $(git -C "$REPO_DIR" rev-parse --short origin/main) (log $BASE/runner.log)"
printf 'runner token sha256: '
security find-generic-password -s minimoi_delegation_runner -a prod -w | tr -d '\n' | shasum -a 256 | cut -d' ' -f1
