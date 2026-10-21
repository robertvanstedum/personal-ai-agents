# Runbook: CheckSourceUnreadable (warning)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
A monitored source (a standing check's source or one of the bridge's snapshots) cannot be read. Unknown, not healthy.

## Likely causes
- The snapshot writer stopped.
- The file moved, changed format or became a symlink.
- Permissions or mounts changed.

## Checks (read-only)
1. Check the source label in the alert; find its file; check mode, owner, format and freshness.

## Safe fixes
- Restart or fix the snapshot writer; restore the mount.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears once the source is readable again.
