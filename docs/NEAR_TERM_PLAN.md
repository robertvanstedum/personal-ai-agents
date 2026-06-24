# Near-Term Plan — mini-moi
*As of: 2026-06-24*
*Update as blocks complete or priorities shift*

---

## Active now

### Block B — German mobile batch
Four small improvements, no design session needed.

| Item | What |
|------|------|
| T1-B | Safe area + font audit (inputs ≥ 16px) |
| T2-C | Post-session summary card (no LLM call) |
| T2-A | iOS Share Sheet (navigator.share) |
| T2-B | Schreiben save toast ("Gespeichert ✓") |

Order: T1-B → T2-C → T2-A → T2-B.
Reference: docs/GESPRACHE_FORWARD_SPEC.md
Gate: none — start now.

---

### Block E — CI/CD Pipeline
GitHub Actions auto-deploy on push to main. Target: this week.

Spec: docs/PHASE3_CICD_PLAN.md (MVP scope)
Design log: #102, status design → promote to spec_ready

MVP: build images → push to ECR → EC2 pulls via SSM send-command →
smoke tests → Telegram notification. No SSH key. No manual steps.

Gate: EC2 monitoring confirmed stable (done ✅).

---

## Next (not started, gates below)

### Block C — CoS page
Needs design session with Claude.ai first.
Design doc: docs/design/ (to be created in design session)
Gate: design session scheduled.

### Career Two-Page Redesign (#7)
Spec exists: docs/specs/spec_career_two_page_2026-06-11.md
Status: spec_ready
Deadline: Aug 3 2026
Gate: none blocking — can start after Block B.

### v1.0 Release
mini-moi platform + Guild v1.0 announcement.
Target: Thursday/Friday this week.
Gate: one confirmed curator briefing from EC2, Block A validated end to end.

---

## Parked

| Item | Gate |
|------|------|
| Block D — Gespräche Phase 1 | 2 weeks stable daily use on EC2 |
| CoS page (build) | Design session first |
| Prometheus + Grafana | After ops monitoring stable |
| Security architecture / guest access | Low urgency |
| Voice command routing | 2–4 weeks daily use data |
| Decisions view UI | ~15 DRs committed |
| PWA wrapper | After mobile loop polished |
| Neo4j seeding | 20+ sources tagged in Postgres |
| S3 live data layer | Phase 4 |

---

## Current system state

| Node | URL | Role | State |
|------|-----|------|-------|
| EC2 | minimoi.ai | Production | All 7 containers running. Cron active. CoS health checks every 30 min. |
| Mac | dev.minimoi.ai | Standby | MINIMOI_ROLE=standby. Test bots active. |

**Known gaps:**
- #51 P1 — X-bookmark pull broken on EC2 (tweepy + OAuth2)
- #52 P3 — Curator Desk "never run"
- #53 P3 — German Dropbox watcher not container-safe
- Sentry not active — add SENTRY_DSN to SSM when ready

---

*Near-Term Plan · 2026-06-24*
*Roadmap: docs/ROADMAP.md*
*Build queue: design_log.json*
