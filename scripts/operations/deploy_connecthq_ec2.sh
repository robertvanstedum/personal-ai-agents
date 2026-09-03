#!/usr/bin/env bash
# Deploy the Connect HQ reference demo on the mini-moi EC2 host as its OWN
# Compose project. Invoked by .github/workflows/deploy-connecthq.yml over SSM.
#
#   usage: deploy_connecthq_ec2.sh <connecthq-v* tag> <expected image digest sha256:...>
#
# Touches only /opt/minimoi/connecthq/* and the `connecthq` Compose project.
# Never invokes the mini-moi project, never uses --remove-orphans against it,
# never restarts unrelated containers. Proves that with a before/after snapshot.
set -euo pipefail

TAG="${1:?tag}"; DIGEST="${2:?digest}"
[[ "$TAG" =~ ^connecthq-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]] || { echo "refusing tag '$TAG'"; exit 2; }
REGISTRY=332704997792.dkr.ecr.us-east-1.amazonaws.com
DIR=/opt/minimoi/connecthq
COMPOSE="docker-compose -p connecthq -f $DIR/docker-compose.yml"

echo "== preflight"
docker network inspect connecthq-edge >/dev/null 2>&1 || {
  echo "network connecthq-edge missing: run the ordinary mini-moi deploy first (it creates the network)"; exit 3; }
test -f "$DIR/docker-compose.yml" || { echo "$DIR/docker-compose.yml missing (workflow syncs it)"; exit 3; }

echo "== secrets: write $DIR/.env fresh from SSM (a rotated parameter is picked up on the next deploy)"
PW=$(aws ssm get-parameter --region us-east-1 --name /minimoi/production/connecthq_db_password --with-decryption --query Parameter.Value --output text)
[ -n "$PW" ] && [ "$PW" != "None" ] || { echo "SSM parameter empty"; exit 3; }
umask 077
printf 'CONNECTHQ_DB_PASSWORD=%s\nCONNECTHQ_IMAGE_TAG=%s\n' "$PW" "$TAG" > "$DIR/.env"
unset PW
# NOTE: rotating the SSM value does not change the password inside an existing
# PostgreSQL volume; rotate the DB role too (ALTER ROLE) or reset the volume.

echo "== snapshot unrelated mini-moi containers (image + started-at) before"
snap() { docker ps -a --filter name=minimoi- --format '{{.Names}} {{.Image}} {{.CreatedAt}}' | grep -v 'minimoi-connecthq' | sort; }
BEFORE=$(snap)

echo "== pull + start the connecthq project"
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
export CONNECTHQ_IMAGE_TAG="$TAG"
$COMPOSE pull app integrations
$COMPOSE up -d

echo "== wait for health"
for i in $(seq 1 30); do
  S=$(docker inspect --format '{{.State.Health.Status}}' minimoi-connecthq 2>/dev/null || echo starting)
  [ "$S" = healthy ] && break; sleep 5
done
test "$(docker inspect --format '{{.State.Health.Status}}' minimoi-connecthq)" = healthy
test "$(docker inspect --format '{{.State.Health.Status}}' minimoi-connecthq-integrations)" = healthy
test "$(docker inspect --format '{{.State.Health.Status}}' minimoi-connecthq-postgres)" = healthy

echo "== verify the exact image"
test "$(docker inspect --format '{{.Config.Image}}' minimoi-connecthq)" = "$REGISTRY/minimoi/connecthq:$TAG"
RUNNING=$(docker inspect --format '{{index .RepoDigests 0}}' "$REGISTRY/minimoi/connecthq:$TAG")
echo "running digest: $RUNNING"
[[ "$RUNNING" == *"$DIGEST"* ]] || { echo "digest mismatch: expected $DIGEST"; exit 4; }

echo "== entry points"
curl -sf http://127.0.0.1:8095/api/v1/health >/dev/null
# hosted app must answer under the prefix: openapi advertises /app/connecthq
curl -sf http://127.0.0.1:8095/openapi.json | grep -q '"/app/connecthq"'
# authenticated portal route: signed-out request must be redirected to login (302), never 404/503
CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5001/app/connecthq)
test "$CODE" = 302 || { echo "portal /app/connecthq returned $CODE (expected 302 to login)"; exit 5; }
# no host port for integrations / postgres
! (ss -ltn 2>/dev/null || netstat -ltn) | grep -Eq ':(8096|5432) ' || echo "WARN: something listens on 8096/5432 on the host — check it is not Connect HQ"

echo "== isolation: unrelated containers unchanged"
AFTER=$(snap)
diff <(echo "$BEFORE") <(echo "$AFTER") && echo "unrelated mini-moi containers untouched"

echo "== done: $TAG @ $RUNNING"
