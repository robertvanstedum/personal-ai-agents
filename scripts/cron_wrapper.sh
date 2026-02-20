#!/bin/bash
# OpenClaw Cron Wrapper
# Runs a script and delivers output to Telegram via OpenClaw messaging
# Usage: cron_wrapper.sh <script_path> [telegram_chat_id]

set -euo pipefail

SCRIPT_PATH="$1"
CHAT_ID="${2:-YOUR_TELEGRAM_CHAT_ID}"  # Default to your Telegram chat
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"

# Validate script exists
if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "ERROR: Script not found: $SCRIPT_PATH" >&2
    exit 1
fi

# Create temp files for output
STDOUT_FILE=$(mktemp)
STDERR_FILE=$(mktemp)
trap "rm -f $STDOUT_FILE $STDERR_FILE" EXIT

# Run the script and capture output
EXIT_CODE=0
bash "$SCRIPT_PATH" > "$STDOUT_FILE" 2> "$STDERR_FILE" || EXIT_CODE=$?

# Read outputs
STDOUT_CONTENT=$(cat "$STDOUT_FILE")
STDERR_CONTENT=$(cat "$STDERR_FILE")

# Prepare message
if [[ $EXIT_CODE -eq 0 ]]; then
    if [[ -n "$STDOUT_CONTENT" ]]; then
        MESSAGE="$STDOUT_CONTENT"
    else
        # Silent success (e.g., balance check with no alert)
        exit 0
    fi
else
    # Error occurred
    MESSAGE="❌ Cron job failed: $(basename "$SCRIPT_PATH")
Exit code: $EXIT_CODE

STDERR:
$STDERR_CONTENT

STDOUT:
$STDOUT_CONTENT"
fi

# Send to Telegram via OpenClaw
$OPENCLAW_BIN message send \
    --channel telegram \
    --target "$CHAT_ID" \
    --message "$MESSAGE" \
    --timeout 30 \
    2>&1 | logger -t openclaw-cron || true

exit $EXIT_CODE
