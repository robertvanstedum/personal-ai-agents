# Spec #116: mini-moi Narrative Synchronization
**File:** `spec_narrative_sync_2026-07-04.md`
**Replaces:** `spec_landing_page_update_2026-07-03.md` (#116)
**Status:** Spec Ready
**Build queue:** #116
**Date:** 2026-07-04
**Author:** Claude.ai design session

---

## Intent

This spec was initially scoped as a landing page update. It is larger than that.

mini-moi exists across five surfaces: the minimoi.ai landing page, the GitHub README, the GitHub About blurb, LinkedIn, and Robert's resume. Each surface tells a version of the same story — what mini-moi is, what it does, and why it exists. Today those five surfaces are inconsistent: version numbers drift, Meu Português is absent from the landing, the resume mini-moi section uses different framing than the README, and LinkedIn reflects an earlier state of the project.

The real work is agreeing on one canonical narrative and expressing it correctly across all five surfaces. The landing page is the most visible output, but it is not the only one. All five surfaces must sync in the same update.

### Why this matters

mini-moi is Robert's primary professional portfolio piece during an active job search. Every surface a recruiter, hiring manager, or AI engineer touches should reflect the same story in the same voice. Inconsistency between GitHub and the resume, or between the landing page and LinkedIn, creates friction and undermines the credibility of the project.

The canonical narrative is: **breadth of background + personal AI platform in daily production use = differentiator in the AI era.** Each surface expresses that narrative in its own format — but the story is identical.

---

## The Five Surfaces

| Surface | Format | Audience | Owner |
|---|---|---|---|
| minimoi.ai landing | Visual, blurb-driven | Guests, recruiters, anyone Robert invites | Claude Code updates |
| GitHub README | Technical, prose | Developers, AI engineers, technical reviewers | Claude Code commits |
| GitHub About | One sentence, 160 chars | Anyone viewing the repo | Robert updates manually |
| LinkedIn | Professional prose | Recruiters, hiring managers, professional network | Robert updates manually |
| Resume mini-moi section | Bullet-driven, concise | Hiring managers, recruiters | Robert updates .docx |

---

## Design Session Required (before build)

The copy for all five surfaces must be written and locked in a Claude.ai design session before Claude Code builds anything. That session covers:

1. Pull current text from all five surfaces side by side
2. Agree on the canonical narrative — one version of the story
3. Write copy in five formats: landing blurbs, README, GitHub About, LinkedIn summary, resume bullets
4. Lock all five, confirm with Robert
5. Hand to Claude Code as one coordinated update

**The design session has not happened yet. This spec documents the scope and intent. Claude Code does not build until copy is locked.**

---

## Scope — what gets updated

### minimoi.ai landing page
- Hero line: review, likely keep (*"Your context. Your goals. Your signal."*)
- What this is: short paragraph below hero — sync with README narrative
- Domain cards: four blurbs (Curator, Mein Deutsch, Meu Português, Guild) — Meu Português currently absent, must be added
- Visitor framing section: one short paragraph setting expectations for guests (*"This is a personal system, not a product. If you're here, Robert invited you."*)
- Version numbers: remove from visitor-facing copy — internal tracking only

### GitHub README
- Opening: canonical narrative statement — what mini-moi is and why
- What's live: current domain list with honest one-liner per domain
- Architecture: multi-agent working model (Robert / Claude.ai / Claude Code / OpenClaw / Grok) — this is a differentiator, surface it
- Tech stack: Python, Flask, AWS EC2, Docker, Postgres, Claude/xAI APIs
- Link to live portal: app.minimoi.ai
- Sync version numbers with landing page

### GitHub About
- One sentence, 160 characters max
- Must capture: personal AI platform, daily production use, multi-agent
- Example draft: *"Personal AI platform in daily production use. Curator, German, Portuguese, Guild — built and maintained with Claude, OpenClaw, and Grok."*

### LinkedIn
- mini-moi section in Experience or Projects
- Same narrative as resume but in LinkedIn's format
- Link to GitHub repo and/or live portal

### Resume
- mini-moi Personal Project section — already partially updated (June 2026 version)
- Opening line: sync with landing/README canonical narrative
- Three bullets: Curator, Mein Deutsch + Meu Português, Guild + Infrastructure
- Pending edits from June session still outstanding (see memory)

---

## What does NOT change

- The opening line: *"LLMs have the world's knowledge. They don't have your intent, your goals, your history, your risk tolerance."* — this is the best single statement of what mini-moi is. Keep it.
- Core architecture and tech stack facts
- The multi-agent working model framing — this is a differentiator

---

## Definition of Done

- [ ] Design session complete — copy locked for all five surfaces
- [ ] minimoi.ai landing updated: visitor framing, four domain blurbs, Meu Português added
- [ ] GitHub README rewritten to canonical narrative
- [ ] GitHub About updated (Robert does manually)
- [ ] LinkedIn updated (Robert does manually)
- [ ] Resume mini-moi section updated (Robert does manually in .docx)
- [ ] All version number references reconciled or removed from visitor-facing copy
- [ ] Robert reviews all five surfaces together before any goes live

---

## Commit (Claude Code scope only)

```
feat: narrative sync — landing page + GitHub README (#116)

- Landing: visitor framing section, four domain blurbs (Meu Português added),
  version numbers removed from visitor copy
- README: canonical narrative, current domain list, multi-agent model surfaced,
  tech stack updated
- Closes #116
```

LinkedIn, GitHub About, and resume are Robert's manual updates — not in this commit.

---

## Notes

- Design session should pull: current landing HTML, current README.md, current resume .docx mini-moi section, current LinkedIn text
- Grok review pass on the copy before it ships — narrative consistency check across all surfaces
- This spec replaces `spec_landing_page_update_2026-07-03.md` — that spec is superseded

---

*Spec · 2026-07-04 · Claude.ai design session · Status: Spec Ready — design session pending*
*Replaces: spec_landing_page_update_2026-07-03.md*
