# Runbook: TelemetryHeartbeatStale (critical)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
A component that reports telemetry has sent no heartbeat for over 15 minutes.

## Likely causes
- The component is down or hung.
- Telemetry was disabled for it.
- The Collector cannot be reached from it.

## Checks (read-only)
1. Check the component is running; check MINIMOI_TELEMETRY_ENABLED; check the Collector health endpoint from inside the network.

## Safe fixes
- Restart the component or the Collector as appropriate.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears when heartbeats resume.
