#!/bin/bash
# Build Docker images and push to ECR. Run on Mac (dev machine).
# Requires: AWS CLI configured with minimoi-deploy credentials (aws configure)
# Usage: ./scripts/push_to_ecr.sh [curator|german|portal|system-bot|cos-bot|all]
set -euo pipefail

ACCOUNT_ID="332704997792"
REGION="us-east-1"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
TARGET="${1:-all}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Logging in to ECR ==="
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ECR}

build_and_push() {
  local service=$1
  local repo=$2
  local dockerfile=$3
  echo ""
  echo "--- Building ${service} (linux/amd64) ---"
  docker buildx build --platform linux/amd64 \
    -t "${ECR}/${repo}:latest" \
    --push \
    -f "${REPO_ROOT}/${dockerfile}" \
    "${REPO_ROOT}"
  echo "--- Done: ${service} ---"
}

case "$TARGET" in
  curator)
    build_and_push curator minimoi/curator docker/Dockerfile.curator
    ;;
  german)
    build_and_push german minimoi/mein-deutsch docker/Dockerfile.german
    ;;
  portal)
    build_and_push portal minimoi/portal docker/Dockerfile.portal
    ;;
  system-bot)
    build_and_push system-bot minimoi/system-bot docker/Dockerfile.telegram
    ;;
  cos-bot)
    build_and_push cos-bot minimoi/cos-bot docker/Dockerfile.cos-bot
    ;;
  all)
    build_and_push curator minimoi/curator docker/Dockerfile.curator
    build_and_push german minimoi/mein-deutsch docker/Dockerfile.german
    build_and_push portal minimoi/portal docker/Dockerfile.portal
    build_and_push system-bot minimoi/system-bot docker/Dockerfile.telegram
    build_and_push cos-bot minimoi/cos-bot docker/Dockerfile.cos-bot
    ;;
  *)
    echo "Usage: $0 [curator|german|portal|system-bot|cos-bot|all]"
    exit 1
    ;;
esac

echo ""
echo "=== All done. Images in ECR: ==="
aws ecr list-images --repository-name minimoi/curator --region ${REGION} --query 'imageIds[*].imageTag' --output text
aws ecr list-images --repository-name minimoi/mein-deutsch --region ${REGION} --query 'imageIds[*].imageTag' --output text
aws ecr list-images --repository-name minimoi/portal --region ${REGION} --query 'imageIds[*].imageTag' --output text
