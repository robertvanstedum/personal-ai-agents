# Plan: Intelligence Layer — Phase C
## Response Capture & Feedback Loop Foundation
**Mini-moi Personal AI Curator**
**Prepared:** March 15, 2026
**Sprint:** 1.0 — Workstream 5, Phase C
**Status:** Ready for OpenClaw validation → Claude Code build
**Depends on:** Phase A ✅ Phase B ✅

---

## Purpose

Phase C closes the intelligence feedback loop at the data layer.

The intelligence layer (Phases A and B) surfaces observations — topic momentum,
blind spots, lateral connections, source anomalies. Currently those observations
go out and nothing comes back in. The system cannot learn from your reactions,
positions, or stated thinking.

Phase C captures your responses and stores them in a structured format designed
for future activation. In 1.0, nothing acts on these responses yet. In 1.1,
the Sonnet lateral connections prompt reads them, the investigation workspace
opens from them, and the RAG layer queries them.

**1.0 delivers the data. 1.1 connects it to infrastructure and acts on it.**

This is the foundation for the full RAG architecture — vector search across
your notes, positions, and dialog history — planned as the major architectural
upgrade post-1.0.

---

## Architecture Note (for README and portfolio)

> Mini-moi 1.0 delivers a complete intelligence observation loop: the system
> surfaces what it notices, and you can now tell it what you think. Those
> responses are stored in a structured, queryable format ready for the next
> layer — graph database, vector search, and automated action — planned for
> 1.1 and beyond.

This narrative should appear in the README architecture section and the
public roadmap.

---

## What Phase C Builds

### New file: `intelligence_responses.json`

Append-only array. Every response you give to any intelligence observation
lands here. Simple enough to build now. Structured enough for 1.1 to query.

**Schema:**
```json
{
  "responses": [
    {
      "id": "resp_001",
      "date": "2026-03-15",
      "observation_type": "lateral_connection",
      "observation_ref": "intelligence_weekly_20260315.json",
      "topic": "crypto as speculative asset vs uncertainty hedge",
      "domain": "finance",
      "reaction": "disagree",
      "position": "Crypto tracks as speculative asset, not uncertainty hedge. Interest is specifically gold/crypto correlation during geopolitical stress, not generic crypto coverage.",
      "confidence": "medium",
      "want_more": true,
      "pending_action": null,
      "acted_on": false,
      "timestamp": "2026-03-15T14:30:00Z"
    }
  ]
}
```

**Field definitions:**

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string | Unique response ID — `resp_NNN` incrementing |
| `date` | string | YYYY-MM-DD |
| `observation_type` | string | `lateral_connection`, `blind_spot`, `topic_velocity`, `source_anomaly`, `freeform` |
| `observation_ref` | string | Filename of the intelligence JSON this responds to. Null if unprompted. |
| `topic` | string | The specific topic or suggestion being responded to |
| `domain` | string | `finance`, `geopolitics`, `health`, `technology`, `personal`, `other` |
| `reaction` | string | `agree`, `disagree`, `already_tracking`, `not_relevant`, `want_more`, `note` |
| `position` | string | Free text — your stated view, as specific as you want |
| `confidence` | string | `high`, `medium`, `low`, `uncertain` |
| `want_more` | boolean | Flag: I want to know more about this topic |
| `pending_action` | string | `open_investigation`, `activate_priority`, `add_source`, null |
| `acted_on` | boolean | False in 1.0 — 1.1 sets true when action is taken |
| `timestamp` | string | ISO 8601 |

**Three response types:**

1. **Reaction to observation** — links back to specific intelligence output via `observation_ref`
2. **Stated position** — your view on a topic, prompted or unprompted. Survives independently of the observation that triggered it.
3. **Research intent** — `want_more: true` + `pending_action: "open_investigation"`. Stored now, acted on in 1.1.

---

## Capture Surfaces

Two surfaces for entering responses. Both write to the same
`intelligence_responses.json` file.

### Surface 1: Web UI — Intelligence tab

On the web UI Intelligence page (new tab or section), each observation
displayed with a response form beneath it:

- **Reaction** — dropdown: Agree / Disagree / Already tracking / Not relevant / Want more
- **Position** — free text field: "Your view (optional)"
- **Want more** — checkbox
- **Submit** button — writes to `intelligence_responses.json`

Display today's intelligence JSON and the most recent weekly JSON.
Previously submitted responses shown read-only beneath each observation.

### Surface 2: Telegram dialog bot

When you reply to a weekly connections or daily intelligence Telegram message,
the dialog bot:
1. Detects it is a reply to an intelligence message (via Telegram reply threading)
2. Parses your reply text
3. Makes a Haiku call to classify: domain, reaction type, position extraction
4. Writes structured response to `intelligence_responses.json`
5. Confirms back: "Noted — stored as [reaction] on [topic] in [domain] domain."

For unprompted notes (not a reply to an intelligence message):
- Bot detects "note:" prefix or classifies intent as a personal note
- Haiku classifies domain and response type
- Stored with `observation_ref: null`, `observation_type: "freeform"`

---

## Files Touched

| # | File | Action |
|---|------|--------|
| 1 | `intelligence_responses.json` | New file — create with empty responses array |
| 2 | `curator_intelligence.py` | Add `save_response()` helper function |
| 3 | Flask server | Add `POST /api/intelligence/respond` endpoint |
| 4 | New HTML page or section | Intelligence tab in web UI — display observations + response form |
| 5 | Telegram dialog bot | Add reply detection + Haiku classification + response write |

---

## What Is Explicitly NOT Built in Phase C

- Sonnet reading `intelligence_responses.json` — 1.1
- Automatic priority activation from `pending_action` — 1.1
- Investigation workspace opening from response — 1.1 (depends on WS3)
- Vector search or graph DB queries against responses — post-1.0
- Response history surfaced in weekly lateral connections — 1.1

These are noted here so the build record is clear: the schema is designed
for these future uses, but Phase C only captures. It does not act.

---

## Integration with Broader Roadmap

**1.0 delivers:**
- Intelligence observation loop (Phases A + B)
- Response capture layer (Phase C)
- `intelligence_responses.json` as the seed of the personal memory system

**1.1 connects:**
- Sonnet reads responses before generating lateral connections
- `pending_action` items activate automatically
- Investigation workspace opens from research intent responses
- Dialog history from Telegram bot stored and queryable

**Post-1.1 activates full RAG:**
- pgvector (already installed) indexes `intelligence_responses.json`,
  `curator_signals.json`, dialog history
- Neo4j (already installed) maps relationships between topics, positions,
  and reading history
- Every LLM call retrieves semantically relevant personal context before generating
- "What have I thought about this before?" becomes a real query

---

## Open Questions for OpenClaw

1. **intelligence_responses.json location** — confirm whether this lives in
   `~/.openclaw/workspace/` alongside other intelligence files, or in the
   project repo directory. Recommendation: workspace (personal data, not source code).

2. **Telegram dialog bot file** — confirm filename and location of the existing
   OpenClaw Telegram dialog bot. Phase C adds reply detection to it.

3. **Flask server file** — confirm filename for the Flask web server to add
   the new API endpoint.

4. **Web UI Intelligence tab** — does a page or section for intelligence output
   already exist in the web UI, or is this new? If new, confirm naming convention
   for HTML files.

5. **Existing response patterns** — confirm whether any response/annotation
   schema already exists in the workspace from earlier design sessions that
   should be reconciled with this schema.

---

## Verification Steps

**After Step 1-3 (backend):**
```bash
curl -X POST http://localhost:8765/api/intelligence/respond \
  -H "Content-Type: application/json" \
  -d '{
    "observation_type": "lateral_connection",
    "observation_ref": "intelligence_weekly_20260315.json",
    "topic": "crypto as speculative asset",
    "domain": "finance",
    "reaction": "disagree",
    "position": "Crypto tracks speculative not hedge. Gold correlation is the angle.",
    "confidence": "medium",
    "want_more": true
  }'
```
Expected: 200 OK, response written to `intelligence_responses.json` with auto-generated id and timestamp.

**After Step 4 (web UI):**
Open Intelligence tab in browser. Confirm today's observations display.
Submit a test response. Confirm it appears read-only beneath the observation.
Confirm `intelligence_responses.json` updated correctly.

**After Step 5 (Telegram):**
Reply to the most recent weekly connections Telegram message with:
*"Disagree on crypto — I track it only through gold correlation, speculative asset thesis"*
Expected: Bot confirms storage, `intelligence_responses.json` updated with
classified response linked to the correct weekly observation.

---

## Success Criteria

- `intelligence_responses.json` created with correct schema
- Web UI accepts and stores responses against specific observations
- Telegram reply correctly classified and stored with observation link
- Unprompted Telegram notes stored with `observation_ref: null`
- `acted_on: false` on all 1.0 responses — no premature action
- `pending_action` field populated correctly when research intent detected
- No regressions to Phase A or Phase B observations

---

## Post-Build

Claude Code writes build summary → "pass to Memory Agent" →
Memory Agent saves to `docs/BUILD_WS5_PhaseC_2026-03-15.md`,
appends CHANGELOG.

README architecture section to reference Phase C as the
feedback loop foundation. To be written by Strategy Agent
(Claude.ai) with Robert's input before public launch.

---

*Prepared by Claude.ai (Strategy Agent) — March 15, 2026*
*For OpenClaw (Memory Agent) validation before Claude Code handoff*
*Convention: Memory Agent saves to `docs/PLAN_WS5_PhaseC_2026-03-15.md`*

---

## Scope Amendments (March 15, 2026 — post-OpenClaw/Claude.ai review)

**Step 5 (Telegram dialog bot) deferred to 1.1.** Steps 1–4 only for 1.0.

### UI Design Notes (Claude.ai)

**1. Position field is primary, reaction is secondary.**
The `position` textarea is the actual value. The reaction dropdown is a classification convenience. UI order per observation:
```
[Observation text — read only]

Your view:
[textarea — 3-4 rows, prominent]

Reaction: [dropdown]  □ Want to know more

[Save response]
```
Position first, reaction second. Forces the richer input.

**2. Two sections with different response treatment.**
Weekly lateral connections get full response form. Daily observations get lighter treatment.

Page structure:
```
INTELLIGENCE — March 15

── WEEKLY CONNECTIONS ───────────────────────────
[Each connection: full form — textarea + reaction + want more + save]

── TODAY'S OBSERVATIONS ─────────────────────────
[Each observation: light form — reaction dropdown + one-line note field]
```

Weekly = substantive positions captured. Daily = quick reaction + optional note.
