# Build Note: Comprehensive Drill Regression Test Suite

## Background

During the drill list / number-selection fix (May 2026), we hit three successive
regressions because there was no automated test coverage for the drill routing
logic. Each fix introduced a new failure in an untested path:

1. `_DRILL_MORE_RE` guard removed → `more`/`next` became unconditional intercept
2. List-select routing added but only in non-active-drill path → `drill 5` still
   failed when a drill was already running
3. List-select routing matched `hint`/`skip` (single lowercase words) → silently
   dropped during active drill because `_drill_list_state` wasn't cleared on
   drill start

## What's needed

A pytest suite (or equivalent) that exercises the full drill message routing
without requiring a live Telegram connection. Should cover:

### Drill list flows
- `drill list` → verb pool shown, `_drill_list_state` populated
- `more` / `next` → next page shown
- Bare number (`5`) → selects verb, starts drill, clears list state
- `drill 5` → same as above (with prefix)
- Bare verb name (`nehmen`) → selects verb, starts drill
- Invalid number → error message, no crash
- Non-pool word → falls through (not intercepted)

### Active drill + list interaction
- `drill list` while drill running → shows list, drill paused
- `drill 5` while drill running + list state populated → switches to new verb
- `hint` while drill running + list state populated → reaches drill answer handler (not list select)
- `skip` while drill running + list state populated → same
- Any single-word answer while list state populated → reaches drill answer handler

### Drill start clears list state
- `drill list` then `drill nehmen phrases` → `_drill_list_state` is empty after start
- `drill list` then `drill nehmen` → same

### Routing guard: `more`/`next`
- `more` with no list state → handled gracefully (restart list or no-op)
- `more` mid-sentence (e.g. "tell me more about X") → should NOT trigger list-more
  (regression risk: `_DRILL_MORE_RE` is `\b(?:more|next)\b`)

### Hint / skip in L1 and L2 drills
- `hint` during L1 drill → reveals expected form
- `skip` during L1 drill → advances
- `hint` during L2 drill → reveals expected sentence
- `skip` during L2 drill → advances

## Suggested approach

Mock `update.message` and route through the same handler functions used in
production. No Telegram API calls needed — just verify which handler function
is invoked and what state changes result.

The routing logic in `handle_message()` is the main target; individual handler
functions (`_handle_drill_l1_answer`, etc.) can be tested independently.

## Priority

High — every drill-related fix to date has caused a routing regression. The
routing logic now has 4 independent code paths (text active-drill, text
non-active, voice active-drill, voice non-active) that must stay in sync.
