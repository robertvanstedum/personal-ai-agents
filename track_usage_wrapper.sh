#!/bin/bash
# Wrapper for track_usage.py cron execution
cd "$HOME/Projects/personal-ai-agents"
source venv/bin/activate
python3 track_usage.py
