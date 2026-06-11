# Spec — Career Focus: Two-Page Design
*mini-moi · Guild · Career Focus*
*Authored: 2026-06-11 — Claude.ai*
*Supersedes: spec_career_board_2026-06-11.md*

---

## The two-page model

**Page 1 — Positions** (`/guild/career`)
Full inventory. Every position, every status. Mass activity management, filters,
sorting, backend reporting. Default view when you open Career Focus.

**Page 2 — Active Pipeline** (`/guild/career/active`)
Kanban. Only the positions that matter right now: ones Robert wants and ones
that have shown interest. Stays clean. Cards fall off automatically after closing.

These serve different purposes and different moments. Most daily work happens
in Positions. Active Pipeline is opened when there are real conversations in progress.

---

## DB changes

```sql
-- Add to jobs.career_opportunities
ALTER TABLE jobs.career_opportunities ADD COLUMN IF NOT EXISTS close_reason TEXT;
ALTER TABLE jobs.career_opportunities ADD COLUMN IF NOT EXISTS priority BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs.career_opportunities ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
```

`closed_at` is set when status changes to 'closed'. Separate from `date_updated`
so the board grace period logic is always clean regardless of other field updates.

Update `test_schema.py` to check all three new columns.

---

## Page 1 — Positions (table)

**Route:** `GET /guild/career`
**Purpose:** Full inventory, mass activity, backend reporting source

### Layout

```
Career Focus             [Active Pipeline →]
11 positions tracked  DEADLINE: AUG 1, 2026

Status: [All ▼]  Type: [All ▼]  Geo: [All ▼]  Priority: [All ▼]

┌──────────────────────────────────────────────────────────────────────┐
│  ★  Principal Engineer — Comcast · Philadelphia  9.0  EMPLOYMENT  Applied  Jun 9  │
│  ★  VP Engineering AI — DT · Germany            9.2  EMPLOYMENT  Applied  Jun 10 │
│  ☆  Director AI — Ericsson · Remote             8.0  CONTRACT    Applied  Jun 10 │
│  ☆  Principal TPM — AT&T · Chicago              7.0  EMPLOYMENT  Applied  Jun 11 │
│  ☆  Principal Engineer — NVIDIA · Chicago       9.0  EMPLOYMENT  Suggested Jun 9 │
│  ...                                                                              │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Table columns

| Column | Notes |
|---|---|
| ★ / ☆ | Priority toggle — one click, posts to `/priority` route |
| Title — Company | Title is a link to the original posting (new tab) |
| Location | Geo field |
| Score | Color-coded: copper ≥8.0, amber 6-7.9, muted <6 |
| Type | EMPLOYMENT / CONTRACT / ADVISORY badge |
| Status | Dropdown — change triggers POST, page refreshes |
| Date | `created_at` formatted as "Jun 9" |

### Filters (query params)

- `status=` — all / suggested / reviewing / applied / interview / closed / rejected
- `type=` — all / employment / contract / advisory
- `geo=` — all / chicago / remote / international
- `priority=` — all / starred

Filters stay active on page refresh (read from request.args).

### Sort order

```sql
ORDER BY priority DESC, fit_score DESC, created_at DESC
```

Starred items always float to top regardless of filter.

### Status change with close reason

When status dropdown changes to "closed", a reason picker appears immediately
below the row (JavaScript inline, same as spec'd in previous version). Both
status and close_reason POST together. On save: set `closed_at = NOW()`.

```python
if new_status == 'closed':
    db_execute(
        "UPDATE jobs.career_opportunities "
        "SET status=%s, close_reason=%s, closed_at=NOW(), date_updated=NOW() "
        "WHERE id=%s",
        (new_status, close_reason, opp_id)
    )
```

### "Active Pipeline →" link

Top right of the page, copper link. Appears when ≥1 card qualifies for the board.

---

## Page 2 — Active Pipeline (board)

**Route:** `GET /guild/career/active`
**Purpose:** Kanban for positions with real movement. Always clean.

### Which cards appear on the board

A position appears on the board if ANY of these conditions are true:

```sql
(priority = TRUE AND status = 'applied')      -- Robert's real bets
OR status = 'reviewing'                        -- Company engaged
OR status = 'interview'                        -- Active conversation
OR (status = 'closed'
    AND closed_at > NOW() - INTERVAL '5 days') -- Recently closed grace period
```

Everything else is table-only. Suggested queue stays in the table. Bulk applied
(non-starred) stays in the table. Rejected goes straight to table, no grace period.

### Board columns

```
STARRED APPLIED  │  REVIEWING  │  INTERVIEW  │  RECENTLY CLOSED
  (I want this)  │ (they engaged) │ (active)  │   (grace period)
```

**RECENTLY CLOSED** — muted, cards at 0.65 opacity. Shows close reason tag.
After `closed_at + 5 days`, the card drops off the board automatically.
Always stays in the Positions table.

The 5-day grace period is configurable — stored in `cos_context.json`:
```json
"career_focus": {
  "board_closed_grace_days": 5
}
```

### Card design on the board

Cards are the same collapsible design from the previous spec:

**Collapsed (default):**
```
★  Principal Engineer — Comcast     9.0  EMPLOYMENT
                         [Applied ▼]  [···]
```

**Expanded (click ··· or body):**
```
★  Principal Engineer — Comcast     9.0  EMPLOYMENT
                         [Applied ▼]  [▲]

  Philadelphia, PA  ·  ⭐ Warm lead — Jane Smith
  "Strong telecom background directly applicable..."
  Applied: Jun 9, 2026
```

Title is a link to original posting (new tab). Star toggle moves card to/from
board (unstar = drops off board, reappears only in Positions table).

### What happens when a card closes on the board

1. Status changes to "closed", reason required
2. Card moves to RECENTLY CLOSED column at 0.65 opacity
3. Shows close reason: `Closed · Filled` / `Closed · Rejected` etc.
4. After `board_closed_grace_days` (default 5): card disappears from board
5. Card and all data remain in the Positions table permanently

### ACCEPTED outcome

When `close_reason = 'accepted'`:
1. Card drops off the board immediately (no grace period — search is over)
2. CoS `/event` receives `search_complete` event
3. CoS sends Telegram: *"Offer accepted — career search complete. Deactivate Loop A?"*
4. Robert confirms → sets `career_focus.active = false` in `cos_context.json`

---

## Routes summary

```python
GET  /guild/career                    # Positions table (default)
GET  /guild/career/active             # Active pipeline board
POST /guild/career/positions/<id>/status    # Update status (+ close_reason)
POST /guild/career/positions/<id>/priority  # Toggle priority star
```

---

## Navigation

### In the nav subnav (Guild pages)

```
Guild  ›  Career Focus  |  Positions  ·  Active Pipeline
```

"Positions" and "Active Pipeline" are the two sub-tabs under Career Focus.

### Contextual link

On the Positions table: `Active Pipeline → (N active)` in the top right.
Count shows how many cards currently qualify for the board.
Disappears if no cards qualify (no active pipeline yet).

---

## Backend reporting (from Positions table)

The table view with full filter set is the reporting surface. Queries that become
useful over time (CoS Loop F or a future analytics view):

- Applications per week (`created_at` grouped by week)
- Response rate: positions that moved from applied → reviewing / interview
- Score distribution of applications vs. responses
- Geo breakdown: Chicago vs. remote vs. international response rates
- Time-to-response: `reviewing.date_updated - applied.date_updated`

No special reporting schema needed — all derivable from `jobs.career_opportunities`
with the columns already defined.

---

## Definition of done

**Positions page:**
- [ ] Table renders all positions from `jobs.career_opportunities`
- [ ] Filters work: status, type, geo, priority (query params persist on refresh)
- [ ] Sort: priority first, then score, then date
- [ ] Priority star toggle works — row re-sorts on next load
- [ ] Status dropdown: "Closed" reveals reason picker, both save together
- [ ] `closed_at` set when status → closed
- [ ] Title links to original posting (new tab)
- [ ] "Active Pipeline → (N)" link visible when board has qualifying cards

**Active Pipeline board:**
- [ ] Board filter: only priority-applied + reviewing + interview + recently-closed
- [ ] Four columns: Starred Applied / Reviewing / Interview / Recently Closed
- [ ] Recently Closed at 0.65 opacity with close reason tag
- [ ] Cards drop off board after `board_closed_grace_days` (default 5)
- [ ] Rejected cards never appear on board
- [ ] Accepted: drops off board immediately, CoS Telegram notification
- [ ] Collapsible cards: ··· expands, ▲ collapses
- [ ] Empty board state: "No active positions. Star an application or wait for a response."

**Both pages:**
- [ ] Schema migration applied (close_reason, priority, closed_at)
- [ ] test_schema.py passes with new columns
- [ ] Owner-only auth on all `/guild/career/*` routes
- [ ] Robert signs off visually on both pages

---

## Commit

```bash
git add minimoi_portal/app.py \
        minimoi_portal/templates/guild/ \
        domains/guild/db/schema_career_v2.sql
git commit -m "feat: Career Focus two-page design — Positions table + Active Pipeline board"
git push origin main
```

---

*Career Focus · two-page spec · Guild · 2026-06-11*
*Supersedes spec_career_board_2026-06-11.md*
