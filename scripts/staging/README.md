# Staging: dev.minimoi.ai on Docker (issue #234)

dev.minimoi.ai is the staging environment for production. It runs the
**production compose file** (`docker-compose.prod.yml`) plus a small override
(`docker-compose.staging.yml`), with images built from one pinned commit, and
keeps all of its state in one folder outside every git worktree.

The Cloudflare tunnel sends `dev.minimoi.ai` to `http://localhost:5001`, so
whoever holds port 5001 on the Mac serves dev.minimoi.ai. After the cutover,
that is the staging portal container. No tunnel change is needed.

Design and reasoning: `planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/STAGING_CUTOVER_PLAN_2026-09-27.md`.

## Layout

| What | Where |
|---|---|
| State root (`MINIMOI_ROOT`, laid out like `/opt/minimoi`) | `~/minimoi-staging` (mode 700; override with `STAGING_ROOT`, absolute) |
| Secrets + settings for compose and every container | `~/minimoi-staging/.env` (mode 600, written by `env.sh`) |
| Where each `.env` name came from (names and sources, no values) | `~/minimoi-staging/env.sources` (mode 600, written by `env.sh`, read by `verify.sh`) |
| Last failed build, if any | `~/minimoi-staging/state/build.failed` (shown by `status.sh`) |
| Image tag | `~/minimoi-staging/release.env` (`MINIMOI_IMAGE_TAG`, written by `build.sh`) |
| Release record | `~/minimoi-staging/RELEASE` (SHA, ref, time, image IDs and architectures) |
| Seed record | `~/minimoi-staging/SEEDED_FROM.txt`, `SHA256SUMS.sources`, `SHA256SUMS.seed` |
| Gateway model map | `~/minimoi-staging/config/litellm.staging.yaml` (from `services/model_gateway/litellm.staging.yaml`) |
| Build Queue (the portal's live queue folder) | `~/minimoi-staging/data/guild/` mounted at `/app/runtime/guild` |
| Bots switch | `~/minimoi-staging/state/bots.on` (absent = bots off) |
| Job logs | `~/minimoi-staging/logs/` |
| Pinned release worktree (build context and compose source; never mounted) | `~/.worktrees/staging-release` (detached) |
| Compose project | `minimoi-staging` |
| Volumes (external: `down -v` cannot remove them) | `minimoi-staging-postgres-data`, `minimoi-staging-cos-agent-a-state`, `minimoi-staging-cos-agent-a-auth` |

Host ports, all on `127.0.0.1`: 5001 portal (tunnel target), 5432 postgres,
8766 curator, 8767 german, 8769 cos-scheduler, 8770 portuguese, plus two kept
for the Mac-only Records beta: 14000 model gateway, 18790 Agent A.

## What differs from production

Only what `docker-compose.staging.yml` says:

- local images `minimoi-staging/<ECR repository>:<tag>` built natively (arm64)
  from the pinned commit, with `pull_policy: never`; `postgres:latest` is the
  local PG 18.4 image and is never pulled;
- `MINIMOI_ROLE=standby` on every Python service (test bots, crons suppressed,
  test SSM path; staging containers have no AWS credentials, so a missing value
  fails closed);
- the portal gets `BASE_URL=https://dev.minimoi.ai`, in-network backends, and
  `MINIMOI_GUILD_NEXT=1` + `MINIMOI_GUILD_PROTO=1` (the `/guild-next` Shop floor
  and `/guild-proto` prototype, B1 deliverable (b)). Production never sets them;
- `system-bot` and `cos-bot` are in the `bots` profile, off by default;
- the model gateway reads `litellm.staging.yaml`: production's logical names,
  with `minimoi-cos-agent` on Claude Haiku 4.5 and the other routes as the dev
  gateway on `main` (grok-4.3, Sonnet 4.6 fallback);
- the IoT Connect stack is not part of staging (`/app/iotconnect` answers
  "unavailable"); the edge network has a staging-only name.

`docker-compose.prod.yml` reads every host path as
`${MINIMOI_ROOT:-/opt/minimoi}/...`. EC2 never sets `MINIMOI_ROOT`, so
production renders exactly as before (`tests/test_staging_environment.py`).

## Scripts

All scripts are bash, `set -euo pipefail`, and never print a secret value.
Run them from any checkout of this repository (the root checkout is fine).
`lib.sh` holds the only compose command line and refuses `down -v`.

| Script | Does |
|---|---|
| `build.sh <ref> [--reviewed-branch] [--no-fetch] [--allow-emulated]` | fetch, pin `~/.worktrees/staging-release` at `<ref>` (must be on `origin/main` unless `--reviewed-branch`), build the 9 images with deploy.yml's service map, fail if any image is not native, write `RELEASE`, `release.env`, the gateway config. Refuses a `STAGING_RELEASE_DIR` that is not the dedicated release worktree it created (marker in its git folder). On a failed build it puts the worktree back on the commit `RELEASE` pins and writes `state/build.failed` |
| `seed.sh [--ref R] [--queue-ref R]` | copy the dev data into `~/minimoi-staging` (refuses a non-empty target); queue from `origin/main:data/guild/build_queue.json` |
| `seed.sh --postgres` | copy the dev Postgres volume into `minimoi-staging-postgres-data` (only while no container uses the source), prove it by digest, create the two fresh Agent A volumes |
| `seed.sh --docs-only` | refresh `docs/specs` and `docs/design` from the pinned release (like `sync_docs.sh`: add/update, never delete) |
| `seed.sh --drift` | list source files that changed since the seed (a writer that was missed); exit 1 if any |
| `env.sh [--force]` | write `~/minimoi-staging/.env` (600) from the root checkout's `.env` by name, Keychain only for a missing value, and `env.sources` (names and sources). The two bot tokens are **never** taken from the root `.env`: only from the Keychain test accounts `telegram/cos_test_bot_token` and `telegram/system_test_bot_token`, and only while `state/bots.on` exists |
| `up.sh [--allow-holder PORT[,PORT]] [service ...]` | `up -d --no-build --remove-orphans`; refuses when a staging host port is held by anything but a staging container (names the holder) unless that port is listed in `--allow-holder`; bots only with `state/bots.on`, no native poller loaded and the Keychain bot tokens in `.env` |
| `down.sh [service ...]` | whole stack down (volumes and files kept), or stop named services |
| `status.sh` | containers, images vs `RELEASE`, health, port holders |
| `verify.sh [--no-guild-routes] [--allow-holder PORT[,PORT]]` | the acceptance checks below; non-zero on any failure |
| `focus.sh mc\|mc+cos\|all\|show` | run only the containers under test; the set is persisted and `up.sh`/`verify.sh` respect it (section "Focus") |
| `mc.sh token\|build\|up\|down\|status\|clear-selfcheck` | Master Craftsman's own Compose project `minimoi-staging-mc` (section "Master Craftsman stage A"); never touches the main project |
| `mc.sh gateway-keys\|key` | Stage C, **Robert runs these**: the gateway's key database (recreates only `model-gateway` in the main project), then MC's capped key (recreates only `mc-agent`); nothing secret is printed (section "Master Craftsman stage C") |
| `cos.sh key\|off\|status` | CoS's own capped gateway key, **Robert runs `cos.sh key`**: CoS's routes only, monthly cap, recreates only `cos-agent-a`; `off` goes back to the master key (section "CoS's own capped gateway key") |
| `jobs.sh lesen\|intelligence\|leitura` | run one background job by hand, logged to `logs/` (Spec 159: dev jobs run only when triggered). `jobs.sh curator` refuses (exit 3): the curator skips every run outside `MINIMOI_ROLE=production`, and staging never runs as production |

### What `verify.sh` proves

1. every container runs the release image under project `minimoi-staging`, is
   not restarting, and is healthy where it has a health check;
2. `/health` is 200 in-network for portal, curator, german, portuguese and
   cos-scheduler; the gateway (`127.0.0.1:14000`) and Agent A
   (`127.0.0.1:18790`) answer from the host;
3. the portal answers on `127.0.0.1:5001`, and **every staging host port** is
   listened on and held only through a staging container (no native process,
   no other project's container); `--allow-holder` turns a listed port into a
   warning during the cutover;
4. the queue folder is mounted and **Save is on**: inside the portal,
   `QueueStore(GUILD_QUEUE_PATH).write_problem()` returns `None`, the queue
   reads, and the host file's checksum equals the container's
   (`scripts/operations/check_queue_mount.sh`, the same proof as the EC2 deploy);
5. `/guild-next` and `/guild-proto` are registered: anonymous requests get the
   login redirect or JSON 401, never 404 and never 200;
6. every bind mount comes from `~/minimoi-staging` or the Docker socket, never
   from `~/Projects`, `~/.worktrees` or `~/.codex`;
7. `MINIMOI_ROLE=standby` in every Python container; no `AWS_*`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_POLLING_BOT_TOKEN` anywhere; the Guild flags
   only on the portal; `TELEGRAM_COS_BOT_TOKEN` and `TELEGRAM_SYSTEM_BOT_TOKEN`
   are in `.env` (and the containers) only while `state/bots.on` exists, and
   `env.sources` records each as `keychain:telegram/<test account>` (names
   and sources only);
8. Postgres has the `guild` and `research` schemas;
9. Master Craftsman: the gateway is on the internal `minimoi-staging-mc-net`;
   with `state/mc.enabled`, MC runs the release's image in its own project,
   healthy after its self-check, only on that network, with no published port
   and no CoS credential name, and its stage-A key gets 401 (anything else
   fails).

With a focus set (`focus.sh`), the services it keeps stopped are reported as
"off (focus: <set>)" and everything running is checked in full.

## Cutover runbook (one sitting; every runtime step needs Robert's go-ahead)

`S=~/minimoi-staging`. Scripts path below is `scripts/staging/` in the root
checkout (`~/Projects/personal-ai-agents`) or in any worktree that has them.
Expected dev.minimoi.ai outage: from step 3 to step 6, about 15 minutes.

**Freeze window, step 1 through step 7d.** Do not use dev German, Portuguese,
Curator, the queue Save, or the German/system **test** bot while the cutover
runs. The seed copies the dev data in step 1, but the native writers (the old
curator container, the native portal with its German and Portuguese servers,
the native system-bot) keep running until steps 4, 6, 7b, 7c and 7d. Anything
written there after the seed does not reach staging. Step 5 checks this with
`seed.sh --drift` right before `up.sh`, and step 7 checks it again.

### 1. Seed the files (nothing stops; the freeze window starts)

```bash
scripts/staging/seed.sh                       # --ref origin/main --queue-ref origin/main by default
```

Verify: `SEEDED_FROM.txt`, `SHA256SUMS.sources`, `SHA256SUMS.seed` exist;
"queue seeded with N items". Sources are only read.
Rollback: nothing to undo in the old stack. To retry, move the whole staging
root aside (`mv ~/minimoi-staging ~/minimoi-staging.failed-$(date +%s)`) and
seed again.

### 2. Write the staging `.env` (Robert runs this; nothing stops)

```bash
scripts/staging/env.sh                        # names and sources only are printed
```

Verify: `stat -f %Lp ~/minimoi-staging/.env` is `600`; the report lists no
`MISSING`; both bot tokens say "not written (bots off)", and any
`TELEGRAM*TOKEN*` in the root `.env` says "IGNORED" (the staging bot tokens
never come from the root `.env`). Rollback: delete `~/minimoi-staging/.env`
and `env.sources` (they only hold copies and labels).

### 3. Build and pin the release (nothing stops)

```bash
scripts/staging/build.sh origin/main          # or: <reviewed-branch> --reviewed-branch
docker compose -p minimoi-staging --env-file ~/minimoi-staging/.env --env-file ~/minimoi-staging/release.env \
  -f ~/.worktrees/staging-release/docker-compose.prod.yml \
  -f ~/.worktrees/staging-release/docker-compose.staging.yml config --quiet && echo CONFIG_OK
```

Verify: 9 images `minimoi-staging/*:<sha7>` (`docker images minimoi-staging/*`),
all native (build.sh fails otherwise), `CONFIG_OK`. Optional flock test across
two containers on the staging bind mount (plan §7 step 0) before any Save.
Rollback: nothing to undo (optionally `docker rmi` the staging images).

### 4. Stop the old dev containers (keep them and their volumes), copy Postgres

```bash
launchctl bootout gui/$(id -u)/com.user.docker-compose 2>/dev/null || true
mv ~/Library/LaunchAgents/com.user.docker-compose.plist ~/Library/LaunchAgents/com.user.docker-compose.plist.disabled-2026-09-27
OLD="postgres-ai-agents minimoi-curator minimoi-german minimoi-model-gateway minimoi-cos-agent-a minimoi-cos-dev"
docker inspect $OLD > ~/minimoi-staging/rollback/containers-before-2026-09-27.json
docker stop $OLD
for c in postgres-ai-agents minimoi-curator minimoi-german minimoi-model-gateway minimoi-cos-agent-a; do docker rename "$c" "$c-pre-staging"; done
scripts/staging/seed.sh --postgres            # copy + digest proof + fresh Agent A volumes
```

Verify: none of the old names running; `lsof -nP -iTCP:5432 -sTCP:LISTEN` empty;
`docker volume ls` still lists every `personal-ai-agents_*`; "postgres copy
verified"; `SEEDED_FROM.txt` has the volume line.
Rollback: `for c in postgres-ai-agents minimoi-curator minimoi-german minimoi-model-gateway minimoi-cos-agent-a; do docker rename "$c-pre-staging" "$c"; done; docker start $OLD`;
rename the plist back (it need not be reloaded). The staging volume copy can stay.

### 5. Re-check the seed, then start staging (bots off)

```bash
scripts/staging/seed.sh --drift               # must print "0 changed, 0 added, 0 removed" and exit 0
scripts/staging/up.sh --allow-holder 5001,8767,8770 && scripts/staging/status.sh
```

If `--drift` reports anything, something wrote a source after step 1: do not
start. Move the seeded parts aside
(`mkdir ~/minimoi-staging/rollback/stale-seed && mv ~/minimoi-staging/{data,auth,docs,agent_logs,cos_memory.md,SEEDED_FROM.txt,SHA256SUMS.sources,SHA256SUMS.seed} ~/minimoi-staging/rollback/stale-seed/`),
run `seed.sh` again (the Postgres volume, `.env`, `RELEASE` and `config/`
stay), and repeat this step.

`up.sh` refuses when any staging host port (5001, 5432, 8766, 8767, 8769,
8770, 14000, 18790) is held by anything other than a staging container, and
names the holder. At this step only the native portal (5001), German (8767)
and Portuguese (8770) servers are expected, hence `--allow-holder`; any other
holder (for example an old container still running) is a stop.

Verify: 8 containers up under `minimoi-staging`, images match `RELEASE`,
gateway and Agent A healthy. Ports 5001, 8767 and 8770 do not forward yet
(the native holders are still there, and Colima's forwarder does not retry).
Rollback: `scripts/staging/down.sh`, then the step 4 rollback.

### 6. Retire the native portal, then verify

```bash
launchctl bootout gui/$(id -u)/com.vanstedum.portal-boot-restart 2>/dev/null || true
launchctl bootout gui/$(id -u)/com.vanstedum.minimoi-portal
cd ~/Library/LaunchAgents
mv com.vanstedum.minimoi-portal.plist com.vanstedum.minimoi-portal.plist.disabled-2026-09-27
mv com.vanstedum.portal-boot-restart.plist com.vanstedum.portal-boot-restart.plist.disabled-2026-09-27
lsof -nP -iTCP:5001 -sTCP:LISTEN              # expect nothing
docker restart minimoi-portal
scripts/staging/verify.sh --allow-holder 8767,8770   # 5001 held only through the staging portal
curl -s -o /dev/null -w '%{http_code}\n' https://dev.minimoi.ai/health          # 200 through the tunnel
```

Then Robert signs in once on https://dev.minimoi.ai (an old cookie may need a
fresh login) and opens `/guild/build`, `/guild-next/guild/build`,
`/app/curator`, `/app/german`, `/app/portuguese`, `/app/cos`; the parent checks
`docker logs minimoi-portal --since 10m` for 5xx.
Rollback: `docker stop minimoi-portal`; rename both plists back;
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist`
(and the boot-restart plist); confirm the native Python process holds 5001.

### 7. Retire the leftover native services, one at a time

For each: boot out, rename the plist to `.disabled-2026-09-27`, bring up the
staging replacement, verify, and run `seed.sh --drift` for that source.
After 7d, `scripts/staging/verify.sh` with no `--allow-holder` must pass and
`seed.sh --drift` must be clean: the freeze window ends there.
Rollback for each: stop the staging service (`down.sh <service>`), rename the
plist back, `launchctl bootstrap`, and for a port, `docker restart` the staging
container only **after** the native one has bound (order matters).

| # | Native job | Replacement | Verify |
|---|---|---|---|
| 7a | `com.vanstedum.cos-bot` | `touch ~/minimoi-staging/state/bots.on; scripts/staging/env.sh --force; scripts/staging/up.sh` (env.sh now writes the two test tokens from the Keychain) | `docker logs minimoi-cos-bot`: polling, no `Conflict`/409; `verify.sh` check 7 shows both tokens from `keychain:telegram/...` |
| 7b | `com.vanstedum.system-bot` | same profile (both bots start together; boot out both natives before 7a's `up.sh`) | `/status` to the system **test** bot says `role=standby`; no 409 |
| 7c | `com.vanstedum.german-html-server` | `docker restart minimoi-german` | `lsof :8767` is the forwarder; `/app/german` loads; `seed.sh --drift` |
| 7d | `com.user.portuguese` | `docker restart minimoi-portuguese` | `lsof :8770` is the forwarder; `/app/portuguese` loads |
| 7e | `com.vanstedum.lesen-refresh` | `scripts/staging/jobs.sh lesen` (manual) | job ok; writes `~/minimoi-staging/data/german/config/lesen_articles.json` |
| 7f | `com.user.portuguese-leitura` | `scripts/staging/jobs.sh leitura` (manual) | exits 0 against the staging DB |
| 7g | `com.vanstedum.curator-priority-feed` | none (no active priorities, no prod equivalent) | nothing writes the root `priorities.json` |

Bots rollback: `rm ~/minimoi-staging/state/bots.on; scripts/staging/down.sh system-bot cos-bot; scripts/staging/env.sh --force; scripts/staging/up.sh`
(drops the token names from `.env` and the other containers), then rename the
native plists back and `launchctl bootstrap` them.

Kept local on purpose: Records x3 (use `127.0.0.1:18790` and `:14000`),
`com.vanstedum.cloudflared`, both Colima plists, `ai.openclaw.gateway`,
`com.user.private-sync`, `com.user.operations`, the Codex CoS worktree and
its `cos-work-dev` volume.

### Full rollback to the pre-staging dev setup

```bash
scripts/staging/down.sh                       # keeps every volume and ~/minimoi-staging
cd ~/Library/LaunchAgents; for f in *.disabled-2026-09-27; do mv "$f" "${f%.disabled-2026-09-27}"; done
for c in postgres-ai-agents minimoi-curator minimoi-german minimoi-model-gateway minimoi-cos-agent-a; do docker rename "$c-pre-staging" "$c"; done
docker start postgres-ai-agents minimoi-curator minimoi-german minimoi-model-gateway minimoi-cos-agent-a minimoi-cos-dev
for p in com.vanstedum.minimoi-portal com.vanstedum.portal-boot-restart com.vanstedum.cos-bot com.vanstedum.system-bot \
         com.vanstedum.german-html-server com.user.portuguese com.vanstedum.lesen-refresh com.user.portuguese-leitura \
         com.vanstedum.curator-priority-feed; do launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$p.plist 2>/dev/null || true; done
lsof -nP -iTCP:5001 -sTCP:LISTEN              # the native Python portal again
```

Nothing in the old sources was moved or changed. Anything written on staging
during the trial (Saves, German sessions) stays in `~/minimoi-staging` for
manual reconciliation.

## Updating staging to a new release

```bash
scripts/staging/build.sh origin/main          # new images + pinned worktree; running containers untouched
scripts/staging/seed.sh --docs-only           # like the EC2 docs sync
scripts/staging/up.sh && scripts/staging/verify.sh
```

Rollback: `build.sh <previous sha>` (from `RELEASE`), then `up.sh`.

If `build.sh` fails, the running containers are untouched and the release
worktree is put back on the commit `RELEASE` still pins; `status.sh` shows
"LAST BUILD FAILED". If that restore also failed, `status.sh` shows
"MISMATCH" and `up.sh`, `down.sh` and `verify.sh` refuse until `build.sh`
succeeds. Curator runs cannot be triggered on staging (`jobs.sh curator`
refuses): the curator only runs as production.

## Agent A OpenClaw upgrade (2026.7.1 to 2026.9.6)

Upgrade to current stable OpenClaw 2026.9.6; 2026.7.1 is outside supported
security fixes. Staging first; every runtime step needs Robert's go-ahead.

**Why a snapshot first.** On its first start, 9.6 rewrites the state database
(schema v18) in place, and 7.1 cannot read it afterwards. An image swap alone
is not a rollback. The config file is not the problem: the image's
`apply-config.sh` copies the pinned `openclaw.json` over the volume's copy on
every start, and keeps the previous file beside it as
`openclaw.json.replaced-by-image`.

**What to expect.** The image is about 4.9 GB (7.1: 1.7 GB), and the gateway
idles at about 0.9 GB RSS (7.1: about 0.3 GB) under a 1200m `mem_limit`.
Compose adds its own health check (`/healthz` every 15 s, 60 s start
period); the image's own runs only every 180 s.

### 1. Snapshot both staging Agent A volumes (container stopped)

```bash
S=~/minimoi-staging; STAMP=$(date +%Y%m%d-%H%M%S)
AGENT_A_IMG=$(docker inspect -f '{{.Config.Image}}' minimoi-cos-agent-a)   # the running 7.1 image (instead of alpine: no pull)
echo "$AGENT_A_IMG" > "$S/rollback/agent-a-image-before-9.6.txt"
cp "$S/RELEASE" "$S/rollback/RELEASE-before-9.6"                             # the release to go back to
echo "$STAMP" > "$S/rollback/agent-a-pre96-stamp.txt"
scripts/staging/down.sh cos-agent-a                                          # SQLite is copied at rest
for v in state auth; do
  docker volume create "minimoi-staging-cos-agent-a-$v-pre96-$STAMP"
  docker run --rm --network none --user 0:0 \
    -v "minimoi-staging-cos-agent-a-$v:/from:ro" -v "minimoi-staging-cos-agent-a-$v-pre96-$STAMP:/to" \
    --entrypoint cp "$AGENT_A_IMG" -a /from/. /to/
done
```

Or, as files: the same loop with
`--entrypoint tar "$AGENT_A_IMG" -C /from -czf - . > "$S/rollback/cos-agent-a-$v-pre96-$STAMP.tar.gz"`
(and only `-v …:/from:ro`). Verify: `docker volume ls | grep pre96` lists
both copies (or both tarballs are non-empty).

### 2. Build, start, verify

```bash
scripts/staging/build.sh claude/agent-a-openclaw-2026-9-6 --reviewed-branch
scripts/staging/up.sh && scripts/staging/verify.sh
```

Then check the upgrade itself:

```bash
docker inspect -f '{{.State.Health.Status}}' minimoi-cos-agent-a          # healthy (no-spend probe: about 12 s); note the time
docker exec minimoi-cos-agent-a node openclaw.mjs --version                 # OpenClaw 2026.9.6 (eb377ac)
docker logs minimoi-cos-agent-a 2>&1 | head -3                               # "applied the image's pinned openclaw.json" once
docker exec minimoi-cos-agent-a node openclaw.mjs config validate --json     # valid, no warnings
docker exec minimoi-cos-agent-a node openclaw.mjs gateway call cron.status --json   # "enabled": false
```

`cron.list` shows two jobs (heartbeat, skill-collection-review), both
`enabled: false`, and no "Memory Dreaming Promotion" job. Then one real
Agent A turn from the CoS surface (this one costs a model call).

### 3. Rollback (back to 7.1)

```bash
S=~/minimoi-staging; STAMP=$(cat "$S/rollback/agent-a-pre96-stamp.txt")
PREV=$(cat "$S/rollback/agent-a-image-before-9.6.txt")
scripts/staging/down.sh cos-agent-a
for v in state auth; do
  docker run --rm --network none --user 0:0 \
    -v "minimoi-staging-cos-agent-a-$v-pre96-$STAMP:/from:ro" -v "minimoi-staging-cos-agent-a-$v:/to" \
    --entrypoint sh "$PREV" -c 'find /to -mindepth 1 -delete && cp -a /from/. /to/'
done
scripts/staging/build.sh "$(sed -n 's/^sha=//p' "$S/rollback/RELEASE-before-9.6" | tail -n 1)"
scripts/staging/up.sh && scripts/staging/verify.sh
```

Wipe before copy: 9.6 leaves SQLite `-wal`/`-shm` files and new state
beside the database, and a merge-restore would mix them with the 7.1 files.
From tarballs, replace the `cp -a` with
`tar xzf - -C /to < "$S/rollback/cos-agent-a-$v-pre96-$STAMP.tar.gz"` (run
with `-i`). Keep the `pre96` copies until 9.6 has run cleanly for a week.

### Production

Production deploys on push to `main` and recreates `cos-agent-a` against its
existing state volume. `scripts/operations/deploy_scoped_release.sh` now
snapshots both Agent A volumes on every release that includes `cos-agent-a`:
after the image pull, it stops the old container, writes
`/opt/minimoi/backups/cos-agent-a/<UTC stamp>/cos-agent-a-{state,auth}.tar.gz`
(root-only, with a `SNAPSHOT` file holding the previous image, its OpenClaw
version line and checksums), and only then recreates it. If anything fails
while Agent A is stopped, including `up -d` itself, an exit trap starts the
old container again and the deploy fails; the size report and pruning are
best effort.

Retention: a set taken from a different OpenClaw major.minor than the image
being deployed (the pre-9.6 set, taken under 2026.7) gets a `KEEP` file and
is never pruned automatically; delete it by hand once 9.6 has run cleanly
for a few weeks. Of the sets from the same major.minor, the newest 5 are
kept. Before merging, check free disk on EC2 (`df -h /var/lib/docker
/opt/minimoi`): the 9.6 image needs about 5 GB.

Production restore, on EC2 (the archive root is the folder name, for example
`.openclaw/`, hence `--strip-components=1`):

```bash
cd /opt/minimoi
SNAP=/opt/minimoi/backups/cos-agent-a/<stamp>; PREV=$(sed -n 's/^previous_image=//p' $SNAP/SNAPSHOT)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 332704997792.dkr.ecr.us-east-1.amazonaws.com
docker pull "$PREV"                                   # the deploy pruned it locally; needs the tag still in ECR
docker stop minimoi-cos-agent-a
for pair in "state:/home/node/.openclaw" "auth:/home/node/.config/openclaw"; do
  v=${pair%%:*}; path=${pair#*:}
  vol=$(docker inspect -f "{{range .Mounts}}{{if eq .Destination \"$path\"}}{{.Name}}{{end}}{{end}}" minimoi-cos-agent-a)
  docker run --rm -i --network none --user 0:0 -v "$vol:/to" --entrypoint sh "$PREV" \
    -c 'find /to -mindepth 1 -delete && tar xzf - -C /to --strip-components=1' < "$SNAP/cos-agent-a-$v.tar.gz"
done
MINIMOI_IMAGE_TAG=${PREV##*:agent-a-} docker-compose -f /opt/minimoi/docker-compose.prod.yml up -d --no-deps cos-agent-a
```

Then revert the upgrade on `main`; its deploy snapshots the restored volume
again and recreates the 7.1 image, which reads it.

## Focus: run only the containers under test (focus.sh)

On the 8 GB Mac, run only what a test needs. The set is persisted in
`~/minimoi-staging/state/focus` (its name) and `state/focus.stopped` (the
services kept stopped). `up.sh` then starts only the kept services (with
`--no-deps`, so the portal's dependencies stay stopped), and `verify.sh` reports
the stopped ones as "off (focus: <set>)" instead of failing.

| Set | Running | Use |
|---|---|---|
| `mc` | postgres, model-gateway, portal (+ MC's own project via `mc.sh`); **CoS Agent A and cos-scheduler are stopped** | Master Craftsman work |
| `mc+cos` | the same + cos-agent-a, cos-scheduler | CoS regression checks beside MC |
| `all` | everything (the bots follow `state/bots.on`) | normal staging |

```bash
scripts/staging/focus.sh mc          # stops the rest with down.sh <service>; volumes and files untouched
scripts/staging/focus.sh show
scripts/staging/focus.sh all         # clears the focus and runs up.sh
```

`focus.sh` addresses only the staging project and never MC's own project.

## Master Craftsman stage A: MC's own container, no spend (MC spec v0.9)

MC runs in **its own Compose project**, `minimoi-staging-mc`
(`docker-compose.mc.yml`), never in the main one, so the main project's
`up --remove-orphans` and image pruning can never touch it, and MC's up and
down never touch CoS Agent A, the gateway or the portal. `scripts/staging/mc.sh`
is the only script that runs it. **CoS Agent A is unchanged** (image, config and
service). The one main-stack definition change is permanent: the model
gateway is also on the internal network `minimoi-staging-mc-net` (no host
address on its bridge). Rolling out this release still recreates every
main-stack container once, because every image gets the new tag (see Steps).

**What stage A proves, with no model call:** MC starts and restarts on its
own; it is checked on a loopback-only gateway before anything can reach it; it
reaches only the gateway, where its placeholder key is refused (401); it
cannot reach CoS, Postgres, cos-scheduler, the portal, the host or the
internet; a failed MC never affects CoS. The Shop floor stays "off" (turns are
stage B; the first real reply is stage C, after Robert's key and cap).

MC's secrets live only in `~/minimoi-staging/mc.env` (mode 600): in stage A,
only `MC_OPENCLAW_GATEWAY_TOKEN` (MC's own OpenClaw token). `mc.sh up` refuses
if an MC value equals one of CoS's (compared, never printed) or if an MC name
is in `.env`.

**Start-up on the 2-CPU VM.** Two OpenClaw starts at once can make one fail
its start-up lease (seen in the #249 probe). `mc.sh up` waits until the gateway
and CoS Agent A are healthy before starting MC; a start that still fails is
retried inside the start (up to 10 times, 30 s apart: after a hard kill
OpenClaw's owner lease on MC's state stays held for a few minutes) and then by
`restart: on-failure:3`; the healthcheck allows 10 minutes. MC's
failures never restart CoS. After a Colima restart, start MC with `mc.sh up`
once CoS is healthy.

### Steps (the coordinator runs them after review; each runtime step needs Robert's go-ahead)

What restarts, honestly (#250 review F2): `build.sh` gives **every** image the
new release tag, so step 3's `up.sh` recreates **every running main-stack
container**, CoS Agent A included (identical CoS content: same OpenClaw, same
config, no migration; about a minute without CoS on dev). The gateway is also
recreated onto `minimoi-staging-mc-net`. Raising Colima's CPUs (step 2)
restarts the whole VM, so both happen in one window and CoS restarts there,
not again later. After that window, MC's up, down, restart and failures never
touch CoS or the gateway.

**Focus `mc` stops CoS Agent A and cos-scheduler** (CoS is down on dev while
that set is active). Focus `mc+cos` keeps CoS up.

0. **Before the window (no change):**
   - the CoS regression question (step 8) — the "before" answer;
   - `colima ssh -- free -m`;
   - recommended: snapshot both CoS Agent A volumes as in "Agent A OpenClaw
     upgrade" step 1 (CoS is Robert's daily assistant; the volumes do not
     change, but the container restarts).
1. **Build first, nothing stopped:**
   `scripts/staging/build.sh claude/mc-separate-pr2-stage-a --reviewed-branch`
   (after merge: `origin/main`), then `scripts/staging/mc.sh build`.
2. **Colima to 3 CPUs** (Robert approved; the Mac has 8 cores; vCPUs cost no
   memory). This restarts the VM and all of staging:
   ```bash
   colima stop && colima start --cpu 3 --memory 4
   colima list                                    # CPUS 3, MEMORY 4GiB
   ```
3. **The main-stack change:** `scripts/staging/up.sh && scripts/staging/verify.sh`.
   Every main-stack container is recreated with the new tag (CoS with
   identical content) and the gateway joins `minimoi-staging-mc-net`.
   Then **the CoS regression question** — the "after" answer, once CoS is
   healthy again. It also proves the gateway still has provider egress on two
   networks, which the probe cannot.
4. **Baseline for independence** (after step 3, not before):
   `docker inspect -f '{{.Name}} {{.State.StartedAt}}' minimoi-cos-agent-a minimoi-model-gateway`.
5. **Focus, then MC:**
   ```bash
   scripts/staging/focus.sh mc+cos                # keeps CoS up; `mc` stops CoS
   scripts/staging/mc.sh token                    # MC's own OpenClaw token into mc.env (not printed)
   touch ~/minimoi-staging/state/mc.enabled
   scripts/staging/mc.sh up                       # waits for the gateway and CoS, then starts MC
   scripts/staging/mc.sh status                   # self-check: serving
   scripts/staging/verify.sh                      # section 9: MC checks; CoS as before
   ```
6. **Boundary tests** (C1-C8): `verify.sh` section 9 covers the running MC,
   including that it reaches no host address; the full set runs in a
   throwaway probe beside staging:
   `python3 scripts/staging/mc_probe/stage_a.py --mc-image minimoi-staging/mc-agent:<sha7> --cos-image minimoi-staging/cos-scheduler:agent-a-<sha7> --gateway-image minimoi-staging/cos-scheduler:model-gateway-<sha7>`
   (needs about 1.5 GB free in the VM; run it under focus `mc`).
7. **Memory and independence:**
   - `docker stats --no-stream` with focus `mc` and with `mc+cos`; stop if the
     VM's available memory is under 0.5 GB with MC up and idle (v0.9 §7);
   - `docker restart minimoi-mc-agent`, then
     `docker kill minimoi-mc-agent && scripts/staging/mc.sh up`: CoS Agent A's
     and the gateway's `StartedAt` must equal step 4's, and CoS must answer
     throughout.
8. **The CoS regression question** (step 0, step 3 and after step 7; costs one
   CoS call each, with Robert's go-ahead): one real CoS question with web search
   from `https://dev.minimoi.ai/app/cos`. Pass: a cited answer **and** a new line
   with `"logical_model":"minimoi-cos-web-search"` in
   `~/minimoi-staging/data/model_gateway_receipts.jsonl`.

**For stage C (recorded now; done in stage C below).** In stage A, with no key database, LiteLLM
refuses a non-master key with `400 no_db_connection`, and C5 and check 9j
accept exactly that as a refusal. **That exception ends in stage C:** once the
key database exists, a `no_db_connection` means the database is down or
misconfigured, and C5 must then accept only 401/403, with a positive control
first (MC's own `/key/info` 200 with its real key, and the master key's
`/v1/models` 200), re-run with the real key before any paid turn.

### Rollback

- **MC off:** `scripts/staging/mc.sh down; rm ~/minimoi-staging/state/mc.enabled`.
  CoS, the gateway and the portal are not touched.
- **MC's self-check failed** (`mc.sh status` says `selfcheck-failed`; it stays
  up, unhealthy, and never loops): read it with
  `docker exec minimoi-mc-agent cat /home/node/.openclaw/.mc-selfcheck-failed`,
  fix the cause, then `scripts/staging/mc.sh clear-selfcheck`.
- **The gateway network change:** build and `up.sh` the previous release; the
  gateway is recreated once without `minimoi-staging-mc-net` (MC must be down).
- **Focus:** `scripts/staging/focus.sh all`.
- MC's volumes (`minimoi-staging-mc-agent-{state,auth}`) are never removed by a
  script; remove them by hand only when MC is retired.

## Master Craftsman stage B: turns through the one-way relay (no spend)

The Shop floor's owner route `POST /guild-next/api/v1/mc/turns` sends a
**kept, on-the-record** note to Master Craftsman, server side, through
`mc-relay` (MC's own project; MC spec v0.9 §4):

```
browser --(owner session, CSRF)--> portal --(relay caller token, mc-front)--> mc-relay --(MC's OpenClaw token, mc-net)--> mc-agent --> model gateway
```

- **One way.** The portal and the relay share `minimoi-staging-mc-front`
  (internal, no host address); MC is only on `minimoi-staging-mc-net` with the
  relay and the gateway, so MC can reach nothing of the portal. The relay
  forwards only `POST /v1/chat/completions` (model exactly `openclaw/mc-agent`,
  `user` `guild-mc:*`, 256 KB, no streaming, one at a time) and `GET /readyz`
  to MC; everything else is 403, and it drops every `x-openclaw-*` header.
- **Tokens.** The portal holds only the relay's caller token
  (`MC_RELAY_TOKEN` → the portal's `MC_RUNTIME_TOKEN`); the relay holds MC's
  own OpenClaw token. Both live only in `mc.env` (interpolation only) and never
  reach the browser, the HTML or a log line.
- **The gate.** `state/mc.turns` (`on`/`off`, default off) sets the portal's
  `MINIMOI_GUILD_MC_TURNS`; `state/mc.mode` sets `MINIMOI_GUILD_MC`. Production
  never sets either, and never mounts `/guild-next`.
- **Honest states.** Before any answer the header reads "unavailable ·
  connected, no answer yet", never "live". On staging, MC's key is still a
  placeholder (stage C adds the real one), so a real turn ends
  **"unavailable · its model key was refused"**: the note stays kept, and
  nothing is shown as an answer. Off-the-record notes never reach MC; the
  payment scrub runs again before a note leaves; a stub reply is kept as the
  stub's, never Master Craftsman's.

### Steps (after review; each runtime step needs Robert's go-ahead)

1. **`mc.sh token` first** (adds `MC_RELAY_TOKEN` to `mc.env` if missing; not
   printed). MC's project interpolates `MC_RELAY_TOKEN` for `mc-relay`, so
   every `mc.sh down`/`up` (and any compose call on MC's project) fails
   without it (stage B rollout finding).
2. Build first, nothing stopped: `build.sh <branch> --reviewed-branch`, then
   `mc.sh build`.
3. **Roll the release out:** `scripts/staging/up.sh && scripts/staging/verify.sh`.
   As in stage A, the new image tag recreates every running main-stack
   container once (CoS with identical content), and the portal joins
   `mc-front` in that same recreate. Then the CoS regression question (stage A
   step 8).
4. `mc.sh up` (starts `mc-agent` and `mc-relay`), `mc.sh status`,
   `verify.sh` (section 9: relay, networks, codes from the portal).
5. Turn it on: `printf 'openclaw\n' > ~/minimoi-staging/state/mc.mode;
   printf 'on\n' > ~/minimoi-staging/state/mc.turns; scripts/staging/up.sh portal`.
6. **Exit check on staging:** Robert keeps a note on `/guild-next/guild/build`;
   under the note a quiet "MC · Waiting for a response… Ns" line appears (not a
   platform message), then it is replaced in place by "Master Craftsman is
   unavailable · its model key was refused … Your note is kept; Master
   Craftsman did not answer." `docker logs minimoi-portal` shows
   `mc turn <id> start` / `end status=unavailable class=key_refused echo=True`
   and no token; `docker logs minimoi-mc-relay` shows the same id.

Rollback: `printf 'off\n' > ~/minimoi-staging/state/mc.turns; scripts/staging/up.sh portal`
(turns off; nothing else changes); `mc.sh down` stops MC and the relay.

## Master Craftsman stage C: first real reply (Robert's key and cap)

Stage C gives MC a real, capped model key. Everything before Robert's two
commands is no-spend; the first paid call is Robert's own first note.

- **The gateway's key database.** A `litellm_keys` database with its own
  `litellm_keys` role (random password, never printed) in the existing
  Postgres. Its URL lives only in `~/minimoi-staging/gateway.env` (mode 600,
  interpolation only, read by the gateway alone), and the
  `docker-compose.staging-keys.yml` overlay applies only while
  `state/gateway.keys` reads `on`. Production has no key database and is
  unchanged.
- **MC's route** `minimoi-mc-agent` (staging only): Claude Haiku 4.5 on
  **MC's own** Anthropic key (`MC_ANTHROPIC_API_KEY` in `mc.env`; a
  placeholder until Robert pastes it), no fallback.
- **MC's virtual key:** that route only; chat completions only (not
  `/key/info`: LiteLLM answers `/key/info?key=<another key>` for any key allowed
  to call it); a monthly cap (`30d`); rpm 10.
- **The `/anthropic/*` pass-through is closed on staging.** LiteLLM's
  pass-through routes sign upstream calls with the default names
  `ANTHROPIC_API_KEY` / `XAI_API_KEY`, so any key holder could spend CoS's
  provider key there. Staging's gateway now reads `GATEWAY_ANTHROPIC_API_KEY` /
  `GATEWAY_XAI_API_KEY` (same values from `.env`) and gets the default names
  **empty**. Production still has the gap, but only master-key holders can
  reach it there (production has no virtual keys); closing it in production is
  its own PR and needs Robert's OK.
- **What a refusal looks like.** OpenClaw 2026.9.6 turns an upstream budget
  400, a 500 and a 429 alike into its own `500 internal error`, so the Shop
  floor says "unavailable" without naming the cap. It never shows a failure as
  an answer (probe `stage_b.py`, ERROR_SURFACE).

### Spend: what bounds it

Measured in `mc_probe/stage_c.py` (throwaway gateway and Postgres, LiteLLM
1.93.1, stand-in provider; no real key):

- **Normal operation:** the gateway checks MC's cap before every call, so the
  cap can be passed by at most one call. MC runs one turn at a time, and one
  Haiku 4.5 turn costs at most about $0.04.
- **Key database down, inside the 60 s key cache:** MC's key is **still
  served** (the cached key and the in-memory spend counter still apply the
  cap). CoS is unaffected (master key).
- **Key database down, past the 60 s cache:** MC's key is **refused**
  (`503 no_db_connection`) with no provider call. CoS still answers.
- **Gateway restarted while the key database is down:** the gateway **does
  not start** (it exits; `restart: unless-stopped` retries it until Postgres
  is back). **CoS is down for that time too**, not only MC. So with the key
  database on, Postgres is a dependency of CoS's gateway; bring Postgres back
  first.
- **The undercount:** the calls served inside the cache window were **lost
  from the stored spend** when the gateway restarted before the database came
  back (the probe: 3 of 3 lost). The bound is that window: rpm 10 × 60 s =
  at most **10 turns, about $0.40, per outage**; in practice one or two of
  Robert's notes, since turns are owner-only and one at a time.
- **So the Anthropic console limit on MC's own key is the hard ceiling** (step
  3 asks Robert to confirm it); the gateway cap is the working limit. A
  database outage is not a spend stop: use the turn gate or `mc.sh down`.

### Steps (after review; each runtime step needs Robert's go-ahead)

**The CoS regression question, in every step below** (one real CoS call, with
Robert's go-ahead): one CoS question with web search from
`https://dev.minimoi.ai/app/cos`. It passes **only** with a cited answer
**and** a new line with `"logical_model":"minimoi-cos-web-search"` in
`~/minimoi-staging/data/model_gateway_receipts.jsonl` (count the lines before
and after). An answer without that new receipt line is a **fail**: it means
the xAI web-search route under the renamed `GATEWAY_XAI_API_KEY` did not run.
CoS's xAI chat fallback, which a normal question never reaches, is proven in
`mc_probe/stage_c.py` (Anthropic 500 on the primary, then xAI on CoS's key).

1. **Roll the release out** (coordinator): `build.sh <branch> --reviewed-branch`,
   `mc.sh build`, `mc.sh token`, `up.sh && verify.sh`. The gateway is recreated
   with the renamed provider keys and MC's route (placeholder key). Then the CoS
   regression question, **with its receipt line**: CoS's routes now read
   `GATEWAY_ANTHROPIC_API_KEY` / `GATEWAY_XAI_API_KEY`.
2. **RB-H3: a known-invalid key gets Anthropic's own refusal** (coordinator; no
   spend, the placeholder is still in place):
   ```
   docker exec minimoi-model-gateway python -c "
   import json,os,urllib.request as u,urllib.error as e
   r=u.Request('http://127.0.0.1:4000/v1/chat/completions',method='POST',data=json.dumps({'model':'minimoi-mc-agent','max_tokens':1,'messages':[{'role':'user','content':'x'}]}).encode(),headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY'],'Content-Type':'application/json'})
   try: u.urlopen(r,timeout=60); print('ANSWERED: stop, this must not happen')
   except e.HTTPError as x: print(x.code, x.read()[:300])"
   ```
   Pass: `401` with an Anthropic authentication error naming an invalid
   x-api-key. Anything else: stop.
3. **Robert, command 1** (one paste): `scripts/staging/mc.sh gateway-keys`.
   **Before it, set a monthly spend limit on MC's own Anthropic key in the
   Anthropic console** (for example $20, just above the cap in step 4). The
   command first asks Robert to type `yes` to confirm that limit exists, and
   changes nothing otherwise: the console limit is the hard ceiling, because
   the gateway's cap can undercount while its key database is down (see
   "Spend: what bounds it"). Then it makes the key database and its role, asks
   for **Master Craftsman's own Anthropic key** (hidden; it refuses CoS's
   key), recreates **only** the model gateway (CoS's calls fail for about
   30 s) and checks that the key tables exist. A re-run with nothing new
   leaves the database, its password and the gateway as they are. Then the
   CoS regression question again, **with its receipt line** (coordinator).
4. **Robert, command 2** (one number): `scripts/staging/mc.sh key`. It asks for
   MC's monthly cap in dollars (Enter for 15), makes MC's key inside the
   gateway, writes it to `mc.env` without printing it, and recreates only
   `mc-agent`. It prints only the cap, period, model, routes, rpm and the last
   four characters of the key id. Running it again asks before rotating.
5. **Checks** (coordinator; no spend): `scripts/staging/verify.sh`. Section 9
   now runs the stage C checks with MC's real key:
   - **C5/K1:** from inside MC, every route but chat on MC's own model must be
     refused with **401 or 403 only** (the stage-A `no_db_connection`
     exception is gone): `/v1/models`, `/health`, `/model/info`, `/spend/logs`,
     `/key/list`, `/key/info` (another key's too), `/key/generate`,
     embeddings, `/v1/responses`, `/v1/messages`, `/anthropic/*`, `/openai/*`,
     and every `minimoi-cos-*` model.
   - **Positive controls** (inside the gateway, master key): MC's key info
     `200` with the model, routes, `30d` and the cap; `/v1/models` `200`.
   - The gateway's `ANTHROPIC_API_KEY` and `XAI_API_KEY` are empty (no
     pass-through spend); `MC_ANTHROPIC_API_KEY` is set.
   - `mc.sh status`: MC healthy, self-check passed.
   K1b (a spent budget refuses the next call) is proven in the throwaway
   `mc_probe/stage_c.py`, not on staging: it would cost a real call.
6. **Robert's first note.** Turn MC on (stage B step 5, if not already), keep
   one short note on `/guild-next/guild/build`. Pass: a reply kept as
   **Master Craftsman's**, the header then reads **live**, and verify.sh's
   check 9 shows MC's spend above $0 and far below the cap.
   `docker logs minimoi-portal` shows `mc turn <id> end status=answered`.

### Rollback

- **Stop MC's turns:** `printf 'off\n' > ~/minimoi-staging/state/mc.turns;
  scripts/staging/up.sh portal`. `mc.sh down` stops MC and the relay.
- **Take the key database away** (every virtual key stops working; if CoS has
  its own key, run `scripts/staging/cos.sh off` first so CoS is back on the
  master key): `printf 'off\n' > ~/minimoi-staging/state/gateway.keys;
  scripts/staging/up.sh model-gateway`, then the CoS regression question with
  its receipt line. The database,
  its role and `gateway.env` are kept; `mc.sh gateway-keys` turns it back on
  without a new password.
- **The key database down:** see "Spend: what bounds it". Stop MC with the
  turn gate or `mc.sh down`; do not restart the gateway until Postgres is
  back (it would not start, and CoS would be down with it).

## Usage records (usage-record U1; staging only)

Every call through the staging gateway, on every route and outcome, leaves
one standard usage record: `~/minimoi-staging/data/usage/usage-YYYY-MM.jsonl`
(mode 600, one JSON line per call; no prompt, answer or key). The design is
`planning-studio/.../documents/USAGE_RECORD_DESIGN_NOTE_2026-09-29.md`.

- **What is recorded:** `ok` (tokens and LiteLLM's priced cost), `error` (a
  deployment was tried and failed; cost null), `refused` (the gateway said no
  before any deployment: a key, route, model, budget or rate limit; cost null).
  `actor` is `mc` or `cos` (from the key alias `mc-agent-*`/`cos-agent-*`,
  else the route name).
- **How:** `services/usage/litellm_recorder.py` is a second LiteLLM callback
  next to `receipt_callback` (CoS's receipts are unchanged). build.sh copies
  it and `services/usage/usage_record.py` to `config/usage/`; the gateway
  mounts them read-only and writes `data/usage/`. Writing is fire and forget:
  a full queue or a write error drops the line with a warning and never fails
  a call. Production is unchanged: no production image or config loads it.
- **Rollout:** `build.sh <branch> --reviewed-branch`, then
  `up.sh model-gateway` (the gateway is recreated; CoS's calls fail for
  about 30 s), `verify.sh` ("gateway usage recorder mounted"), then the CoS
  regression question: pass is a cited answer **and** a new line in
  `data/usage/usage-*.jsonl` with `"route":"minimoi-cos-web-search"`,
  `"actor":"cos"`, `"status":"ok"` (as well as the receipt line). One MC note
  on the Shop floor leaves an `"actor":"mc"` line with its tokens and cost.
- **Direct calls (U2):** calls that skip the gateway record through
  `services/usage/direct.py` into the same store: the CoS loops' Tavily
  searches (`kind: search`, `units: {"searches": 1}`, no cost on the free
  tier) and CoS's direct Grok backend (tokens from the SDK's `usage`; cost not
  priced yet, `cost_source: none`). cos-bot and cos-scheduler mount
  `data/usage/` with `MINIMOI_USAGE_DIR` set. No query, prompt or answer is
  recorded. (The loops' Tavily key is read from the Mac Keychain, so inside
  staging's containers the loops skip search, and write no search record.)
- **The Shop floor footer (U3):** under each live MC reply, "Done in 1.2s ·
  96 output tokens". The portal reads `data/usage/` read-only and sums the
  output tokens of MC's gateway records inside that turn's window (MC takes
  one turn at a time, so that is exactly the turn's calls; OpenClaw's own
  usage is zeros and is never used). The record lands a moment after the
  reply, so the page asks again a few times; with no store or no matching
  record it says "tokens unknown", never a guess.
- **Proof without spend:** `mc_probe/stage_c.py` checks MC and CoS records,
  every CoS route, the fallback's error, refusals (MC's key on CoS's models,
  the spent budget), and that no content or key reaches the store.
- **Rollback:** build and `up.sh model-gateway` the previous release; the
  records already written stay.

## CoS's own capped gateway key (cos.sh key; staging only)

Robert, September 28 2026: CoS gets its own capped gateway key too, instead of
the master key (production later, as its own step with Robert's OK).

- **What changes:** only Agent A's `MINIMOI_MODEL_GATEWAY_KEY` (its provider
  config and its bounded web-search plugin both read it). With `state/cos.key`
  on, `docker-compose.staging-cos-key.yml` gives it CoS's own virtual key from
  `~/minimoi-staging/cos.env` (mode 600, interpolation only). The gateway keeps
  the master key; staging's provider keys are reused (nothing new from a
  provider).
- **The key:** CoS's four routes (`minimoi-cos-agent`, `-xai-fast`,
  `-web-search`, `-anthropic`); chat completions and responses only (not
  `/key/info`, not embeddings, not the pass-through); a monthly budget (`30d`);
  rpm 60 (a CoS turn can make several calls); alias `cos-agent-…`.
- **The trade-off:** with its own key CoS depends on the gateway's key
  database: with Postgres down, CoS's key is refused after the gateway's 60 s
  key cache (the MC stage C probe). `cos.sh off` goes back to the master key.

### Steps (each needs Robert's go-ahead)

1. **Before** (coordinator): the CoS regression question. It passes only with
   a cited answer **and** a new usage line in
   `~/minimoi-staging/data/usage/usage-*.jsonl` with
   `"route":"minimoi-cos-web-search"`, `"actor":"cos"`, `"status":"ok"` (and
   the receipt line in `data/model_gateway_receipts.jsonl`). Note its
   `key_ref`: `null` (the master key).
2. **Robert, one number** (about 5 minutes): `scripts/staging/cos.sh key`. It
   asks for CoS's monthly cap in dollars (Enter for 30), makes the key inside
   the gateway, writes it to `cos.env` without printing it, and recreates
   **only** `cos-agent-a` (CoS's turns fail for about a minute). It prints only
   the cap, period, models, routes, rpm and the last four characters of the
   key id. A re-run asks before rotating. It needs the key database
   (`mc.sh gateway-keys`, done in MC stage C).
3. **Checks** (coordinator; no spend): `verify.sh` section 10: from inside
   Agent A, its key is not the master key and is refused (401/403 only) on
   `/key/list`, `/v1/models`, MC's route, embeddings and the pass-through;
   with the master key, its record shows CoS's four routes, chat and responses
   only, `30d` and the cap (and what it has spent).
4. **After** (coordinator): the CoS regression question again. Pass: a cited
   answer **and** a new usage line with `"route":"minimoi-cos-web-search"`,
   `"actor":"cos"`, `"status":"ok"` and `"key_ref":"cos-agent-…"`. A 401/403
   from the gateway in Agent A's log means the key's scope is wrong: `cos.sh off`.

Rollback: `scripts/staging/cos.sh off` (state/cos.key off; Agent A is recreated
with the master key; the key stays in `cos.env` and the gateway, unused).

**Order when taking the key database away:** run `cos.sh off` **before**
turning `state/gateway.keys` off. With CoS's own key and no key database,
every CoS call is refused (and `up.sh` refuses to start with state/cos.key on
and the key database off).

`verify.sh` section 10 also fails when any usage record since the latest
`cos.sh key` shows a **refused** call on CoS's key (`key_ref` `cos-agent-…`):
that is a route CoS uses that its key's scope misses.

## The local Workshop (slice 4a: observe and record, read only; staging only)

The Workshop screen (`/guild-next/guild/workshop?item=<id>`, or **Open in
Workshop** on a queue item) shows what the Mac is doing: the item's approved
scope, the next actor, the agent clients running, the last event, the host's
admission verdict for a new run, queued work, this month's gateway spend,
Needs you, and recovery steps. It is read only and model free: the page and
its one-minute refresh read files and the queue, never a model, and nothing
launches (the launcher is 4b). On a phone it leads with Now, Needs you and
Budget, folds the rest, then the chat.

**Where the data comes from.** `scripts/workshop/workshop.py` runs on the Mac
from the repository (no container, no key):

```bash
venv/bin/python3 scripts/workshop/workshop.py observe   # one host reading: memory, swap, disk, agent clients
venv/bin/python3 scripts/workshop/workshop.py event --actor claude-code --item pr:265 \
    --kind needs_you --stage review --text "Review slice 2" --next-actor robert
venv/bin/python3 scripts/workshop/workshop.py state     # the derived state
venv/bin/python3 scripts/workshop/workshop.py sync      # to ~/minimoi-staging/data/workshops/
```

The record is `~/minimoi-workshops/<id>/events.jsonl` (append only) plus
`state.json` (derived, written atomically under a lock). `sync` appends the
new whole lines and replaces `state.json`; only those two files go. The portal
mounts `data/workshops` read only (`MINIMOI_WORKSHOPS_DIR`, id
`MINIMOI_WORKSHOP_ID=mac`).

**The admission verdict.** `ok` (room for one run), `tight` (another agent
session is running, or a resource is near its limit), `blocked` (memory free
under 10%, swap over 6 GB, disk free under 10 GB, or three or more agent
sessions), `unknown` (a probe failed). Each verdict names its limit, and the
screen shows all four. It counts agent **sessions on this Mac, outside
Docker**: terminal or desktop Claude Code, and the Codex CLI (native or npm),
whoever started them. A wrapper and its child, or a parent and a child of the
same client, count as one session. The ChatGPT app's bundled Codex and an
OpenClaw gateway are listed as background apps and are not counted. The
staging containers' agents run inside the Colima VM, where the Mac's `ps`
cannot see them. `ps` is read with the executable path last; arguments are
read only for `node` processes, to find the script, and are never kept. A missing, unreadable or stale record (the host
reading is over 15 minutes old) shows as unknown, never as "nothing running".
`observe` writes a health event when the verdict, the reasons or the clients
change, and every 9 minutes otherwise, so a quiet host stays fresh.

**Not installed yet.** Nothing runs `observe` and `sync` on a schedule: until a
launchd job is added (with Robert's go-ahead), run them by hand before looking
at the screen, or the page shows unknown after 15 minutes.

## Master Craftsman streaming (S1; staging only)

**What it is.** MC's replies on the Shop floor stream: "Working… Ns", then the
text as it is written, then "Done in Ns · N output tokens". The spec is
`SPEC_STREAMING_CHAT_COS_MC_v0.2` with the v0.3/v0.3.1 amendment (in
planning-studio). CoS does not stream yet (that is S2).

**Switches.** Streaming happens only when both are true, and the server decides:
- `MINIMOI_GUILD_MC_STREAM` is on. The code default is off; `docker-compose.staging.yml`
  sets it on. `/guild-next` never mounts in production.
- The MC backend supports streaming (openclaw and the stub do; grok does not).

With either one false, the page uses the non-streaming `/mc/turns`, unchanged.

**Path.** browser → portal `POST /guild-next/api/v1/mc/turns/stream` → mc-relay
(`stream: true`) → MC → gateway.
- The relay emits its own NDJSON, and nothing of MC's JSON passes.
- The portal sends `ack` only after its guards, and dispatches only after
  `ack`.
- One dispatch per turn: a `request_id` already sent is refused on both
  endpoints for 10 minutes.
- A browser that leaves does not stop the run. The relay and the portal read
  to the end, and a real answer is kept (a reload shows it).
- Only the limits and the owner's **Stop** (`POST …/mc/turns/<turn_id>/stop`,
  then the relay's `POST /v1/turns/stop`) end a run early.
- The UI says what Stop cannot save: what was already generated may still be
  billed.

**Limits.**

| Hop | Wall clock | Idle | Text | Line |
|---|---|---|---|---|
| Relay | 120 s | 30 s | 256 KB | 64 KB |
| Portal | 125 s | 30 s | 256 KB | 64 KB |

The portal also sends `render` (server-rendered, sanitised Markdown) at most
every 500 ms, only on change, and none past 32 KB.

**Usage.**
- The portal's usage mount is now **read-write**, for one kind of line only:
  `emitter: runtime-stream`, `actor: mc`, `route: openclaw/mc-agent`, keyed by
  the turn id (`correlation_id`), with `cost_usd` null.
- A test holds that `minimoi_portal/guild_ui/mc/stream_usage.py` is the
  portal's only writer.
- The gateway's own record stays the source for cost. An interrupted or stopped
  run leaves a `status: error` line and an `mc_turns.jsonl` line.

**Rollout.**
- The portal and the relay both changed. The relay is `docker/mc-agent/relay.mjs`,
  in the MC image, so a staging rollout rebuilds the portal and MC's image.
- **No-spend probe first:** `scripts/staging/mc_probe/stage_b.py` (throwaway
  containers; it mounts this branch's relay). It checks P4 (text, finish,
  usage, timings), P1 (a mid-stream failure), P2 (Stop, and whether OpenClaw
  cancels its own upstream call) and a caller that leaves.
- **First run** (2026-09-29, MC image `fa1e9ec`, OpenClaw 2026.9.6): 22/22.
  - P4: first text at 1.8 s, finish at 3.4 s, with usage passed through.
  - P1: after a mid-stream failure, OpenClaw retried its upstream call five
    times, then went quiet. The relay's 30 s idle limit ended it as a failure;
    it may have been paid, several times.
  - P2: Stop made OpenClaw close its own call to the model endpoint.
  - A caller that left did not stop the run.
- The gateway-to-provider hop on abort is not probed yet (stage C).

**Two real turns, without a browser** (paid; they run after rollout, with
Robert's budget). These go through the same server path as the Shop floor,
inside the portal container:

```bash
scripts/staging/mc_cost_probe.sh --stream --yes-spend                          # turn A, then turn B
scripts/staging/mc_cost_probe.sh --stream --yes-spend --stop-after-first-text  # turn B alone
```

- **Turn A** (streaming and usage) prints:
  - the time to the first delta and to the finish;
  - the event counts;
  - the `mc_turns.jsonl` line;
  - the `runtime-stream` record;
  - the gateway's records in the turn's window, with output tokens compared.
- **Turn B** stops after the first text, and prints:
  - the stopped status;
  - the `runtime-stream` error line;
  - whether the gateway recorded the aborted call, and with what tokens.
- Spend is never above $1, and a turn whose cost cannot be read stops the
  probe.

## Rules

- **One writer per state folder.** No Mac-native process writes
  `~/minimoi-staging` while staging runs: the queue store's `flock` does not
  cross the Colima VM boundary. `verify.sh` lists native processes with files
  open there. **One exception:** `scripts/workshop/workshop.py sync` writes
  `data/workshops/` one way. The portal mounts that folder read only, so the
  sync is its only writer: `state.json` is replaced atomically, and a torn
  `events.jsonl` line is skipped. The sync runs for a moment and holds no files
  open, so `verify.sh` does not list it unless it runs during the check.
- Never `down -v`, never delete the external volumes or the
  `personal-ai-agents_*` volumes (the rollback copy).
- The staging bots use the **test** bot tokens; start them only after their
  native pollers are booted out (one poller per token). `up.sh` captures
  `launchctl list` before matching, so the check cannot pass by accident.
- The staging bot tokens come **only** from the Keychain test accounts, never
  from the root `.env` (it may hold a production token, and the environment
  variable wins over the role in `utils/telegram.py`). `env.sh` has no
  `TELEGRAM*TOKEN*` name in its copy list, reports any in the root `.env` as
  ignored, and records every name's source in `env.sources`.
- Staging `.env` never holds `TELEGRAM_BOT_TOKEN`, `TELEGRAM_POLLING_BOT_TOKEN`,
  any `AWS_*` or production token; `env.sh` refuses to write them.

## Local development (not staging)

Staging is for proving a release. Day-to-day code changes run **outside**
Docker, from your own worktree, on ports that never collide with staging:

```bash
cd <your worktree>
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
cp -Rp ~/minimoi-staging/data/german /tmp/german-dev-state            # a throwaway COPY, never the staging folder
unset MINIMOI_ROOT                                                    # local dev never points at staging data
MINIMOI_ROLE=standby GERMAN_STATE_DIR=/tmp/german-dev-state PORT=18567 \
  .venv/bin/python domains/german/html_server.py
```

- Use a `185xx` port per service (for example portal 18501, german 18567,
  portuguese 18570, curator 18566).
- Portuguese: `PORTUGUESE_DATA_DIR=/tmp/portuguese-dev-state` (a copy).
- Portal: `GUILD_QUEUE_PATH=/tmp/guild-dev/build_queue.json` with a copied queue
  (the queue store refuses a folder inside the code tree).
- `DATABASE_URL`: the read-only `robert_ro` role against staging's Postgres on
  `127.0.0.1:5432`, or a separate `minimoi-local` Postgres; never the staging
  writer role.
- The Guild prototype runs standalone:
  `.venv/bin/python prototype-lab/projects/guild-interaction-prototype/app.py --port 18895`.
- When the change is ready: PR, review, merge, then `build.sh` + `up.sh` +
  `verify.sh` puts it on dev.minimoi.ai.
