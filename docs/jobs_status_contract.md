# Scheduled jobs: registry and status contract (v1)

Tool-neutral on purpose. A job, the watchdog, the Guild Operate tile and any future
exporter (Prometheus, Grafana, something else) share only two things: the **registry**
(what should run) and one **status file per job** (what did). Nothing here binds MiniMoi
to a scheduler, an alert channel or a dashboard.

Code: `core/jobs/registry.py`, `core/jobs/status.py`, `core/jobs/schedule.py`.
Jobs today: `scripts/memory/daily_watch.py` (writes status), `scripts/jobs/watchdog.py` (reads it).

## Registry: `config/scheduled_jobs.json`

`{"schema_version": 1, "jobs": [ ... ]}`. One entry per job, on any host.

| field | meaning |
|---|---|
| `id`, `name` | lowercase id (`memory-watch`) and a human name |
| `host` | where it runs: `mac`, `ec2`, ... A reader only judges jobs of its own host |
| `schedule` | `{"text": "daily 04:00 Chicago", "kind": "daily", "at": "04:00", "tz": "America/Chicago"}` or `{"text": ..., "kind": "interval", "every_s": N}` |
| `expected_every_s` | how often a run should finish (86400 for a daily job) |
| `grace_s` | optional, default 7200: slack before "late" |
| `missed_after_s` | no completed run for this long = missed |
| `stuck_after_s` | `running` for this long = stuck |
| `active_from` | optional ISO time: the job is expected to have run by `active_from + expected_every_s + grace_s`. Before that, a missing status file is not a fault |
| `status_file` | plain file name under the jobs status root |
| `owner`, `runbook` | who answers for it; the OPERATIONS.md anchor |

## Status file: `<jobs_root>/<status_file>`

The jobs root is `MINIMOI_JOBS_ROOT` (default `~/minimoi-staging/data/jobs` on the Mac).
Files are 0600, the folder 0700, every write is temp file + fsync + rename. A job writes the
file when a run **starts** (`running`) and again when it **ends**.

```json
{"schema_version": 1, "job_id": "memory-watch", "host": "mac", "run_id": "20261004T090000Z-1a2b3c",
 "state": "ok", "started_at": "2026-10-04T09:00:01Z", "finished_at": "2026-10-04T09:00:41Z",
 "exit_code": 0, "summary": "2 sources ok", "results": {"claude-code": "ok", "codex": "ok"},
 "next_due_by": "2026-10-05T09:00:00Z", "last_success_at": "2026-10-04T09:00:41Z", "alert": "not_needed"}
```

| field | meaning |
|---|---|
| `state` | `running` \| `ok` \| `warn` (finished, with something to look at) \| `failed` |
| `run_id` | one id per run; the watchdog dedupes on it |
| `started_at`, `finished_at` | UTC ISO. `finished_at` and `exit_code` are `null` while running |
| `summary` | short fixed phrase, at most 120 characters, letters digits and ` _:.,;=()+-` only |
| `results` | `{part: code}`: a part name (`claude-code`) and a fixed code (`ok`, `not_approved`, `disk_low`, `exit_1`) |
| `next_due_by` | the next scheduled start after this run (from the registry schedule) |
| `last_success_at` | finish time of the last run that **completed** (`ok` or `warn`); carried forward through `running` and `failed` files |
| `alert` | `sent` \| `not_sent` \| `not_needed` \| `null`: whether the failure message went out |

**No content, ever.** The character sets above cannot hold a path, a title or a sentence of
conversation, and the writer refuses (and writes nothing) if asked to. No secrets, no file names.

**Readers tolerate bad files.** A missing, unreadable, corrupt or wrong-schema file (or one whose
`job_id` is not the job's) is **unknown**, never healthy. `core.jobs.status.read_status` returns `None`.

## Exit codes (memory-watch wrapper)

`0` finished `ok` or `warn` / `1` finished `failed` (a watch failed, the approval check was
unavailable) / `2` the wrapper itself broke (for example the status file could not be written;
the file then stays `running` and the watchdog reports it stuck).

## Mapping to Prometheus / Grafana (future exporter)

An exporter reads the registry and the status files and needs no change to either. Per job
(labels `job`, `host`):

| metric | from |
|---|---|
| `minimoi_job_last_success_timestamp_seconds` | `last_success_at` |
| `minimoi_job_last_run_timestamp_seconds` | `finished_at` (or `started_at` while running) |
| `minimoi_job_last_run_state` (0 ok, 1 warn, 2 failed, 3 running, -1 unknown/missing) | `state` |
| `minimoi_job_running_seconds` | now - `started_at` while `running` |
| `minimoi_job_next_due_timestamp_seconds` | `next_due_by` |
| `minimoi_job_expected_every_seconds`, `..._missed_after_seconds`, `..._stuck_after_seconds` | registry |
| `minimoi_job_part_ok{part=...}` | `results` (1 when the code is `ok`) |

Alert rules then read the same way the watchdog does: `time() - last_success > missed_after`,
`running_seconds > stuck_after`, `last_run_state == 2`.

## For the Guild Operate tile (Codex)

Build the "Scheduled jobs" tile from this contract. Nothing under `minimoi_portal/` is
written by the jobs work; the tile is yours.

**Inputs.** Registry: `config/scheduled_jobs.json`, read from the image with
`core.jobs.registry.load()`. Status files: the folder named by the portal env var
**`MINIMOI_JOBS_DIR`** (the staging overlay `docker-compose.staging-jobs.yml` sets it to
`/app/data/jobs`, mounted read-only from `${MINIMOI_ROOT}/data/jobs`), read with
`core.jobs.status.read_status(dir, job.status_file, job.id)`. Judge only jobs whose `host`
matches the host that dir belongs to (the Mac staging dir: `mac`). Registry fields used:
`id name host schedule expected_every_s grace_s missed_after_s stuck_after_s active_from status_file`.
Status fields used: `state started_at finished_at last_success_at next_due_by results summary`.

**Per-job light** (first rule that matches wins; `now` is the portal clock):

1. Status file missing, unreadable or corrupt (`read_status` is `None`): **red**, "never ran",
   only if the registry says a run was due, i.e. `active_from` is set and
   `now > active_from + expected_every_s + grace_s`; otherwise **unknown** ("not due yet").
2. Any of `started_at`, `finished_at`, `last_success_at` more than 60 s ahead of `now`: **unknown** (clock skew).
3. `state == failed`: **red**.
4. `state == running` and `now - started_at > stuck_after_s`: **red** ("stuck").
5. Age `a = now - last_success_at`:
   - no `last_success_at`: **yellow** if the job is `running` (first run in progress), else **red**;
   - `a > missed_after_s`: **red** ("missed");
   - `a > expected_every_s + grace_s`: **yellow** ("late").
6. `state == warn`: **yellow**.
7. Otherwise (`ok`, or `running` within `stuck_after_s`, and a success within `expected_every_s + grace_s`): **green**.

**Summary light**: states `unknown`, `red`, `yellow`, `green`; precedence
**unknown > red > yellow > green** (the existing `lights.worst`). If `MINIMOI_JOBS_DIR` is unset the whole tile is
**unknown**, "jobs status not connected" (use `not_configured(...)` like the Agents light). **Unknown is never
green**, and an unreadable file is never treated as healthy. Reasons are at most 40 characters
(`lights.short`), word and shape come from `lights.STATES`.

**Drill-down**, one line per job: name, host, state word, last finished (`finished_at`), next due
(`next_due_by`), per-part codes from `results` (`claude-code: ok · codex: not_approved`). The status
file carries only codes and counts, so everything in it is safe to render.

**Wiring notes.** The tile is an Operate tile, not a Shop floor light: `operate()` in
`pages.py` looks tiles up in `state.lights` (floor lights), so either add a floor light with its own rule
or compute extra lights for Operate tiles that have none. Add a classifier rule so a change under
`core/jobs/` or `config/scheduled_jobs.json` redeploys only the portal (already in `classify_release.py`).
