#!/bin/bash
# Lesen RSS refresh — hourly via launchd.
# The packaged Python command owns the named timezone, time gate, idempotency,
# source-health policy, locks, and exit codes used by production as well.
# Logs: logs/lesen_refresh.log
# Pattern: mirrors run_curator_cron.sh — time gate + idempotency + venv

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
cd "$PROJECT_DIR" || exit 1

echo "📰 Starting lesen refresh at $(date)..."

# Activate virtual environment
source venv/bin/activate

python3 domains/german/lesen_refresh_cli.py

if [ $? -eq 0 ]; then
    echo "✅ Lesen refresh completed at $(date)"
    exit 0
else
    echo "❌ ERROR: Lesen refresh failed at $(date)"
    exit 1
fi
