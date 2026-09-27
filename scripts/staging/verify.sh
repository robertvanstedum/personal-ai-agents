#!/bin/bash
# verify.sh — prove the running staging stack is the release, wired like
# production, and safe. Exits non-zero on any failed check. Prints names and
# status codes only, never a secret value.
#
# Usage: verify.sh [--no-guild-routes]
#   --no-guild-routes  skip the /guild-next and /guild-proto checks, for a
#                      release that predates B1 deliverable (b)
#
# Checks:
#   1. every staging container runs the release image, under this project,
#      is not restarting, and is healthy where it has a health check
#   2. /health answers 200 inside the network for portal, curator, german,
#      portuguese and cos-scheduler; the gateway (127.0.0.1:14000) and
#      Agent A (127.0.0.1:18790) answer from the host
#   3. the portal answers on 127.0.0.1:5001, and no native (non-Docker)
#      process holds port 5001
#   4. the queue folder is mounted and Save is on: the portal's own
#      queue_store.write_problem() returns None, the queue reads, and the
#      host file's checksum equals the container's
#   5. /guild-next and /guild-proto are registered: anonymous requests get
#      the login redirect or JSON 401, never 404 (and never 200)
#   6. every bind mount comes from $STAGING_ROOT (or the Docker socket),
#      never from a git checkout
#   7. MINIMOI_ROLE=standby everywhere it is read; no AWS_*, production
#      Telegram token or Guild flag outside the portal (names only)
#   8. Postgres has the guild and research schemas

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_absolute_root
require_release
S="$STAGING_ROOT"
TAG=$(release_tag)
GUILD_ROUTES=1
for arg in "$@"; do
  case "$arg" in
    --no-guild-routes) GUILD_ROUTES=0 ;;
    *) die "unknown argument: $arg" ;;
  esac
done

FAILS=0
pass() { echo "  ok   $*"; }
fail() { echo "  FAIL $*"; FAILS=$((FAILS + 1)); }
warn() { echo "  warn $*"; }

CONTAINERS=("${STAGING_CORE_CONTAINERS[@]}")
bots_enabled && CONTAINERS+=("${STAGING_BOT_CONTAINERS[@]}")
PYTHON_CONTAINERS=(minimoi-curator minimoi-german minimoi-portuguese minimoi-portal minimoi-cos-scheduler)
bots_enabled && PYTHON_CONTAINERS+=("${STAGING_BOT_CONTAINERS[@]}")

expected_image() {
  case "$1" in
    postgres-ai-agents) echo "postgres:latest" ;;
    minimoi-model-gateway) echo "minimoi-staging/cos-scheduler:model-gateway-$TAG" ;;
    minimoi-cos-agent-a) echo "minimoi-staging/cos-scheduler:agent-a-$TAG" ;;
    minimoi-curator) echo "minimoi-staging/curator:$TAG" ;;
    minimoi-german) echo "minimoi-staging/mein-deutsch:$TAG" ;;
    minimoi-portuguese) echo "minimoi-staging/portuguese:$TAG" ;;
    minimoi-portal) echo "minimoi-staging/portal:$TAG" ;;
    minimoi-cos-scheduler) echo "minimoi-staging/cos-scheduler:$TAG" ;;
    minimoi-system-bot) echo "minimoi-staging/system-bot:$TAG" ;;
    minimoi-cos-bot) echo "minimoi-staging/cos-bot:$TAG" ;;
  esac
}

echo "== 1. containers (release $TAG)"
for name in "${CONTAINERS[@]}"; do
  if ! docker inspect "$name" >/dev/null 2>&1; then fail "$name is absent"; continue; fi
  fmt='{{.State.Status}} {{.State.Restarting}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.Config.Image}} {{index .Config.Labels "com.docker.compose.project"}}'
  read -r state restarting health image project < <(docker inspect --format "$fmt" "$name")
  problems=""
  [[ "$state" == running && "$restarting" == false ]] || problems="$problems state=$state"
  [[ "$health" == none || "$health" == healthy ]] || problems="$problems health=$health"
  [[ "$image" == "$(expected_image "$name")" ]] || problems="$problems image=$image"
  [[ "$project" == "$STAGING_PROJECT" ]] || problems="$problems project=${project:-none}"
  if [[ -z "$problems" ]]; then pass "$name running ($health) $image"; else fail "$name:$problems"; fi
done
if ! bots_enabled; then
  for name in "${STAGING_BOT_CONTAINERS[@]}"; do
    state=$(docker inspect --format '{{.State.Status}}' "$name" 2>/dev/null || echo absent)
    [[ "$state" != running ]] && pass "$name not running (bots profile off)" || fail "$name runs while bots.on is absent"
  done
fi

echo "== 2. service health"
health=$(docker exec minimoi-cos-scheduler python -c "
import urllib.request as u
for x in ('minimoi-portal:5001','minimoi-curator:8766','minimoi-german:8767','minimoi-portuguese:8770','localhost:8769'):
    try:
        print(x, u.urlopen(f'http://{x}/health', timeout=5).status)
    except Exception as e:
        print(x, 'error', type(e).__name__)
" 2>&1) || true
[[ "$(echo "$health" | grep -c ' 200$')" -eq 5 ]] || fail "in-network health probe did not report 5 x 200"
while read -r target code _rest; do
  [[ -n "$target" ]] || continue
  [[ "$code" == 200 ]] && pass "$target/health 200" || fail "$target/health $code"
done <<< "$health"
code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:14000/health/liveliness || true)
[[ "$code" == 200 ]] && pass "gateway 127.0.0.1:14000 liveliness 200" || fail "gateway 127.0.0.1:14000 liveliness $code"
code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:18790/ || true)
[[ "$code" != 000 ]] && pass "Agent A answers on 127.0.0.1:18790 (HTTP $code)" || fail "Agent A does not answer on 127.0.0.1:18790"

echo "== 3. portal on 5001"
code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:5001/health || true)
[[ "$code" == 200 ]] && pass "127.0.0.1:5001/health 200" || fail "127.0.0.1:5001/health $code"
holders=$(lsof -nP -iTCP:5001 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1"("$2")"}' | sort -u || true)
if [[ -z "$holders" ]]; then
  fail "nothing listens on 5001"
else
  native=$(echo "$holders" | grep -Ev '^(ssh|limactl|colima|com\.docke|vpnkit|docker)' || true)
  if [[ -n "$native" ]]; then
    fail "a native process holds 5001: $(echo "$native" | tr '\n' ' ')(retire it: README step 4)"
  else
    pass "5001 held only by the Colima forwarder: $(echo "$holders" | tr '\n' ' ')"
  fi
fi

echo "== 4. Build Queue folder and Save"
queue=$(docker exec minimoi-portal python -c "
import os
from domains.guild.queue_store import QueueStore
path = os.environ.get('GUILD_QUEUE_PATH')
print('path', path)
print('mount', os.path.ismount(os.path.dirname(path)) if path else False)
store = QueueStore(path)
problem = store.write_problem()
print('save', 'on' if problem is None else 'off: ' + problem)
print('items', len(store.read_items()))
" 2>&1) || true
echo "$queue" | sed 's/^/       /'
echo "$queue" | grep -qx 'path /app/runtime/guild/build_queue.json' && pass "GUILD_QUEUE_PATH points into /app/runtime/guild" || fail "GUILD_QUEUE_PATH is wrong"
echo "$queue" | grep -qx 'mount True' && pass "/app/runtime/guild is a mount" || fail "/app/runtime/guild is not a mount"
echo "$queue" | grep -qx 'save on' && pass "write_problem() is None: Save is on" || fail "Save is off"
echo "$queue" | grep -Eq '^items [1-9]' && pass "queue reads" || fail "queue does not read"
if "$RELEASE_DIR/scripts/operations/check_queue_mount.sh" "$S/data/guild/build_queue.json" minimoi-portal \
     /app/runtime/guild/build_queue.json >/dev/null 2>&1; then
  pass "host queue file == portal's queue file (checksum)"
else
  fail "portal does not see $S/data/guild/build_queue.json (check_queue_mount.sh)"
fi

echo "== 5. Guild dev surfaces"
if [[ "$GUILD_ROUTES" == 1 ]]; then
  for url in /guild-next/guild/build /guild-proto/guild/build /guild-next/api/v1/session; do
    read -r code location < <(curl -s -o /dev/null -m 5 -w '%{http_code} %{redirect_url}\n' "http://127.0.0.1:5001$url" || echo "000 -")
    case "$code" in
      301|302|303|307|308)
        [[ "$location" == *"/login"* ]] && pass "$url -> $code login" || fail "$url -> $code $location (expected the login redirect)" ;;
      401) pass "$url -> 401 (JSON guard)" ;;
      404) fail "$url -> 404: not registered (MINIMOI_GUILD_NEXT/PROTO, or the release predates B1 (b))" ;;
      200) fail "$url -> 200 to an anonymous request: the owner guard is missing" ;;
      *) fail "$url -> $code" ;;
    esac
  done
else
  warn "Guild route checks skipped (--no-guild-routes)"
fi

echo "== 6. bind mounts"
before=$FAILS
for name in "${CONTAINERS[@]}"; do
  docker inspect "$name" >/dev/null 2>&1 || continue
  while read -r type src; do
    [[ "$type" == bind ]] || continue
    case "$src" in
      "$S"/*|/var/run/docker.sock) ;;
      *) fail "$name binds $src (outside $S)" ;;
    esac
    case "$src" in
      "$HOME/Projects"/*|"$HOME/.worktrees"/*|"$HOME/.codex"/*) fail "$name binds a checkout path $src" ;;
    esac
  done < <(docker inspect --format '{{range .Mounts}}{{.Type}} {{.Source}}{{"\n"}}{{end}}' "$name")
done
[[ "$FAILS" -eq "$before" ]] && pass "all bind sources are under $S or the Docker socket"

echo "== 7. role and secrets (names only)"
for name in "${PYTHON_CONTAINERS[@]}"; do
  docker inspect "$name" >/dev/null 2>&1 || continue
  role=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$name" | sed -n 's/^MINIMOI_ROLE=//p' | tail -n 1)
  [[ "$role" == standby ]] && pass "$name MINIMOI_ROLE=standby" || fail "$name MINIMOI_ROLE=${role:-unset}"
done
for name in "${CONTAINERS[@]}"; do
  docker inspect "$name" >/dev/null 2>&1 || continue
  names=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$name" | cut -d= -f1)
  bad=$(echo "$names" | grep -E '^(AWS_.*|TELEGRAM_BOT_TOKEN|TELEGRAM_POLLING_BOT_TOKEN|.*_PROD)$' | tr '\n' ' ' || true)
  [[ -z "$bad" ]] || fail "$name carries forbidden names: $bad"
  flags=$(echo "$names" | grep -E '^MINIMOI_GUILD_(NEXT|PROTO)$' | tr '\n' ' ' || true)
  if [[ "$name" == minimoi-portal ]]; then
    [[ "$flags" == *MINIMOI_GUILD_NEXT* && "$flags" == *MINIMOI_GUILD_PROTO* ]] \
      && pass "portal has MINIMOI_GUILD_NEXT and MINIMOI_GUILD_PROTO" || fail "portal lacks the Guild flags"
  else
    [[ -z "$flags" ]] || fail "$name carries Guild flags: $flags"
  fi
done

echo "== 8. database"
schemas=$(docker exec postgres-ai-agents psql -U postgres -d personal_agents -tAc \
  "select string_agg(schema_name, ',' order by schema_name) from information_schema.schemata where schema_name in ('guild','research')" 2>/dev/null || true)
[[ "$schemas" == "guild,research" ]] && pass "schemas guild and research present" || fail "schemas: '${schemas}'"

echo "== native writers under $S (advisory)"
others=$(lsof +D "$S" 2>/dev/null | awk 'NR>1 {print $1"("$2")"}' | sort -u | grep -Ev '^(ssh|limactl|colima|com\.docke|virtiofsd|vz)' || true)
[[ -z "$others" ]] && pass "no native process has files open under $S" || warn "native processes with files open under $S: $(echo "$others" | tr '\n' ' ')"

echo
if [[ "$FAILS" -eq 0 ]]; then
  echo "verify: ALL CHECKS PASSED (release $TAG)"
else
  echo "verify: $FAILS check(s) FAILED"
  exit 1
fi
