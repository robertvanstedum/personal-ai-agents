# Runbook: TelemetryDropping (warning)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
Telemetry is being dropped: either calls that break the vocabulary (contract errors, counted in prod) or a full event queue.

## Likely causes
- A code change emits a label or metric not in config/observability/vocabulary.yaml.
- The Collector is slow or down (queue fills).

## Checks (read-only)
1. Look at the signal label (metrics/logs); check recent changes to emitters; check the Collector.

## Safe fixes
- Add the value to the vocabulary by reviewed change, or fix the emitter; restore the Collector.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears 15 minutes after drops stop.
