#!/bin/bash
# Run only from the isolated intelligence checkout. Does not touch staging RELEASE.
set -euo pipefail
package_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(dirname "$package_dir")"
revision="$(git -C "$repo_dir" rev-parse HEAD)"
if [[ -n "$(git -C "$repo_dir" status --porcelain -- intelligence_layer)" ]]; then
  echo 'Commit the tested intelligence diff before dev deployment.' >&2
  exit 1
fi
export INTELLIGENCE_TAG="$revision"
case "${1:-inspect}" in
  inspect) docker compose -p minimoi-intelligence-dev -f "$package_dir/compose.dev.yml" ps ;;
  build) docker build --network none --build-arg "INTELLIGENCE_REVISION=$revision" -t "minimoi-staging/intelligence:$revision" -f "$package_dir/Dockerfile" "$package_dir" ;;
  up) docker compose -p minimoi-intelligence-dev -f "$package_dir/compose.dev.yml" up -d --no-build ;;
  down) docker compose -p minimoi-intelligence-dev -f "$package_dir/compose.dev.yml" down ;;
  *) echo 'Usage: deploy-dev.sh inspect|build|up|down' >&2; exit 1 ;;
esac
