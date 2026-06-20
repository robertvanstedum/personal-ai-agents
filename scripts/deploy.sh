#!/bin/bash
# Pull latest ECR images and restart containers.
# Run on EC2 from /opt/minimoi/repo/
set -euo pipefail

ACCOUNT_ID="332704997792"
REGION="us-east-1"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "=== Authenticating with ECR ==="
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ECR}

echo "=== Pulling latest images ==="
docker pull ${ECR}/minimoi/curator:latest
docker pull ${ECR}/minimoi/mein-deutsch:latest
docker pull ${ECR}/minimoi/portal:latest

echo "=== Restarting containers ==="
docker-compose -f /opt/minimoi/repo/docker-compose.prod.yml up -d --remove-orphans

echo "=== Pruning old images ==="
docker image prune -f

echo "=== Container status ==="
docker-compose -f /opt/minimoi/repo/docker-compose.prod.yml ps
