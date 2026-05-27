# Design Decisions Log

Running record of significant design choices and the reasoning behind them.
The goal: any agent can read this and understand *why*, not just *what*.

---

## Format

Each entry:
```
### [Date] — [Domain]: [Decision title]
**Decision:** What was decided.
**Why:** The reasoning. Alternatives considered.
**Trade-offs:** What was given up.
**Owner:** Who made the call.
```

---

## Entries

### 2026-05-27 — Guild: CoS at two levels (domain + platform)
**Decision:** One Chief of Staff role, two scopes. Same CoS orchestrates Guild's cabinets AND
reads across all three domains (Curator, German, Guild). The intent-register is platform-wide.
**Why:** Avoiding two separate coordination layers for the same person. Robert doesn't need
a CoS for Guild and a separate CoS for the platform — that's overhead.
**Trade-offs:** The CoS role is slightly broader than a single-domain CoS. Manageable at current scale.
**Owner:** Robert + Claude.ai design session (2026-05-26)

---

### 2026-05-27 — Guild: career/ stays as-is, not renamed to guild/
**Decision:** `career/api-toolkit` stays where it is. Guild references it. No rename or move.
**Why:** No churn while domains are still stabilizing. The api-toolkit is a Design-Build artifact
and works well in its current location. Rename at v1.0 if it makes sense.
**Trade-offs:** Slight inconsistency (career/ inside a Guild domain). Acceptable.
**Owner:** Robert + Claude.ai design session (2026-05-26)

---

### 2026-05-27 — Curator v1.1: X post hard cap = 4
**Decision:** Maximum 4 X bookmark posts per 20-article briefing, shared across Phase 1 + Phase 2.
**Why:** Without a global cap, 6+ X handles could all enter the briefing before the diversity
penalty triggered. Each @handle was a unique source, so there was no penalty until you hit
the same handle twice. This let X dominate regardless of RSS quality.
**Trade-offs:** Hard cap is blunt — a session with 4 genuinely exceptional X posts can't get a 5th.
Acceptable given the typical signal distribution.
**Owner:** Robert + Claude Code (2026-05-27)

---

### 2026-05-27 — Curator v1.1: Age-scaled interest boosts
**Decision:** Both interest_boost and priorities_boost are multiplied by age_multiplier before
being added to final_score.
**Why:** Age penalty correctly reduced base scores but historical boosts (+10 for high-interest
sources) were not adjusted. A 13-day old article with high interest still beat fresh articles
with the same Grok score. After fix: fresh articles win when scores are comparable.
**Trade-offs:** Historical signal strength decays — a source with strong interest history doesn't
get full credit for older content. This is intentional behavior.
**Owner:** Robert + Claude Code (2026-05-27)
