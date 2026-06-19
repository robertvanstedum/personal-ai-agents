# mini-moi Roadmap
*Living document — versioned in GitHub*

---

## German

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| **Gespräche mobile** | | | GESPRACHE_FORWARD_SPEC.md |
| · Mobile fixes | Phase 0 | queued | |
| · Automatic transcript handoff | Phase 1 | target | |
| · Voice command routing | Phase 2 | target | |
| · PWA wrapper | post-mobile | target | |
| · LoRA German error adapter | Phase 2 | target | LEARNING_SYSTEM_ROADMAP.md |
| · Persona aus echtem Gespräch | Phase 3 | target | |
| · Cross-session error detection | Phase 4 | target | |

### Discussion — not yet agreed

- Swipe between German tabs (E7)
- Persona card preview on tap (E5)
- Schreiben history rows clickable

---

## Platform

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| Decision Records practice | Phase 0 | done | DECISION_RECORD_PRACTICE.md |
| **Learning System** | | | LEARNING_SYSTEM_ROADMAP.md |
| · Phase 0 · Foundation | Phase 0 | in_progress — no build task | |
| · Phase 1 · RAG layer | Phase 1 | target | |
| · Phase 2 · LoRA training | Phase 2 | target | |
| · Phase 3 · local-first | Phase 3 | target | |
| **AWS Migration** | | | AWS_MIGRATION_PLAN.md |
| · Phase 0 · Containerization | Phase 0 | spec_ready | |
| · Phase 1 · AWS Foundation | Phase 1 | target | |
| · Phase 2 · Cloud deployment | Phase 2 | target | |
| · Phase 3 · CI/CD pipeline | Phase 3 | target | |
| · Phase 4 · Data layer (RDS + S3) | Phase 4 | target | |
| · Phase 5 · GPU instance (local LLM) | Phase 5 | target | |
| · Phase 6 · Hardening + Monitoring | Phase 6 | target | |
| **Code Quality Review** | | | CODE_REVIEW_PLAN.md |
| · Phase 0 · Hardcoded paths + secrets | parallel to AWS Phase 0 | spec_ready | spec_code_review_phase0_2026-06-19.md |
| · Phase 1-2 · Error handling + auth | parallel to AWS Phase 1-2 | target | |
| · Phase 3-4 · Performance + dead code | parallel to AWS Phase 3-4 | target | |
| **Monitoring Stack** | | | spec_monitoring_stack_2026-06-18.md |
| · Sentry + Prometheus + Grafana + CloudWatch | AWS Phase 6 | spec_ready | spec_monitoring_stack_2026-06-18.md |

### Discussion — not yet agreed

- Model merging / adapter routing
- On-device inference for voice

---

## Curator

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| v1.2 · Mac Mini migration | — | deferred | Replaced by AWS Migration |
| v1.3 · Neo4j intelligence layer | v1.3 | target | (session 2026-04) |

### Discussion — not yet agreed

- Non-Anglophone source expansion
- Automated briefing delivery

---

## Guild

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| CoS interaction page | v1 | target | COS_PAGE_ROADMAP.md |
| Guest access + CoS nudge | v1 | queued | spec_cos_guest_access |
| Security architecture split | v1 | queued | spec_security_architecture |
| Career focus editor | v1 | queued | spec_guild_career_focus_editor |
| Roadmap view maturity | v1 | done | this spec |
| Decisions view | v1 | target | dr_decision_record_practice_mvp |
| Docs browser | v1 | done | spec_guild_docs_view_2026-06-19.md |

### Discussion — not yet agreed

- Guest auto-creation on grant
- Auto-match guest email to career pipeline

---

## Done (recent)

| Item | Shipped | Notes |
|------|---------|-------|
| Decision Record practice | 2026-06-17 | c0dbf35 |
| Mobile audit fixes (11) | 2026-06-16 | All German pages rendering |
| Mein Deutsch v1.1 | 2026-06-16 | mein-deutsch-v1.1 tag |
| Guild v0.9 | 2026-06-13 | |
