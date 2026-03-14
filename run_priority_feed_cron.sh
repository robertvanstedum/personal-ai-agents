#!/bin/bash
# Priority Feed cron job - runs web search pipeline for all active priorities
# Runs daily at 14:00, well separated from the 7AM briefing job
# Results accumulate in priorities.json and display in /curator_priorities.html

PROJECT_DIR="$HOME/Projects/personal-ai-agents"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment
source venv/bin/activate

echo "🔎 Running priority feed pipeline at $(date)..."
python curator_priority_feed.py

if [ $? -eq 0 ]; then
    echo "✅ Priority feed completed successfully at $(date)"
    exit 0
else
    echo "❌ ERROR: Priority feed pipeline failed at $(date)"
    exit 1
fi
