# Runbook: JobMissed (critical)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
A registered job has not succeeded within its missed-after window (from config/scheduled_jobs.json).

## Likely causes
- The job's scheduler (cron/launchd/container) stopped or was removed.
- The job runs but keeps failing (see JobFailed).
- The host was asleep or down at the scheduled time.
- The status file is not being written (see JobStatusUnreadable).

## Checks (read-only)
1. Read the job's status file in the jobs root (state, finished_at, last_success_at).
2. Check the scheduler entry exists and is loaded (launchctl list / docker ps / crontab -l).
3. Read the job's own log for the last run.

## Safe fixes
- Re-run the job once by hand if it is safe to repeat (its runbook says so).
- Restore the scheduler entry from git if it was removed.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
The alert clears after the next successful run (last_success_at moves forward).
