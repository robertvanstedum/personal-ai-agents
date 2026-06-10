#!/bin/bash
# start_docker_services.sh
# Waits for Colima's Docker socket to be ready, then brings up containers.
# Called by com.user.docker-compose launchd plist at login.

COMPOSE_FILE="/Users/vanstedum/Projects/personal-ai-agents/docker-compose.yml"
DOCKER="/opt/homebrew/bin/docker"
MAX_WAIT=60   # seconds to wait for Colima socket
WAITED=0

echo "[$(date)] Waiting for Docker socket..."
until "$DOCKER" info > /dev/null 2>&1; do
    sleep 2
    WAITED=$((WAITED + 2))
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "[$(date)] ERROR: Docker socket not ready after ${MAX_WAIT}s — giving up"
        exit 1
    fi
done

echo "[$(date)] Docker ready after ${WAITED}s. Starting containers..."
"$DOCKER" compose -f "$COMPOSE_FILE" up -d --wait
echo "[$(date)] docker compose up complete: $?"
