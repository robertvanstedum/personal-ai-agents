# SPEC_DEEPER_DIVE_MECHANICS_v1.0_2026-03-29.md
*Date: March 29, 2026*
*Author: claude.ai design session*
*Status: Ready for OpenClaw review → Claude Code build*
*Companion docs:*
- *SPEC_DEEPER_DIVE_ANALYSIS_v1.0_2026-03-28.md (intellectual design — validated)*
- *SPEC_DEEPER_DIVE_POC_v1.0_2026-03-28.md (POC script — validated)*
- *CASE_STUDY_DEEPER_DIVE_POC_2026-03-28.md (POC results)*

---

## What This Is

The mechanics spec for integrating the validated Deeper Dive framework into the full mini-moi flow. The analysis layer is proven. This spec covers the thread lifecycle, the close trigger, the nudge UI, and where output is stored.

---

## Thread Lifecycle

```
Active → sessions running, sources accumulating
  ↓
Expired (duration_days reached) OR user stops thread
  ↓
"Ready to close" state — nudge appears on Research landing page
  ↓
Two paths:
  → User clicks "Start Deeper Dive" → essay generated → lands in Curator
  → User ignores → thread archives quietly, no artifact produced
```

Ignoring the nudge is a valid outcome. If the inquiry ran its course and the user isn't compelled to generate a Deeper Dive, that's information. Nothing lingers, nothing is forced.

---

## Thread State Machine

Add a `status` field to `data/threads/{topic}/thread.json`:

```json
{
  "topic": "strait-of-hormuz",
  "motivation": "...",
  "status": "active",        // active | expired | closed | archived
  "created": "2026-03-28",
  "duration_days": 5,
  "expires": "2026-04-02",
  "deeper_dive_generated": false,
  "deeper_dive_path": null
}
```

**Status transitions:**
- `active` → `expired`: when `expires` date is reached (checked on page load / daily cron)
- `active` → `expired`: when user manually stops thread (new "Stop Thread" action)
- `expired` → `closed`: when user generates Deeper Dive
- `expired` → `archived`: when user dismisses nudge without generating
- `closed` / `archived`: terminal states, thread no longer appears in active list

---

## The Nudge — Research Landing Page

Follows the pattern of the "Start Research Thread" button on Deep Dive pages.

**When a thread reaches `expired` status**, its row on the Research landing page changes state:

- Row text color shifts to muted (consistent with parchment palette)
- "never run" / date text replaced with "Ready to close"
- A **"✦ Start Deeper Dive"** button appears on the row (same style as existing action buttons)
- Clicking navigates to a simple confirmation page (not a form — one click should be enough)

**The confirmation page:**
```
Thread: strait-of-hormuz
Sessions: 3 · Sources: 12 · Duration: 5 days

Generate your Deeper Dive?
This will produce a synthesis + challenge essay
stored in your Curator archive.

[Generate Deeper Dive →]    [Archive without generating]
```

No fields to fill. The script pulls everything it needs from the thread data. User just confirms.

---

## Generation Flow

On "Generate Deeper Dive →":

1. Call `scripts/generate_deeper_dive.py --topic {topic}` (existing POC script)
2. Show a loading state — "Generating your Deeper Dive… (~1 min)"
3. On completion, redirect to the Deeper Dive in Curator
4. Update `thread.json`: `status: closed`, `deeper_dive_generated: true`, `deeper_dive_path: path/to/output`

On "Archive without generating":
1. Update `thread.json`: `status: archived`
2. Redirect back to Research landing page
3. Thread disappears from active list

---

## Output Storage — Curator Archive

Deeper Dives are stored alongside Deep Dives in the Curator archive. Same format family, distinguished by a badge.

**File location:**
```
interests/2026/deep-dives/{topic}-deeper-dive-{NNN}.md
```
Consistent with existing Deep Dive naming convention.

**Archive index update:**
The existing `interests/2026/deep-dives/index.html` lists both Deep Dives and Deeper Dives. Deeper Dives get a **"Deeper Dive"** badge (small, muted, consistent with parchment palette) to distinguish them from single-article Deep Dives.

**Index row for a Deeper Dive:**
```
Mar 29, 2026 | [Deeper Dive] | strait-of-hormuz | 3 sessions · $0.21 | Read →
```

---

## Stop Thread Action

Users should be able to manually stop an active thread before `duration_days` expires. This sets status to `expired` immediately, triggering the nudge.

**Where:** Inside the thread detail view (Sessions page for that topic)
**UI:** A small "Stop Thread" link or button, muted, not prominent — this is a deliberate action, not something to click accidentally

**On click:** Confirmation prompt: "Stop this thread and generate a Deeper Dive?" with yes/no. If yes → status: expired → nudge appears on landing page. If no → thread remains active.

---

## Daily Cron Check

Add a check to the existing daily cron / launchd job:

```python
# For each thread in research_config.json:
# If expires <= today AND status == "active":
#   Set status = "expired"
#   Log to session-log.md
```

This ensures threads transition to expired state even if the user doesn't open the app on the expiry date.

---

## Files Modified / Created

| File | Change |
|---|---|
| `data/threads/{topic}/thread.json` | Add `status`, `deeper_dive_generated`, `deeper_dive_path` fields |
| `research_routes.py` | Add `POST /api/research/close-thread` and `GET /api/research/thread-status` routes |
| `web/dashboard.html` | Add expired row state + "✦ Start Deeper Dive" button |
| `web/deeper_dive_confirm.html` | New — simple confirmation page |
| `scripts/generate_deeper_dive.py` | Minor: accept output path override to write to Curator archive location |
| `interests/2026/deep-dives/index.html` | Add Deeper Dive badge to index rendering |
| `agent/daily_cron.py` (or equivalent) | Add thread expiry check |

No changes to `research.py` session logic.

---

## Acceptance Criteria

- [ ] Thread row shows "Ready to close" + "✦ Start Deeper Dive" button + "Dismiss" (direct archive) when expired\n- [ ] Error handling on generation fail (e.g., redirect to error page with log)
- [ ] Clicking button navigates to confirmation page
- [ ] Confirmation page shows thread metadata (sessions, sources, duration)
- [ ] "Generate" triggers script, shows loading state, redirects to output in Curator
- [ ] "Archive without generating" sets status archived, thread disappears from active list
- [ ] Deeper Dive appears in Curator archive index with badge
- [ ] Daily cron transitions expired threads automatically
- [ ] Stop Thread action available in thread detail view
- [ ] thread.json updated correctly on all state transitions

---

## Out of Scope

- Reading Room (deferred — see vision doc)
- Automatic generation (always user-initiated)
- Email / Telegram notification on expiry
- Editing the Deeper Dive after generation
- Multi-domain deployment (architecture supports it, not built yet)
