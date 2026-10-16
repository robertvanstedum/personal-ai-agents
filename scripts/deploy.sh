#!/bin/bash
# Pull a selected ECR image set and restart containers.
# Run on EC2: bash /opt/minimoi/deploy.sh [seven-character commit tag]
# Omitting the tag retains the manual latest-tag fallback.
set -euo pipefail

ACCOUNT_ID="332704997792"
REGION="us-east-1"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
COMPOSE="/opt/minimoi/docker-compose.prod.yml"
export MINIMOI_IMAGE_TAG="${1:-${MINIMOI_IMAGE_TAG:-latest}}"

# Host-owned nonsecret activation survives image/Compose updates. Never source/eval it.
# Fail before any ECR/Docker mutation when present configuration is invalid.
ACTIVATION_ENV="$(python3 /opt/minimoi/scripts/production_activation.py)"
if [[ -n "$ACTIVATION_ENV" ]]; then
  while IFS='=' read -r activation_key activation_value; do
    export "$activation_key=$activation_value"
  done <<< "$ACTIVATION_ENV"
fi

echo "=== Authenticating with ECR ==="
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ECR}

echo "=== Pulling image set: ${MINIMOI_IMAGE_TAG} ==="
docker-compose -f ${COMPOSE} pull

echo "=== Restarting containers ==="
docker-compose -f ${COMPOSE} up -d --remove-orphans

echo "=== Pruning old images ==="
docker image prune -f

echo "=== Container status ==="
docker-compose -f ${COMPOSE} ps
