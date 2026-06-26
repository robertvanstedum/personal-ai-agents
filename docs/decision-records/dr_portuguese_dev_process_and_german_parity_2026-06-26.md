---
type: decision-record
dr-type: design
domain: portuguese
status: active
lora-candidate: yes
---

# Decision Record — Portuguese Dev-First Process & German UX Parity
*2026-06-26 · Robert (session) · mini-moi*

---

## Decision

Two binding rules for all Portuguese domain work going forward:

1. **Dev-first, batch-to-production.** All changes are built and validated on `dev.minimoi.ai` (Mac, port 8770 via portal) before any push to production (EC2, minimoi.ai). Changes ship to production in batches — not one fix at a time.

2. **Portuguese must match German's established UX patterns.** German is the reference implementation. Any Portuguese tab that diverges from the corresponding German tab's flow, button structure, or data display is a defect, not a design choice — unless explicitly documented otherwise.

---

## Context

The Portuguese domain was built and deployed to production (EC2, minimoi.ai) in one session. Since deployment, individual fixes have been pushed directly to main and deployed to EC2 one at a time:
- Color consistency fixes → push → EC2 deploy
- Maria brevity fix → push → EC2 deploy
- Review prompt fix → push → EC2 deploy
- Telegram send button → push → EC2 deploy
- Escrita correction button → push → EC2 deploy

Each push triggers a full CI/CD pipeline run (~3 minutes per push). Changes go live on minimoi.ai without validation on dev first. A bug in one fix cannot be caught before it's production.

The Escrita tab illustrates the UX parity problem. The writing tab was built with two separate buttons (Corrigir + Salvar) because that's what seemed natural. When Robert compared it against the German Schreiben tab, the difference was immediately visible: German uses one button that toggles between "Korrigieren" and "Speichern" depending on a checkbox. That's a design decision made in German's build session — not arbitrary. Portuguese re-invented it differently without realizing there was a reference.

A full page-by-page audit on 2026-06-26 found high-impact UX drift on every tab.

---

## Alternatives considered

### Continue pushing directly to production
The feedback loop is fast (push → CI → EC2 in ~3 min), so it feels efficient. **Rejected** because: (1) every push runs a full CI pipeline costing time; (2) users on minimoi.ai (daughters, wife) see broken intermediate states; (3) one-by-one deploys never get batched, so related fixes go out as noise rather than as coherent releases.

### Build a staging branch
A separate `staging` branch that CI deploys to a staging environment. **Rejected as overkill** for a personal platform. The Mac is already running the full stack at dev.minimoi.ai. A staging branch adds merge discipline without adding a different test environment. The Mac IS the staging environment.

### Document German patterns, then build Portuguese independently
Write out German UX patterns as a spec and let Portuguese follow that spec. **Rejected** because the spec would be stale the moment German evolves. The correct reference is the live German code — Portuguese should mirror it structurally, not mirror a spec written at one point in time.

---

## Constraints

- EC2 free tier: we want to minimize unnecessary rebuilds and pipeline runs
- Daughters and wife are real users on minimoi.ai — they should see stable pages, not in-progress work
- The Mac is always on and running the full stack; dev.minimoi.ai is always accessible
- Claude Code does not have a separate dev branch. The dev environment runs from main (working directory). Changes should be made, confirmed on dev, then committed and pushed as a batch.

---

## Principles

**Dev validates, production receives.** Every observable change goes to dev.minimoi.ai first. If it works there, it ships. If it doesn't, it never touches production.

**German is the reference, not the spec.** When Portuguese deviates from German, that's a defect unless a DR explicitly records why the deviation is intentional. "It seemed easier" is not a valid reason.

**Batch commits.** Related changes that form a coherent unit (a full tab parity fix, a feature, a page) ship as one commit, not six. One-line fixes to a file that was just fixed are a signal that the work wasn't done correctly the first time in dev.

**Decision records travel with process changes.** When the build process changes (as it is now), a DR captures it before the first build under the new rules, not after.

---

## UX parity defects found — 2026-06-26 audit

The following HIGH-impact drifts were found when comparing Portuguese tabs against German:

### Escrita ↔ Schreiben
- **Button flow**: Portuguese has two buttons (Corrigir + Salvar). German has one button that changes label based on checkbox (Korrigieren/Speichern). **Must match German.**
- **History entries**: Portuguese shows no error/hints count. German shows "N Hinweise". **Must match German.**
- **Word-click translate**: Portuguese adds inline translation tooltip (not in German). This is a Portuguese-specific addition — acceptable, document in a separate DR if kept.

### Palavras ↔ Wörter
- **Practice drill**: Portuguese drill is minimal (one direction, no voice, no assess buttons). German drill has direction toggle, voice input, ✓/✗/Skip assessment. **Must match German.**
- **Anki export**: Portuguese has no export button. German has Anki export per entry. **Must match German.**
- **Practice inside word detail**: German has full inline practice section when expanding a word. Portuguese does not. **Must match German.**
- **Status states**: Portuguese collapses practice + review-ready into one state. German has four states (Bibliothek, Üben, Bereit zu prüfen, Gelernt). **Evaluate — may need schema change.**

### Conversas ↔ Gespräche
- **Two-tab system**: Portuguese has only the KI-Persona tab. German has both KI-Persona and real-person (Mit echtem Mensch) tabs with structured before/during/after flow. **Spec required before building.**
- **Round management**: Portuguese has no persona round tracking or memory advancement. German tracks rounds, surfacing "bereit" state per persona. **Spec required before building.**
- **Telegram prompt send**: Portuguese has no button to send persona prompt to Telegram. German has it. **Must match German.**

### Arquivo ↔ Archiv
- **Hints count**: Portuguese writing entries show no error count. German shows "N Hinweise". **Must match German.**

### Landing
- **Archive link**: Portuguese landing shows Arquivo in nav. German landing does not show Archiv. **Remove from Portuguese landing to match.**

### Admin
- Admin is intentionally different — Portuguese admin will evolve separately as the feature set grows. This drift is **accepted**.

---

## What this is not

This DR does not authorize a full rebuild of Portuguese. The gaps above are categorized:
- **Pattern fixes** (button flow, history counts, Telegram button): Claude Code builds on dev, batches to production
- **Feature parity** (drill depth, round management, two-tab conversations): requires a spec from Claude.ai before Claude Code builds
- **Portuguese-specific additions** (word-click translate, Telegram feedback send): evaluate per feature — not removed until a DR says why

---

## Process going forward

1. Changes start on Mac (dev.minimoi.ai) — `launchd` services restart to pick up code changes
2. Test the golden path on dev before committing
3. Commit as a logical batch (e.g., "Escrita: full German parity" not "fix button" + "fix button again")
4. Push once — CI builds, EC2 deploys, production receives a coherent release

For the Portuguese parity fixes, the build sequence is:
- Pattern fixes (no spec needed): Claude Code builds on dev → Robert validates → batch commit + push
- Feature parity (spec needed): Claude.ai spec → Robert approves → Claude Code builds on dev → Robert validates → batch push

---

## Open questions

- Palavras status states: does Portuguese need "Bereit zu prüfen" (review-ready) as a fourth state, requiring a DB migration?
- Conversas round management: does this exist in the Portuguese domain DB schema or is it German-only?
- Word-click translate in Escrita: keep it (Portuguese advantage) or remove for consistency?

---

## Impact & follow-up

- Next session: bring Escrita and Palavras to German parity (pattern fixes only — no new spec needed)
- Following session: Conversas parity (spec first)
- Decision records for the accepted Portuguese-specific additions (translate tooltip) to be written when those features are confirmed working
