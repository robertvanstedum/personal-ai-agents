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
| `build.sh <ref> [--reviewed-branch] [--no-fetch] [--allow-emulated]` | fetch, pin `~/.worktrees/staging-release` at `<ref>` (must be on `origin/main` unless `--reviewed-branch`), build the 9 images with deploy.yml's service map, fail if any image is not native, write `RELEASE`, `release.env`, the gateway config |
| `seed.sh [--ref R] [--queue-ref R]` | copy the dev data into `~/minimoi-staging` (refuses a non-empty target); queue from `origin/main:data/guild/build_queue.json` |
| `seed.sh --postgres` | copy the dev Postgres volume into `minimoi-staging-postgres-data` (only while no container uses the source), prove it by digest, create the two fresh Agent A volumes |
| `seed.sh --docs-only` | refresh `docs/specs` and `docs/design` from the pinned release (like `sync_docs.sh`: add/update, never delete) |
| `seed.sh --drift` | list source files that changed since the seed (a writer that was missed); exit 1 if any |
| `env.sh [--force]` | write `~/minimoi-staging/.env` (600) from the root checkout's `.env` by name; Keychain only for a missing value |
| `up.sh [service ...]` | `up -d --no-build --remove-orphans`; bots only with `state/bots.on` and no native poller loaded |
| `down.sh [service ...]` | whole stack down (volumes and files kept), or stop named services |
| `status.sh` | containers, images vs `RELEASE`, health, port holders |
| `verify.sh [--no-guild-routes]` | the acceptance checks below; non-zero on any failure |
| `jobs.sh lesen\|curator\|intelligence\|leitura` | run one background job by hand, logged to `logs/` (Spec 159: dev jobs run only when triggered) |

### What `verify.sh` proves

1. every container runs the release image under project `minimoi-staging`, is
   not restarting, and is healthy where it has a health check;
2. `/health` is 200 in-network for portal, curator, german, portuguese and
   cos-scheduler; the gateway (`127.0.0.1:14000`) and Agent A
   (`127.0.0.1:18790`) answer from the host;
3. the portal answers on `127.0.0.1:5001` and **no native process holds 5001**
   (only Colima's forwarder);
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
   only on the portal (names only);
8. Postgres has the `guild` and `research` schemas.

## Cutover runbook (one sitting; every runtime step needs Robert's go-ahead)

`S=~/minimoi-staging`. Scripts path below is `scripts/staging/` in the root
checkout (`~/Projects/personal-ai-agents`) or in any worktree that has them.
Expected dev.minimoi.ai outage: from step 3 to step 6, about 15 minutes.

### 1. Seed the files (nothing stops)

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
`MISSING`. Rollback: delete `~/minimoi-staging/.env` (it only holds copies).

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

### 5. Start staging (bots off)

```bash
scripts/staging/up.sh && scripts/staging/status.sh
```

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
scripts/staging/verify.sh                     # all checks; 5001 held only by the Colima forwarder
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
Rollback for each: stop the staging service (`down.sh <service>`), rename the
plist back, `launchctl bootstrap`, and for a port, `docker restart` the staging
container only **after** the native one has bound (order matters).

| # | Native job | Replacement | Verify |
|---|---|---|---|
| 7a | `com.vanstedum.cos-bot` | `touch ~/minimoi-staging/state/bots.on; scripts/staging/up.sh` | `docker logs minimoi-cos-bot`: polling, no `Conflict`/409 |
| 7b | `com.vanstedum.system-bot` | same profile (both bots start together; boot out both natives before 7a's `up.sh`) | `/status` to the system **test** bot says `role=standby`; no 409 |
| 7c | `com.vanstedum.german-html-server` | `docker restart minimoi-german` | `lsof :8767` is the forwarder; `/app/german` loads; `seed.sh --drift` |
| 7d | `com.user.portuguese` | `docker restart minimoi-portuguese` | `lsof :8770` is the forwarder; `/app/portuguese` loads |
| 7e | `com.vanstedum.lesen-refresh` | `scripts/staging/jobs.sh lesen` (manual) | job ok; writes `~/minimoi-staging/data/german/config/lesen_articles.json` |
| 7f | `com.user.portuguese-leitura` | `scripts/staging/jobs.sh leitura` (manual) | exits 0 against the staging DB |
| 7g | `com.vanstedum.curator-priority-feed` | none (no active priorities, no prod equivalent) | nothing writes the root `priorities.json` |

Bots rollback: `rm ~/minimoi-staging/state/bots.on; scripts/staging/down.sh system-bot cos-bot`,
then rename the native plists back and `launchctl bootstrap` them.

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

## Rules

- **One writer per state folder.** No Mac-native process writes
  `~/minimoi-staging` while staging runs: the queue store's `flock` does not
  cross the Colima VM boundary. `verify.sh` lists native processes with files
  open there.
- Never `down -v`, never delete the external volumes or the
  `personal-ai-agents_*` volumes (the rollback copy).
- The staging bots use the **test** bot tokens; start them only after their
  native pollers are booted out (one poller per token).
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
