# Roadmap Review — mini-moi
*Created: 2026-06-17 — Claude Code*
*Audience: Claude.ai*
*Purpose: Review Roadmap items across all four domains. Advise on sequencing,
gaps, and what should be specced first when Robert starts the next build session.*

---

## Context

Build queue is now a clean slate — 0 spec ready, 0 incomplete, 0 in build.
All recent work is done and committed. Robert starts fresh from the Roadmap
tomorrow and wants Claude.ai's read on what to tackle first and whether the
Roadmap is correctly structured.

Source of truth: `_working/ROADMAP.md` (read-only in portal, edit in file).

---

## German

### Agreed targets — Gespräche mobile
Reference doc: `docs/GESPRACHE_FORWARD_SPEC.md`

The mobile showcase vision is the primary German priority. The full loop
(persona → VAD session → Beenden → Analysieren → Neue Sitzung) works on
mobile now. Next tier:

| Item | Phase | Status | Notes |
|------|-------|--------|-------|
| Mobile fixes (11 items) | Phase 0 | queued | All shipped — this row is stale |
| Automatic transcript handoff | Phase 1 | target | Remove manual Analysieren tap; needs design session first |
| Voice command routing | Phase 2 | target | Intent classifier; needs 2-4 weeks daily use data first |
| PWA wrapper | post-mobile | target | After mobile loop is polished |
| LoRA German error adapter | Phase 2 | target | Signal accumulating; Phase 2 concern |
| Persona aus echtem Gespräch | Phase 3 | target | Long-range |
| Cross-session error detection | Phase 4 | target | Long-range |

**Items ready to spec now** (from GESPRACHE_FORWARD_SPEC.md, no design session needed):
- T1-B — Safe area + font size audit (`env(safe-area-inset-bottom)`, all inputs ≥ 16px)
- T2-A — iOS Share Sheet (`navigator.share` after session)
- T2-B — Schreiben save confirmation toast (1.5s "Gespeichert ✓")
- T2-C — Post-session summary card (turns, persona, duration — no LLM call)

**Note:** The Gespräche toggle (KI-Personas / Konversation pill) was mentioned
as a next item before the Gespräche mobile sprint, but it isn't in the Roadmap
or the forward spec. The tab rename (Mit KI-Persona / Mit echtem Mensch) shipped
instead. Is the pill toggle still wanted, or does the tab rename cover it?

### Discussion — not yet agreed
- Swipe between German tabs (E7)
- Persona card preview on tap (E5)
- Schreiben history rows clickable

---

## Platform

### Agreed targets

| Item | Phase | Status | Notes |
|------|-------|--------|-------|
| Decision Records practice | Phase 0 | done | Committed 2026-06-17 |
| Learning System — Foundation | Phase 0 | in progress | DR folder exists, habit started |
| Learning System — RAG layer | Phase 1 | target | Needs DR inventory to be worth using |
| Learning System — LoRA training | Phase 2 | target | Long-range |
| Learning System — local-first | Phase 3 | target | Long-range |

**Question for Claude.ai:** The Learning System Phase 0 is marked "in progress"
but there's no active build task for it. What does "Foundation" mean concretely —
is it just establishing the DR habit (which is happening), or is there a build
component (DR index, search UI) that should be in the queue?

### Discussion — not yet agreed
- Model merging / adapter routing
- On-device inference for voice

---

## Curator

### Agreed targets

| Item | Phase | Status | Notes |
|------|-------|--------|-------|
| v1.2 · Mac Mini migration | v1.2 | target | From April 2026 session |
| v1.3 · Neo4j intelligence layer | v1.3 | target | From April 2026 session |

**Note:** Curator v1.1 is complete and running daily. v1.2 (Mac Mini migration)
and v1.3 (Neo4j) are both from an April session. No active work planned near-term.
Is the sequencing still correct — v1.2 before v1.3? Any update on Mac Mini
migration timeline?

### Discussion — not yet agreed
- Non-Anglophone source expansion
- Automated briefing delivery

---

## Guild

### Agreed targets

| Item | Phase | Status | Notes |
|------|-------|--------|-------|
| CoS interaction page | v1 | target | Design session pending — COS_PAGE_ROADMAP.md |
| Guest access + CoS nudge | v1 | queued | Spec exists: spec_cos_guest_access |
| Security architecture split | v1 | queued | Spec exists: spec_security_architecture |
| Career focus editor | v1 | queued | Spec exists: spec_guild_career_focus_editor |
| Roadmap view maturity | v1 | in progress | Build partially done |
| Decisions view | v1 | target | Needs DR inventory first |

**Priority question for Claude.ai:** Three items are queued (guest access,
security architecture, career focus editor) — each has a spec. What order
should these go to Claude Code? Robert's career focus deadline is Sep 1 2026,
which may affect priority. The CoS interaction page is the highest-value
Guild item long-term but needs a design session first.

**Roadmap view maturity** is marked in progress but may be complete — the
portal Roadmap view renders correctly. Worth confirming whether any build
work remains.

**Decisions view** needs a DR inventory (a few DRs committed) before
there's enough to display. The habit is just starting.

### Discussion — not yet agreed
- Guest auto-creation on grant
- Auto-match guest email to career pipeline

---

## Questions for Claude.ai

1. **German next:** The T1-B/T2-A/T2-B/T2-C items are ready to spec and
   small enough to batch into one session. Is that the right first move,
   or is something else higher priority in German?

2. **Gespräche toggle:** Still wanted, or covered by the tab rename?

3. **Guild sequencing:** Of the three queued specs (guest access, security
   architecture, career focus editor), what order given the Sep 1 career deadline?

4. **Learning System Phase 0:** Is there a build component, or just the habit?

5. **Roadmap view maturity:** Is the in-progress item actually done?

6. **Anything missing?** Are there agreed items or recent decisions not
   reflected in the Roadmap?
