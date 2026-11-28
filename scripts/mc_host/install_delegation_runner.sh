#!/bin/bash
# Install the Mac delegation runner for PRODUCTION (#365, #693): MC on minimoi.ai hands research to Claude Code or
# Codex here, and a build Robert confirmed to Claude Code. Outbound HTTPS only (core/mc_delegation_dispatcher/door.py); production keeps only the token's SHA-256.
#
#   bash scripts/mc_host/install_delegation_runner.sh [--rotate]
#
# Makes (once) a runner token in the login Keychain (service minimoi_delegation_runner, account prod), never printed.
# Copies the runner's code from origin/main into ~/minimoi-runtime/runner-prod/bundle (outside any checkout, so a
# branch switch never changes what runs), makes a bare mirror of the public repository, and loads the launchd job
# ai.minimoi.delegation-runner.prod. Research (read-only checkouts; Claude Code 2 USD per job, Codex 30 minutes per
# job) and builds (Claude Code only, 5 USD and 60 minutes per job; the caps are set per job by the relay, and
# --max-cost-usd 5 is this runner's own ceiling). One job at a time. Prints ONLY the runner token's SHA-256, for
# `deploy_mc_container_ec2.sh runner on <sha256>`.
#
# A build (#693; Robert's decisions of 27 Nov 2026): the runner commits the result on a branch mc/<job>, runs the
# tests itself, and then pushes that ONE branch and opens a DRAFT pull request. Nothing merges, releases or deploys.
# The push and the pull request need a GitHub credential. The runner gets it itself, at publish time, outside the
# delegated agent, holds it in memory for that one publish and writes it nowhere; this script never reads or prints
# it. Which one is --publish-credential (MINIMOI_PUBLISH_CREDENTIAL here, default gh):
#
#   gh (default; Robert, 27 Nov 2026: for now)  the Mac's existing GitHub login: the runner runs `gh auth token`.
#      Nothing to set up beyond `gh auth login`. That login can write to every repository Robert owns; the runner
#      uses it for this one repository, the one mc/<job> branch and one draft pull request, and for nothing else.
#   keychain (the narrower choice, for later)  a token for this one repository, which Robert creates himself:
#      1. GitHub > Settings > Developer settings > Fine-grained personal access tokens > Generate new token.
#         Repository access: ONLY robertvanstedum/personal-ai-agents. Repository permissions: Contents: Read and
#         write, Pull requests: Read and write (Metadata: Read-only is added by GitHub). Nothing else. Set an expiry.
#      2. Put it in the login Keychain, typed at the prompt so it is never in the shell history:
#           security add-generic-password -s minimoi_delegation_publish -a prod -w
#         (service minimoi_delegation_publish, account prod; to replace it: security delete-generic-password
#         -s minimoi_delegation_publish -a prod, then add it again).
#      3. MINIMOI_PUBLISH_CREDENTIAL=keychain bash scripts/mc_host/install_delegation_runner.sh
#
# With no credential from the chosen source the runner still commits and tests a build on the Mac, says so in its
# log at start, and the job's result says "not published: no_publish_credential". Issue #695 will replace this step
# with a central GitHub service.
#
# The runner's own test run uses the interpreter named by MINIMOI_BUILD_TEST_PYTHON, or this checkout's
# venv/bin/python when it exists; it must have pytest and the project's dependencies. Without either, the test run
# uses /usr/bin/python3 and its record will say plainly that pytest could not run.
#
# The version 2 host runtime (spec v0.5 §2.2; S1-H) is the same program under a host configuration:
#
#   bash scripts/mc_host/install_delegation_runner.sh --host-config TEMPLATE --python PY [--ref REF] [--base DIR] \
#        [--home DIR] [--path-prefix DIR]... [--label LABEL] [--load]
#
# renders the bundle (core/minimoi_actions added), records its digest, pins the interpreter into host.yaml and the
# service file (launchd ai.minimoi.runtime.<env>, or the systemd units beside this script) and loads nothing without
# --load (scripts/mc_host/runtime_install.py). Without --host-config this script is exactly production's installer.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
for arg in "$@"; do
  if [[ "$arg" == --host-config || "$arg" == --host-config=* ]]; then
    exec "${MINIMOI_INSTALL_PYTHON:-/usr/bin/python3}" -I "$REPO_DIR/scripts/mc_host/runtime_install.py" "$@"
  fi
done
BASE="$HOME/minimoi-runtime/runner-prod"
BUNDLE="$BASE/bundle"
MIRROR="$BASE/mirrors/personal-ai-agents.git"
LABEL=ai.minimoi.delegation-runner.prod
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOOR=https://minimoi.ai/internal/mc/v1/delegation-runner
PUBLISH_REPO=robertvanstedum/personal-ai-agents
PUBLISH_CREDENTIAL="${MINIMOI_PUBLISH_CREDENTIAL:-gh}"
[[ "$PUBLISH_CREDENTIAL" == gh || "$PUBLISH_CREDENTIAL" == keychain ]] || { echo "MINIMOI_PUBLISH_CREDENTIAL takes gh or keychain" >&2; exit 2; }
TEST_PYTHON="${MINIMOI_BUILD_TEST_PYTHON:-$REPO_DIR/venv/bin/python}"
TEST_PYTHON_ARGS=""
if [[ -x "$TEST_PYTHON" ]]; then
  TEST_PYTHON_ARGS="<string>--test-python</string><string>$TEST_PYTHON</string>"
else
  echo "note: no test interpreter at $TEST_PYTHON; the runner's own test run will use /usr/bin/python3" >&2
fi

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
    <string>--max-cost-usd</string><string>5</string><string>--scopes</string><string>research,build</string>
    <string>--publish-repo</string><string>$PUBLISH_REPO</string>
    <string>--publish-credential</string><string>$PUBLISH_CREDENTIAL</string>
    $TEST_PYTHON_ARGS
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
if [[ "$PUBLISH_CREDENTIAL" == gh ]]; then
  echo "publish credential: gh (the runner runs gh auth token at publish time; builds open a draft pull request)"
elif security find-generic-password -s minimoi_delegation_publish -a prod >/dev/null 2>&1; then
  echo "publish credential: keychain, present (builds open a draft pull request)"
else
  echo "publish credential: keychain, NOT present (builds are committed and tested on the Mac, not published; see the top of this script)"
fi
printf 'runner token sha256: '
security find-generic-password -s minimoi_delegation_runner -a prod -w | tr -d '\n' | shasum -a 256 | cut -d' ' -f1
