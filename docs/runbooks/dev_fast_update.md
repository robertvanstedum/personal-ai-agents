# Dev fast update: a small change to Dev in minutes

Dev only (`dev.minimoi.ai`, the EC2 staging host). Production still deploys from `main` through GitHub, on Robert's go
for an exact commit; nothing here touches it.

## What changed, and why it was 40 minutes

Today's chain, from the Mac: routes-off, release-off, release-dry, release-on, routes-on. Where the time went:

| Cost | Where |
|---|---|
| MC restarted 3 to 4 times, 6 to 12 minutes each | routes-off, release-off, release-on, routes-on: OpenClaw's owner lease plus a fixed health wait |
| CoS Agent A restarted 2 to 4 times, up to 11 minutes each | the same steps |
| every target rolled back to the pre-Beta baseline, then rebuilt | release-off, release-on |
| the payload sent in about 60 KB SSM chunks | about 4 to 10 calls |

The fast path:

1. **`update`: release to release.** `beta_dev_deploy.py update` moves Dev from the release it runs to the next one.
   It rebuilds only the images whose files changed since the commit Dev runs, each `FROM` the image that container runs
   now, and recreates only those containers. If anything fails, it restores **the release that was running**, not the
   baseline, and proves the restore. `rollback` still goes all the way back to the pre-Beta baseline, after any number of
   updates.
2. **Only what changed restarts.** A CSS fix rebuilds and restarts the portal alone. A file under `core/` reaches every
   image that runs `core/`, as before. Workers are still held, stopped, for the whole swap.
3. **Routes stay on.** An update runs under the plan-routes override. It refuses a change to anything routes verified
   (`ROUTES_SENSITIVE`: MC's `start.sh`, `model-access.cjs`, the relay, MC's tools plugin, CoS's plan access, apply-config
   and pinned config, `core/model_routes/`, the gateway config). After a verified update it moves the routes operator's
   saved images on, so a later routes rollback restores the right images. `beta_dev_routes_payload.py check` says in one
   read whether routes can stay: `skip`, `cycle` (routes-off, update, routes-on), `off` or `stop`.
4. **MC stops cleanly and is ready when it is ready.** `start.sh` now keeps its shell alive on `docker stop`, waits for the
   gateway to finish its own shutdown, and never retries after a stop. Before, the shell died at once, tini exited, and
   the gateway was killed mid-shutdown. That this is what left the owner lease held is a **hypothesis**: no log shows a
   lease release yet. The first MC restart after this lands is the test: its journal's `recreated` line gives the seconds.
   Every recreate now also runs the container's own healthcheck itself, every 3 seconds, and calls it ready after two passes
   in a row, instead of waiting for Docker's next scheduled check. Unhealthy still fails. A container Docker still reports as
   `starting` passes verification only if that check passed.
5. **One SSM call for a small update.** An update ships only the changed files, the operator and the meta. The layout check
   (every other file the image holds is the commit's) travels as one digest per image over a shared path list, which the
   host recomputes inside the built image. The last part carries the unpack, so a tarball under about 60 KB is one call.

Every existing check stays: payload hashes against the commit, before the build and again before it ships; the running
files explained by a commit; overlays built and checked (overlay config, hashes inside the image, smoke imports, import
resolution, check 6c for MC, `node --check`) before any worker stops; compose differing only by the changed images; Dev
settings, contracts and unrelated containers unchanged; recovery-required, with workers left stopped, when a restore cannot
be proved; the read-only delivery record written only after a verified result. An update adds its own refusals: it must be
built from the exact commit Dev runs, under a release name of its own, with none of its tags already present, and with no
earlier update unresolved (`update-in-flight.json`).

## When an update fails (8 Nov)

The first live update's portal never answered `/health`: `core/founders_ui` read a CoS page file at startup that the portal
image did not hold, because the portal target never shipped `domains/cos/`. Now:

- The portal ships and lays out `domains/cos/`. Until a release built with that runs on Dev, an update that rebuilds the
  portal also ships `domains/cos/` as changed since the portal's base (`catch_up`; about 170 KB, so 6 SSM calls from
  cc85389f). Drop `catch_up` from the portal's target once one has run.
- A sealed startup check imports `minimoi_portal.app` and gets `/health` before any worker stops.
- A container that restarts 3 times while its health is awaited fails at once, not after the full wait.
- Before a restore replaces a failed container, its last 200 log lines and state are kept in the operator folder as
  `failed-<container>-<time>.private.log` (0600); the journal's `failed_logs` line names the file.

## Running it (each run waits for Robert's go)

```bash
python3 scripts/staging/beta_dev_payload.py read
```

`APPLIED <release> <commit>` is what Dev runs. Then, with that commit as `--from` and a new release name:

```bash
python3 scripts/staging/beta_dev_payload.py build --commit HEAD --from <dev commit> --release beta-20261108a --operator-commit HEAD --out _working/dev-update
```

```bash
python3 scripts/staging/beta_dev_routes_payload.py check --operator-commit HEAD
```

```bash
python3 scripts/staging/beta_dev_payload.py send --out _working/dev-update
```

```bash
python3 scripts/staging/beta_dev_payload.py start update
```

```bash
python3 scripts/staging/beta_dev_payload.py read
```

`build` prints which containers the update reaches and how many SSM calls the send takes. `start update-dry-run` instead
of `start update` builds and checks everything and changes nothing live. Going back one release is an update too:
build from the earlier commit, `--from` the one Dev runs now.

When `check` says `cycle`, or the update refuses because routes are on: routes-off, the update, routes-on.

## Estimated times (not yet measured on Dev)

From the code's own waits and the sizes measured locally (a CSS change's tarball is 40 KB, one SSM call). SSM calls take
about 10 to 20 seconds each (ssm_run.sh polls every 5 seconds). Not included: time waiting for a live CoS turn to end (up
to 5 minutes) or for a worker that ignores TERM (up to 2 minutes).

| Change | What restarts | Estimate |
|---|---|---|
| (a) CSS only, under `minimoi_portal/` | the portal; workers held and resumed | **3 to 5 minutes**: build 5 s, send 1 call, start 1 call, host checks and build about 1 minute, portal recreate under 1 minute, verify and workers under 1 minute, read 1 call |
| (b) portal Python, under `minimoi_portal/` | the portal | **3 to 5 minutes**, as (a), plus the smoke import and import check; 2 SSM calls if the file is large (`guild_ui/api.py`: 82 KB tarball) |
| (b') Python under `core/` | every image that runs `core/`: portal, scheduler, curator, German, Portuguese | **6 to 9 minutes**; they recreate one after another |
| (c) MC persona (a file under the pinned persona release) | the portal (it reads `services/mc_operations/`) and MC | **6 to 8 minutes** if the clean stop releases the lease; **10 to 12** if it does not (the lease clears after about 4 minutes; start.sh retries every 30 s) |

A new persona version (`0.1.28`) also moves the operator's `PERSONA` pin, an ordinary commit; routes still stay on, since
the routes check reads only the routes operator's own files.

The first run after this lands ships the new `start.sh`, which routes verified: that one run is routes-off, update,
routes-on, about 25 to 35 minutes. After it, the paths above apply.

## Considered, not built

- **Hot file swap without a recreate** (`docker cp` plus hash checks). Not built: the container's image would no longer
  hold what it serves, so "compose would run what is running", the image hash checks and every restore, which recreate from
  images, would stop being true. The portal's recreate is under a minute of the estimates above.
- **S3 transfer with the instance's own role.** Not built: it needs the instance role to read a bucket prefix, and whether
  it can is unverified; no IAM change without Robert's OK. With the update's small tarballs a typical change is one SSM call
  anyway. For a large full release: Robert can check, read-only,
  `aws iam list-attached-role-policies --role-name <Dev instance role>`, then name a bucket prefix to use.
