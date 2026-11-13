#!/bin/bash
# guildhall_prod.sh: Guildhall on production's own host, on Robert's word. Switching it on or off is by hand; once it is on,
# every production release keeps it current (refresh, called by .github/workflows/deploy.yml, #550).
#
#   guildhall_prod.sh [check]   read-only. Every gate by name, then what comes next. Exit 0: the next step may run.
#   guildhall_prod.sh status    read-only. Exit 0 only when Guildhall is fully up and verified.
#   guildhall_prod.sh start     GUILDHALL_PROD_APPROVED=1. Resumable: each run continues from what already exists.
#   guildhall_prod.sh stop      GUILDHALL_PROD_APPROVED=1. Keeps state, keys, journals and the network.
#   guildhall_prod.sh refresh   GUILDHALL_PROD_APPROVED=1. The release's step: Records onto the running portal's digest, the
#                               workers onto MINIMOI_ROOMS_WORKER_IMAGE (when given), every gate first, verified after, and
#                               anything it changed put back if a step fails. A host where Guildhall is not on is skipped.
#                               Exit 0 refreshed, current or skipped; 1 refused, nothing changed; 5 failed and put back;
#                               6 failed and NOT fully put back (act now: status, then stop or start).
#
# start waits at one point (exit 3): the worker tokens are issued by Records itself, so Records must be up before
# scripts/operations/minimoi_1_2_prod_bind.py can write them. Run that, then run start again.
#
# Inputs, on the first start only:
#   MINIMOI_ROOMS_WORKER_IMAGE         digest printed by .github/workflows/rooms-worker-image.yml
#   MINIMOI_GUILDHALL_BACKUP_RECEIPT   private receipt of the backup taken for this release
# stop also needs GUILDHALL_RELEASE_TAG when no portal is running to read the tag from.
#
# Stays true: production_internal only, no Dev value, no public port on Records, authority and launch gates untouched.
# Never prints a value: output is gate names and relative file names. Child output goes to a 0600 log or nowhere.
set +x
set -uo pipefail
umask 077
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH

# Tests run the whole script under a sandbox folder. A host that has /opt/minimoi can never use one.
S="${GUILDHALL_PROD_SANDBOX:-}"
if [[ -n "$S" ]]; then
  if [[ -e /opt/minimoi || "$S" != /* ]]; then echo "refused: sandbox on a real host" >&2; exit 2; fi
fi

REGISTRY=332704997792.dkr.ecr.us-east-1.amazonaws.com
ROOT="$S/opt/minimoi"
ETC="$S/etc/minimoi"
BINDING="$ETC/records-production.json"
STOPPED_BINDING="$ETC/records-production.stopped.json"
PREVIOUS_BINDING="$ETC/records-production.previous.json"
NETWORK=minimoi-records-production-1
KEYS="$ROOT/secrets/records-production"
STATE_PARENT="$ROOT/state/records-production"
STATE="$STATE_PARENT/records"
INPUTS="$KEYS/prepare-inputs.json"
SECRETS="$ROOT/secrets/1-2"
STATE12="$ROOT/state/1-2"
ENV_FILE="$ROOT/guildhall.env"
PREVIOUS_ENV_FILE="$ROOT/guildhall.env.previous"
OBSERVATION="$ROOT/release-status/guildhall.json"
MAIN_ENV="$ROOT/.env"
WORKERS_FILE="$ROOT/docker-compose.guildhall-production.yml"
MAIN_LAYER="$ROOT/docker-compose.guildhall-production-main.yml"
HELD_LAYER="$ROOT/guildhall-kit/docker-compose.guildhall-production-main.yml"
ONE_TWO_LAYER="$ROOT/docker-compose.minimoi-1-2-production.yml"
DEPLOY="$ROOT/scripts/deploy_scoped_release.sh"
DEPLOYMENT="$ROOT/scripts/rooms/deployment.py"
PLAN="$ROOT/scripts/rooms/production_plan.py"
RECORDS_FILE="$ROOT/docker-compose.records-production.yml"
RECORDS_PORTAL_FILE="$ROOT/docker-compose.records-production-portal.yml"
LOG_DIR="$ROOT/operators/guildhall-logs"
WORKER_PROJECT=minimoi-guildhall-production
TRIES=30; [[ -n "$S" ]] && TRIES=1
GATE_MODE=start

# Written by minimoi_1_2_prod_setup.py apply.
SETUP_TOKENS="cos-guildhall/cos-service.token guildhall-mc/mc-service.token"
# Written by minimoi_1_2_prod_bind.py, after Records is up: Records issues the first ones, the gateway the discussion keys.
BOUND_TOKENS="guildhall-cos/rooms-worker-cos.token guildhall-cos/cos.token guildhall-cos/cos-service.token
guildhall-cos/discussion-gateway.token guildhall-mc/rooms-worker.token guildhall-mc/mc.token guildhall-mc/mc-relay.token
guildhall-mc/discussion-gateway.token cos-guildhall/meeting-observe.token mc-rooms-work/work.token"

read -r -d '' HELPER <<'PY'
import hashlib, json, os, re, secrets, stat, subprocess, sys
REGISTRY = "332704997792.dkr.ecr.us-east-1.amazonaws.com"
PORTAL_DIGEST = re.compile(re.escape(REGISTRY) + r"/minimoi/portal@sha256:[a-f0-9]{64}\Z")
WORKER_DIGEST = re.compile(re.escape(REGISTRY) + r"/minimoi/cos-scheduler@sha256:[a-f0-9]{64}\Z")
PORTAL_TAG = re.compile(re.escape(REGISTRY) + r"/minimoi/portal:([0-9a-f]{7})\Z")
SCHEDULER_TAG = re.compile(re.escape(REGISTRY) + r"/minimoi/cos-scheduler:([0-9a-f]{7})\Z")
ALLOWED = {"minimoi-records", "minimoi-portal", "minimoi-cos-scheduler", "minimoi-guildhall-cos", "minimoi-guildhall-mc"}
DEV = ("minimoi-staging", "_working", "/Users/", "/home/", "dev.minimoi")
ENV_KEYS = {"MINIMOI_ROOMS_WORKER_IMAGE", "MINIMOI_GUILDHALL_COS_SECRETS", "MINIMOI_GUILDHALL_COS_JOURNAL",
            "MINIMOI_GUILDHALL_MC_SECRETS", "MINIMOI_GUILDHALL_MC_JOURNAL", "MINIMOI_RECORDS_NETWORK"}
KEY_NAMES = {"owner": "owner.key", "session": "session.key", "portal": "portal-bridge.key"}


def fail(code=1):
    raise SystemExit(code)


def no_dev(value):
    if not isinstance(value, str):
        fail()
    # A test's sandbox folder may sit under a home directory; the production paths never do.
    sandbox = os.environ.get("GUILDHALL_PROD_SANDBOX", "")
    tail = value[len(sandbox):] if sandbox and value.startswith(sandbox) else value
    if any(token in tail for token in DEV):
        fail()


def private_info(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in {0, os.geteuid()}:
        fail()
    return info


def private_file(path):
    info = private_info(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size == 0:
        fail()


def write_private(path, raw):
    temporary = path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)


def read_env(path):
    values = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or key in values:
            fail()
        values[key] = value
    return values


def expected_env(root, network, image):
    return {"MINIMOI_ROOMS_WORKER_IMAGE": image,
            "MINIMOI_GUILDHALL_COS_SECRETS": root + "/secrets/1-2/guildhall-cos",
            "MINIMOI_GUILDHALL_COS_JOURNAL": root + "/state/1-2/guildhall-cos-journal",
            "MINIMOI_GUILDHALL_MC_SECRETS": root + "/secrets/1-2/guildhall-mc",
            "MINIMOI_GUILDHALL_MC_JOURNAL": root + "/state/1-2/guildhall-mc-journal",
            "MINIMOI_RECORDS_NETWORK": network}


def first(stdin):
    rows = json.loads(stdin)
    if not isinstance(rows, list) or len(rows) != 1:
        fail()
    return rows[0]


def main(command, *args):
    if command == "private":
        private_file(args[0])
    elif command == "private_dir":
        info = private_info(args[0])
        if not stat.S_ISDIR(info.st_mode):
            fail()
    elif command == "owner_dir":      # What deployment.py and production_activation.py both require of /etc/minimoi.
        info = os.lstat(args[0])
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid not in {0, os.geteuid()}:
            fail()
    elif command == "tree_private":   # No group or other bit anywhere under a folder.
        for folder, names, files in os.walk(args[0]):
            for path in [folder, *(os.path.join(folder, n) for n in names + files)]:
                private_info(path)
    elif command == "no_dev":
        no_dev(args[0])
    elif command == "receipt":
        no_dev(args[0])
        if not args[0].startswith("/"):
            fail()
        private_file(args[0])
    elif command == "worker_image":
        if not WORKER_DIGEST.fullmatch(args[0]):
            fail()
    elif command == "generate_keys":  # All new, or nothing: never reuses a value made for something else.
        folder = args[0]
        if any(os.path.lexists(os.path.join(folder, n)) for n in KEY_NAMES.values()):
            fail()
        raw = {"owner.key": secrets.token_urlsafe(48), "session.key": secrets.token_urlsafe(48),
               "portal-bridge.key": "prod-" + secrets.token_hex(4) + ":" + secrets.token_urlsafe(48)}
        for name, value in raw.items():
            write_private(os.path.join(folder, name), (value + "\n").encode())
    elif command == "write_inputs":   # Container paths: this folder is mounted read-only at /records-keys.
        folder, target = args
        pins = {key: hashlib.sha256(open(os.path.join(folder, name), "rb").read()).hexdigest()
                for key, name in KEY_NAMES.items()}
        body = {"destination": "/records-state/records", "runtime_data_dir": "/var/lib/minimoi-records",
                "owner_key_file": "/records-keys/owner.key", "session_key_file": "/records-keys/session.key",
                "portal_key_file": "/records-keys/portal-bridge.key", "key_sha256": pins}
        if os.path.lexists(target):
            os.unlink(target)
        write_private(target, json.dumps(body, sort_keys=True).encode())
    elif command == "write_binding":
        path, image, state, key, network, receipt = args
        body = {"schema": "minimoi.records-production-binding.v1",
                "inputs": {"image": image, "state": state, "portal_key": key, "network": network,
                           "backup_receipt": receipt}}
        write_private(path, json.dumps(body, sort_keys=True).encode())
    elif command == "check_binding":
        path, state, key, network = args
        private_file(path)
        document = json.load(open(path, encoding="utf-8"))
        inputs = document.get("inputs") if isinstance(document, dict) else None
        if (not isinstance(inputs, dict) or set(document) != {"schema", "inputs"}
                or document["schema"] != "minimoi.records-production-binding.v1"
                or set(inputs) != {"image", "state", "portal_key", "network", "backup_receipt"}):
            fail()
        for value in inputs.values():
            no_dev(value)
        if (not PORTAL_DIGEST.fullmatch(inputs["image"]) or inputs["state"] != state or inputs["portal_key"] != key
                or inputs["network"] != network):
            fail()
    elif command == "binding_receipt":
        print(json.load(open(args[0], encoding="utf-8"))["inputs"]["backup_receipt"])
    elif command == "binding_image":   # A digest: not a secret.
        image = json.load(open(args[0], encoding="utf-8"))["inputs"]["image"]
        if not PORTAL_DIGEST.fullmatch(image):
            fail()
        print(image)
    elif command == "rebind":          # The same binding on another reviewed portal digest; every other input kept.
        path, image = args
        if not PORTAL_DIGEST.fullmatch(image):
            fail()
        document = json.load(open(path, encoding="utf-8"))
        document["inputs"]["image"] = image
        write_private(path, json.dumps(document, sort_keys=True).encode())
    elif command == "env_image":
        image = read_env(args[0]).get("MINIMOI_ROOMS_WORKER_IMAGE", "")
        if not WORKER_DIGEST.fullmatch(image):
            fail()
        print(image)
    elif command == "observe":         # Nonsecret: what the delivery record shows for Guildhall. Digests, a tag, a result.
        path, result, tag, records, worker, changed = args
        commit = os.environ.get("MINIMOI_RELEASE_COMMIT", "")
        body = {"format": "minimoi.guildhall-observation/1", "result": result, "release_tag": tag,
                "release_commit": commit if re.fullmatch(r"[0-9a-f]{40}", commit) else "",
                "records_image": records.rpartition("@")[2], "worker_image": worker.rpartition("@")[2],
                "changed": [c for c in changed.split(",") if c],
                "observed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")}
        if not os.path.isdir(os.path.dirname(path)):
            return
        temporary = path + ".tmp"
        if os.path.lexists(temporary):
            os.unlink(temporary)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(body, sort_keys=True) + "\n")
        os.chmod(temporary, 0o644)
        os.rename(temporary, path)
    elif command == "write_env":
        path, root, network, image = args
        if not WORKER_DIGEST.fullmatch(image):
            fail()
        lines = "".join(f"{k}={v}\n" for k, v in sorted(expected_env(root, network, image).items()))
        if os.path.lexists(path):
            os.unlink(path)
        write_private(path, lines.encode())
    elif command == "check_env":
        path, root, network = args
        private_file(path)
        values = read_env(path)
        if set(values) != ENV_KEYS or not WORKER_DIGEST.fullmatch(values["MINIMOI_ROOMS_WORKER_IMAGE"]):
            fail()
        for value in values.values():
            no_dev(value)
        if values != expected_env(root, network, values["MINIMOI_ROOMS_WORKER_IMAGE"]):
            fail()
    elif command == "merge_main_env":  # Nonsecret references and one hash. Only these keys change; never logged.
        path, root, mc_token = args
        digest = hashlib.sha256(open(mc_token, "rb").read().strip()).hexdigest()
        wanted = {"MINIMOI_COS_GUILDHALL_SECRETS": root + "/secrets/1-2/cos-guildhall",
                  "MINIMOI_COS_GUILDHALL_STATE": root + "/state/1-2/cos-guildhall",
                  "MINIMOI_MC_ROOMS_WORK_SECRETS": root + "/secrets/1-2/mc-rooms-work",
                  "MINIMOI_MC_ROOMS_TOKEN_SHA256": digest}
        before = os.stat(path)
        kept = [line for line in open(path, encoding="utf-8").read().splitlines()
                if line.partition("=")[0] not in wanted]
        text = "\n".join(kept + [f"{k}={v}" for k, v in sorted(wanted.items())]) + "\n"
        temporary = path + ".guildhall.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, stat.S_IMODE(before.st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(before.st_mode))
        os.chown(temporary, before.st_uid, before.st_gid)
        os.rename(temporary, path)
    elif command == "main_env_ready":
        path, root, mc_token = args
        values = read_env_loose(path)
        if (values.get("MINIMOI_COS_GUILDHALL_SECRETS") != root + "/secrets/1-2/cos-guildhall"
                or values.get("MINIMOI_COS_GUILDHALL_STATE") != root + "/state/1-2/cos-guildhall"
                or values.get("MINIMOI_MC_ROOMS_WORK_SECRETS") != root + "/secrets/1-2/mc-rooms-work"
                or values.get("MINIMOI_MC_ROOMS_TOKEN_SHA256") != hashlib.sha256(open(mc_token, "rb").read().strip()).hexdigest()):
            fail()
    elif command == "health":          # stdin: docker inspect of one container.
        state = first(sys.stdin.read()).get("State") or {}
        print((state.get("Health") or {}).get("Status") or ("running" if state.get("Running") else "stopped"))
    elif command == "running":
        if (first(sys.stdin.read()).get("State") or {}).get("Running") is not True:
            fail()
    elif command == "portal_tag":
        match = PORTAL_TAG.fullmatch((first(sys.stdin.read()).get("Config") or {}).get("Image", ""))
        if not match:
            fail()
        print(match.group(1))
    elif command == "scheduler_tag":
        match = SCHEDULER_TAG.fullmatch((first(sys.stdin.read()).get("Config") or {}).get("Image", ""))
        if not match:
            fail()
        print(match.group(1))
    elif command == "portal_digest":   # stdin: docker image inspect.
        found = [d for d in first(sys.stdin.read()).get("RepoDigests") or [] if PORTAL_DIGEST.fullmatch(d)]
        if len(set(found)) != 1:
            fail()
        print(found[0])
    elif command == "wired":           # The same three signs deployment.py reads.
        row = first(sys.stdin.read())
        env = dict(s.split("=", 1) for s in (row.get("Config") or {}).get("Env") or [] if "=" in s)
        wired = (env.get("MINIMOI_RECORDS_PRODUCTION") == "1"
                 or any(m.get("Destination") == "/run/secrets/rooms-bridge/portal-bridge.key" for m in row.get("Mounts") or [])
                 or any(n.startswith("minimoi-records-production-") for n in (row.get("NetworkSettings") or {}).get("Networks") or {}))
        if not wired:
            fail()
    elif command == "has_env":         # Name and value are fixed flags, never secrets.
        row = first(sys.stdin.read())
        if args[0] not in ((row.get("Config") or {}).get("Env") or []):
            fail()
    elif command == "no_ports":
        row = first(sys.stdin.read())
        host = row.get("HostConfig") or {}
        published = [v for v in ((row.get("NetworkSettings") or {}).get("Ports") or {}).values() if v]
        if host.get("PortBindings") or host.get("PublishAllPorts") or published:
            fail()
    elif command == "network":         # stdin: docker network inspect. args: name, then members that must be there.
        row = first(sys.stdin.read())
        members = {m.get("Name") for m in (row.get("Containers") or {}).values()}
        if row.get("Name") != args[0] or row.get("Internal") is not True or not members <= ALLOWED or not set(args[1:]) <= members:
            fail()
    elif command == "run_plan":        # stdin: deployment.py --backend-plan. Runs the fixed command it answered with.
        plan = json.loads(sys.stdin.read())
        argv, references = plan["argv"], plan["environment_references"]
        if plan.get("activated") is not False or plan.get("plan_only") is not True:
            fail()
        preflight = argv[:2] == ["docker", "run"] and "--network=none" in argv and "--pull=never" in argv
        start = argv[:3] == ["docker-compose", "--project-name", "minimoi-records-production"] and "--pull" in argv
        if args[0] == "preflight" and not preflight or args[0] == "start" and not start:
            fail()
        if set(references) != {"MINIMOI_RECORDS_IMAGE", "MINIMOI_RECORDS_STATE", "MINIMOI_RECORDS_PORTAL_KEY", "MINIMOI_RECORDS_NETWORK"}:
            fail()
        code = subprocess.run(argv, env={**os.environ, **references}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300).returncode
        if code:
            fail()
    else:
        fail(2)


def read_env_loose(path):
    values = {}
    for line in open(path, encoding="utf-8"):
        key, sep, value = line.rstrip("\n").partition("=")
        if sep:
            values[key] = value
    return values


try:
    main(*sys.argv[1:])
except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, subprocess.SubprocessError):
    raise SystemExit(1)
PY

H() { python3 -I -c "$HELPER" "$@" 2>/dev/null; }

say() { printf '%s\n' "$*"; }
FAILS=0
ok()      { say "  ok       $1"; }
info()    { say "  $1"; }
bad()     { say "  REFUSED  $1"; FAILS=$((FAILS + 1)); }
die()     { say "refused: $1" >&2; exit "${2:-1}"; }

short() { printf '%s' "${1#"$ROOT"/}"; }
# Never grep -q on a pipe: it exits early, the writer dies of SIGPIPE, and pipefail turns a match into a failure.
container_exists() { docker ps -a --format '{{.Names}}' 2>/dev/null | grep -x "$1" >/dev/null; }
inspect() { docker inspect "$1" 2>/dev/null; }
portal_env_ok() { python3 "$DEPLOYMENT" --portal-env >/dev/null 2>&1; }
backend_plan() { python3 "$DEPLOYMENT" --backend-plan "$1" 2>/dev/null; }

records_health() {
  container_exists minimoi-records || { echo absent; return 0; }
  inspect minimoi-records | H health || echo unknown
}
portal_wired() { container_exists minimoi-portal && inspect minimoi-portal | H wired; }
network_exists() { docker network inspect "$NETWORK" >/dev/null 2>&1; }
network_ok() { docker network inspect "$NETWORK" 2>/dev/null | H network "$NETWORK" "$@"; }

release_tag() { inspect minimoi-portal | H portal_tag; }
portal_digest() {
  local image
  image=$(inspect minimoi-portal | H portal_tag) || return 1
  docker image inspect "$REGISTRY/minimoi/portal:$image" 2>/dev/null | H portal_digest
}

keys_state() {   # none | all | partial
  local n=0 name
  for name in owner.key session.key portal-bridge.key; do [[ -e "$KEYS/$name" ]] && n=$((n + 1)); done
  case "$n" in 0) echo none ;; 3) echo all ;; *) echo partial ;; esac
}
state_state() {  # none | complete | incomplete
  [[ -e "$STATE" ]] || { echo none; return 0; }
  if [[ -e "$STATE/INCOMPLETE" || ! -f "$STATE/COMPLETE" ]]; then echo incomplete; else echo complete; fi
}

missing_tokens() {
  local name
  for name in $BOUND_TOKENS; do
    H private "$SECRETS/$name" || echo "secrets/1-2/$name"
  done
}
tokens_agree() {
  cmp -s "$SECRETS/guildhall-cos/cos-service.token" "$SECRETS/cos-guildhall/cos-service.token"
}
guildhall_on_main_project() {
  inspect minimoi-portal | H has_env MINIMOI_MC_ROOMS_PRODUCTION=1 &&
    inspect minimoi-cos-scheduler | H has_env MINIMOI_GUILDHALL_COS_PRODUCTION=1
}
workers_running() {
  local name
  for name in minimoi-guildhall-cos minimoi-guildhall-mc; do
    container_exists "$name" && inspect "$name" | H running || return 1
  done
}
worker_logged_profile() {   # Looks for a fixed marker; the log line itself is never shown.
  docker logs "$1" 2>&1 | grep -F '"profile": "production_internal"' >/dev/null
}

require_root_and_production() {
  [[ "$(id -u)" == 0 ]] || die "run as root on the production host" 2
  local dev
  for dev in "$S"/opt/minimoi-staging "$S"/home/*/minimoi-staging; do
    [[ -e "$dev" ]] && die "this is a Dev host, not production" 2
  done
  [[ -f "$MAIN_ENV" ]] || die "no $(short "$MAIN_ENV"): not a prepared production host" 2
  docker ps -a --format '{{.Names}}' >/dev/null 2>&1 || die "cannot read the local Docker daemon" 2
}

# Every read-only gate. FAILS counts what stops the next step. Sets NEXT.
gates() {
  local path binding_state keys state health wired=no tag sched

  say "layers and files"
  if [[ -f "$ONE_TWO_LAYER" ]]; then ok "1.2 layer"; else bad "1.2 layer missing: $(short "$ONE_TWO_LAYER")"; fi
  if grep -q 'docker-compose.minimoi-1-2-production.yml' "$DEPLOY" 2>/dev/null; then ok "release script reads the 1.2 layer"
  else bad "release script does not read the 1.2 layer: every release would strip CoS's settings"; fi
  for path in "$DEPLOYMENT" "$PLAN" "$RECORDS_FILE" "$RECORDS_PORTAL_FILE" "$WORKERS_FILE"; do
    if [[ -f "$path" ]]; then ok "$(short "$path")"; else bad "missing $(short "$path")"; fi
  done
  if [[ -f "$HELD_LAYER" || -f "$MAIN_LAYER" ]]; then ok "Guildhall main layer available"
  else bad "missing $(short "$HELD_LAYER")"; fi
  if [[ -d "$SECRETS" && -d "$STATE12" ]]; then ok "1.2 folders"; else bad "1.2 folders missing: run minimoi_1_2_prod_setup.py apply"; fi

  say "portal"
  if ! container_exists minimoi-portal; then
    bad "no portal container: its digest is what Records runs on"
  else
    tag=$(release_tag) || tag=""
    sched=$(inspect minimoi-cos-scheduler | H scheduler_tag) || sched=""
    if [[ -n "$tag" ]]; then ok "portal runs an immutable release tag"; else bad "portal is not on a release tag"; fi
    if [[ -n "$tag" && "$tag" == "$sched" ]]; then ok "CoS scheduler is on the same release"
    elif [[ "$GATE_MODE" == refresh && -n "$sched" ]]; then
      # start redeploys both on the portal's tag; a refresh redeploys neither, and a scoped release may move one alone.
      info "pending  CoS scheduler is on another release tag: a refresh redeploys neither"
    else bad "CoS scheduler is not on the portal's release"; fi
    if portal_digest >/dev/null; then ok "portal digest"; else bad "portal digest not found in minimoi/portal"; fi
    if portal_wired; then wired=yes; fi
  fi

  say "keys and state"
  keys=$(keys_state); state=$(state_state)
  case "$keys" in
    none) info "pending  Records keys: start makes them" ;;
    all)
      if H private_dir "$KEYS" && H private "$KEYS/owner.key" && H private "$KEYS/session.key" && H private "$KEYS/portal-bridge.key"
      then ok "Records keys are private"; else bad "Records keys are not private"; fi ;;
    *) bad "only some Records keys exist: inspect $(short "$KEYS")" ;;
  esac
  case "$state" in
    none)
      info "pending  Records state: start prepares it"
      [[ "$keys" == all ]] && info "pending  keys exist with no state: start prepares from them"
      if python3 -I -c 'import os,sys
s=os.statvfs(sys.argv[1]);sys.exit(0 if s.f_bavail*s.f_frsize>=(5<<30)+(16<<20) else 1)' "$ROOT" 2>/dev/null
      then ok "disk reserve for Records"; else bad "under the 5 GB Records reserve"; fi ;;
    complete)
      if [[ "$keys" != all ]]; then bad "Records state without its keys"
      elif H tree_private "$STATE"; then ok "Records state is prepared and private"
      else bad "Records state is not private"; fi ;;
    *) bad "Records state is incomplete: it is never retried, inspect $(short "$STATE")" ;;
  esac

  say "binding"
  if [[ -e "$ETC" ]]; then
    if H owner_dir "$ETC"; then ok "/etc/minimoi is owner-only"; else bad "/etc/minimoi must be owned by root, mode 0700"; fi
  fi
  health=$(records_health)
  if [[ -e "$BINDING" ]]; then
    binding_state=present
    if H check_binding "$BINDING" "$STATE" "$KEYS/portal-bridge.key" "$NETWORK"; then ok "binding"
    else bad "binding is not this host's production binding"; fi
    if backend_plan status >/dev/null; then ok "deployment.py admits the binding"
    else bad "deployment.py refuses the binding"; fi
  elif [[ "$wired" == yes || "$health" != absent ]]; then
    binding_state=orphaned; bad "binding missing while Records or a wired portal exists"
  elif [[ -e "$STOPPED_BINDING" ]]; then
    binding_state=stopped; info "pending  binding: start restores it after a stop"
  elif [[ "$state" != none || "$keys" != none ]]; then
    binding_state=orphaned; bad "binding missing while keys or state exist, and no record of a stop"
  else
    binding_state=fresh; info "pending  binding: start writes it"
  fi
  if [[ "$binding_state" == fresh || "$binding_state" == stopped ]]; then
    if [[ "$binding_state" == fresh || -n "${MINIMOI_GUILDHALL_BACKUP_RECEIPT:-}" ]]; then
      if [[ -z "${MINIMOI_GUILDHALL_BACKUP_RECEIPT:-}" ]]; then bad "MINIMOI_GUILDHALL_BACKUP_RECEIPT is required"
      elif H receipt "$MINIMOI_GUILDHALL_BACKUP_RECEIPT"; then ok "backup receipt"
      else bad "backup receipt must be a private production file, not a Dev path"; fi
    fi
    if portal_env_ok; then ok "deployment.py: nothing wired"; else bad "deployment.py --portal-env refuses"; fi
  fi

  say "network"
  if network_exists; then
    if network_ok; then ok "network is internal with only allowed members"
    else bad "network is not internal, or has a member that is not allowed"; fi
  else
    info "pending  network: start creates it"
  fi

  say "Records"
  case "$health" in
    healthy)
      ok "Records is healthy"
      if inspect minimoi-records | H no_ports; then ok "no public port on Records"; else bad "Records publishes a port"; fi
      if portal_env_ok; then ok "--portal-env"; else bad "--portal-env refuses"; fi ;;
    absent) info "pending  Records: start runs it" ;;
    *) info "pending  Records is $health: start brings it back (the portal's next release refuses until then)" ;;
  esac
  # A stopped Records with its binding is a reboot, and start brings it back. Anything else is a layer placed too early.
  if [[ -f "$MAIN_LAYER" && ( "$health" == absent || ! -e "$BINDING" ) ]]; then
    bad "$(short "$MAIN_LAYER") is placed but Records is not healthy: stop first"
  fi

  say "tokens"
  for path in $SETUP_TOKENS; do
    if H private "$SECRETS/$path"; then ok "secrets/1-2/$path"; else bad "missing secrets/1-2/$path (setup apply)"; fi
  done
  local missing
  missing=$(missing_tokens)
  if [[ "$health" == healthy && -n "$missing" ]]; then
    while read -r path; do bad "missing $path (minimoi_1_2_prod_bind.py)"; done <<< "$missing"
  elif [[ -n "$missing" ]]; then
    info "pending  worker tokens: minimoi_1_2_prod_bind.py, once Records is up"
  else
    ok "worker tokens"
    if tokens_agree; then ok "CoS service token agrees"; else bad "guildhall-cos and cos-guildhall hold different cos-service tokens"; fi
  fi

  say "workers"
  if [[ -e "$ENV_FILE" ]]; then
    if H check_env "$ENV_FILE" "$ROOT" "$NETWORK"; then ok "guildhall.env"
    else bad "guildhall.env is not this host's: wrong key, wrong path, a Dev value, or no immutable worker digest"; fi
  elif [[ -z "${MINIMOI_ROOMS_WORKER_IMAGE:-}" ]]; then
    bad "MINIMOI_ROOMS_WORKER_IMAGE is required (the digest rooms-worker-image.yml printed)"
  elif H worker_image "$MINIMOI_ROOMS_WORKER_IMAGE"; then ok "worker image is an immutable digest"
  else bad "MINIMOI_ROOMS_WORKER_IMAGE is not an immutable production digest"; fi
  if workers_running; then ok "both workers running"; else info "pending  workers: start runs them"; fi
}

verify_up() {
  local fails=0 name
  [[ "$(records_health)" == healthy ]] || { say "  REFUSED  Records is not healthy"; fails=1; }
  inspect minimoi-records | H no_ports || { say "  REFUSED  Records publishes a port"; fails=1; }
  network_ok minimoi-records minimoi-portal minimoi-cos-scheduler minimoi-guildhall-cos minimoi-guildhall-mc ||
    { say "  REFUSED  network is not internal with exactly the allowed members"; fails=1; }
  portal_env_ok || { say "  REFUSED  deployment.py --portal-env"; fails=1; }
  workers_running || { say "  REFUSED  a worker is not running"; fails=1; }
  for name in minimoi-guildhall-cos minimoi-guildhall-mc; do
    worker_logged_profile "$name" || { say "  REFUSED  $name has not logged production_internal"; fails=1; }
  done
  return "$fails"
}

cmd_check() {
  gates
  say ""
  if (( FAILS > 0 )); then say "result: $FAILS refused"; return 1; fi
  if workers_running && verify_up >/dev/null; then say "result: Guildhall is up"; else say "result: start may run"; fi
  return 0
}

cmd_status() {
  say "Records: $(records_health)"
  say "binding: $([[ -e "$BINDING" ]] && echo present || echo absent)"
  say "main layer: $([[ -f "$MAIN_LAYER" ]] && echo placed || echo held)"
  say "portal wired: $(portal_wired && echo yes || echo no)"
  say "network: $(network_exists && echo exists || echo absent)"
  say "workers: $(workers_running && echo running || echo 'not running')"
  if verify_up; then say "result: Guildhall is up"; return 0; fi
  say "result: not up"
  return 1
}

require_approved() {
  [[ "${GUILDHALL_PROD_APPROVED:-}" == 1 ]] || die "GUILDHALL_PROD_APPROVED=1 is required: this changes production, on Robert's word" 2
}

wait_records_healthy() {
  local i
  for (( i = 0; i < TRIES; i++ )); do
    [[ "$(records_health)" == healthy ]] && return 0
    (( TRIES > 1 )) && sleep 3
  done
  return 1
}

private_log() {
  mkdir -p "$LOG_DIR" && chmod 700 "$LOG_DIR"
  printf '%s/%s-%s.log' "$LOG_DIR" "$1" "$(date -u +%Y%m%dT%H%M%SZ)"
}

prepare_records() {
  local digest="$1"
  mkdir -p "$KEYS" "$STATE_PARENT" && chmod 700 "$KEYS" "$STATE_PARENT" || die "cannot make the Records folders"
  if [[ "$(keys_state)" == none ]]; then
    H generate_keys "$KEYS" || die "could not make the Records keys"
    say "made: Records keys (not shown)"
  fi
  if [[ "$(state_state)" == none ]]; then
    H write_inputs "$KEYS" "$INPUTS" || die "could not write the preparation inputs"
    docker run --rm --pull=never --user 0 --network=none --read-only --tmpfs /tmp:size=16m,mode=1777 \
      --cap-drop=ALL --security-opt=no-new-privileges:true --memory=384m --pids-limit=64 \
      --env PYTHONDONTWRITEBYTECODE=1 --env AWS_EC2_METADATA_DISABLED=true \
      --mount "type=bind,src=$KEYS,dst=/records-keys,readonly" \
      --mount "type=bind,src=$STATE_PARENT,dst=/records-state" \
      "$digest" python -m services.records_prepare --inputs /records-keys/prepare-inputs.json --execute >/dev/null 2>&1 ||
      die "records_prepare refused. It is never retried: inspect $(short "$STATE")"
    [[ "$(state_state)" == complete ]] || die "records_prepare did not finish"
    H tree_private "$STATE" || die "prepared state is not private"
    say "prepared: empty Records state"
  fi
}

ensure_network() {
  if ! network_exists; then
    docker network create --internal "$NETWORK" >/dev/null 2>&1 || die "could not create the network"
    say "made: internal network"
  fi
  network_ok || die "network is not internal, or has a member that is not allowed"
}

write_binding() {
  local digest="$1" receipt="${MINIMOI_GUILDHALL_BACKUP_RECEIPT:-}"
  [[ -e "$BINDING" ]] && return 0
  if [[ -z "$receipt" && -e "$STOPPED_BINDING" ]]; then receipt=$(H binding_receipt "$STOPPED_BINDING") || receipt=""; fi
  H receipt "$receipt" || die "backup receipt is not a private production file"
  mkdir -p "$ETC" && chmod 700 "$ETC" || die "cannot make /etc/minimoi"
  H write_binding "$BINDING" "$digest" "$STATE" "$KEYS/portal-bridge.key" "$NETWORK" "$receipt" || die "could not write the binding"
  say "wrote: binding"
}

start_records() {
  if [[ "$(records_health)" != healthy ]]; then
    backend_plan preflight | H run_plan preflight || die "Records preflight refused"
    say "passed: Records preflight"
    backend_plan start | H run_plan start || die "Records did not start"
    wait_records_healthy || die "Records did not become healthy"
    say "up: Records"
  fi
  portal_env_ok || die "deployment.py --portal-env refuses Records"
}

deploy_main_project() {
  local tag log
  guildhall_on_main_project && [[ -f "$MAIN_LAYER" ]] && return 0
  tag=$(release_tag) || die "portal is not on a release tag"
  H merge_main_env "$MAIN_ENV" "$ROOT" "$SECRETS/guildhall-mc/mc-service.token" || die "could not update .env"
  H main_env_ready "$MAIN_ENV" "$ROOT" "$SECRETS/guildhall-mc/mc-service.token" || die ".env does not hold Guildhall's values"
  install -m 0644 "$HELD_LAYER" "$MAIN_LAYER" || die "could not place the main layer"
  log=$(private_log deploy)
  if ! bash "$DEPLOY" "$tag" cos-scheduler portal >"$log" 2>&1; then
    die "the release failed and may be half done; log in $(short "$log"). Run stop to go back." 5
  fi
  say "released: CoS scheduler and portal, with Guildhall's layer"
}

# Robert's own row in Rooms' access list, through the portal that reads it. seed_owner never overwrites a list that exists,
# so a re-run, or a list changed since, stays as it is. Without this row he is refused Rooms on a working Guildhall.
owner_access_ok() {
  docker exec -w /app minimoi-portal python3 -c '
import os
from minimoi_portal import rooms_identity as R
folder = os.environ["MINIMOI_ROOMS_ACCESS_DIR"]
os.makedirs(folder, mode=0o700, exist_ok=True)
access = R.RoomsAccess(folder)
access.seed_owner("account:robert", "robert", granted_by="guildhall_prod-start")
raise SystemExit(0 if access.decide("account:robert").ok else 1)
' >/dev/null 2>&1
}
ensure_owner_access() {
  owner_access_ok || die "Robert's Rooms access could not be confirmed. Nothing was undone: read the access list, or run stop." 6
  say "confirmed: Robert's Rooms access"
}

WORKER_LOG=""
workers_up() {   # 1: ECR login failed. 2: compose refused. Output goes to a private log only.
  WORKER_LOG=$(private_log workers)
  if ! aws ecr get-login-password --region us-east-1 2>/dev/null | docker login --username AWS --password-stdin "$REGISTRY" >"$WORKER_LOG" 2>&1; then
    return 1
  fi
  docker-compose --project-name "$WORKER_PROJECT" --project-directory "$ROOT" --env-file "$ENV_FILE" \
    -f "$WORKERS_FILE" --profile guildhall-production up -d >>"$WORKER_LOG" 2>&1 || return 2
}

start_workers() {
  local code=0
  if [[ ! -e "$ENV_FILE" ]]; then
    H write_env "$ENV_FILE" "$ROOT" "$NETWORK" "${MINIMOI_ROOMS_WORKER_IMAGE:-}" || die "could not write guildhall.env"
    say "wrote: guildhall.env"
  fi
  H check_env "$ENV_FILE" "$ROOT" "$NETWORK" || die "guildhall.env is not this host's"
  workers_up || code=$?
  (( code == 1 )) && die "ECR login failed"
  (( code == 2 )) && die "the workers did not start; log in $(short "$WORKER_LOG")"
  say "up: workers"
}

wait_verified() {
  local i
  for (( i = 0; i < TRIES; i++ )); do
    verify_up >/dev/null && return 0
    (( TRIES > 1 )) && sleep 3
  done
  verify_up
}

cmd_start() {
  require_approved
  # A wait for tokens is not a refusal here: it is the point start stops at, below.
  local failures digest missing
  failures=$(gates | grep 'REFUSED' | grep -v 'minimoi_1_2_prod_bind.py' || true)
  if [[ -n "$failures" ]]; then
    printf '%s\n' "$failures"
    die "the gates refused; nothing changed"
  fi
  digest=$(portal_digest) || die "portal digest not found"

  prepare_records "$digest"
  ensure_network
  write_binding "$digest"
  rm -f "$STOPPED_BINDING"
  start_records

  missing=$(missing_tokens)
  if [[ -n "$missing" ]]; then
    say "waiting: Records is up. Run minimoi_1_2_prod_bind.py, then start again. Expected:"
    printf '  %s\n' $missing
    exit 3
  fi
  tokens_agree || die "guildhall-cos and cos-guildhall hold different cos-service tokens"

  deploy_main_project
  ensure_owner_access
  start_workers
  wait_verified || die "started, but not verified. Nothing was undone. Read status, or run stop." 6
  say "result: Guildhall is up. Prove it with one real meeting: named turn, open question, stop, reload."
}

# ---- refresh: the release's own step, once Guildhall is on (#550) -------------------------------------------------------
# The merge that starts the release is Robert's word for this. It changes only what the release changed: Records follows
# the portal's digest (Records runs that image), the workers follow the worker image CI built. Every gate runs first; a
# refusal changes nothing. Once something moved, any failure puts it back and verifies, so a release never leaves a
# half-refreshed Guildhall behind silently: the deploy fails with what happened, and guildhall.json records it.
R_TAG=""; R_RECORDS=""; R_WORKER=""; R_CHANGED=""; R_RECORDS_MOVED=0; R_WORKERS_MOVED=0

short_digest() { local d="${1##*@}"; printf '%s' "${d:0:19}"; }

finish() {   # RESULT EXIT-CODE MESSAGE. The one way out of a refresh.
  H observe "$OBSERVATION" "$1" "$R_TAG" "$R_RECORDS" "$R_WORKER" "$R_CHANGED" || say "  (could not write $(short "$OBSERVATION"))"
  say "guildhall-result: $1"
  say "result: $3"
  exit "$2"
}

put_back() {   # Newest change first. 0: what moved is back, and Guildhall verifies.
  local fails=0 code=0
  if (( R_WORKERS_MOVED )); then
    if mv -f "$PREVIOUS_ENV_FILE" "$ENV_FILE" && { workers_up || code=$?; (( code == 0 )); }; then
      R_WORKER=$(H env_image "$ENV_FILE") || R_WORKER=""
      say "put back: workers on $(short_digest "$R_WORKER")"
    else
      say "  REFUSED  the workers could not be put back${WORKER_LOG:+; log in $(short "$WORKER_LOG")}"; fails=1
    fi
  fi
  if (( R_RECORDS_MOVED )); then
    if mv -f "$PREVIOUS_BINDING" "$BINDING" && backend_plan start | H run_plan start && wait_records_healthy && portal_env_ok; then
      R_RECORDS=$(H binding_image "$BINDING") || R_RECORDS=""
      say "put back: Records on $(short_digest "$R_RECORDS")"
    else
      say "  REFUSED  Records could not be put back on its previous image"; fails=1
    fi
  fi
  (( fails == 0 )) && wait_verified && return 0
  return 1
}

refresh_failed() {   # MESSAGE
  say "  FAILED   $1"
  if put_back; then
    finish rolled_back 5 "the refresh failed ($1). What it changed is back and Guildhall verifies; the release is not fully done."
  fi
  finish rollback_failed 6 "the refresh failed ($1) and Guildhall is NOT verified. Act now: guildhall_prod.sh status, then stop or start."
}

refresh_records() {   # TARGET portal digest
  local target="$1"
  if [[ "$R_RECORDS" != "$target" ]]; then
    cp -p "$BINDING" "$PREVIOUS_BINDING" || finish refused 1 "could not keep the previous binding; nothing changed"
    if ! H rebind "$BINDING" "$target"; then
      mv -f "$PREVIOUS_BINDING" "$BINDING"
      finish refused 1 "could not write the binding for the new image; nothing changed"
    fi
    if ! backend_plan preflight | H run_plan preflight; then
      mv -f "$PREVIOUS_BINDING" "$BINDING"
      finish refused 1 "Records' preflight refused the release's portal image; Records stays on $(short_digest "$R_RECORDS"), nothing changed"
    fi
    say "passed: Records preflight on the release's portal image"
    R_RECORDS_MOVED=1; R_RECORDS="$target"; R_CHANGED="records"
    backend_plan start | H run_plan start || refresh_failed "Records did not start on the release's portal image"
  elif [[ "$(records_health)" != healthy ]]; then
    backend_plan start | H run_plan start || refresh_failed "Records did not start"
  fi
  wait_records_healthy || refresh_failed "Records did not become healthy"
  portal_env_ok || refresh_failed "deployment.py --portal-env refuses Records"
  say "up: Records on $(short_digest "$R_RECORDS")"
}

refresh_workers() {   # IMAGE worker digest
  local image="$1" code=0
  if [[ "$image" != "$R_WORKER" ]]; then
    cp -p "$ENV_FILE" "$PREVIOUS_ENV_FILE" || refresh_failed "could not keep the previous guildhall.env"
    R_WORKERS_MOVED=1
    H write_env "$ENV_FILE" "$ROOT" "$NETWORK" "$image" || refresh_failed "could not write guildhall.env"
    R_WORKER="$image"; R_CHANGED="${R_CHANGED:+$R_CHANGED,}workers"
  fi
  H check_env "$ENV_FILE" "$ROOT" "$NETWORK" || refresh_failed "guildhall.env is not this host's"
  workers_up || code=$?
  (( code == 1 )) && refresh_failed "ECR login failed"
  (( code == 2 )) && refresh_failed "the workers did not start; log in $(short "$WORKER_LOG")"
  say "up: workers on $(short_digest "$R_WORKER")"
}

cmd_refresh() {
  require_approved
  GATE_MODE=refresh
  local report failures target worker
  # Not on here: a release never switches Guildhall on or back on. That stays start's, by hand.
  if [[ ! -e "$BINDING" && ! -f "$MAIN_LAYER" ]] && ! container_exists minimoi-records \
     && ! container_exists minimoi-guildhall-cos && ! container_exists minimoi-guildhall-mc; then
    if [[ -e "$STOPPED_BINDING" ]]; then finish skipped 0 "Guildhall was stopped on this host; start switches it back on. Nothing changed."; fi
    finish skipped 0 "Guildhall is not switched on on this host. Nothing changed."
  fi
  R_TAG=$(release_tag) || R_TAG=""
  report=$(gates)
  printf '%s\n' "$report"
  failures=$(grep 'REFUSED' <<< "$report" || true)
  [[ -z "$failures" ]] || finish refused 1 "the gates refused; nothing changed"
  [[ -e "$BINDING" && -f "$MAIN_LAYER" ]] || finish refused 1 "Guildhall is only partly on (binding or main layer missing): run check. Nothing changed."
  [[ -z "$(missing_tokens)" ]] || finish refused 1 "worker tokens are missing: run check. Nothing changed."
  tokens_agree || finish refused 1 "guildhall-cos and cos-guildhall hold different cos-service tokens. Nothing changed."
  guildhall_on_main_project ||
    finish refused 1 "the portal or CoS scheduler runs without Guildhall's layer: the main release dropped it. Nothing changed."
  target=$(portal_digest) || finish refused 1 "portal digest not found. Nothing changed."
  R_RECORDS=$(H binding_image "$BINDING") || finish refused 1 "the binding holds no portal digest. Nothing changed."
  R_WORKER=$(H env_image "$ENV_FILE") || finish refused 1 "guildhall.env holds no worker digest. Nothing changed."
  worker="${MINIMOI_ROOMS_WORKER_IMAGE:-$R_WORKER}"
  H worker_image "$worker" || finish refused 1 "MINIMOI_ROOMS_WORKER_IMAGE is not an immutable production digest. Nothing changed."

  say "refresh"
  refresh_records "$target"
  refresh_workers "$worker"
  owner_access_ok || refresh_failed "Robert's Rooms access could not be confirmed"
  say "confirmed: Robert's Rooms access"
  wait_verified || refresh_failed "Guildhall did not verify after the refresh"
  if [[ -n "$R_CHANGED" ]]; then finish refreshed 0 "Guildhall refreshed ($R_CHANGED) and verified."; fi
  finish current 0 "Guildhall was already on this release's images; verified."
}

cmd_stop() {
  require_approved
  local tag="" wired=no name log
  portal_wired && wired=yes
  if [[ "$wired" == no && ! -e "$BINDING" && ! -f "$MAIN_LAYER" ]] && ! container_exists minimoi-records \
     && ! container_exists minimoi-guildhall-cos && ! container_exists minimoi-guildhall-mc; then
    say "result: nothing to stop"
    return 0
  fi
  # Learn the release before anything changes: the portal may be gone in step 3.
  if [[ "$wired" == yes || -f "$MAIN_LAYER" ]]; then
    tag="${GUILDHALL_RELEASE_TAG:-}"
    [[ -n "$tag" ]] || tag=$(release_tag) || die "no release tag: set GUILDHALL_RELEASE_TAG"
    [[ "$tag" =~ ^[0-9a-f]{7}$ ]] || die "GUILDHALL_RELEASE_TAG must be a release tag"
    [[ -f "$DEPLOY" ]] || die "no release script on this host; nothing changed"
  fi

  # 1. Workers, then the main layer.
  for name in minimoi-guildhall-cos minimoi-guildhall-mc; do
    if container_exists "$name"; then
      docker stop --time 30 "$name" >/dev/null 2>&1
      docker rm "$name" >/dev/null 2>&1 || die "could not remove $name"
    fi
  done
  say "stopped: workers"
  if [[ -f "$MAIN_LAYER" ]]; then
    mkdir -p "$(dirname "$HELD_LAYER")" && mv -f "$MAIN_LAYER" "$HELD_LAYER" || die "could not take the main layer away"
    say "held: main layer"
  fi

  # 2. Records, then its binding. The binding check refuses a wired portal with no binding, so step 3 comes before any release.
  if container_exists minimoi-records; then
    docker stop --time 30 minimoi-records >/dev/null 2>&1
    docker rm minimoi-records >/dev/null 2>&1 || die "could not remove minimoi-records"
  fi
  say "stopped: Records (state and keys kept)"
  if [[ -e "$BINDING" ]]; then
    mv -f "$BINDING" "$STOPPED_BINDING" || die "could not move the binding away"
    say "moved: binding"
  fi

  # 3 and 4. Only a wired portal has to go, and it is down from here until the release ends.
  if [[ -n "$tag" ]]; then
    if [[ "$wired" == yes ]]; then
      docker rm -f minimoi-portal >/dev/null 2>&1 || die "could not remove the wired portal"
      say "removed: wired portal. The portal is down until the release ends."
    fi
    log=$(private_log stop)
    if ! bash "$DEPLOY" "$tag" portal cos-scheduler >"$log" 2>&1; then
      die "the release failed; the portal may be down. Log in $(short "$log"). Run: $(short "$DEPLOY") $tag portal cos-scheduler" 5
    fi
    say "released: portal and CoS scheduler, without Guildhall"
  fi
  portal_env_ok || die "deployment.py --portal-env still refuses"
  say "result: Guildhall is stopped. Network, state, keys, journals and guildhall.env are kept."
}

main() {
  local command="${1:-check}"
  case "$command" in
    check|status|start|stop|refresh) ;;
    *) die "usage: guildhall_prod.sh [check|status|start|stop|refresh]" 2 ;;
  esac
  require_root_and_production
  case "$command" in
    check) cmd_check ;;
    status) cmd_status ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    refresh) cmd_refresh ;;
  esac
}

main "$@"
