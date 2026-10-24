# Dev deploy path — design v0.1 (24 Oct 2026)

Status: **design, awaiting Robert's go.** Nothing built, nothing changed on dev.
Author: Claude Code. Reviewer: Codex.

## Problem

There is no standard way to put a branch or commit on dev (`dev.minimoi.ai`, the Mac behind the
Cloudflare tunnel). `deploy.yml` deploys only `main`, only to production. For dev, each operator
(Claude Code, Codex) has hand-written a builder and a `compose.py` per change under
`~/minimoi-runtime/operators/<change>/`. It works, but:

- It depends on whichever agent is operating, and each record is shaped differently.
- The portal's compose chain is now **63 files**. Every promotion appended one more `new.json`, and
  some chain files live in the shared checkout (`services/mc_operations/dev-native-overlay.yml`,
  several `_working/…/new.json`). If one of those is edited, the next `up` silently picks it up.
- Overlay images are layered on overlay images (`FROM minimoi-portal:dev-journeys-20261021` …).
  Deleted files never leave, and nobody can say for certain which commit dev is running.
- Rollback exists, but it is a different command in each record.

Goal: **one command** puts a named service at a pinned commit on dev, writes one dated operator
record with a one-command rollback, verifies health, and rolls back by itself if verification fails.
Production is untouched; production stays behind the ITIL change control standard.

## Command shape

```
scripts/operations/deploy_dev.sh <service> <commit> --operator <name> [--overlay] [--with-migrations-applied] [--dry-run]
scripts/operations/deploy_dev.sh rollback <service> [--operator <name>]      # back to the previous pin
scripts/operations/deploy_dev.sh status [<service>]                          # what commit/image each service runs
```

`deploy_dev.sh` is a thin wrapper; the logic is `scripts/operations/dev_deploy.py` (testable,
matching `release_status.py` / `production_activation.py`). It runs from **any worktree** and reads the
commit from git's object store (`git archive <commit>`); it never checks anything out, so the shared
checkout `~/Projects/personal-ai-agents` is never touched.

## 1. Services supported

| v1 | Service | Container | Health |
|---|---|---|---|
| yes | portal (+ linked `board-api` sidecar, same image) | minimoi-portal, minimoi-board-api-dev | `127.0.0.1:5001/health`, `https://dev.minimoi.ai/health` |
| yes | german | minimoi-german | `127.0.0.1:8767/health` |
| yes | portuguese | minimoi-portuguese | `127.0.0.1:8770/health` |
| yes | cos-scheduler | minimoi-cos-scheduler | `127.0.0.1:8769/health` |
| later | curator | separate compose project, seeded state, cron-driven | — |
| later | cos-agent-a, mc-relay, records | Node / separate projects | Docker healthcheck |
| **no** | model-gateway | holds provider keys; restart is out of bounds | — |
| **no** | postgres | data | — |

German and Portuguese are both in v1 so a parity fix can be deployed to both the same way.
Each supported service is one row in a table in `dev_deploy.py` (container, compose service,
Dockerfile, health URLs, overlay paths, env pass-through). Adding a service = adding a row + a test.

**board-api:** it runs the portal image as a sidecar. A portal deploy moves both: stop board-api,
recreate portal, verify, then recreate board-api on the same image from its own recorded chain —
the order Codex's `runtime-deployment/deploy.py` uses today. If either fails, both roll back.
*Open question for Codex:* whether its `headless_lifecycle.py stop/start` must wrap this, or a plain
`compose up --no-deps board-api` is enough.

## 2. Image build: full by default, overlay as a fast path

- **Full (default):** `docker build -f docker/Dockerfile.<service>` over a `git archive <commit>`
  context — the same Dockerfile CI uses for production. The image is a function of the commit:
  deletions are real, dependencies match `requirements.txt`, and nothing from earlier overlays leaks
  in. Runs under the heavy-job lock.
- **Overlay (`--overlay`):** `FROM <running image>` and replace the service's source directories
  wholesale (remove, then copy from the archive) — fast, no dependency install. Allowed **only** when
  the running image carries a `minimoi.source_commit` label and `requirements*.txt`, the service's
  Dockerfile and `package*.json` are unchanged between that commit and the target. Otherwise refused
  with "use a full build".
- Every image built by this path is labelled `minimoi.source_commit=<40-char sha>`,
  `minimoi.deploy_record=<record dir>`, `minimoi.environment=dev`, and tagged
  `minimoi-<service>:dev-<short sha>`. Today's dev images have no label; the first deploy of each
  service must be full (or `--accept-unknown-current`, recorded).

**Drop check.** If the running image's source commit is known and is *not* an ancestor of the target,
the deploy would remove commits that are on dev now (for example Codex's trial code). The script lists
them and refuses unless `--replace-unrelated` is given.

## 3. Pinning and the compose chain

- One pin file per service, `~/minimoi-runtime/dev-pins/<service>.json`
  (`{"services": {"<svc>": {"image": "sha256:…"}}}`), pinned by immutable image ID, never by tag.
- First deploy of a service appends that pin file to the end of the service's recorded chain once.
  After that, each deploy **rewrites the pin file in place**; the chain stops growing.
- The script re-reads the running container's compose labels (project, working dir, env files,
  config files) exactly as today's `compose.py` does, so it re-applies what is running, not what the
  repo's compose files say.
- After rendering, the resolved image must equal the pin. If someone later appended a file that
  overrides the image, the deploy refuses rather than guessing.
- Env values the chain interpolates but no env file holds (today: `MC_NATIVE_RELAY_TOKEN`, read from
  the running portal's `MC_RUNTIME_TOKEN`) come from a per-service allowlist in `dev_deploy.py`, read
  from the running container, passed to `docker compose` only via the process environment, never
  printed or written.

## 4. Before/after config check

Before `up`, render the chain with the new pin (`docker compose config --format json <svc>`) and
compare with the running container: effective environment (image defaults + compose env), mounts
(type, source, target, rw), published ports, command/entrypoint, networks. **The only allowed
difference is the image.** Anything else stops the deploy and prints the differing *keys* (never
values) — that catches a chain file edited behind our back. A deliberate config change is not this
command's job; it goes through a reviewed overlay as today.

Recorded as `config-before.json` / `config-after.json` with key lists and SHA-256 digests only.

## 5. Health checks (then automatic rollback)

After `up`, within a bounded wait (default 90 s):

1. Container running, image ID = pinned ID, `minimoi.source_commit` label = requested commit.
2. Docker healthcheck `healthy`, when the service defines one.
3. Service HTTP health URL(s) return 200 (portal: local and public).
4. Source spot-check: SHA-256 of the copied/built source files inside the container match the commit
   (same idea as Codex's `LIVE_SOURCE_HASHES.json`).
5. Gateway untouched: SHA-256 of `~/.openclaw/openclaw.json` unchanged, gateway process not restarted.

Any failure → re-pin the previous image, `up`, re-run checks, write `FAILURE.json`, exit non-zero.

## 6. Operator record

`~/minimoi-runtime/operators/dev-deploy-<service>-<short sha>-<UTC stamp>/`:

| File | What |
|---|---|
| `REQUEST.json` | service, commit, branch (if given), operator, mode, flags, started |
| `build/` | Dockerfile used, `build.log`, `files.sha256` |
| `pin-before.json` / `pin-after.json` | previous and new pin (image IDs) |
| `running-before.json` / `running-after.json` | image, env key list + digest, mounts |
| `config-before.json` / `config-after.json` | rendered config digests (section 4) |
| `health.json` | each check and its result |
| `RESULT.json` or `FAILURE.json` | outcome, timings, `production_changed: false` |
| `rollback.sh` | one line: `deploy_dev.sh rollback <service> --to <record>` |

Plus one line per deploy/rollback in `~/minimoi-runtime/dev-pins/HISTORY.jsonl`, which is what
`status` reads. When Codex's dev release-observation publisher (`dev_release_publish.py`, on
`codex/mc-runtime-recovery`, not merged) lands, the script calls its `begin`/`finish` so the portal can
show which commit dev runs; until then the record is the source.

## 7. Rollback

`deploy_dev.sh rollback <service>` restores `pin-before.json` from the latest record (or `--to <record>`),
runs the same config check and `up`, the same health checks, and writes its own dated record. The
previous image is kept by a `minimoi-<service>:dev-prev` tag so a `docker image prune` cannot remove it.
Before the first deploy of a service, its current image ID is captured, so even the first rollback goes
back to the exact pre-path state.

## 8. Migrations: only explicitly

The script never applies migrations. If the diff between the running image's source commit and the
target adds or changes files under any `*/db/migrations/` or `*/migrations/` directory, it refuses and
names them. (There is no applied-migrations ledger table to check against, so when the running commit
is unknown the script lists the target's migration files and requires the acknowledgement below.) The operator applies them as a separate,
recorded step (as with migration 011 on 22 Oct), then re-runs with `--with-migrations-applied`, which
is recorded in `REQUEST.json`. Rollback of code does not roll back schema; the refusal message says so.

## 9. Shared-host rules (enforced, not just documented)

- **One heavy job at a time:** the whole deploy (build + up + verify) runs under
  `_working/tools/heavy_job.py <operator> dev-deploy -- …`, which also refuses under 8 GiB free.
- **One deployer at a time:** a lock in `~/minimoi-runtime/dev-pins/.deploy.lock`.
- **Holds:** an operator running a trial can write `~/minimoi-runtime/dev-pins/HOLD-<service>`
  (owner, reason, until). Deploys to a held service refuse unless `--operator` matches the holder.
  This replaces "ask in `_working/` and hope". Codex's current MC trial would hold `portal`.
- **Portal only:** refuse while an MC turn is claimed (Codex's `mc_turn_ledger` lease probe).
- **Never** edit, checkout, reset or restore in `~/Projects/personal-ai-agents`; source comes from
  `git archive`.
- **Never** restart or reconfigure the OpenClaw gateway or model-gateway; checked in section 5.
- `--no-deps` always: only the named service (and its linked sidecar) is recreated.

## 10. CI `workflow_dispatch` dev job: not in v1

Reaching the Mac from GitHub needs either a self-hosted runner on the Mac or an inbound deploy
endpoint through the tunnel. A runner on this Mac would execute repository workflow code on the host
that holds the Keychain, the Docker socket, both agents' workspaces and personal data; an inbound
endpoint puts a deploy trigger on the public internet. Both enlarge the attack surface more than the
convenience is worth today. CI's part stays what it is: tests on the branch/PR before a dev deploy.
If wanted later, the safer shape is pull-based — a local launchd job that picks up a signed request —
as its own decision.

## Out of scope

Production deploys; config/env changes (they stay reviewed overlays); compacting the existing 63-file
chain (worth doing once this path exists — proposal: render it once into a single reviewed file —
but it changes the running config's provenance and needs its own go).

## Decisions requested from Robert

1. Go to build as above (services in section 1, full build default, overlay as fast path).
2. Holds file (section 9) as the coordination mechanism with Codex.
3. No CI dev job in v1 (section 10).
