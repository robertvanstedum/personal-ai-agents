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
9. Master Craftsman (checks 9a-9p): the portal's `MINIMOI_GUILD_MC` matches
   `state/mc.mode`; `cos-agent-a`'s start matches `state/mc.agent` (and a CoS
   or MC self-check failure is shown loudly); MC key names only on their
   holders and never in `.env`; a no-key gateway call gets 401; per-agent
   effective tools; scheduler off; CoS callers pinned to `cos-agent-a`.

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

## Master Craftsman stage 1a (MC beside CoS in Agent A's OpenClaw, no MC spend)

Spec: MC spec v0.7 (`SPEC_MASTER_CRAFTSMAN_OPENCLAW_BACKEND_v0.7_2026-09-27.md`)
with Robert's September 28 decisions. Staging only; production keeps #244's
start path and the CoS-only config. **The coordinator runs these steps after
review; every runtime step needs Robert's go-ahead.**

What 1a turns on: `cos-agent-a` starts through `start-with-mc.sh` (overlay
`docker-compose.staging-mc.yml`) with a second agent, `mc-agent`: its own
workspace, only `session_status`, pinned to its own route and key. MC's key
does not exist yet, so the container gets a placeholder the gateway refuses
(401): **no MC turn can spend anything**. The Shop floor shows
"Master Craftsman is unavailable · not connected yet". CoS keeps its current
gateway key (N8 = no).

**The readiness gate.** On every start the script checks the config on a
loopback-only gateway first (nothing outside the container can connect; CoS
callers get "connection refused", like a stopped container), then serves the
checked config on the LAN. Expect about 1 to 2 minutes (probe: 44 to 52 s)
before `127.0.0.1:18790` and in-network CoS callers answer after any restart;
the health check has a 240 s start period. If CoS's own check fails, the
container stays up and unhealthy and OpenClaw is not started (no loop). If
only MC's check fails, CoS starts alone and `verify.sh` fails 9b loudly. If
the check cannot complete (a gateway call times out twice on a busy host),
nothing is served and nothing sticky is written: the container exits and
Docker restarts it (`state` shows `check-inconclusive-*` meanwhile).

Switches (both under `~/minimoi-staging/state/`, read by `lib.sh`):

| File | Values | Effect |
|---|---|---|
| `mc.agent` | `on` / absent or `off` | adds `docker-compose.staging-mc.yml` (combined start) |
| `mc.mode` | `off` (default), `stub`, `openclaw`, `grok` | the portal's `MINIMOI_GUILD_MC`; `openclaw` needs `mc.agent` on; `grok` shows "not built yet" |

MC's secrets (1b) go only in `~/minimoi-staging/mc.env` (mode 600), never in
`.env`.

### 0. Before (no change)

```bash
colima ssh -- free -m                                   # "available" >= about 1.7 GB with staging idle; stop below 1.2 GB
ps -axo rss=,command= | grep -i '[o]penclaw' | head -5 # Robert's personal OpenClaw on the Mac: record its RSS (KB); spec 1a entry
docker exec minimoi-cos-agent-a node /app/openclaw.mjs gateway call sessions.list --params '{"agentId":"cos-agent-a","limit":500}' --json \
  | python3 -c 'import json,sys; t=sys.stdin.read(); d=json.loads(t[t.index("{"):]); print("cos sessions:", len(d.get("sessions") or d.get("items") or []))'   # baseline for 9p
```

Optional, no spend, staging untouched (needs about 1.3 GB free in Colima):
re-run the isolation gates on the image `build.sh` makes in step 2:
`python3 scripts/staging/mc_probe/gates.py --image minimoi-staging/cos-scheduler:agent-a-<sha7>`.

### 1. Snapshot both Agent A volumes (container stopped)

Adding a second agent with `agents.ownership: "explicit"` may migrate state
(V17), so snapshot first, as for the 9.6 upgrade:

```bash
S=~/minimoi-staging; STAMP=$(date +%Y%m%d-%H%M%S)
AGENT_A_IMG=$(docker inspect -f '{{.Config.Image}}' minimoi-cos-agent-a)
echo "$AGENT_A_IMG" > "$S/rollback/agent-a-image-before-mc.txt"
cp "$S/RELEASE" "$S/rollback/RELEASE-before-mc"
echo "$STAMP" > "$S/rollback/agent-a-premc-stamp.txt"
scripts/staging/down.sh cos-agent-a
for v in state auth; do
  docker volume create "minimoi-staging-cos-agent-a-$v-premc-$STAMP"
  docker run --rm --network none --user 0:0 \
    -v "minimoi-staging-cos-agent-a-$v:/from:ro" -v "minimoi-staging-cos-agent-a-$v-premc-$STAMP:/to" \
    --entrypoint cp "$AGENT_A_IMG" -a /from/. /to/
done
docker volume ls | grep premc                            # both copies listed
```

### 2. Build the reviewed branch, MC still off

```bash
printf 'off\n' > ~/minimoi-staging/state/mc.agent; printf 'off\n' > ~/minimoi-staging/state/mc.mode
scripts/staging/build.sh claude/mc-stage-1a --reviewed-branch
scripts/staging/up.sh && scripts/staging/verify.sh      # 9b: CoS-only start, image's CoS-only config
```

### 3. Turn Master Craftsman on (the CoS-changing step)

```bash
printf 'on\n' > ~/minimoi-staging/state/mc.agent; printf 'openclaw\n' > ~/minimoi-staging/state/mc.mode
scripts/staging/up.sh                                   # recreates cos-agent-a (combined start) and the portal
until [ "$(docker inspect -f '{{.State.Health.Status}}' minimoi-cos-agent-a)" = healthy ]; do sleep 5; done
docker exec minimoi-cos-agent-a cat /tmp/minimoi-mc/state          # serving-combined
docker logs minimoi-cos-agent-a 2>&1 | grep start-with-mc          # CHECK phase, then "serving CoS and Master Craftsman"
scripts/staging/verify.sh                               # all checks, including 9a-9p
colima ssh -- free -m                                   # record; stop if "available" < 0.5 GB
```

Then, no spend: an MC turn over loopback must answer **401** (the gateway
refuses MC's placeholder key; nothing reaches a provider):

```bash
docker exec minimoi-cos-agent-a node -e "fetch('http://127.0.0.1:18789/v1/chat/completions',{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+process.env.OPENCLAW_GATEWAY_TOKEN},body:JSON.stringify({model:'openclaw/mc-agent',user:'guild-mc:stage-1a-check',messages:[{role:'user',content:'stage 1a check'}]})}).then(async r=>console.log(r.status,(await r.text()).slice(0,160)))"
# expect: 401 {"error":{...,"type":"authentication_error"}}
```

Robert opens `https://dev.minimoi.ai/guild-next/guild/build`: the conversation
header reads "Master Craftsman is unavailable · not connected yet".

### 4. One real CoS turn with web search (N13; costs one CoS call)

This turn matters more than usual: the combined config turns Tool Search off
(`tools.toolSearch: false`), so CoS's model now gets `web_search` and
`session_status` directly instead of through `tool_search`/`tool_call`, as it
does in production today (probe gate (g)). The answer must show a real search.

With Robert's go-ahead, from the CoS surface (`https://dev.minimoi.ai/app/cos`),
ask something that needs a search, for example "What is today's date in
Chicago, and one headline from today? Search the web." Expect a cited answer
and a new line in `~/minimoi-staging/data/model_gateway_receipts.jsonl`. Then
`scripts/staging/verify.sh` again; the CoS session count (9p) must not be
lower than step 0's.

### 5. Clean stop, then the rollback round trip (tested once)

```bash
time docker stop -t 30 minimoi-cos-agent-a              # well under 30 s; exit code 0
printf 'off\n' > ~/minimoi-staging/state/mc.agent; printf 'off\n' > ~/minimoi-staging/state/mc.mode
scripts/staging/up.sh && scripts/staging/verify.sh      # CoS-only start (#244's apply-config.sh); 9b passes
printf 'on\n' > ~/minimoi-staging/state/mc.agent; printf 'openclaw\n' > ~/minimoi-staging/state/mc.mode
scripts/staging/up.sh && scripts/staging/verify.sh      # combined again
```

### Rollback

- **MC off, CoS untouched:** `printf 'off\n' > ~/minimoi-staging/state/mc.agent; printf 'off\n' > ~/minimoi-staging/state/mc.mode; scripts/staging/up.sh`.
  `cos-agent-a` is recreated on #244's start and the CoS-only config.
- **Back to the previous release:** `scripts/staging/build.sh "$(sed -n 's/^sha=//p' ~/minimoi-staging/rollback/RELEASE-before-mc | tail -n 1)"` then `up.sh && verify.sh`.
- **If CoS misbehaves after MC was on** (state migrated, V17): restore the
  snapshot with the previous image, as in the 9.6 rollback:

```bash
S=~/minimoi-staging; STAMP=$(cat "$S/rollback/agent-a-premc-stamp.txt"); PREV=$(cat "$S/rollback/agent-a-image-before-mc.txt")
printf 'off\n' > "$S/state/mc.agent"; printf 'off\n' > "$S/state/mc.mode"
scripts/staging/down.sh cos-agent-a
for v in state auth; do
  docker run --rm --network none --user 0:0 \
    -v "minimoi-staging-cos-agent-a-$v-premc-$STAMP:/from:ro" -v "minimoi-staging-cos-agent-a-$v:/to" \
    --entrypoint sh "$PREV" -c 'find /to -mindepth 1 -delete && cp -a /from/. /to/'
done
scripts/staging/build.sh "$(sed -n 's/^sha=//p' "$S/rollback/RELEASE-before-mc" | tail -n 1)"
scripts/staging/up.sh && scripts/staging/verify.sh
```

- **Self-check markers** (sticky, on the state volume): read, fix, clear, restart.
  - MC's check failed (CoS runs alone): `docker exec minimoi-cos-agent-a cat /home/node/.openclaw/.mc-selfcheck-failed`, then `docker exec minimoi-cos-agent-a rm /home/node/.openclaw/.mc-selfcheck-failed && docker restart minimoi-cos-agent-a`. A new release retries by itself.
  - CoS's check failed (both down, container unhealthy): the same with `.cos-selfcheck-failed`. With `mc.agent` off the marker is ignored (#244's start), which is the fast way back.

Keep the `premc` copies until MC has run cleanly for a week.

### Stage 1b (not in 1a)

Robert creates MC's own capped key (about $15/month) and MC's own Anthropic
key with a console limit, and stores them himself; they go into
`~/minimoi-staging/mc.env` (`MC_MODEL_GATEWAY_KEY`, `MC_ANTHROPIC_API_KEY`).
1b also needs the gateway's key database, the known-invalid-key 401 proof
(H3), K1/K1b, and the receipt-callback change (RB-H1) before any paid MC turn.

## Rules

- **One writer per state folder.** No Mac-native process writes
  `~/minimoi-staging` while staging runs: the queue store's `flock` does not
  cross the Colima VM boundary. `verify.sh` lists native processes with files
  open there.
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
