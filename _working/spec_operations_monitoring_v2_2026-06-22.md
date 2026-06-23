# Spec — Operations Monitoring: Mac + EC2 Division of Labor
*Created: 2026-06-22 — Claude.ai + Grok*
*Status: spec_ready — build after Block B German batch*
*Priority: Medium*

---

## Context

EC2 is production and has no operational monitoring. Mac has informal
monitoring via OpenClaw. This spec adds production-grade monitoring on
EC2 and clarifies responsibilities between OpenClaw, CoS, and the
system bot.

**Core principle for this phase:**
CoS detects problems and escalates to Robert. Robert decides what
action to take. CoS does not self-heal or restart services. Human in
control of all production changes.

---

## Division of Labor

| Agent | Scope | Nature |
|-------|-------|--------|
| OpenClaw | Independent system-wide checks — spans beyond minimoi | Proactive, autonomous, no hard dependency |
| CoS | minimoi-specific EC2 operational health | Detection + escalation only |
| minimoi_system_bot | Alert delivery channel | Sends notifications, not the checker |

Overlap between OpenClaw and CoS is intentional — resilience over elegance.

---

## Part 1 — EC2 Health Monitoring (CoS task)

Periodic task in `chief_of_staff.py`, runs on EC2 only via
`is_production()` guard, alerts via `minimoi_system_bot`.

**Two-layer tooling:**

Layer 1 — AWS-native (CloudWatch):
- EBS disk utilization
- Instance CPU and memory
- Network in/out
Already collected by AWS at no extra cost. Query via `boto3`.

Layer 2 — Portable Linux tools (work on any future machine):
- `docker ps` — container status
- `df -h` — disk usage
- `free -m` — memory pressure
- `curl localhost:{port}/health` — Flask app responding

Both layers used. AWS metrics for infrastructure visibility,
Linux tools for application health. Neither alone is sufficient.

**Checks every 30 minutes:**
- All four containers running: portal, curator, german, postgres
- Disk < 80% (CloudWatch EBS metric + df -h cross-check)
- Memory < 85%
- All three `/health` endpoints return 200

**Alert conditions (CoS escalates, does not fix):**
- Container not running → immediate alert
- Disk > 80% → alert with usage and largest directories
- Memory > 85% → alert with top consumers
- Any `/health` non-200 → alert with app name and status

**Alert format — informative and actionable:**
```
⚠️ EC2 Health Alert
Container: minimoi-curator is not running
Last seen: 14 minutes ago
To diagnose: docker logs minimoi-curator
To restart: docker-compose up -d curator
```

Robert sees the problem, context, and the exact command.
CoS does not run the command.

**Implementation:**
```python
def check_ec2_health():
    """
    Runs on EC2 only. Detects and escalates. Does not fix.
    """
    if not is_production():
        return
    # Layer 2: subprocess for docker ps, df, free, curl /health
    # Layer 1: boto3 for CloudWatch metrics
    # Alert via get_system_token() if thresholds breached
```

No new SSM parameters needed. Uses existing EC2 IAM role.

---

## Part 2 — Mac Monitoring Cleanup

- Remove stale `rvsopenbot` references from Mac monitoring code
- Confirm all Mac alerts route through `get_system_token()`
- Confirm `minimoi_cmd_bot` and `com.user.telegram-feedback-bot`
  launchd jobs are unloaded and disabled on Mac
- Update any hardcoded keyring references that survived A3

---

## Part 3 — OPERATIONS.md Update

Update `docs/OPERATIONS.md` with:
- Two-node architecture (EC2 = production, Mac = standby)
- What CoS monitors on EC2 and at what interval
- What OpenClaw monitors independently
- How to manually check EC2 health (commands included)
- How to restart containers on EC2 if CoS alerts
- Alert routing (all alerts → minimoi_system_bot)
- Colima maintenance procedures for Mac

---

## Definition of Done

- [ ] CoS health check task running on EC2 every 30 minutes
- [ ] Alerts fire via minimoi_system_bot for all four conditions
- [ ] Verified: stop a container on EC2, alert arrives within 30 min
- [ ] Stale rvsopenbot references removed from Mac monitoring
- [ ] Old Mac launchd jobs unloaded and disabled
- [ ] docs/OPERATIONS.md updated for two-node architecture

## Commit sequence

```
1. Add check_ec2_health() to chief_of_staff.py (is_production() guard)
2. Mac monitoring cleanup — rvsopenbot references, retired launchd jobs
3. Update docs/OPERATIONS.md
```

## Commit message

`Operations: EC2 health monitoring via CoS, Mac cleanup,
OPERATIONS.md updated for two-node architecture.`

---

*Spec · 2026-06-22 · Claude.ai*
*Register in design_log as spec_ready*
*Roadmap companion: docs/OPERATIONS_ROADMAP.md*
