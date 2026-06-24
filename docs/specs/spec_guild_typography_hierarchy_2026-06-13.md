# Spec — Guild Typography: Section Header Tier + Oversized Stat
*mini-moi · Guild*
*Created: 2026-06-13 06:16 CDT — Claude.ai*
*For: Claude Code*

---

## The diagnosis

Guild currently has **one style doing two jobs**. The small-caps, light-gray,
letter-spaced label style is used both for:

1. **Major section dividers** — `SYSTEMS`, `CAREER FOCUS`, `BUILD`, `AHEAD`
   on `/guild`; `SPEC READY`, `IN BUILD`, `BLOCKED`, `RECENTLY DONE` on
   `/guild/build/queue`; `SPEC`, `STATUS`, `AGE`, `ISSUE` on
   `/guild/build`.
2. **Minor annotations** — `Status:`, counters, filter labels.

Job 1 is structurally an H2 — it tells you what major chunk of the page
you're in. Job 2 is a caption. With both collapsed into one tiny/light
style, the page has no visual anchors — nothing reads as "this is a
section," everything is the same quiet weight.

**This has come up before and the fix didn't land** — likely because
"too small and faint" reads as a tweak to one element, when the actual fix
is structural: split into two tiers, apply the new tier everywhere job-1
labels appear.

---

## Fix 1 — New section-header tier

Create a second style, distinctly larger/bolder/darker than the current
label style:

- **Size**: roughly double the current label size
- **Weight**: semibold to bold (current is thin/regular)
- **Color**: dark — matching body text color, not the current muted gray
- Small-caps / letter-spacing can stay if it still looks good at the new
  size/weight — that's a style choice, not the bug

**Apply this new style to every job-1 instance**, across all Guild pages:

- `/guild`: `SYSTEMS`, `CAREER FOCUS`, `BUILD`, `AHEAD`
- `/guild/build/queue`: `SPEC READY`, `IN BUILD`, `BLOCKED`, `RECENTLY DONE`
- `/guild/build`: `SPEC`, `STATUS`, `AGE`, `ISSUE` (table header row)
- Any other instance of a label functioning as a section/column divider
  rather than an inline annotation — if uncertain whether something is
  job 1 or job 2, ask: "if this were a printed page, would this be a
  heading or a caption?"

**Leave the existing style alone** for job-2 instances (`Status:`, filter
labels, counters like the `(3)` after `SPEC READY`) — those are correctly
sized as captions already.

---

## Fix 2 — The `48` is oversized

On `/guild`, Career Focus card: the `48` (days to Aug 1 deadline) currently
renders larger than the `1 / 10 / 12` pipeline stats (Interview / Suggested
/ Total) beneath it. The pipeline stats are the actual "state of my search"
numbers — a countdown shouldn't out-weigh them.

**Bring `48` down to match or sit below the size of `1/10/12`.** The "DAYS
TO AUG 1 DEADLINE" / context line stays as-is.

---

## Definition of Done

- [ ] New section-header style defined: ~2x current label size,
      semibold/bold, dark color
- [ ] Applied to all named job-1 instances above on `/guild`,
      `/guild/build/queue`, `/guild/build`
- [ ] Job-2 labels (`Status:`, filter text, counters) unchanged
- [ ] `48` reduced to ≤ the size of the `1/10/12` stats on the same card
- [ ] Visual sign-off: Robert confirms `/guild` now has clear section
      breaks at a glance, and `48` no longer dominates the Career Focus card

---

## Commit

```bash
git add domains/guild/templates/ domains/guild/static/
git commit -m "style: Guild section-header tier — distinct from caption
labels; reduce oversized days-to-deadline stat

Splits the single small-caps label style into two tiers: section dividers
(SYSTEMS/CAREER FOCUS/BUILD/AHEAD, queue columns, table headers) now larger/
bolder/darker, captions (Status:, counters) unchanged. Also resizes the 48
days-to-deadline number to not exceed the pipeline stat numbers beside it."
git push origin main
```

---

*Spec · Guild · 2026-06-13*
