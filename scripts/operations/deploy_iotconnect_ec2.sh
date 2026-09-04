#!/usr/bin/env bash
# Deploy the IoT Connect reference demo on the mini-moi EC2 host as its OWN
# Compose project. Invoked by .github/workflows/deploy-iotconnect.yml over SSM.
#
#   usage: deploy_iotconnect_ec2.sh <iotconnect-v* tag> <expected image digest sha256:...>
#
# Touches only /opt/minimoi/iotconnect/* and the `iotconnect` Compose project.
# Never invokes the mini-moi project, never uses --remove-orphans against it,
# never restarts unrelated containers. Proves that with a before/after snapshot.
#
# Database-password contract (Spec #154 §7). The value in SSM
# /minimoi/production/iotconnect_db_password must be:
#   24-128 printable ASCII characters, no whitespace, quotes, or the characters
#   no whitespace, no single quote -- e.g.  openssl rand -base64 48 | cut -c1-40
# A percent-encoded copy (IOTCONNECT_DB_PASSWORD_URLENC) is written for the DSN.
# It is written single-quoted into $DIR/.env so `$` is literal to Compose, and
# validated by validate_db_password() before anything is written. The value is
# never printed.
set -euo pipefail

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=lib/iotconnect_release_lib.sh
. "$_LIB_DIR/iotconnect_release_lib.sh"

TAG="${1:?tag}"; DIGEST="${2:?digest}"
# One release-tag grammar, byte-identical to minimoi_portal.config.RELEASE_TAG_ERE.
[[ "$TAG" =~ ^iotconnect-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$ ]] || { echo "refusing tag '$TAG'"; exit 2; }
REGISTRY=332704997792.dkr.ecr.us-east-1.amazonaws.com
DIR=/opt/minimoi/iotconnect
COMPOSE="docker-compose -p iotconnect -f $DIR/docker-compose.yml --env-file $DIR/.env"

echo "== preflight"
docker network inspect iotconnect-edge >/dev/null 2>&1 || {
  echo "network iotconnect-edge missing: run the ordinary mini-moi deploy first (it creates the network)"; exit 3; }
test -f "$DIR/docker-compose.yml" || { echo "$DIR/docker-compose.yml missing (workflow syncs it)"; exit 3; }

echo "== secrets: write $DIR/.env fresh from SSM (a rotated parameter is picked up on the next deploy)"
PW=$(aws ssm get-parameter --region us-east-1 --name /minimoi/production/iotconnect_db_password --with-decryption --query Parameter.Value --output text)
[ -n "$PW" ] && [ "$PW" != "None" ] || { echo "SSM parameter empty"; exit 3; }
validate_db_password "$PW" || {
  unset PW
  echo "SSM /minimoi/production/iotconnect_db_password does not meet the password contract:"
  echo "  24-128 printable ASCII characters, no whitespace, no single quote"
  echo "  generate with: openssl rand -base64 48 | cut -c1-40"
  echo "  (the value itself is never printed)"
  exit 3; }
umask 077
# Single-quoted values: Compose reads them literally, so `$` is not interpolated.
PW_URLENC=$(urlencode_db_password "$PW")
printf "IOTCONNECT_DB_PASSWORD='%s'\nIOTCONNECT_DB_PASSWORD_URLENC='%s'\nIOTCONNECT_IMAGE_TAG='%s'\n" "$PW" "$PW_URLENC" "$TAG" > "$DIR/.env"
unset PW PW_URLENC
# NOTE: rotating the SSM value does not change the password inside an existing
# PostgreSQL volume; rotate the DB role too (ALTER ROLE) or reset the volume.

echo "== snapshot unrelated mini-moi containers (image + started-at) before"
snap() { docker ps -a --filter name=minimoi- --format '{{.Names}} {{.Image}} {{.CreatedAt}}' | grep -v 'minimoi-iotconnect' | sort; }
BEFORE=$(snap)

echo "== pull + start the iotconnect project"
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
export IOTCONNECT_IMAGE_TAG="$TAG"
$COMPOSE pull app integrations
$COMPOSE up -d

echo "== wait for health"
for i in $(seq 1 30); do
  S=$(docker inspect --format '{{.State.Health.Status}}' minimoi-iotconnect 2>/dev/null || echo starting)
  [ "$S" = healthy ] && break; sleep 5
done
test "$(docker inspect --format '{{.State.Health.Status}}' minimoi-iotconnect)" = healthy
test "$(docker inspect --format '{{.State.Health.Status}}' minimoi-iotconnect-integrations)" = healthy
test "$(docker inspect --format '{{.State.Health.Status}}' minimoi-iotconnect-postgres)" = healthy

echo "== verify the exact image"
test "$(docker inspect --format '{{.Config.Image}}' minimoi-iotconnect)" = "$REGISTRY/minimoi/iotconnect:$TAG"
RUNNING=$(docker inspect --format '{{index .RepoDigests 0}}' "$REGISTRY/minimoi/iotconnect:$TAG")
echo "running digest: $RUNNING"
[[ "$RUNNING" == *"$DIGEST"* ]] || { echo "digest mismatch: expected $DIGEST"; exit 4; }

echo "== read-only hosted-mode smoke (trusted portal identity headers)"
hosted_smoke http://127.0.0.1:8095

echo "== entry points"
curl -sf http://127.0.0.1:8095/api/v1/health >/dev/null
# hosted app must answer under the prefix: openapi advertises /app/iotconnect
curl -sf http://127.0.0.1:8095/openapi.json | grep -q '"/app/iotconnect"'
# authenticated portal route: signed-out request must be redirected to login (302), never 404/503
CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5001/app/iotconnect)
test "$CODE" = 302 || { echo "portal /app/iotconnect returned $CODE (expected 302 to login)"; exit 5; }
# no host port for integrations / postgres
! (ss -ltn 2>/dev/null || netstat -ltn) | grep -Eq ':(8096|5432) ' || echo "WARN: something listens on 8096/5432 on the host — check it is not IoT Connect"

echo "== isolation: unrelated containers unchanged"
AFTER=$(snap)
diff <(echo "$BEFORE") <(echo "$AFTER") && echo "unrelated mini-moi containers untouched"

echo "== done: $TAG @ $RUNNING"
