# Project Brief: Personal AI Agents (Curator RSS)

**Owner:** Robert  
**Repository:** https://github.com/robertvanstedum/personal-ai-agents (public)  
**Workspace:** `~/projects/personal-ai-agents`  
**Private Repo:** https://github.com/robertvanstedum/rvs-openclaw-agent (memory/logs/workspace)

---

## Project Overview

**Goal:** Build AI-powered automation tools, starting with an RSS curator that delivers personalized geopolitical/finance briefings.

**Current Focus:** RSS Curator with deep dive research feature

**Tech Stack:**
- Python 3.14
- Anthropic Claude (Sonnet 4), xAI Grok (cost optimization)
- OpenClaw agent framework (main assistant)
- RSS feeds (15 sources: geopolitical, finance, contrarian perspectives)

---

## Three-Way Workflow

**Claude Web (Projects)** → Exploratory design, architecture, debugging with screenshots  
**Claude CLI** → Code implementation with direct file access (`cd ~/projects/personal-ai-agents && claude "...")  
**OpenClaw (Mini-moi)** → Execution, memory management, automation, workspace tasks

**Decision:** Use Claude for design/debugging (flat $20/month), OpenClaw for focused execution (pay-per-use API)  
**Cost Optimization:** ~$20-30/month vs $100+ for single API-based approach

---

## Current System Architecture

### Core Components

**1. Curator (`curator_rss_v2.py`)**
- Fetches 390 articles from 15 RSS feeds daily
- Scores/ranks with AI (grok-3-mini, ollama/phi, or claude-sonnet-4)
- Generates HTML briefing (top 20 articles)
- Cost: Free (ollama) | $0.18/day (grok-3-mini) | $0.90/day (claude-sonnet-4)
- Flags: `--model=[ollama|xai|sonnet]` `--dry-run` (optional preview)

**2. Web Server (`curator_server.py`)**
- Runs on localhost:8765
- Handles feedback (like/dislike/save)
- Triggers deep dive analysis
- **Must be running** for interactive features

**3. Deep Dive System (`curator_feedback.py`)**
- Generates research briefings tailored to user interests
- Uses Claude Sonnet for analysis (~$0.15 per dive)
- Outputs HTML with bibliography
- Stores in `interests/2026/deep-dives/`

**4. History Tracking**
- `curator_history.json` - Article index with hash_ids
- `curator_cache/{hash_id}.json` - Full article content
- Hash ID: 5-char MD5 of article URL (stable, unique)

### Key Files

```
curator_rss_v2.py       # Main curator + HTML generator
curator_server.py       # Web server for interactive features
curator_feedback.py     # Deep dive generation + history
curator_latest.html     # Generated briefing (opens in browser)
curator_history.json    # Article tracking
interests/2026/deep-dives/  # Deep dive archive
```

---

## Recent Work (Feb 21, 2026)

### Completed Today

✅ **Upgraded xAI Model** - grok-beta → grok-3-mini (newer model, better performance)  
✅ **Independent Flags** - Implemented `--dry-run` and `--model` as separate, composable flags  
  - `--dry-run` - Preview without saving (buttons disabled, no archive/history)  
  - `--model=[ollama|xai|sonnet]` - Choose LLM (free local | $0.18/day | premium)  
  - Mix and match: `--dry-run --model=sonnet` tests premium without committing  
✅ **Timestamp Archive** - Archive naming now includes time (YYYY-MM-DD-HHMM)  
  - Multiple runs per day preserved instead of overwriting  
  - Archive index shows: Date | Time | Model | Articles  
✅ **Model Tracking** - Metadata stored in HTML for reproducibility  
  - Know exactly which model generated each briefing  
  - Enables A/B comparison of model performance  
✅ **Display Polish** - Legacy entries show clean "-" instead of "unknown"/"?"  
✅ **Navigation Fixes** - Deleted orphaned files, fixed relative paths  
✅ **Operations Manual** - Created OPERATIONS.md with daily health checks  
✅ **Mac Mini Planning** - Documented migration plan in PROJECT_BRIEF.md  

### Designed (Not Yet Built)

📋 **Deep Dive Rating System** - See `docs/FEATURE_DEEP_DIVE_RATINGS.md`  
  - 4-star rating with optional comments  
  - Two-sided feedback loop (what to do more/less of)  
  - AI-assisted theme extraction  
  - Automatic prompt injection for future deep dives  
📋 **Deep Dive Delete Feature** - Cleanup for mistaken/low-quality dives  

### Next Session

- Mac Mini migration (always-on server setup)  
- Implement Deep Dive ratings (Phase 1: UI)  
- Implement Deep Dive delete feature  

---

## Previous Work (Feb 20, 2026)

✅ **Security Cleanup** - Removed all credentials from public repo (API keys in keyring)  
✅ **xAI Integration** - 80% cost reduction ($0.90 → $0.18/day)  
✅ **Bug Fix: JavaScript Closure** - Deep dive buttons now capture correct article (Claude-identified)  
✅ **Bug Fix: Hash_id Lookup** - Eliminated ambiguity from multiple runs per day  
✅ **Bug Fix: Duplicate Heading** - Removed double "Deep Dive Analysis" heading (Claude-identified)  
✅ **Three-Way Workflow** - Documented Claude Web + CLI + OpenClaw collaboration pattern  
✅ **Repository Public** - `personal-ai-agents` now public on GitHub

### Technical Decisions

**Multi-Provider Strategy:**
- xAI Grok for daily briefings (cheap, good quality)
- Anthropic Sonnet for deep dives (best quality)
- OpenAI GPT-4o-mini available (cheapest, not yet implemented)

**Credential Management:**
- macOS Keychain via `keyring` (primary)
- Environment variables (fallback)
- OpenClaw auth profiles separate (for OpenClaw internal use)
- Script: `setup_keys.py` manages Anthropic, xAI, Telegram keys

**History System:**
- Hash_id as canonical identifier (5-char MD5 of URL)
- Date-rank format deprecated (caused ambiguity)
- Supports: hash_id, date-rank, yesterday-N references

---

## Known Issues / Limitations

**Server Dependency:**
- Interactive features (like/dislike/deep dive) require `curator_server.py` running
- Must restart server after code changes to curator_feedback.py or curator_server.py
- Start: `cd ~/projects/personal-ai-agents && source venv/bin/activate && python3 curator_server.py &`

**Multiple Runs Per Day:**
- Multiple curator runs create duplicate ranks in history
- Fixed by using hash_id instead of date-rank for lookups
- History accumulates appearances (by design for tracking)

**Feed Quality:**
- Some feeds (O Globo) occasionally return 0 articles
- German sources (FAZ, Die Welt) may have paywall articles
- Diversity scoring ensures variety across sources

---

## Deployment & Infrastructure

### Current Setup (MacBook Air)
- **Challenge:** `curator_server.py` requires manual start after reboots/crashes
- **Limitation:** MacBook not always-on → server unavailable when closed/sleeping
- **Impact:** Interactive features (deep dive buttons) fail when server down

### Mac Mini Migration Plan

**Phase 1: MacBook Auto-Start (Immediate Fix)**
- Create launchd plist to keep `curator_server.py` running
- Auto-start on login
- Auto-restart on crash
- Location: `~/Library/LaunchAgents/com.user.curator-server.plist`

**Phase 2: Mac Mini as Always-On Server**

**Setup Steps:**
1. Set up Python 3.14 + venv on Mac Mini
2. Clone `personal-ai-agents` repo to Mini
3. Migrate credentials from MacBook Keychain → Mini Keychain
   - Anthropic API key
   - xAI API key
   - Telegram bot token
   - Use `setup_keys.py` on Mini
4. Set up launchd on Mini for `curator_server.py` auto-start
5. Move cron jobs from MacBook to Mini
   - Daily curator run (7 AM CST)
   - Balance tracking
6. Update `curator_latest.html` to point to Mini's local IP
   - Change `localhost:8765` → `{mini-ip}:8765`
   - Configure Mini firewall to allow port 8765
7. Test end-to-end: MacBook browser → Mini server → deep dive generation

**Benefits:**
- 24/7 availability for interactive features
- MacBook can sleep/travel without breaking workflow
- Centralized automation (one machine for all cron jobs)

**Status:** Phase 1 (launchd plist) - next immediate task

---

## Pending Features

### Delete Deep Dive (Requested Feb 20)
- **Goal:** Remove mistaken/low-quality deep dives from archive
- **UI:** Trash icon (🗑️) with confirmation prompt
- **Backend:** Server endpoint + history flag (soft delete recommended)
- **Estimate:** 1-2 hours for robust implementation
- **Docs:** `FEATURE_DELETE_DEEP_DIVES.md`

### Future Enhancements
- Telegram inline buttons for mobile workflow
- CLI history viewer (`curator-history`)
- Batch deep dive generation
- Archive search/filter
- Auto-cleanup old deep dives
- Multi-model comparison framework

---

## Development Workflow

### Making Changes

**Design/Architecture:**
```bash
# Use Claude Web (Projects) or Claude CLI
cd ~/projects/personal-ai-agents
claude "Design a delete feature for deep dive archive..."
```

**Implementation:**
```bash
# OpenClaw executes the design
# Tell OpenClaw: "Implement this design: [paste from Claude]"
```

**Testing:**
```bash
cd ~/projects/personal-ai-agents
source venv/bin/activate

# Run curator
python3 curator_rss_v2.py --mode=xai --open

# Start server (if needed)
python3 curator_server.py &

# Test in browser (opens automatically)
```

**Committing:**
```bash
git add -A
git commit -m "Feature: description"
git push origin main
```

### Common Commands

```bash
# Daily curator run
python3 curator_rss_v2.py --mode=xai --telegram

# View history
cat curator_history.json | jq '.[] | {title, appearances}'

# Check server status
ps aux | grep curator_server

# Restart server
pkill -f curator_server && python3 curator_server.py &
```

---

## Cost Tracking

**Daily Operations:**
- Curator (xAI mode): $0.18/day = $5.40/month
- Deep dives (occasional): ~$0.15 each
- OpenClaw (focused tasks): ~$10-20/month
- Claude Pro (unlimited design): $20/month flat

**Total:** ~$35-45/month for full system

**Savings:**
- Before xAI: $0.90/day = $27/month (curator only)
- Before Claude Pro: $100+/month (chatty API sessions)
- **Total optimization:** ~$90/month saved

---

## Key Learnings

**JavaScript Closures:**
- Variables captured by reference in loops cause bugs
- Solution: IIFE to capture by value at creation time

**Unique Identifiers:**
- Date-rank format breaks with multiple runs per day
- Hash_id (MD5 of URL) is robust and stable

**Cost Optimization:**
- xAI Grok comparable quality to Anthropic Haiku
- 80% cost reduction for daily operations
- Keep Sonnet for quality-critical tasks (deep dives)

**Workflow Split:**
- Long exploratory sessions → Claude (flat cost)
- Focused execution → OpenClaw (low token usage)
- Direct coding → Claude CLI (fast, file access)

---

## Quick Reference

**Repos:**
- Public: https://github.com/robertvanstedum/personal-ai-agents
- Private: https://github.com/robertvanstedum/rvs-openclaw-agent

**Docs:**
- `CHANGELOG.md` - Full project history
- `CLAUDE.md` - Three-way workflow guide
- `FEATURE_*.md` - Feature specifications
- `PROJECT_BRIEF.md` - This file

**Key Scripts:**
- `curator_rss_v2.py --mode=xai --open` - Generate briefing
- `curator_server.py` - Interactive web server
- `setup_keys.py` - Credential management
- `curator_feedback.py bookmark {hash_id}` - Manual deep dive

**Credentials:**
- Anthropic: keyring ("anthropic", "api_key")
- xAI: keyring ("xai", "api_key")  
- Telegram: keyring ("telegram", "bot_token")
- Setup: `python3 setup_keys.py`

---

## Status Summary

**Production Ready:**
- ✅ Daily briefing generation (xAI mode)
- ✅ Deep dive analysis (Sonnet)
- ✅ Web UI with feedback buttons
- ✅ History tracking and caching
- ✅ Cost-optimized pipeline

**In Development:**
- 🟡 Delete feature for deep dive archive

**Working Well:**
- Three-way workflow (Claude + OpenClaw)
- Cost optimization (80% reduction)
- Bug identification (Claude caught closure issues)
- Rapid iteration and documentation

**Next Session:**
- Implement delete feature (awaiting design from Claude)
- Test full workflow end-to-end
- Consider batch operations for archive cleanup

---

**Last Updated:** Feb 21, 2026, 1:47 PM CST  
**Version:** 1.1 (Added Feb 21 updates: --dry-run, --model flags, timestamp archive, model tracking)
