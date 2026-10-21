# Runbook: GuestRequestWaiting (warning)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
A guest access request has waited more than 48 hours. Counts only; no names are in telemetry.

## Likely causes
- Requests not yet reviewed.

## Checks (read-only)
1. Open the Guild guest requests view (authorized) to see who is waiting.

## Safe fixes
- Grant or reject the request (owner action).

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears when no request is older than 48 hours.
