# Design: GitHub & Docs Cleanup — Pre-External Review
**File:** `docs/design/design_github_docs_cleanup_2026-07-04.md`
**Status:** Design — not build-ready
**Date:** 2026-07-04
**Author:** Claude.ai design session

---

## Intent

Before inviting the T-Mobile AI team (or any external AI engineers) to review mini-moi, the public GitHub repo and documentation need to present the project honestly and professionally. This is not about hiding anything — it is about making the signal-to-noise ratio high enough that a senior engineer can understand what was built, why, and how it is structured, without wading through 18 months of build artifacts.

This is a design document, not a build spec. Decisions made here feed into a build spec when ready.

---

## Current State (as of 2026-07-04)

**What's in the public repo today:**
- 54 spec files in `docs/specs/` — bulk are historical shipped work
- 18 files in `docs/design/` — mix of active design docs and old artifacts
- `_working/` — gitignored, not visible externally
- `README.md` — not reviewed for external audience
- GitHub Issues — mix of open, closed, stale, and mislabeled
- Multiple `copy`, `v2`, `(1)` filename variants from development
- Handoff docs committed as specs

**What a senior AI engineer should see:**
- Clean README explaining what mini-moi is, the architecture, and the multi-agent working model
- Active specs clearly separated from historical record
- Code that speaks for itself — not overwhelmed by process artifacts
- GitHub issues that reflect real open work, not accumulated debt

---

## Decisions to make before building

### 1. README rewrite
Current README is functional but not portfolio-quality. Needs:
- What is mini-moi (one paragraph, no jargon)
- Architecture overview (domains, multi-agent model, AWS setup)
- What's live and in daily use
- Tech stack (Python, Flask, AWS EC2, Docker, Postgres, Claude/xAI APIs)
- Link to live portal (app.minimoi.ai)
- Screenshot or two

**Decision needed:** how much of the multi-agent working model (Claude.ai / Claude Code / OpenClaw / Grok) to surface publicly? It's a differentiator but also unusual — worth thinking about framing.

### 2. Docs archive strategy
- `docs/archive/` already created today for old design docs
- `docs/specs/` historical specs: move to `docs/archive/specs/` or leave?
- Recommendation: leave historical specs in `docs/specs/` — they tell the build story. Archive only truly stale docs (pre-June, superseded, one-time handoffs).

### 3. GitHub Issues cleanup
- Close all issues that are actually done but still open
- Add labels consistently (bug, feature, tech-debt, security, hotfix)
- Archive or close stale issues older than 60 days with no activity
- Ensure open issues reflect real current work

### 4. Filename cleanup
- Delete: `spec_gesprache_ki_session_2026-06-15 copy.md` (macOS duplicate)
- Delete or rename any other `copy`, `(1)`, `v2` variants in committed files
- Confirm no sensitive filenames (names, emails, internal project references)

### 5. What to keep private
- `mini-moi-private` repo stays private — sensitive data, investigation artifacts, SEI engagement notes
- `personal-ai-agents` public repo: code + specs only, no personal data

---

## Open Questions

1. Should the live portal URL (app.minimoi.ai) be in the public README? It requires login — public link is low risk, but think about it.
2. Should guest access be offered to the T-Mobile AI team reviewers? Could be a powerful demo — they see the actual platform, not just the code.
3. How much of the career/job search context in the repo (resume bullets, SEI engagement) should be surfaced or hidden for a technical review?

---

## Next Step

When ready to build: convert this into a proper spec with Definition of Done and Commit sections. The build is mostly Claude Code doing file operations and GitHub API calls — low complexity, high impact.

---

*Design document · mini-moi · 2026-07-04 · Not build-ready — decisions pending*
