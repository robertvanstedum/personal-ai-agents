# PLAN_CostMonitor_2026-03-28.md

**Workstream:** POC / Infrastructure
**Date:** 2026-03-28
**Strategy Agent:** OpenClaw (revised draft v3, incorporating Claude.ai feedback)
**Intent:** Extend existing cost tools into a unified monitoring utility for xAI + OpenClaw. Showcase methodology on GitHub via PLAN/BUILD docs. POC measures Grok 4.20-beta costs across phases as a data point.

## Problem and Intent
- Baseline tools: `track_usage.py` (curator log summaries), `curator_costs.json` (daily breakdowns), session_status (per-session tracking).
- Improvements: Aggregate xAI-wide (add session parsing), add alerting (thresholds + Telegram), and POC measurement (structured tracking of Grok 4.20-beta deltas in BUILD.md).
- Separate concerns: Aggregation as core extension, alerting as new script, POC measurement as artifact.
- Fits WAYS_OF_WORKING: Extend without rewrite, validate locally, one step at a time. Aligns with OPERATIONS.md (e.g., cron integration) and ROADMAP (infra upgrades).

## Grok 4.20-Beta Rate Card (Pinned for POC)
- Input: $0.005 per 1k tokens
- Output: $0.015 per 1k tokens
- Source: xAI docs as of 2026-03-28 (reconfirm if rates change mid-POC).

## Exact Files to Modify/Create
- Modify: `track_usage.py` (add aggregation + breakdowns).
- Modify: `curator_costs.json` schema (add optional "xai_daily" and "multi_agent" fields; backward-compatible—existing readers ignore new fields, add migration script if needed for backfill).
- Create: `scripts/cost_alert.py` (Telegram notifier).
- Create: `launchd/com.vanstedum.cost-monitor.plist` (daily cron).
- Docs: This PLAN.md, `docs/poc/BUILD_CostMonitor_2026-03-28.md` (for actuals + lessons).

## Step-by-Step Build Sequence
### Concern 1: Cost Aggregation (Extend track_usage.py)
1. Add OpenClaw session parsing (via sessions_history) for xAI costs + multi-agent breakdowns.
2. Output unified summaries (curator + xAI, per-phase tokens).

### Concern 2: Cost Alerting (New script + cron)
3. Build `cost_alert.py` with thresholds (e.g., warn >$1/day) using existing bot_token; integrate with curator_costs.json.
4. Set up plist for daily run (extend OPERATIONS.md hourly patterns).

### Concern 3: POC Measurement (Artifact in BUILD.md)
5. Track deltas via session_status at phase ends; populate table in BUILD.md (format below).

## Open Questions for Memory Agent (OpenClaw Validation)
- Confirmed: No conflicts—extends idempotency from OPERATIONS.md; curator_costs.json changes optional (migration: simple script to backfill new fields without breaking readers).
- Confirmed: Aligns with DECISIONS.md (e.g., soft thresholds; log as DEC-004 if tradeoffs emerge).
- Confirmed: ROADMAP fit—supports infra migration (e.g., DB for long-term storage).

## Verification Steps
- Aggregation: `track_usage.py --summary` shows breakdowns.
- Alerting: Simulate threshold breach; verify Telegram send.
- POC: Table in BUILD.md populated with actuals.

**Converted from:** OpenClaw revised draft v3. Ready for Claude.ai review.