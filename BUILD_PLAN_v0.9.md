# Personal-AI-Agents — v0.9 Build Plan
**Date:** Feb 28, 2026  
**Partners:** Claude (design), OpenClaw (agent/memory), Claude Code (implementation)  
**Deadline:** ~2 weeks — repo goes live alongside resume and job search

---

## Context

The system is functionally complete and learning. v0.9 is a **portfolio milestone**, not a 
feature-complete milestone. The goal is a clean, impressive repo a hiring manager can read 
in 5 minutes. Feature work continues as active development post-launch.

**The story we're telling:**
> "Personal AI briefing system that reads 400+ articles/day, curates the top 20, and learns 
> from 415 signals bootstrapped from years of X bookmarks. Runs at 7 AM daily. $35-45/month."

---

## v0.9 Checklist

### 1. Cost Tracking (today — in progress)

**Goal:** Unified cost visibility across OpenClaw (chat) and Curator (scoring runs).

**What's already done:**
- `track_usage.py` parses OpenClaw session transcripts → writes to `daily_usage.json`
- OpenClaw chat (Sonnet) costs already tracked

**What needs to be built:**
- Instrument `curator_rss_v2.py` to append one record per run to `curator_costs.json`
- Each record: `date`, `timestamp`, `model`, `use_type`, `input_tokens`, `output_tokens`, `cost_usd`
- One record per run (not per day) — Postgres-ready from day one, no restructuring later
- Build `cost_report.py` with three modes:

```bash
python cost_report.py          # today
python cost_report.py week     # last 7 days table
python cost_report.py month    # last 30 days table
```

**Output format (plain text, Telegram-friendly — no box-drawing characters):**

```
Cost Report - Feb 28
--------------------
Chat (Sonnet):    $0.56
Curator (xAI):    $0.18  1 run
Curator (Haiku):  $0.03  2 runs
--------------------
Today total:      $0.77
Month so far:    $12.43

Week (Feb 22-28):
Date       Chat   Curator   Total
Feb 28    $0.56    $0.21    $0.77
Feb 27    $0.83    $0.18    $1.01
Feb 26    $0.43    $0.19    $0.62
--------------------
Week total $2.78   $1.15    $3.93
```

**File locations:**
- Chat costs: `~/.openclaw/workspace/logs/usage/daily_usage.json`
- Curator costs: `~/Projects/personal-ai-agents/curator_costs.json` (create with `[]` if not exists)
- Report script reads both files and merges for combined output

**Implementation note for Claude Code:**
Read `track_usage.py` first to confirm the exact schema of `daily_usage.json` before writing 
anything. Match that structure exactly.

---

### 2. README (after cost tracking)

**Goal:** Someone landing on the repo immediately understands what it does and why it's impressive.

**Must cover:**
- What it does (400+ articles/day → top 20 → 7 AM Telegram delivery)
- How it learns (415 signals: likes, saves, dislikes + X bookmark bootstrap)
- Architecture overview (curator → scorer → feedback loop)
- Cost story ($35-45/month, optimized from $100+ by switching to grok-3-mini)
- How to run it
- Roadmap (shows active development continues)

---

### 3. Code Cleanup

**Goal:** No clutter, no confusion, professional first impression.

- Remove or archive unused files (old HTML files, deprecated scripts, experimental one-offs)
- Remove accidentally committed junk files from root (`bash`, `exit`, `git` files if still present)
- Consolidate duplicate/experimental scripts that shouldn't be public
- Quick pass on comments and docstrings — code should be readable to a stranger
- Verify public/private repo split is still clean

---

### 4. Security Check

**Goal:** No credentials, tokens, or secrets anywhere in the public repo — including history.

- Scan codebase for hardcoded API keys, tokens, or credentials
- Confirm `.gitignore` covers all secret files (`.env`, config files with tokens, keychain refs)
- **Check commit history** — removing a file doesn't remove it from git history:
  ```bash
  git log --all -S "sk-"        # scan for API key patterns
  git log --all -S "xai-"
  git log --all -S "token"
  ```
- If secrets found in history: use `git filter-repo` to scrub before tagging
- Confirm nothing in the public repo that was meant to stay private

---

### 5. Tag v0.9-beta

Once items 1–4 are complete:
```bash
git tag -a v0.9-beta -m "Portfolio milestone: RSS curation + X bookmark learning loop"
git push origin v0.9-beta
```

---

## What's Already Built (v0.9 scope complete)

- RSS ingestion and scoring — xAI grok-3-mini (primary) + Haiku fallback
- Learning feedback loop — like/save/dislike → updates user profile → influences future scoring
- X bookmark ingestion — 415 signals bootstrapped from years of saved bookmarks
- Telegram delivery — 7 AM daily briefing
- Voice note capture — routes to VOICE_NOTES.md or executes commands
- Cloudflare Tunnel webhook architecture — designed, pending live implementation

---

## 1.0 Scope (post job search launch, ~2+ weeks out)

These continue as active development after the repo goes public — which is a positive signal 
for anyone watching the repo.

| Feature | Description |
|---|---|
| Phase 3C: t.co enrichment | Follow tweet links → identify trusted domains → improve source scoring |
| Phase 3D: Source ingestion | Add discovered Substack/domain URLs as RSS feeds |
| Telegram webhook live | Button callbacks, /run /status /briefing commands fully working |
| Postgres migration | `curator_costs.json` already structured as rows — `COPY` ready |

---

## Working Arrangement

| Partner | Role |
|---|---|
| Claude (claude.ai) | Design, architecture, strategic decisions, this document |
| OpenClaw | Agent orchestration, memory, Telegram interface, running commands |
| Claude Code | File edits, implementation, code changes |
