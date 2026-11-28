# Jobs status contract (schema_version 1)

This is the contract between every scheduled job and the watchdog, the daily check, MC's health block, the Guild tile and any exporter. The code is the reference:
- `core/jobs/status.py`: writing and validation;
- `core/jobs/judge.py`: how a status reads against its registry entry;
- `core/jobs/registry.py`: the registry, `config/scheduled_jobs.json`.

This document was cited by that code from the start, but was only written on 28 Nov 2026 (WS3 C1.2).

## The file

There is one JSON file per job, under the host's jobs root (`MINIMOI_JOBS_ROOT`, or a mapped root via `status_root`). It's written at the start of a run (`running`) and again at its end. Each write is atomic. Files are mode 0600 and folders 0700.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | 1 | |
| `job_id`, `host` | id tokens | must match the registry entry |
| `run_id` | token | one per run |
| `state` | `running` \| `ok` \| `warn` \| `failed` | `warn` = completed with warnings |
| `started_at`, `finished_at` | ISO UTC | `finished_at` is null only while running |
| `exit_code` | int or null | |
| `summary` | ≤ 120 chars, fixed phrase charset | never content |
| `results` | ≤ 20 `part: code` pairs, narrow charset | never content |
| `next_due_by` | ISO or null | |
| `last_success_at` | ISO or null | finish of the last completed run; carried across failed runs |
| `alert` | `sent` \| `not_sent` \| `not_needed` \| null | what the job's own wrapper did |
| `progress` *(optional)* | `{work_due, last_progress_at, phase}` | see below |
| `last_refusal_at`, `last_refusal_reason` *(optional, together)* | ISO, code | the last run refused at its lock |

**Never any content.** The character sets can't hold a path, a title or conversation text. A writer that tries gets `StatusError`, and nothing is written. A reader treats a missing, unreadable or contract-breaking file as **unknown, never healthy**.

## Progress (for jobs with `progress_required`)

- `work_due`: an integer ≥ 0. A positive count where the job knows it. `1` means "more to do, count unknown" (the meaning seed).
- `last_progress_at`: when the job last moved (embedded, applied, exported), or null. It's carried across failed and refused runs.
- `phase`: one of:
  - `active`: running, with work if `work_due > 0`;
  - `idle_no_work`;
  - `complete`: one-time work finished;
  - `outside_window`.

The registry declares `progress_required`, `idle_allowed_s` and an optional `progress_window {start, end, tz}`.

## How a status reads (`judge`)

The rules are applied in order, and the first match decides.

1. **No readable file:** red `never_ran` after `active_from + expected_every_s + grace_s`, otherwise unknown `not_due`. Every active status-file job has an `active_from`; a registry test enforces it.
2. **A time in the future** (beyond 60 s): unknown `skew`.
3. **`failed`:** red. Running past `stuck_after_s`: red `stuck`. No completed run on record: red `no_success`, or yellow `first_run` while running.
4. **Last success older than `missed_after_s`:** red `missed`.
5. **Progress** (only for `progress_required`, on a completed run). Failure precedence is kept, because rules 1–4 decide first.
   - A missing block, unknown phase, bad `work_due`, or a missing or future `last_progress_at` while work is due gives **unknown `progress_unknown`**.
   - `complete`, `idle_no_work`, `outside_window`, the clock outside `progress_window`, or `work_due == 0` all mean no progress is expected.
   - Otherwise idle time is counted from the later of `last_progress_at` and the current window's start. Beyond `idle_allowed_s` it's **yellow `no_progress`**; beyond twice that, **red `no_progress`**.
6. **Last success older than `expected_every_s + grace_s`:** yellow `late`.
7. **`warn`:** yellow `warn`.
8. Otherwise green `ok`.

## Who tells whom (`scripts/jobs/`)

- **The hourly watchdog** (alerts on in production, off on Dev) sends each red condition once, keyed by occurrence. It announces recovery only after a completed run.
- **The daily check** (09:10 Chicago):
  - sends *needs action* items: red, any yellow off for more than 24 h, and a host silent for more than 72 h;
  - repeats each open item once per Chicago day;
  - announces recovery only when the same job is green in a fresh report (not stale, not future-dated). Any other change of observation keeps the incident open.
