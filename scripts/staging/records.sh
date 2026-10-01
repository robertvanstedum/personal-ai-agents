#!/bin/bash
# records.sh — Rooms' Records service on staging (Guild 1.1 slice 4, spec §6):
# its OWN Compose project (docker-compose.records.yml), like mc.sh for Master
# Craftsman. It never touches the main project's containers; the portal joins
# Records' internal network permanently from docker-compose.staging.yml.
#
# Rooms R1 (docs/specs/minimoi-connected-work/ROOMS_R1.md §3.8) adds its
# sibling, minimoi-rooms-worker, to the same project: it answers Master
# Craftsman's meeting turns and has no portal dependency.
#
# Usage:
#   records.sh build      build minimoi-staging/records:<release tag> and
#                         minimoi-staging/rooms-worker:<release tag> from the
#                         pinned release worktree (build.sh's RELEASE)
#   records.sh up         start minimoi-records, and minimoi-rooms-worker when its
#                         secrets folder is provisioned (else Records alone)
#   records.sh provision  once, owner-run: MC and the worker principals, their
#                         scoped credentials and MC's teammate card, written
#                         straight to $STAGING_ROOT/secrets/rooms-worker (mode 700,
#                         files 600) with the relay caller token from mc.env;
#                         no value is printed. Refuses if the folder has tokens.
#   records.sh preflight  read-only build gate: MC's effective tools (from its
#                         running gateway) are session_status only; from inside the
#                         containers, Records and the relay answer (positive
#                         controls) while MC, the gateway and Postgres neither
#                         resolve by name nor answer at their own IP addresses,
#                         alongside the declared network attachments (a probe that
#                         fails to run never passes); the worker holds no portal
#                         URL, credential or code (it shares records-net and
#                         mc-front with the portal by design); no published port
#                         on Records, worker, MC or relay
#   records.sh provision-connector  Rooms R2: Claude Code hosted by the Mac connector
#                         (tokens to $STAGING_ROOT/secrets/rooms-connector, not printed)
#   records.sh rotate-session-key  Rooms R3b rollback step: new cookie signing key
#                         (Records stopped); every browser sign-in ends
#   records.sh down       stop and remove the containers (data and journal kept)
#   records.sh status     state and health of both
#
# Records keeps its own sign-in. On its first start it creates owner-key.txt
# (mode 600) in the data folder, unless the owner put his own one there first;
# this script never creates, reads or prints a credential.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RECORDS_PROJECT="minimoi-staging-records"
RECORDS_FILE="docker-compose.records.yml"
RECORDS_CONTAINER="minimoi-records"
RECORDS_NETWORK="minimoi-staging-records"
WORKER_CONTAINER="minimoi-rooms-worker"
WORKER_SECRETS="$STAGING_ROOT/secrets/rooms-worker"
WORKER_JOURNAL="minimoi-staging-rooms-journal"
# Rooms R2: the Mac connector's secrets and its door sidecar.
CONNECTOR_SECRETS="$STAGING_ROOT/secrets/rooms-connector"
DOOR_CONTAINER="minimoi-records-door"
connector_ready() {
  [[ -d "$CONNECTOR_SECRETS" && "$(file_mode "$CONNECTOR_SECRETS")" == 700 ]] || return 1
  local f
  for f in rooms-connector-mac.token claude-code.token; do
    [[ -f "$CONNECTOR_SECRETS/$f" && ! -L "$CONNECTOR_SECRETS/$f" && "$(file_mode "$CONNECTOR_SECRETS/$f")" == 600 ]] || return 1
  done
}
MC_FRONT_NETWORK="minimoi-staging-mc-front"
env_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1 | sed "s/^'\\(.*\\)'\$/\\1/; s/^\"\\(.*\\)\"\$/\\1/"; }

worker_ready() {
  [[ -d "$WORKER_SECRETS" && "$(file_mode "$WORKER_SECRETS")" == 700 ]] || return 1
  local f
  for f in rooms-worker.token mc.token mc-relay.token; do
    [[ -f "$WORKER_SECRETS/$f" && ! -L "$WORKER_SECRETS/$f" && "$(file_mode "$WORKER_SECRETS/$f")" == 600 ]] || return 1
  done
}

records_compose() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -v|--volumes|--volumes=*) die "refusing 'compose $*': Records' data is never removed by a script" ;;
    esac
  done
  require_absolute_root
  require_release
  [[ -f "$RELEASE_DIR/$RECORDS_FILE" ]] || die "the pinned release has no $RECORDS_FILE (it predates Guild 1.1 slice 4)"
  MINIMOI_ROOT="$STAGING_ROOT" docker compose -p "$RECORDS_PROJECT" --env-file "$STAGING_RELEASE_ENV" \
    -f "$RELEASE_DIR/$RECORDS_FILE" "$@"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  build)
    require_absolute_root
    require_release
    sha=$(release_sha); tag=$(release_tag)
    image="minimoi-staging/records:$tag"
    note "building $image from $RELEASE_DIR at ${sha:0:7}"
    docker build -f "$RELEASE_DIR/docker/Dockerfile.records" -t "$image" \
      --label "minimoi.staging.release=$sha" "$RELEASE_DIR"
    if [[ -f "$RELEASE_DIR/docker/Dockerfile.rooms-worker" ]]; then
      note "building minimoi-staging/rooms-worker:$tag"
      docker build -f "$RELEASE_DIR/docker/Dockerfile.rooms-worker" -t "minimoi-staging/rooms-worker:$tag" \
        --label "minimoi.staging.release=$sha" "$RELEASE_DIR"
    fi ;;
  up)
    require_absolute_root
    folder="$STAGING_ROOT/data/records"
    [[ -d "$folder" ]] || die "missing $folder; run build.sh (it creates it at mode 700)"
    [[ "$(file_mode "$folder")" == 700 ]] || die "$folder must be mode 700 (Records refuses a shared folder)"
    docker network inspect "$RECORDS_NETWORK" >/dev/null 2>&1 \
      || die "missing network $RECORDS_NETWORK; bring the main stack up first (up.sh portal)"
    if worker_ready && grep -q "rooms-worker:" "$RELEASE_DIR/$RECORDS_FILE"; then
      docker network inspect "$MC_FRONT_NETWORK" >/dev/null 2>&1 \
        || die "missing network $MC_FRONT_NETWORK; bring the main stack up first (up.sh portal)"
      docker volume inspect "$WORKER_JOURNAL" >/dev/null 2>&1 || docker volume create "$WORKER_JOURNAL" >/dev/null
      services="records rooms-worker"
      if connector_ready && grep -q "records-door:" "$RELEASE_DIR/$RECORDS_FILE"; then services="$services records-door"; fi
      # shellcheck disable=SC2086
      records_compose up -d --no-build $services
      note "up: $services"
    else
      records_compose up -d --no-build records
      note "up: Records only (the Rooms worker needs: records.sh provision)"
    fi
    note "Records answers only through the portal at https://dev.minimoi.ai/app/records/ (owner sign-in, then Records' own sign-in)" ;;
  provision)
    require_absolute_root
    [[ "$(docker inspect -f '{{.State.Running}}' "$RECORDS_CONTAINER" 2>/dev/null || true)" == true ]] || die "start Records first (records.sh up)"
    [[ -f "$STAGING_MC_ENV" ]] || die "missing $STAGING_MC_ENV (mc.sh token writes the relay caller token)"
    relay=$(env_value "$STAGING_MC_ENV" MC_RELAY_TOKEN)
    [[ -n "$relay" ]] || die "mc.env has no MC_RELAY_TOKEN (run mc.sh token)"
    if [[ -e "$WORKER_SECRETS/rooms-worker.token" || -e "$WORKER_SECRETS/mc.token" ]]; then
      die "$WORKER_SECRETS already holds tokens; revoke them in Records and remove the files first"
    fi
    outbox="$STAGING_ROOT/data/records/.rooms-outbox"
    [[ ! -e "$outbox/mc.token" && ! -e "$outbox/rooms-worker.token" ]] || die "an earlier provision left tokens in $outbox; inspect it first"
    docker exec "$RECORDS_CONTAINER" python manage.py provision-rooms --data-dir /data --out /data/.rooms-outbox \
      --teammate mc --label "Master Craftsman"
    ( umask 077; mkdir -p "$WORKER_SECRETS" )
    chmod 700 "$WORKER_SECRETS"
    mv "$outbox/mc.token" "$outbox/rooms-worker.token" "$WORKER_SECRETS/"
    rmdir "$outbox" 2>/dev/null || true
    ( umask 077; printf '%s\n' "$relay" > "$WORKER_SECRETS/mc-relay.token" )
    chmod 600 "$WORKER_SECRETS"/*.token
    unset relay
    note "provisioned: MC (membership-scoped) and the Rooms worker (work-scoped) credentials are in $WORKER_SECRETS (not printed). Next: records.sh up" ;;
  provision-connector)
    # Rooms R2 (ROOMS_R2.md §3.3-3.4), once, owner-run: Claude Code as a
    # teammate hosted by the Mac connector's own work principal. Revokes
    # Claude Code's existing credentials (listed) and creates claude-code-manual
    # for hand-posted notes. Tokens go only to $CONNECTOR_SECRETS (700/600).
    require_absolute_root
    [[ "$(docker inspect -f '{{.State.Running}}' "$RECORDS_CONTAINER" 2>/dev/null || true)" == true ]] || die "start Records first (records.sh up)"
    [[ ! -e "$CONNECTOR_SECRETS/claude-code.token" ]] || die "$CONNECTOR_SECRETS already holds tokens; revoke them in Records and remove the files first"
    outbox="$STAGING_ROOT/data/records/.rooms-outbox-connector"
    [[ ! -e "$outbox" ]] || die "an earlier provision left $outbox; inspect it first"
    docker exec "$RECORDS_CONTAINER" python manage.py provision-rooms --data-dir /data --out /data/.rooms-outbox-connector \
      --teammate claude-code --label "Claude Code" --worker-principal rooms-connector-mac --revoke-legacy --manual \
      --card '{"host":"this Mac","connector":"rooms-connector","tools_profile":"none (no tools, no MCP, no settings)","billing_route":"Robert'"'"'s Claude subscription (claude.ai sign-in)","max_turn_s":180,"auto_accept":true}'
    ( umask 077; mkdir -p "$CONNECTOR_SECRETS" )
    chmod 700 "$CONNECTOR_SECRETS"
    mv "$outbox"/*.token "$CONNECTOR_SECRETS/"
    rmdir "$outbox" 2>/dev/null || true
    chmod 600 "$CONNECTOR_SECRETS"/*.token
    note "provisioned: Claude Code (membership-scoped), claude-code-manual, and the connector's work principal; tokens in $CONNECTOR_SECRETS (not printed). Next: records.sh up, then connector.sh install" ;;
  rotate-session-key)
    # Rooms R3b rollback step (ROOMS_R3.md §3): a new cookie signing key ends
    # every browser session under any Records version, so a cookie revoked by
    # R3b cannot come back under older code. Records must be stopped first.
    require_absolute_root
    # Only a successful Docker answer proves Records is stopped or absent; a
    # query error (daemon, socket, permission) stops here (review B3-01).
    rc=0; state=$(docker inspect -f '{{.State.Running}}' "$RECORDS_CONTAINER" 2>&1) || rc=$?
    if [[ "$rc" == 0 ]]; then
      [[ "$state" == false ]] || die "stop Records first (records.sh down)"
    elif ! grep -qi "no such object" <<< "$state"; then
      die "cannot tell whether Records is running (docker query failed); nothing rotated"
    fi
    key="$STAGING_ROOT/data/records/session-key.txt"
    [[ -f "$key" && ! -L "$key" ]] || die "no session key at $key"
    ( umask 077; openssl rand -base64 36 > "$key.new" )
    chmod 600 "$key.new"
    mv "$key.new" "$key"
    note "rotated the Records session key: every browser sign-in ends (sign in again). Bearer clients are unaffected." ;;
  preflight)
    # Rooms R1 build gate (ROOMS_R1.md §6): MC's EFFECTIVE tools from its running
    # gateway, and the boundaries as real connection attempts from inside the
    # containers. Read-only; names and codes only.
    bad=0
    check() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; else echo "FAIL $1: expected [$3], found [$2]"; bad=1; fi; }
    nets() { docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$1" 2>/dev/null | xargs -n1 | sort | xargs; }
    ports() { docker inspect -f '{{json .HostConfig.PortBindings}}' "$1" 2>/dev/null | sed 's/^null$/{}/'; }
    oc() { docker exec minimoi-mc-agent node /app/openclaw.mjs gateway call "$1" --timeout 90000 --params "$2" --json 2>/dev/null || true; }
    jget() { docker exec -i minimoi-mc-agent node -e "let t='';process.stdin.on('data',c=>t+=c).on('end',()=>{let d={};try{d=JSON.parse(t.slice(t.indexOf('{')))}catch(e){};let v;try{v=($1)}catch(e){v='error'};console.log(typeof v==='string'?v:JSON.stringify(v))})"; }
    oc sessions.create '{"agentId":"mc-agent","key":"agent:mc-agent:rooms-preflight"}' >/dev/null
    tools=$(oc tools.effective '{"sessionKey":"agent:mc-agent:rooms-preflight"}' | jget "(d.groups||[]).flatMap(g=>(g.tools||[]).map(t=>t.id)).sort().join(',')")
    check "MC effective tools (running gateway)" "${tools:-none}" "session_status"
    allow=$(docker exec minimoi-mc-agent node -e '
      const c = require(process.env.OPENCLAW_CONFIG_PATH || "/home/node/.openclaw/openclaw.json");
      const found = new Set();
      (function walk(v) { if (v && typeof v === "object") { if (v.tools && Array.isArray(v.tools.allow)) found.add(JSON.stringify(v.tools.allow));
        for (const k of Object.keys(v)) walk(v[k]); } })(c);
      console.log([...found].join(" ") || "none");' 2>/dev/null || echo unreadable)
    check "MC configured tools.allow (every block; none is a failure)" "$allow" '["session_status"]'
    check "rooms-worker networks" "$(nets "$WORKER_CONTAINER")" "$MC_FRONT_NETWORK $RECORDS_NETWORK"
    check "records networks" "$(nets "$RECORDS_CONTAINER")" "$RECORDS_NETWORK"
    check "mc-agent networks" "$(nets minimoi-mc-agent)" "minimoi-staging-mc-net"
    for c in "$WORKER_CONTAINER" "$RECORDS_CONTAINER" minimoi-mc-agent minimoi-mc-relay; do check "$c published ports" "$(ports "$c")" "{}"; done
    check "rooms-worker read-only root" "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$WORKER_CONTAINER" 2>/dev/null)" "true"
    if docker inspect "$DOOR_CONTAINER" >/dev/null 2>&1; then
      # Rooms R2: the door is the only published port, on loopback only.
      check "records-door networks" "$(nets "$DOOR_CONTAINER")" "$RECORDS_NETWORK minimoi-staging-records-door"
      check "records-door published ports" "$(ports "$DOOR_CONTAINER")" '{"18881/tcp":[{"HostIp":"127.0.0.1","HostPort":"18881"}]}'
      check "records-door holds no credential" "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$DOOR_CONTAINER" | grep -ciE 'token|key|secret' || true)" "0"
      check "records-door read-only root" "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$DOOR_CONTAINER")" "true"
      check "door from the Mac, no credential (expect 401)" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18881/api/v1/me || echo curl_failed)" "401"
      lan=$(ipconfig getifaddr en0 2>/dev/null || true)
      if [[ -n "$lan" ]]; then
        rc=0; curl -s -o /dev/null --max-time 5 "http://$lan:18881/api/v1/me" || rc=$?
        check "door on the LAN address $lan (curl exit 7 = connection refused)" "$rc" "7"
      fi
    fi
    # One probe run inside a container. Outcomes are exact: an HTTP status,
    # "nxdomain" (the name did not resolve), "refused", "timeout", "no_route"
    # (the kernel had no route to that address), or "probe_error:..." /
    # "exec_failed" (the probe itself did not run). No single negative outcome
    # is called isolation: each forbidden path is judged on three observations
    # together, declared attachments (above), name resolution, and a direct
    # connection attempt to the service's own IP address.
    probe() {
      docker exec "$1" python -c "
import errno, socket, sys, urllib.error, urllib.request as u
url = sys.argv[1]; host = url.split('/')[2].split(':')[0]
try:
    socket.getaddrinfo(host, None)
except socket.gaierror:
    print('nxdomain'); sys.exit(0)
try:
    print(u.urlopen(url, timeout=4).status)
except urllib.error.HTTPError as e:
    print(e.code)
except urllib.error.URLError as e:
    r = e.reason
    if isinstance(r, ConnectionRefusedError): print('refused')
    elif isinstance(r, (TimeoutError, socket.timeout)): print('timeout')
    elif isinstance(r, OSError) and r.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH): print('no_route')
    else: print('probe_error:' + type(r).__name__)
except (TimeoutError, socket.timeout):
    print('timeout')
except OSError as e:
    print('no_route' if e.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH) else 'probe_error:' + type(e).__name__)
except Exception as e:
    print('probe_error:' + type(e).__name__)" "$2" 2>/dev/null || echo exec_failed
    }
    ip_on() { docker inspect -f "{{with index .NetworkSettings.Networks \"$2\"}}{{.IPAddress}}{{end}}" "$1" 2>/dev/null; }
    forbidden() {   # label, from-container, by-name URL, target container, target network, port, path
      local name addr ip
      name=$(probe "$2" "$3")
      ip=$(ip_on "$4" "$5")
      if [[ -z "$ip" ]]; then echo "FAIL $1: could not read $4's address on $5"; bad=1; return; fi
      addr=$(probe "$2" "http://$ip:$6$7")
      if [[ "$name" == nxdomain && ( "$addr" == timeout || "$addr" == no_route ) ]]; then
        echo "PASS $1: name not resolvable, address $ip not reachable ($addr)"
      else
        echo "FAIL $1: by name [$name], by address $ip [$addr]; expected nxdomain and timeout/no_route"; bad=1
      fi
    }
    # Positive controls first: they prove the probe runs and the allowed paths work.
    check "worker -> Records /health (allowed)" "$(probe "$WORKER_CONTAINER" http://minimoi-records:18880/health)" "200"
    check "worker -> MC relay /healthz (allowed)" "$(probe "$WORKER_CONTAINER" http://mc-relay:8790/healthz)" "200"
    forbidden "worker -> MC itself" "$WORKER_CONTAINER" http://mc-agent:18789/healthz minimoi-mc-agent minimoi-staging-mc-net 18789 /healthz
    forbidden "worker -> model gateway" "$WORKER_CONTAINER" http://model-gateway:4000/health minimoi-model-gateway minimoi-staging-mc-net 4000 /health
    forbidden "worker -> Postgres" "$WORKER_CONTAINER" http://postgres:5432/ postgres-ai-agents minimoi-staging_default 5432 /
    forbidden "Records -> MC relay" "$RECORDS_CONTAINER" http://mc-relay:8790/healthz minimoi-mc-relay minimoi-staging-mc-front 8790 /healthz
    # The portal shares records-net and mc-front with the worker by design
    # (ROOMS_R1.md §3.8): the boundary is that the worker holds no portal URL,
    # credential or code, not a network wall. Checked here, not probed.
    wenv=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$WORKER_CONTAINER" 2>/dev/null | grep -ci "portal\|5001\|dev.minimoi" || true)
    check "worker environment names no portal" "$wenv" "0"
    wcode=$(docker exec "$WORKER_CONTAINER" sh -c 'grep -rl "minimoi_portal" /app 2>/dev/null | wc -l' 2>/dev/null | tr -d ' ' || echo exec_failed)
    check "worker image holds no portal code" "$wcode" "0"
    [[ "$bad" == 0 ]] || die "preflight failed" ;;
  down)
    records_compose down ;;
  status)
    for c in "$RECORDS_CONTAINER" "$WORKER_CONTAINER"; do
      docker inspect -f '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$c" 2>/dev/null \
        || echo "$c: absent"
    done
    worker_ready && echo "rooms-worker secrets: provisioned" || echo "rooms-worker secrets: not provisioned (records.sh provision)" ;;
  *)
    sed -n '2,44p' "$0"; exit 2 ;;
esac
