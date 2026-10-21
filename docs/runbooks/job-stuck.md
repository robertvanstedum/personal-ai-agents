# Runbook: JobStuck (major)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
A job has been in state running for longer than its stuck-after window.

## Likely causes
- The job hangs on I/O, a lock or an external call.
- The process died without writing its final status.

## Checks (read-only)
1. Find the process (ps, docker top) and how long it has run.
2. Check locks it may wait on (for example the heavy-job lock).
3. Read the job log tail.

## Safe fixes
- Stop the hung process only if the job is safe to interrupt; then let the next scheduled run proceed.
- If the process is gone, the next run overwrites the stale running status.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
The alert clears when the job finishes or the next run starts cleanly.
