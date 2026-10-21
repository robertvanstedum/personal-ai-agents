# Runbook: CollectorExportFailing (major)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
The OpenTelemetry Collector cannot export to one of its backends.

## Likely causes
- A backend is down or its credentials expired.
- Network egress is blocked.
- The archive volume is full.

## Checks (read-only)
1. docker logs otel-collector --tail 100; check the exporter named in otelcol_exporter_* metrics.

## Safe fixes
- Fix credentials or connectivity; free disk. The file_storage queue holds data while you fix it.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears when send failures stop for the evaluation window.
