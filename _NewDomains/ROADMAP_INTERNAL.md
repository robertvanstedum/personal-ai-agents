# Mini-moi — Internal Vision
*Private — not for README or public docs*
*Created: March 22, 2026*

---

## The Arc

1. **Localhost tool (now)** — functional, personal daily use, CLI gaps closing
2. **Demo-ready** — screen recording + screenshots for GitHub portfolio
3. **Hosted with auth** — self + family/friends. Real users, real auth, real deployment.
4. **Commercial port** — architecture proven on personal use, lifted into standalone product for a new domain. Mini-moi stays personal. Commercial version is new.

---

## Design Principle

UI must be built once, built right. Left rail + design tokens are the foundation that scales from localhost → hosted → commercial without a rewrite. Do not build localhost-only shortcuts (hardcoded ports, no auth layer, no user model) that have to be torn out later.

---

## Today's Priority (March 22, 2026)

Functionality over UI. Close the research loop: `candidates.html` + `save.html` working without CLI. Production flow for research backend. Robert will tune the research tool over the next week before expanding scope.

---

## Domain Expansion Sequence

1. Geopolitics & Finance — live (v1.0)
2. Research Intelligence — in progress
3. Language Learning — next
4. Commercial domain — TBD (architecture proven first)

---

---

## Daily Briefing 1.1 + Deep Dive Bridge
*Captured: March 22, 2026*

### The Key Insight

Daily briefing and research agent are complementary layers, not competing ones:

- **Briefing** — what's happening now. Current events, scored by profile. Wide and fast.
- **Research agent** — what's intellectually deep. Academic sources, citation chains, theoretical frameworks. Narrow and slow.

**Deep Dive is the bridge:**
```
Briefing article → user flags for Deep Dive →
Deep Dive auto-generates research queries →
research agent session runs →
findings appear in observe layer →
feeds back to next briefing scoring
```

One gesture ("go deeper on this") crosses from current events into scholarship. The loop closes.

### UI Implication

Deep Dive page becomes the connection point between the two domains. Users don't need to manually operate both systems. The separation is clear (briefing vs research) but the flow is seamless (Deep Dive bridges them).

### v1.1 Scope — Three Things

1. **Broader briefing sources** — fix X-heavy source weighting
2. **Deeper briefing sources** — more substantive, less news
3. **Deep Dive → research session handoff** — the new architectural connection

---

## Related Docs

- Public roadmap: `ROADMAP.md` (repo root)
- Vision / ownership architecture: `~/.openclaw/workspace/VISION.md`
- Build plan (current): `_NewDomains/research-intelligence/docs/specs/build-plan-2026-03-22.md`\n\n## Upcoming Builds (Post-Phase 1)\n- Implement Source Intelligence Upgrade (GitHub #10)\n- Implement Deeper Dive Mechanics (GitHub #11)\n\nPrioritize: Novelty + Discovery first for Source Upgrade.
