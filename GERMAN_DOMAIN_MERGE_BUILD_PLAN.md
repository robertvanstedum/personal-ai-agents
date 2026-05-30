# Build Plan: Merge feat/german-html-interface → main
*mini-moi · German domain v0.9 Beta*
*Status: PENDING — do not execute until Robert gives go-ahead*
*Reviewed: claude.ai · 2026-05-25*

---

## Overview

`feat/german-html-interface` is 105 commits ahead of main, spanning
the full German domain build from HTML scaffold through May 25 fixes.
Main is stale and doesn't reflect production. Every hotfix risk grows
while this gap exists.

**Branch stats:** 78 files changed · 13,015 insertions · 2,860 deletions

**Merge strategy:** Squash merge — 105 commits include many iterative
UI trials that add noise. Main gets one clean milestone commit.
Branch history is preserved on `feat/german-html-interface` permanently.

---

## Pre-Conditions Before Executing

These must be true before the merge runs:

- [ ] L2 drill fixes (fuzzy match, verb presence check, cache reset) tested
 and stable in production
- [ ] NL routing fixes (voice handler, session triggers, noise tolerance)
 tested and stable
- [ ] Gespräche buttons-move confirmed working
- [ ] Lesen auto-refresh (Issue #38) running cleanly for at least 1 day
- [ ] Robert gives explicit go-ahead

---

## Step 1 — Pre-Merge Cleanup (on branch, before merge)

### 1a. Reset live Curator files to main

These files diverged on the branch by accident (Curator ran while
on this branch). They are live-generated daily and must not land on main:

```bash
git checkout feat/german-html-interface
git checkout main -- curator_latest.json curator_briefing.html curator_latest.html
git commit -m "chore: reset live curator files to main — not part of German domain"
```

### 1b. Remove accidental backup zip

```bash
git rm _NewDomains/german_backup_20260521_221856.zip
git commit -m "chore: remove accidental backup zip from branch"
```

### 1c. Decide — _working/ files

These are interim handoff docs (drill fix specs, NL routing audit,
Issue #37 post-mortem, etc.). **Recommendation: keep them.** They are
the audit trail for key decisions and don't affect functionality.
OpenClaw to confirm before merge executes.

### 1d. Decide — gitignore for live state files

The following JSON files are user/runtime state that changes frequently:
`lesen_articles.json`, `phrasebook.json`, `persona_memory.json`,
`drill_pool.json`, `writing_sessions.json`, `notes.json`

**Recommendation: keep in git for now.** Personal project, single user.
Main will show article refreshes and phrase saves as diffs — acceptable.
Revisit when/if this moves to a server.

OpenClaw to confirm before merge executes.

---

## Step 2 — Squash Merge

```bash
git checkout main
git merge --squash feat/german-html-interface
git commit -m "feat: German domain v0.9 Beta — Mein Deutsch HTML interface

Full German learning domain: Lesen, Gespräche, Schreiben, Wörter, Archiv, Admin.

Key deliverables:
- german_domain.py: all domain logic extracted from telegram_bot.py
- Mein Deutsch HTML interface (7 tabs, Flask server on port 8767)
- Lesen: RSS feed, category cards, reading view, phrase save, DeepL translation
- Gespräche: persona selector, scene prompts, transcript analysis, session memory
- Schreiben: writing with LLM correction
- Wörter: phrasebook with drill integration
- Drill: L1/L2 verb drill, phrase practice, fuzzy match, verb filter
- NL routing: voice + text command routing for all German commands
- Ops: run_lesen_refresh.sh hourly auto-refresh (Curator pattern)
- Perf: photos compressed 91% (34MB → 3.5MB), 1-day browser cache

Fixes: #37 (drill routing), #38 (lesen empty pool)
Branch: feat/german-html-interface (105 commits, 2026-05-19 – 2026-05-25)
"
```

---

## Step 3 — Post-Merge Cleanup (Claude Code)

```bash
# Delete merged and stale feature branches
git branch -d feat/german-domain-extraction
git push origin --delete feat/german-domain-extraction

git branch -d poc/research-source-quality
git push origin --delete poc/research-source-quality

git branch -d poc/research-web-ui
git push origin --delete poc/research-web-ui

# Bulk-delete local claude/* worktree branches (20 local-only)
git branch | grep 'claude/' | xargs git branch -d
```

---

## Step 4 — Post-Merge Tasks (OpenClaw)

- [ ] Update `CHANGELOG.md` with German domain v0.9 Beta milestone
- [ ] Tag the merge commit: `git tag v0.9-beta && git push origin v0.9-beta`
- [ ] Confirm `_working/` decision recorded in CHANGELOG or ARCHITECTURE.md
- [ ] Confirm gitignore decision recorded

---

## Step 5 — Verification

```bash
git log main --oneline -3
# → squash commit visible at HEAD

git diff main feat/german-html-interface
# → empty (or only _working/ files if kept)

launchctl list | grep vanstedum
# → curator and lesen-refresh both present, no error codes

curl localhost:8767
# → HTML server responding

launchctl list | grep lesen
# → com.vanstedum.lesen-refresh present
```

---

## What This Does NOT Do

- Does not change any running code — squash merge is a history operation
- Does not affect launchd services or the running bot
- Does not resolve remote `claude/*` worktree branches
 (those need `git push origin --delete` per branch — separate cleanup pass)

---

## Squash Commit Checklist (Robert — before go-ahead)

- [ ] Confirm branch start date for commit message (2026-05-19 – 2026-05-25)
- [ ] Review squash commit message above — adjust scope if needed
- [ ] Give explicit go-ahead when pre-conditions are met
