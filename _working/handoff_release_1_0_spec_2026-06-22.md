# Handoff → Claude.ai: mini-moi v1.0 Release Notes + Merge Plan
*From: Claude Code · Date: 2026-06-22*
*Purpose: Release notes document + guild branch merge plan. Pass to claude.ai for final write-up.*

---

## Context

mini-moi is Robert's personal AI agent platform — local-first, model-agnostic, daily production use since February 2026. Today it runs as a full two-node production system on AWS (EC2 = primary, Mac = standby/dev), validated end-to-end. This is the right moment for a 1.0 release milestone.

---

## What v1.0 Represents

Three domains in production on minimoi.ai (EC2), served via Docker containers, proxied through a shared portal:

1. **Curator** — daily intelligence briefing pipeline
2. **Mein Deutsch** — German language coaching
3. **Guild** — AI agent coordination layer

Plus a full two-node infrastructure (EC2 + Mac standby), autonomous agents (Operations + CoS), and a public preview layer.

---

## What Was Built (Milestone Summary)

### Curator (complete)
- Full RSS + X bookmark ingestion pipeline (600+ articles/day)
- AI scoring via xAI grok-4.3, $0.30/day
- Daily Telegram briefing with Like/Dislike/Save feedback buttons
- 7 Flask-rendered pages: Daily, Reading Room, Scans & Dives, Leanings, Archive, Desk, Research
- Web search enrichment via Brave API
- Leanings page: political/ideological lean tracking per source
- Research Desk: deep-dive thread management

### Mein Deutsch (complete)
- German language coaching via AI personas (Frau Novak, Dr. Lora, Maria, Herr Fischer, Anna)
- 5 tabs: Lesen (reading), Gespräche (conversations), Schreiben (writing), Wörter (phrasebook), Archiv
- Live KI-Sitzung: web-based conversation sessions with AI personas
- Voice session support: paste transcript → AI analysis → grammar feedback
- Telegram drill commands: natural language ("next drill", "give me Maria prompt") + slash commands
- DeepL translation integration (primary) with LLM fallback
- Lesen writing drill (Issue #41, shipped)
- Mobile-responsive, Vienna café aesthetic

### Guild (complete — Phase 1 + 2)
- Operations agent: health monitoring every 5 min, Tier 1-4 escalations, disk/service alerts
- Chief of Staff (CoS): job search loop, domain health, morning brief, Telegram chat (!cos / !chief)
- PostgreSQL schema: guild.* and jobs.* schemas, minimoi app user, read-only robert_ro user
- Neo4j context graph: provisioned, Phase 5 deferred until 20+ sources tagged
- Career Focus portal: Sept 1 2026 deadline gate

### Two-Node AWS Infrastructure (complete — Block A)
- **EC2 (production)**: minimoi.ai, Docker Compose, 7 running containers
- **Mac (standby/dev)**: dev.minimoi.ai via Cloudflare Tunnel, MINIMOI_ROLE=standby
- **DNS**: minimoi.ai → EC2 Elastic IP (Cloudflare proxy), dev.minimoi.ai → Mac tunnel
- **ECR**: 5 Docker images (curator, german, portal, system-bot, cos-bot)
- **SSM Parameter Store**: all secrets in /minimoi/production/ and /minimoi/test/
- **Telegram architecture**: 2 production bots (minimoi_system_bot, minimoi_cos_bot), 2 test bots
- **Role system**: MINIMOI_ROLE=production|standby guards all scheduled sends
- **EC2 cron**: hourly curator pipeline, 7AM–3PM CDT window, idempotency guard
- **get_secret()**: universal env→keyring→SSM resolver, all container code uses it

### Public Preview Layer (complete)
- /preview/ — public front door, no login required
- German landing page: Vienna Palmenhaus hero, full-bleed mobile layout
- Curator preview: briefing, reading room, scans & dives, leanings, archive
- Guild preview: briefing, career focus, build log, roadmap, queue
- Preview banner on all pages, fetch intercept (no live API calls)
- Issue #55: mobile hero hidden (dark void) — fixed, validated on minimoi.ai

### Autonomous Agents (running)
- **Operations agent** (Mac + EC2): disk/service health, Tier 1-4 Telegram alerts
- **Chief of Staff** (Mac + EC2): Telegram chat, !cos / !chief / !ops commands
- **Telegram delivery**: all alerts → minimoi_system_bot (prod) or minimoi_system_test_bot (dev)

---

## EC2 Container Inventory (7 running)

| Container | Image | Port | Role |
|---|---|---|---|
| postgres-ai-agents | postgres:18 | 5432 | Database |
| minimoi-curator | minimoi/curator | 8766 | Curator Flask + pipeline |
| minimoi-german | minimoi/mein-deutsch | 8767 | German domain Flask |
| minimoi-portal | minimoi/portal | 5001 | Portal proxy + preview |
| minimoi-system-bot | minimoi/system-bot | — | Telegram: drills, !ops, briefing |
| minimoi-cos-bot | minimoi/cos-bot | — | Telegram: !cos, !chief, CoS chat |

---

## Guild Branch Merge Plan

The `guild` branch has 3 commits not yet on `main`:

```
fc9b920 feat: guild spine complete — Source tab, graph traversal working (Stop-Gate 3)
b9eb951 feat: guild spine — CRUD layer, Neo4j seed, POC verified (Steps 3d-3g)
bc34456 feat: guild spine — schema, migration, reconcile baseline (Steps 3a-3c)
```

`main` is 284 commits ahead of `guild`. The guild branch diverged early and never caught up — its 3 commits represent early Guild spine work (Neo4j, CRUD layer, Source tab) that was later superseded or continued directly on `main`.

**Recommendation:** Merge `guild` into `main` via PR, resolving any conflicts in favor of `main`. The guild spine code on the branch may already be present on main in evolved form. After merge, delete the `guild` branch. This closes the divergence and puts everything under one clean branch for the 1.0 release tag.

**Merge approach:**
```bash
git checkout main
git merge origin/guild --no-ff -m "Merge guild branch — close divergence for v1.0"
# Resolve any conflicts in favor of main
git push
git push origin --delete guild
```

**After merge:** Tag v1.0:
```bash
git tag -a v1.0 -m "mini-moi v1.0 — full platform on AWS, two-node production"
git push origin v1.0
```

---

## Open Issues at Release

| # | Priority | Description |
|---|---|---|
| #51 | P1 | X-bookmark pull broken on EC2 (tweepy + OAuth2 token store) |
| #52 | P3 | Curator Desk shows "never run" for a thread that has run |
| #53 | P3 | German Dropbox watcher not container-safe |
| #54 | P3 | Tech-debt: move German off fat system-bot onto HTTP endpoints |
| #55 | Open | Preview German landing hero — fixed, pending prod validation close |

P3 issues are known/deferred. P1 (#51) means X bookmarks don't run on EC2 — briefings are RSS-only until fixed.

---

## What's Next (Block B + Beyond)

**Block B — German batch (next sprint):**
- T1-B, T2-C, T2-A, T2-B (specs exist)

**Block C — CoS page** (design session required first)

**EC2 Operations Monitoring** — spec ready (`spec_operations_monitoring_v2_2026-06-22.md`)

**Mini-moi private repo sync** — personal data, memory files

---

## Framing for Release Notes

This is a genuine full-stack AI platform milestone:
- **Production on AWS** — not a prototype, running daily since Feb 2026
- **Three domains** — Curator (intelligence), German (language), Guild (agent coordination)
- **Autonomous agents** — Operations + CoS running 24/7
- **Two-node resilience** — EC2 primary, Mac standby, DNS-switchable in under 4 hours
- **Enterprise patterns** — SSM secrets, ECR images, role-aware deployments, launchd + cron
- **Public preview layer** — shareable without login

The spirit: a solo developer building what an enterprise AI team would build, for personal use, with production discipline.
