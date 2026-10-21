# Runbook: ScrapeLimitHit (warning)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
Prometheus refused a scrape because it exceeded the series cap (2,000). New series are being dropped.

## Likely causes
- A label with too many values was added (for example a per-request ID).
- Many new jobs or services at once.

## Checks (read-only)
1. Find the metric with the most series: topk(10, count by (__name__)({__name__=~"minimoi_.*"})).

## Safe fixes
- Remove the high-cardinality label by reviewed change; raise the cap only with a documented estimate.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears 15 minutes after the limit stops being hit.
