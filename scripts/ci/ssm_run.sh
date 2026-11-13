#!/bin/bash
# ssm_run.sh LABEL TIMEOUT_SECONDS PARAMETERS_JSON: one SSM command on production, awaited, its output printed (#550).
#
# PARAMETERS_JSON is an AWS-RunShellScript parameters file ({"commands": [...], "executionTimeout": ["N"]}), so no command
# is ever quoted into a shell string. Needs EC2_INSTANCE_ID and AWS credentials in the environment. The remote output goes
# to stdout (and to $SSM_STDOUT when set, for the caller to read a result line from). Exit 0 only on Success. CI only:
# never part of a service image.
set -uo pipefail

LABEL="${1:?label}"
TIMEOUT="${2:?timeout seconds}"
PARAMETERS="${3:?parameters file}"
: "${EC2_INSTANCE_ID:?EC2_INSTANCE_ID}"

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$EC2_INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --timeout-seconds 600 \
  --parameters "file://$PARAMETERS" \
  --query 'Command.CommandId' --output text) || { echo "$LABEL: could not send the command"; exit 1; }
echo "$LABEL command ID: $COMMAND_ID"

STATUS=Pending
for (( waited = 0; waited <= TIMEOUT; waited += 10 )); do
  STATUS=$(aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$EC2_INSTANCE_ID" \
    --query 'Status' --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Success|Failed|Cancelled|TimedOut) break ;; esac
  sleep 10
done

OUT=$(aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$EC2_INSTANCE_ID" \
  --query 'StandardOutputContent' --output text 2>/dev/null || true)
ERR=$(aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$EC2_INSTANCE_ID" \
  --query 'StandardErrorContent' --output text 2>/dev/null || true)
printf '%s\n' "$OUT"
[[ -z "$ERR" || "$ERR" == None ]] || printf '%s\n' "$ERR" >&2
[[ -z "${SSM_STDOUT:-}" ]] || printf '%s\n' "$OUT" > "$SSM_STDOUT"

if [[ "$STATUS" != Success ]]; then
  echo "$LABEL: $STATUS"
  exit 1
fi
echo "$LABEL: Success"
