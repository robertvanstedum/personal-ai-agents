# DESIGN_RESEARCH_LANDING_v1.0.md
*Date: March 28, 2026*
*Author: claude.ai design session*
*Status: Ready for Claude Code*

---

## Problem

The current Research landing page presents heavy per-topic cards with stats, article previews, and action buttons. It reads as an operational dashboard, not an orientation page. First-time and returning users don't immediately understand what they're looking at or where to go. Cards are 4–5x taller than necessary, requiring excessive scrolling to see all topics.

---

## Intent

Replace the card-based layout with a compact, scannable thread list. The page should answer two questions immediately: *What is this?* and *What am I working on?* — then get out of the way.

---

## Layout

### Zone 1: Header (minimal)
- **Title:** "Research Intelligence" — keep as-is
- **Subtext:** "Your active research threads" — replaces current subtitle
- **Budget bar:** Retained but visually demoted — single slim line, small text, no card or border wrapping. Sits quietly below the subtext, not as a prominent element.

### Zone 2: Thread List
One row per active topic. Rows are compact, single-line height with comfortable padding.

**Row anatomy (left to right):**
- **Topic name** — left-aligned, styled as a clickable link, normal weight (not bold)
- **Inline stats** — `13 sessions · 3 saved · 8 candidates` — following the name, small muted text
- **Last run date** — right-aligned, muted

**Interaction:**
- Clicking anywhere on the row navigates into that topic
- Subtle hover state (background tint, consistent with parchment palette)
- No buttons on this page

---

## What Is Removed

| Element | Disposition |
|---|---|
| Article preview line (⭐ item) | Moved inside topic view |
| Run Session button | Moved inside topic view |
| Observe button | Moved inside topic view |
| Candidates button | Moved inside topic view |
| Heavy card borders and padding | Removed entirely |

---

## Visual Style

- Maintain existing parchment palette and typography throughout
- Rows separated by a hairline rule or subtle spacing — no card borders
- Stats text: muted, approximately 80% opacity, smaller than topic name
- Last run date: muted, right-aligned, same small size as stats
- Budget bar: slim, no surrounding card — integrate as a quiet status line

---

## What Does NOT Change

- Left rail navigation (Curator / Research / Language / Jobs)
- Top nav (Sessions · Observations · ···)
- Parchment color palette
- Typography choices

---

## Acceptance Criteria

- [ ] Landing page shows orienting header + subtext
- [ ] Budget bar present but visually demoted (no card)
- [ ] All active topics visible as compact single-line rows
- [ ] Row click navigates into topic view
- [ ] No action buttons (Run Session, Observe, Candidates) on landing page
- [ ] Article preview lines removed from landing page
- [ ] Hover state on rows
- [ ] Consistent with existing parchment palette

---

## Out of Scope for This Build

- Navigation v1.2 left rail (separate spec)
- Reading Room (separate build)
- Any changes to topic detail view
