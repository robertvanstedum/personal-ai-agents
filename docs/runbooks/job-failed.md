# Runbook: JobFailed (major)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
The job's last run exited non-zero.

## Likely causes
- A dependency was unavailable (database, network, API).
- Bad input or a code regression.
- Disk full or permissions.

## Checks (read-only)
1. Read the job's summary and results in its status file (fixed codes only).
2. Read the job's own log for the failing run.
3. Check disk and the dependencies it names.

## Safe fixes
- Fix the cause, then re-run once if the job is safe to repeat.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
The alert clears when a later run exits 0.
