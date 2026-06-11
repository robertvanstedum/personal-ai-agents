# Handoff — Career Focus: Closed Column + Close Reason Design
*mini-moi · Guild · Career Focus*
*Authored: 2026-06-11 — Claude Code*
*For: Claude.ai (OpenClaw) design review*
*Do not build until Robert approves the design*

---

## Current state (as of this morning)

The Career Focus pipeline at `minimoi.ai/guild/career` has 6 columns:

```
SUGGESTED → REVIEWING → APPLIED → INTERVIEW → CLOSED → REJECTED
```

**What was just changed (this session):**
- "ARCHIVED" renamed to "CLOSED" — clearer intent (position no longer in play)
- "OFFER" column removed — Robert's goal is getting to interview; offer is rare and marks end-of-search
- Status update bug fixed — the route's `valid` set wasn't accepting "closed", so card moves silently failed. Now fixed.

**DB column:** `jobs.career_opportunities.status` — plain TEXT, no enum constraint.

**Route:** `POST /guild/career/positions/<id>/status` in `minimoi_portal/app.py` — accepts any value in the `valid` set, updates DB, redirects back to the board.

---

## Robert's design question

Robert wants "CLOSED" to stay as the column (bucket for positions no longer in play) but with a **close reason** that distinguishes *why* a position is closed:

- **Filled** — job was taken off the board / already filled before I applied
- **Rejected** — company rejected my application
- **Declined** — offer made, I turned it down
- **Accepted** — offer made and accepted (positive outcome — end of search)

The column stays CLOSED. The reason adds context to each card.

---

## Design questions for Claude.ai to think through

### 1. Where does the reason live — DB or status value?

**Option A — Separate `close_reason` column**
- DB: add `close_reason TEXT` to `jobs.career_opportunities`
- UI: when user selects "Closed" from dropdown, a second picker appears (reason)
- Pros: clean separation — status is pipeline position, reason is outcome detail
- Cons: requires schema migration + two-step UI interaction

**Option B — Compound status value**
- Store `closed:filled`, `closed:rejected`, `closed:declined`, `closed:accepted` as the status string
- No schema change — just expand the `valid` set and update the dropdown
- Pros: no migration, simple to implement
- Cons: the status field is doing double duty; harder to query cleanly (`WHERE status = 'closed'` breaks)

**Option C — Single combined status, no "CLOSED" bucket**
- Replace CLOSED with four explicit terminal statuses: FILLED, REJECTED, DECLINED, ACCEPTED
- These become the final pipeline columns (or hidden — only shown if cards exist)
- Pros: maximally explicit, no ambiguity
- Cons: 4 more columns is visual noise; most of the time these are empty

### 2. What does the card look like when closed?

In the current CLOSED column, a card just shows its normal fields.
With a reason, it could show:

```
Lead Principal Engineer — NVIDIA
· Chicago  EMPLOYMENT  9.00/10
[CLOSED — FILLED]          ← copper tag or muted label?
```

Or the card could be visually dimmed (opacity: 0.6) since it's no longer actionable.

### 3. Should CLOSED cards be hidden by default?

The board's purpose is tracking active opportunities. Closed cards are historical record.
Options:
- Always visible in the CLOSED column (current)
- Hidden by default, "show closed (N)" toggle at column header
- Completely separate "Closed" view (tab or filter)

### 4. ACCEPTED outcome — end of search?

If status = accepted, the career search is over. Should this:
- Trigger a Telegram notification via CoS?
- Lock the pipeline (no further moves)?
- Just be a status like any other?

---

## Recommendation for Claude.ai to evaluate

Robert's framing ("closed is the column, reason is distinct") maps most cleanly to **Option A** — separate `close_reason` column. It's the right architecture for a pipeline tool. The migration is one ALTER TABLE. The UI change is a conditional reason picker that appears after selecting "Closed."

The four reasons Robert named:
| Reason key | Label | Notes |
|---|---|---|
| `filled` | Filled | Position taken off board before I applied |
| `rejected` | Rejected | Company passed on my application |
| `declined` | Declined | Offer made, I turned it down |
| `accepted` | Accepted | Offer accepted — end of search |

---

## Files to touch if this gets built

| File | Change |
|---|---|
| `domains/guild/db/schema_phase4.sql` | Add `close_reason TEXT` migration |
| `minimoi_portal/app.py` | Update route to accept + save `close_reason`; update `valid` set |
| `minimoi_portal/templates/guild/career_focus.html` | Conditional reason picker on close; show reason on closed cards |

---

## Definition of done (for spec, not build)

- [ ] Design decision: Option A / B / C — or something else?
- [ ] Close reason displayed on card in CLOSED column
- [ ] Reason picker UX decided (inline, modal, second dropdown?)
- [ ] ACCEPTED outcome behavior decided (notification? lock?)
- [ ] Robert signs off on the spec before Claude Code builds

---

*Handoff written: 2026-06-11*
*Status: design review — do not build*
