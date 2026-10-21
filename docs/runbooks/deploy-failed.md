# Runbook: DeployFailed (major) / DeployNotFinished (warning)

**Spec:** 161 §12.7. **Source rule:** `config/observability/rules/minimoi_o1.yaml`. Read-only checks come first; change nothing until the cause is known.

## What it means
The last deployment failed verification, or has been unverified or in progress for over an hour.

## Likely causes
- An image failed to start or failed its checks.
- The deploy script stopped half-way.
- Verification never ran.

## Checks (read-only)
1. Read the release status file and the CI deploy job log.
2. docker ps for the services in the release.

## Safe fixes
- Roll back per OPERATIONS.md (previous image tag); re-run verification.

## Escalate when
- The cause is unclear after the checks, or the fix needs a production change outside this runbook. Production changes need Robert's approval.

## Verify the clear
Clears when a release reports a fully passing verification.
