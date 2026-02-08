#!/bin/bash

# start-agent.sh – Quick startup for personal AI agent

echo "Starting personal AI agent..."

# 1. Go to project folder
cd ~/Projects/personal-ai-agents || { echo "Folder not found!"; exit 1; }

# 2. Activate venv
source ai-env/bin/activate || { echo "Venv activation failed!"; exit 1; }

# 3. Start Docker containers
docker compose up -d
sleep 5  # give Docker 5 seconds to start
docker ps  # quick check

# 4. Start uvicorn (foreground – Ctrl+C to stop)
#    or background: nohup uvicorn main:app --reload > uvicorn.log 2>&1 &
echo "Starting uvicorn server..."
uvicorn main:app --reload

# Optional: Open docs in browser
# open http://127.0.0.1:8000/docs
