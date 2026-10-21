# Runbook: JobStatusUnreadable (warning)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
The job's status file is missing, unreadable or breaks the status contract. This is unknown, never healthy.

## Likely causes
- The job has never run since it was registered (check active_from).
- The jobs folder mount or permissions changed.
- The writer is an old version that does not follow the contract.

## Checks (read-only)
1. ls -l the status file in the jobs root; check owner, mode 0600, and that it is a regular file.
2. Validate it with core.jobs.status.read_status.

## Safe fixes
- Fix the mount or permissions; run the job once to write a fresh status.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
The alert clears once a valid status file is read.
