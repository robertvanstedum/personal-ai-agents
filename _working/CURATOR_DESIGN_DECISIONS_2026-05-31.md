# Curator Design Decisions — Confirmed
*Resolved in concept sessions. Handed to Claude Code 2026-05-31.*
*For OpenClaw: use this as the brief for the Leaning PLAN.*

All seven questions from the concept paper are settled. No re-deliberation needed.

---

## Q1 — Naming: Lean + Hold ✓

**Confirmed:** `Question → Lean → Hold`

- **Hold** over Position — "Position" carries trading-room meaning; "Hold" means "where I stand now," conveys commitment without finality.
- Applies to the Leaning object's state field.

**Build implication:** Leaning schema uses `state: "question" | "lean" | "hold"`.

---

## Q2 + Q3 — Pull vs Priority + Modal: dissolved by Topic state machine ✓

**Confirmed:** Priority-as-a-separate-object is superseded. The three Topic states ARE the distinction.

| Old concept | New model |
|---|---|
| Curator Priority (free, indefinite, daily stream) | Topic in `dormant` state — passively catches relevant articles, no session cost |
| Research Thread / active pull (paid sessions, engagement-gated) | Topic in `active-pull` state |
| One-time deep pull | Topic in `one-shot` state |

The "Investigate this" modal offers **three Topic states**, not "Session vs Priority":
- **Watch for it** → `dormant` (free, indefinite, rides the daily stream)
- **Go find it** → `active-pull` (paid sessions, 10–14 day engagement gate)
- **One deep pull now** → `one-shot` (single session, then closes)

**Build implications:**
- `curator_signals.json` (the old Priority object) is superseded. No new writes after migration; existing signals stay read-only as history.
- The `dormant` state needs routing behavior: when a Topic is dormant, relevant articles from the daily briefing should surface to it. This is the "free watch" path. (Implementation: tag/keyword match against Topic's tags + queries — deterministic, no AI cost. This was the Q2 Hybrid-C decision from the earlier handoff doc.)
- The "Investigate this" modal is a UI work item — three radio buttons → one Topic record with the chosen state. Not a data model change; the state machine already exists.
- `activate_topic()` already handles dormant → active-pull. `one-shot` state already exists in the machine.

---

## Q4 — Boundary on manually added Sources: no check ✓

**Confirmed:** Fully Robert's judgment at add time. No territory gate.

- System enforces territory at **pull time** (what it goes looking for in sessions).
- At **add time** (what you bring to it): your call. Old book, personal reading, anything — carry it in as-is.
- Optional soft flag ("this feels off-territory — override?") is available but never a block. Build it as a future nicety, not a requirement.

**Build implication:** `promote_manual()` already implements this correctly — no territory check, no gate. Carry this behavior forward unchanged. If the soft flag is ever added, it's advisory output only, never blocking.

---

## Q5 — Teammate read trigger: on-demand + free badge ✓

**Confirmed:** AI call is explicit (you tap "Get teammate read"), never automatic. Free badge shows count of new Sources since last read.

**Build implications for Leaning schema:**
```json
{
  "last_read_at": "2026-05-31T...",
  "sources_since_last_read": 3,
  "teammate_reads": [
    {
      "read_at": "...",
      "assessment": "...",
      "implication": "...",
      "source_count_at_read": 7
    }
  ]
}
```
- `sources_since_last_read` increments free (no AI) whenever a Source is attached to the Leaning.
- `last_read_at` + `teammate_reads` append on each explicit read call.
- Badge = `sources_since_last_read > 0`. Costs nothing to show; read is Robert's decision.
- All reads route through the budget gate (`run.py` daily $3 / weekly $10 / total $20).

---

## Q6 — Leaning evidence: both auto-gathered and manually attached ✓

**Confirmed:** Two paths, always available.

1. **Auto-gathered** — system collects relevant Sources from the Leaning's grouped Topics.
2. **Manual attach** — hand-attach anything regardless of origin (found it yourself, outside the pull, from a different domain).

**Build implication:** Leaning evidence is a single `sources` list with an `attachment_type` field:
```json
{
  "sources": [
    {
      "source_id": "src_20260531_001",
      "attachment_type": "auto",    // "auto" | "manual"
      "stance": "supports",          // "supports" | "complicates" | "neutral"
      "note": "..."
    }
  ]
}
```
Both paths write to the same list. UI can filter/display by `attachment_type` if useful.

---

## Q7 — Naming: Scan / Dive ✓

**Confirmed:** `Scan` replaces Brief/Quick Take. `Dive` replaces Deep Study/Deep Dive/Deeper Dive.

- **Scan** = quick, cheap, further-reading appendix. Replaces current `interests/2026/deep-dives/` article-level outputs.
- **Dive** = longer, harder, costs more, manually triggered, cost-monitored. Replaces current `data/deeper_dives/` thread-level analyses.
- Rename is **atomic** — two directories + two route handlers in one commit to avoid muscle-memory confusion with the old names.

**Build implication — atomic rename commit:**
```
interests/2026/deep-dives/     → interests/2026/scans/
data/deeper_dives/             → data/dives/
```
Route handlers in `curator_server.py` and `research_routes.py` updated in the same commit.
The index template (`interests/2026/deep-dives/index.html`) becomes `interests/2026/scans/index.html`.

---

## Summary for OpenClaw — next PLAN scope

**What the Leaning PLAN needs to cover (in priority order):**

1. **Atomic rename** (Q7) — Scan/Dive. Small, bounded, do first so naming is consistent throughout the Leaning build. One commit.

2. **Leaning object** — schema (Q1: `question|lean|hold` state), evidence list (Q6: auto + manual with `attachment_type` + `stance`), teammate read fields (Q5: badge + on-demand reads + budget gate).

3. **"Investigate this" modal** (Q2+Q3) — UI work: three-state picker when promoting an article or creating a Topic. Dormant/active-pull/one-shot options. Wraps existing `create_topic()` + `activate_topic()` calls.

4. **Dormant routing** (Q2+Q3 dissolution) — dormant Topics catch relevant articles from the daily stream via deterministic tag/keyword match. No AI cost. Surfaces matches in the UI (collapsed section, per the Hybrid-C decision). Implementation after Leaning object exists.

**Out of scope for Leaning PLAN:**
- DB spine (Postgres + Neo4j — separate Track-B)
- Domain pluggability
- Canonicalization UI
- Portfolio-tension scan (Guild)

---

*These decisions are final. Build to them.*
