#!/bin/bash
# Pull latest ECR images and restart containers.
# Run on EC2: bash /opt/minimoi/deploy.sh
set -euo pipefail

ACCOUNT_ID="332704997792"
REGION="us-east-1"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
COMPOSE="/opt/minimoi/docker-compose.prod.yml"

echo "=== Authenticating with ECR ==="
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ECR}

echo "=== Pulling latest images ==="
docker-compose -f ${COMPOSE} pull

echo "=== Restarting containers ==="
docker-compose -f ${COMPOSE} up -d --remove-orphans

echo "=== Pruning old images ==="
docker image prune -f

echo "=== Container status ==="
docker-compose -f ${COMPOSE} ps
