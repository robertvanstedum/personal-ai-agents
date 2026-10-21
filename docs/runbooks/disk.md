# Runbook: DiskHigh (warning) / DiskCritical (critical)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
A filesystem is at or above 80% (warning) or 95% (critical) used.

## Likely causes
- Docker images, build cache or logs growing.
- Backups or archives accumulating locally.
- A runaway log or temp file.

## Checks (read-only)
1. df -h; docker system df; du -sh on the largest folders.
2. Check backup retention and log rotation.

## Safe fixes
- docker image prune / builder prune (never volumes without review).
- Remove old local archives already confirmed off-host.
- Expand the volume if growth is real.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears when usage falls below the threshold for the evaluation window.
