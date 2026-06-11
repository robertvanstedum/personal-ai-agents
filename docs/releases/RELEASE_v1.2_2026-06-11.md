# Release Notes — v1.2 Curator Intelligence Archive
*mini-moi · personal-ai-agents*
*Released: 2026-06-11*

## What this is

Curator v1.2 is a major structural update to the intelligence briefing domain.
The complete information architecture was redesigned and rebuilt as a Flask-rendered
portal, replacing the previous mix of static HTML and dynamic pages. Scoring and
source quality logic was also significantly improved.

## What's in v1.2

### Full information architecture redesign

All seven Curator pages are now Flask-rendered Jinja2 templates with a consistent
navigation structure:

`DAILY · READING ROOM · SCANS & DIVES · LEANINGS · ARCHIVE · DESK`

Previously, several pages were static HTML files with inconsistent navigation and
no server-side rendering. Every page now renders dynamically from the same Flask
application with a unified nav, consistent design tokens, and correct routing.

| Page | Route | What changed |
|---|---|---|
| Landing | `/` | Editorial splash with four watercolor domain cards |
| Daily | `/briefing` | Consistent nav, topic filter badges |
| Reading Room | `/curator_library.html` | Now in unified nav |
| Scans & Dives | `/scans-dives` | Flask-rendered, replaces static index |
| Leanings | `/research/leanings` | AI Observations folded in, sidebar removed |
| Archive | `/archive` | 105 daily editions, full search, THREAD/SCAN/DIVE sections |
| Desk | `/research/dashboard` | Renamed "The Desk", portal sidebar removed |

Old static URLs redirect to new Flask routes via 301.

### Scoring improvements

**Source trust tier system** — four tiers with score multipliers applied at
scoring time, not as post-filters. Tier 1 (flagship) receives a 1.3× multiplier;
Tier 4 (low-signal) receives 0.6×.

**Two-lane age penalty** — FAST sources (news, X posts) penalized steeply after
48 hours. SLOW sources (think tanks, academic, long-form) penalized gently — a
3-week-old Foreign Affairs piece is not stale in the same way a 3-week-old tweet is.

**X post hard cap** — maximum 4 X posts per daily briefing regardless of score.
Prevents a single trending topic from flooding the briefing.

### New trusted sources

Promoted to Tier 1 or Tier 2:
- Al Jazeera (geopolitics)
- Deutsche Welle (German-language international)
- Spiegel International (European perspective)
- Crisis Group (conflict analysis)
- Adam Tooze Chartbook (macroeconomics)
- NY Fed Liberty Street Economics (monetary policy)

### Synthesizer + Challenger — Phase 1

Cross-provider review pattern built and tested. Sonnet (primary synthesis) →
Grok (challenger) → Sonnet (final review). First live exchange confirmed:
Grok caught a factual overstating (58.4% ≠ ">60%"), Claude accepted 1 of 4
challenges — exactly the discipline the pattern calls for. Curator deep dive
integration ships in v1.3.

## What comes in v1.3

- **Synthesizer + Challenger Phase 2** — ChallengerService wired into Curator
  deep dives. Collapsed challenger review section in portal when `show_process=True`.
- **Scan → Deeper Dive** — one-click promotion from a scan to a thread + dive
  without manual thread creation
- **Archive navigation** — "load more" pattern for daily editions, bottom anchor
  on expanded sections
- **UI consistency pass** — Daily accent color alignment, badge redesign,
  Reading Room visual token update

---

*Curator v1.2 · mini-moi · 2026-06-11*
