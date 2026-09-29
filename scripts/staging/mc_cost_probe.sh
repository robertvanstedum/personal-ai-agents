#!/bin/bash
# mc_cost_probe.sh — the fresh-versus-long context cost probe for Master
# Craftsman (Guild 1.1 slice 2). Operator only: it runs inside the staging
# portal container through docker exec, never through a browser or Robert's
# login. Each turn is a PAID model call: it refuses without --yes-spend and
# stops once the spend passes $1 (or a lower --cap).
#
# Usage: mc_cost_probe.sh [--turns 3|4] [--cap DOLLARS] [--principal NAME] --yes-spend
#        mc_cost_probe.sh --stream --yes-spend [--stop-after-first-text] [--cap DOLLARS]
#
# It prints, per turn: the conversation, input and output tokens and the cost,
# from the gateway's usage records (data/usage/). See
# minimoi_portal/guild_ui/mc/cost_probe.py.
#
# --stream (streaming S1) goes through the same server path as
# /mc/turns/stream: turn A (streaming and usage: timings, event counts, the
# mc_turns.jsonl line, the runtime-stream record, the gateway's records with
# output tokens compared), then turn B (Stop after the first text: the stopped
# status, the runtime-stream error line, and whether the gateway recorded the
# aborted call). --stop-after-first-text runs turn B alone. A turn whose cost
# cannot be read stops the probe. See minimoi_portal/guild_ui/mc/stream_probe.py.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[[ "$(docker inspect -f '{{.State.Running}}' minimoi-portal 2>/dev/null)" == true ]] || die "minimoi-portal is not running"
exec docker exec -i minimoi-portal python -m minimoi_portal.mc_cost_probe "$@"
