# Spec #117: Guild Navigation & Build Queue Redesign
**File:** `spec_guild_navigation_redesign_2026-07-04.md`
**Status:** Backlog
**Date:** 2026-07-04
**Author:** Claude.ai design session

---

## Intent

The current Guild Build Log is a flat list of 83+ items that is unreadable at scale. The Queue and Build Log tabs don't do meaningfully different jobs. The Roadmap is a static markdown file that goes stale. There is no way to capture an idea without immediately treating it as a build commitment. And there is no API — meaning only manual file edits can change build queue state, which breaks the three-actor working model (Robert, OpenClaw, Claude Code).

This spec redesigns the Guild navigation and data model to give each tab a clear, distinct job; introduces a full item lifecycle from Idea to Done; adds a write API so all three actors can create and transition items; and makes the Roadmap a live, editable page instead of a static file.

---

## Status Lifecycle

```
Idea → Design → Backlog → Spec Ready → In Build → Done
```

Side states (can apply from any active status):
- `Blocked` — waiting on something external
- `Deferred` — consciously paused, revisit later
- `Cancelled` — not doing this

### Status definitions

| Status | Meaning | Spec file required? |
|---|---|---|
| `Idea` | Captured thinking, proof of concept, experiment. May mutate, merge, or expire. | No |
| `Design` | Being actively specced. Not ready to build. | Optional |
| `Backlog` | Specced and ready. Not in current scope. | Yes |
| `Spec Ready` | Promoted into current sprint. Next to build. | Yes |
| `In Build` | Claude Code is actively building this. | Yes |
| `Done` | Shipped. | Yes |
| `Blocked` | Waiting on dependency or decision. | — |
| `Deferred` | Paused consciously. | — |
| `Cancelled` | Not doing this. | — |

### Key transitions
- **Promote:** Backlog → Spec Ready (pulling into sprint)
- **Demote:** Spec Ready → Backlog (bumped by higher priority)
- **Expire:** Idea → Cancelled (quiet archive, no drama)
- **Merge:** two Ideas collapse into one (note on both items)

All transitions must be one-click from the Queue or Build Log card. No clicking into the spec to change status.

---

## Tab Redesign

### Queue tab — current sprint only
Shows: `Spec Ready` + `In Build` only.
- Card view, not a table
- Each card shows: title, status badge, age, summary line (one sentence — what this is), spec file link, GitHub issue link if present
- Summary line is a required field for Spec Ready and above; optional for Idea/Design
- Actions on each card: promote, demote, edit, view spec
- `+ New Item` button prominent in header
- Target: readable at a glance, never more than ~10 items

### Build Log tab — full backlog
Shows: everything — all statuses, filterable
- Default view: Backlog + Design + Idea (active backlog, not historical)
- Filter pills: All / Idea / Design / Backlog / Spec Ready / In Build / Done / Deferred / Cancelled
- Grouped by status by default, not flat list
- Each row: title, status, age, summary line, spec file link
- Actions: promote, demote, edit
- `+ New Item` button in header
- Historical (Done/Cancelled/Deferred) collapsed by default, expandable

### Roadmap tab — live editorial
- NOT a static markdown render
- Two sections: **Committed** (directions we are definitely going) and **Exploring** (active directions not yet committed)
- Each entry: title + 2-3 sentence description, editable from UI
- "Last updated" date shown prominently — honest signal of staleness
- Items are added deliberately by Robert; nothing auto-populates from Build Log
- Edit button visible; saves to `roadmap.json` on host (volume-mounted, same pattern as build_queue.json)
- Bottom: link to full Build Log for anyone wanting more detail

### Docs tab — unchanged

---

## Write API

All three actors (Robert via UI, OpenClaw, Claude Code) must be able to:
- Create a new item
- Update item status
- Update item fields (title, summary, spec file, GitHub issue)
- Transition status (promote/demote/cancel/defer)

### Endpoints

```
POST   /api/guild/items              — create new item
GET    /api/guild/items              — list all items (filterable by status)
GET    /api/guild/items/:id          — get single item
PATCH  /api/guild/items/:id          — update fields
PATCH  /api/guild/items/:id/status   — transition status
DELETE /api/guild/items/:id          — soft delete (sets Cancelled)

POST   /api/guild/roadmap            — update roadmap section
GET    /api/guild/roadmap            — get roadmap content
```

### Item schema

```json
{
  "id": "uuid",
  "title": "Human-readable title",
  "summary": "One sentence — what this is and why it matters",
  "status": "backlog",
  "spec_file": "spec_example_2026-07-04.md",
  "github_issue": 62,
  "age_days": 10,
  "created_at": "2026-07-04T00:00:00Z",
  "updated_at": "2026-07-04T00:00:00Z",
  "notes": "Optional — merge notes, demotion reason, etc."
}
```

### Data source
- `build_queue.json` on EC2 host (already volume-mounted)
- Schema migration: existing items need `summary` and `id` fields added; status values normalized to new lifecycle
- Roadmap stored in `roadmap.json` on EC2 host (new file, needs volume mount added)

---

## File-Not-Found Handling

Current behavior: clicking a spec shows "Spec file not found." with broken UI and greyed-out back button.

Fix:
- Graceful message: "Spec file not available — may not have been synced to this instance."
- Back button always functional
- Download and Print buttons hidden (not broken) when file absent
- Optional: show item metadata (title, status, summary) even when spec file missing

Historical specs (pre-volume-mount) will not be backfilled — graceful fallback is sufficient.

---

## `+ New Item` behavior

Button appears in both Queue and Build Log headers.

Modal fields:
- Title (required)
- Summary line (required for all statuses including Idea — one sentence minimum)
- Status (default: Idea)
- Spec file name (optional at creation)
- GitHub issue number (optional)
- Notes (optional)

On save: writes to `build_queue.json` via write API. No file system access required from UI.

---

## Definition of Done

- [ ] Queue tab shows only Spec Ready + In Build, card view with summary lines
- [ ] Build Log tab shows full backlog, grouped by status, filterable by all status values
- [ ] New status values live in system: Idea, Design, Backlog (alongside existing Spec Ready, In Build, Done, Blocked, Deferred, Cancelled)
- [ ] One-click promote/demote from Queue and Build Log cards
- [ ] Roadmap tab renders from `roadmap.json`, editable from UI
- [ ] All write API endpoints functional and documented
- [ ] OpenClaw and Claude Code can create/update items via API
- [ ] `+ New Item` modal works from Queue and Build Log
- [ ] File-not-found shows graceful message with functional back button
- [ ] `build_queue.json` schema migrated — existing items have id and summary fields
- [ ] `roadmap.json` volume-mounted on EC2 host

---

## Commit

Single PR on `dev` branch. Claude Code builds and tests on dev.minimoi.ai before any ECR push. Robert reviews and approves before prod deploy.

Suggested commit message:
```
feat(guild): navigation redesign — lifecycle statuses, write API, live roadmap (#TBD)
```

---

## Notes for Grok review

- Schema migration of existing 83 items needs care — status normalization from old values to new lifecycle
- Write API auth: should it require the same session auth as the portal, or is internal-only (loopback) acceptable for OpenClaw/Claude Code calls?
- Consider whether `roadmap.json` should be a separate volume mount or merged into a broader `guild_data.json`
- Summary line: required at creation for all statuses including Idea — one sentence is a low bar and forces enough thinking to make the capture useful. Empty ideas are noise.

---

*Spec · 2026-07-04 · Claude.ai design session · Status: Backlog*
