# Runbook: MemoryHigh (warning) / MemoryCritical (critical)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
Memory use is at or above 85% (warning) or 95% (critical).

## Likely causes
- A container exceeding its expected footprint.
- Too many concurrent heavy jobs.
- A leak after a deploy.

## Checks (read-only)
1. docker stats --no-stream; top.
2. Compare with the last deploy time.

## Safe fixes
- Restart the offending container if safe; stagger heavy jobs (batch window); roll back a leaking deploy.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears when usage falls below the threshold for the evaluation window.
