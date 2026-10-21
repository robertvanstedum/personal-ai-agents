# Runbook: Watchdog (always firing)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
This alert always fires. It is routed to the external dead-man switch, which alarms when it stops hearing it.

## Likely causes
- If the dead-man switch alarms: Prometheus, Alertmanager, the Collector, the network or the host is down, or notifications are broken.

## Checks (read-only)
1. docker ps for prometheus, alertmanager, otel-collector; check the host is up.
2. Check Alertmanager can reach the internet (dead-man ping).

## Safe fixes
- Restart the failed component; follow the Collector runbook if exports fail.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
The dead-man switch recovers on the next ping.
