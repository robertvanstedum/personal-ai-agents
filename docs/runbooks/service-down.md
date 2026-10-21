# Runbook: ServiceDown (critical)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
The health snapshot reports a service as failing or unreachable.

## Likely causes
- The container stopped, is restarting, or failed its health check.
- A dependency (database, gateway) is down.
- The host is out of memory or disk.

## Checks (read-only)
1. docker ps / docker inspect the service; docker logs --tail 100.
2. Check DiskCritical and MemoryCritical.
3. Check the last deployment (DeployFailed).

## Safe fixes
- Restart only that service if the cause is transient; otherwise roll back the last deploy per OPERATIONS.md.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
The alert clears when the next health snapshot shows the service ok.
