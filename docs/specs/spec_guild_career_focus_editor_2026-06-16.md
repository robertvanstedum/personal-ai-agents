# Spec — Guild Milestone & Goals Editor
*Created: 2026-06-16 — Claude.ai*
*Status: Ready for `_working/` — low priority, build after Gespräche queue*
*Reasoning: Claude Code design questions answered in session 2026-06-16*

---

## Context

The Guild dashboard already displays key career data correctly: days to
deadline, interview count, suggested roles, upcoming loop schedule. The
`cos_context.json` structure and `_career_deadline` helper are already
wired. What's missing is a simple UI to edit the underlying milestones
and goals without touching JSON files directly.

This spec is deliberately minimal. The goal is the simplest thing that
removes the need to hand-edit JSON for the fields Robert actually changes.

---

## What this builds

A simple edit panel accessible from the Guild Career Focus section.
Date + label milestone pairs, editable in the UI. Nothing more complex
than that for v1.

---

## Scope decisions (locked)

**In scope:**
- Key dates: date + label pairs (e.g. "Aug 3 — Contract ends",
  "Sep 1 — Career placement deadline", "Jun 15 — Loops B/C/D first run")
- Goal text: the short label shown under the deadline counter
  (e.g. "Fall preparation completed by Sep 1")
- Career deadline date: the primary countdown date
- Status per milestone: pending / completed (simple toggle)

**Out of scope — config, not UI:**
- Search sources and scout terms
- Target company lists
- Role keywords and filters
- Loop schedules (those are operational config)

**Domain scope:** Career only for v1. Structure must allow German practice
goals and Curator scout terms to be added in v2 without a rebuild.
Use a `domain` field in the data structure from day one.

---

## Data structure

Extend `cos_context.json` (or equivalent) with a `milestones` array:

```json
{
  "career": {
    "deadline": "2026-09-01",
    "deadline_label": "Career placement deadline",
    "goal_text": "Fall preparation completed by Sep 1.",
    "milestones": [
      {
        "id": "m1",
        "date": "2026-08-03",
        "label": "Contract ends",
        "domain": "career",
        "status": "pending"
      },
      {
        "id": "m2",
        "date": "2026-09-01",
        "label": "Career placement deadline",
        "domain": "career",
        "status": "pending"
      }
    ]
  }
}
```

`id` is a short stable string, not a UUID — easy to read in the file
if someone does open it directly.

---

## UI — edit panel

**Entry point:** "VIEW →" link in the Career Focus card on the Guild
dashboard already exists. Wire it to open the edit panel (currently
it goes nowhere or to a placeholder).

**Panel layout — single page, no modal:**

```
CAREER FOCUS                                    [← Back to Guild]

Goal
[Fall preparation completed by Sep 1.        ] ← editable text field

Primary deadline
[2026-09-01] ← date input   [Career placement deadline] ← text field

Milestones
─────────────────────────────────────────────
[2026-08-03]  [Contract ends              ]  [pending ▾]  [✕]
[2026-09-01]  [Career placement deadline  ]  [pending ▾]  [✕]
─────────────────────────────────────────────
[+ Add milestone]

[Save]
```

**Fields per milestone row:**
- Date picker or `<input type="date">`
- Label text field (free text, short — one line)
- Status dropdown: pending / completed
- Delete button [✕]

**"+ Add milestone"** appends a new empty row.

**[Save]** POSTs to a new route (see below), shows inline "Saved ✓"
confirmation for 2s. No page reload.

**Design system:** parchment background, amber accent, Georgia headings —
matches existing Guild panel style. Input borders amber on focus.
No new CSS classes if existing ones cover it.

---

## Routes

**GET `/guild/career-focus`** (or wire existing "VIEW →" link)
Renders the edit panel with current values from `cos_context.json`.

**POST `/guild/career-focus/save`**
Accepts JSON body:
```json
{
  "goal_text": "...",
  "deadline": "2026-09-01",
  "deadline_label": "...",
  "milestones": [...]
}
```
Writes to `cos_context.json` (or equivalent).
Returns `{"status": "ok"}` on success.
Returns `{"status": "error", "message": "..."}` on failure —
show inline error, do not overwrite existing data on failure.

**Auth:** owner-only. Same auth check as existing Guild routes.

---

## Validation

- Date fields: must be valid dates, not in the past by more than 1 year
  (catches obvious typos)
- Label fields: required, max 60 characters
- Goal text: required, max 120 characters
- Milestone count: no hard limit but warn (inline, not blocking) if
  more than 10 — "Consider archiving completed milestones"
- On save failure: show error inline, do not navigate away, preserve
  unsaved edits

---

## What does not change

- The Guild dashboard display logic — it reads from the same
  `cos_context.json` fields, which now have a stable structure
- The `_career_deadline` helper — it reads `career.deadline`, unchanged
- Loop schedules, search config, role keywords — not touched
- Any other Guild panel or route

---

## v2 hooks (do not build now, just keep the door open)

- `domain` field on milestones makes German practice goals and Curator
  scout terms addable without restructuring
- A second tab on the edit panel ("GERMAN GOALS" / "CURATOR") can be
  added when those domains have goals worth editing in UI
- Milestone archiving (move completed milestones to an archive array)

---

## Definition of Done

- "VIEW →" link on Career Focus card opens the edit panel
- Goal text, primary deadline, and milestone rows are editable
- Adding, editing, and deleting milestone rows works without page reload
- Save writes to `cos_context.json` and shows "Saved ✓" inline
- Guild dashboard reflects updated values after save (on next load or
  soft refresh)
- Auth check: non-owner cannot access `/guild/career-focus` edit panel
- Validation: date and label fields validated before save
- Error on save: inline message, no data loss
- Design system: matches existing Guild panel style
- Verified: add a milestone, save, reload Guild dashboard, confirm it
  appears correctly

## Commit

`Add Guild career focus editor — milestone and goal editing UI wired
to cos_context.json.`

---

*Spec · 2026-06-16 · Claude.ai*
*Priority: low — build after Gespräche consolidated pass and transcript
paste panel*
