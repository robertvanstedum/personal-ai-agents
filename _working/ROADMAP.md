# mini-moi Roadmap
*Living document — versioned in GitHub*
*Updated: via git — see commit history for changes*

---

## How this works

Agreed targets are committed to `docs/` in the repo and listed below
with their source document. Discussion items that haven't been agreed
live in session summaries — not here.

The act of committing is the promotion. If it's not in `docs/`, it's
not an agreed target.

When an item is ready to build: write a spec in `_working/` with
DoD + Commit, then update the item's status here to `queued`.

---

## German

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| **Gespräche mobile** | | | GESPRACHE_ROADMAP.md |
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
| · Phase 0 · Foundation | Phase 0 | in_progress | |
| · Phase 1 · RAG layer | Phase 1 | target | |
| · Phase 2 · LoRA training | Phase 2 | target | |
| · Phase 3 · local-first | Phase 3 | target | |

### Discussion — not yet agreed

- Model merging / adapter routing
- On-device inference for voice

---

## Curator

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| v1.2 · Mac Mini migration | v1.2 | target | (session 2026-04) |
| v1.3 · Neo4j intelligence layer | v1.3 | target | (session 2026-04) |

### Discussion — not yet agreed

- Non-Anglophone source expansion
- Automated briefing delivery

---

## Guild

### Agreed targets

| Item | Phase | Status | Source |
|------|-------|--------|--------|
| Guest access + CoS nudge | v1 | queued | spec_cos_guest_access |
| Security architecture split | v1 | queued | spec_security_architecture |
| Career focus editor | v1 | queued | spec_guild_career_focus_editor |
| Roadmap view maturity | v1 | in_progress | this spec |
| Decisions view | v1 | target | dr_decision_record_practice_mvp |

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
