#!/bin/bash
# restart_portal.sh — Stop and restart the minimoi portal launchd agent.
# Usage: ./restart_portal.sh

LABEL="com.vanstedum.minimoi-portal"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "Stopping portal..."
launchctl unload "$PLIST" 2>/dev/null

sleep 1

echo "Starting portal..."
launchctl load "$PLIST"

sleep 2

# Verify
STATUS=$(launchctl list | grep "$LABEL")
if [ -n "$STATUS" ]; then
    echo "✅ Portal running — http://localhost:5001"
    echo "$STATUS"
else
    echo "❌ Portal did not start — check logs:"
    echo "   tail -20 logs/portal_stderr.log"
fi
