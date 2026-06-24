# Handoff — Claude Code → OpenClaw
*2026-06-24 — end of session*

---

## What shipped today

### Build page 500 fix
- `now() - string` TypeError in both `build_log.html` and `build_queue.html`
- Added `parse_dt()` Jinja2 global to portal `app.py`
- Deployed to dev and prod. Both pages working.

### Spec triage (build queue)
- **10 items cancelled**: stale handoffs, superseded specs, executed one-time docs
- **#7 career two-page** promoted to `spec_ready` (Aug 3 deadline)
- **#101 Prometheus + Grafana** added as new deferred item (`spec_monitoring_prometheus_grafana_2026-06-22.md`)
- **#84** already deferred; reason updated: "Sentry absorbed into #100; Prometheus/Grafana deferred indefinitely"

### #100 Operations Monitoring v2 + Sentry — DONE
- **EC2 health check**: already live in CoS `chief_of_staff.py` — `check_ec2_health()` runs every 30 min (loop H). Checks containers, disk <80%, memory <85%, `/health` endpoints, CloudWatch CPU. Alerts via `minimoi_system_bot`.
- **Sentry**: `sentry-sdk[flask]` added to all 3 Docker requirements files. `_init_sentry()` added to `curator_server.py`, `domains/german/html_server.py`, `minimoi_portal/app.py`. Silent no-op until `SENTRY_DSN` added to SSM at `/minimoi/production/sentry_dsn`. No redeploy needed once DSN is set.
- **Mac cleanup**: `com.user.telegram-feedback-bot` unloaded and disabled (renamed to `.plist.disabled`). Superseded by `minimoi-system-bot` on EC2.
- **OPERATIONS.md updated**: two-node architecture section, EC2 container inventory, automated health check description, manual EC2 commands, deploy workflow, updated Telegram bot table and disabled jobs table.
- All three images (portal, curator, german) pushed to ECR and deployed on EC2.

### Disk crisis resolved
- Mac was at 97% — cleared Telegram/ShipIt caches (1.7GB), Docker build cache (10GB), deleted Colima VM entirely (63GB freed)
- Mac now at ~30% free. Colima deleted — restart with `colima start` when local Docker needed.
- EC2 also hit disk-full during deploy — cleared with `sudo docker system prune -af`, redeployed successfully.
- EC2 disk: 30GB volume. Reminder: `docker image prune -f` after each deploy to avoid repeat.

---

## Current build queue state

| # | Title | Status |
|---|-------|--------|
| 7 | Career Two-Page Redesign | **spec_ready** — Aug 3 deadline |
| 100 | Operations Monitoring v2 + Sentry | **done** — shipped today |
| 101 | Prometheus + Grafana Metrics Stack | deferred — after #100 |

Queue: 2 spec_ready, 0 in_build, 0 blocked, 5 deferred, 47 done, 10 cancelled.

---

## Pending (Robert action needed)

- Add `SENTRY_DSN` to AWS SSM: `/minimoi/production/sentry_dsn` — no redeploy needed
- EC2 disk expansion: 8GB → 20GB still within free tier (30GB EBS allowance total)
- iOS TTS validation: Robert to test Gespräche voice on iPhone (`_gestureAudio` fix from prev session)

---

## Next build: #7 Career Two-Page Redesign

Spec exists: `_working/spec_career_two_page_2026-06-11.md`
Status: spec_ready
Deadline: Aug 3 2026
Gate: Needs design session with Claude.ai before Claude Code builds (design-first rule).

---

## Parked / not touched today
Security architecture, guest access, career focus editor, voice command routing, decisions view, PWA wrapper, German landing hero mobile CSS issue.
