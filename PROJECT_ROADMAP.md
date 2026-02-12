# PROJECT ROADMAP
**Personal AI Agent System - Evolution & Thinking**

> This document captures the thought process, decisions, and evolution of building a personal AI agent system that combines OpenClaw, Neo4j, Postgres, and local LLMs. It's a living document showing how the system grows from concept to reality.

---

## Vision

Build a personal AI agent ecosystem that:
- **Learns from interactions** (Neo4j decision traces)
- **Curates information** (geopolitics research)
- **Integrates with daily life** (messaging, automation)
- **Runs locally or on-server** (portable, private)
- **Uses cost-effective models** (right tool for the job)

---

## System Architecture

### Components
- **OpenClaw** - Agent framework, scheduling, messaging integration
- **Neo4j** - Personal context graph (decisions, motives, patterns)
- **Postgres** - Structured research artifacts (articles, publications)
- **Ollama** - Local LLM for cheap operations
- **FastAPI** - Bridge between components

### Philosophy
- **Private first** - Personal data stays under your control
- **Portable** - MacBook → server migration path
- **Cost-conscious** - Cheaper models for bulk, premium for quality
- **Organized** - Structure before chaos

---

## Evolution of Thinking

### The Starting Point
Initially built a separate personal-ai-agents project (Python + FastAPI + Neo4j + Postgres + Ollama) to experiment with local AI and personal context. Worked well for basic queries and decision traces, but lacked scheduling, messaging integration, and structured workflow.

### The Integration Insight
OpenClaw provides the agent framework, scheduling, and messaging — but it's ephemeral (session-based memory). Personal-ai-agents provides the persistent context (Neo4j) and research storage (Postgres). **Combined = powerful.**

### The Architecture Decision
Things were getting complex. Rather than build and fix later, decided to **plan the architecture first**:
- How should repos be organized?
- What runs where?
- How do components talk to each other?
- How to make it portable (MacBook → server)?

Created `ARCHITECTURE.md` to think through these decisions before committing to a structure.

### The Cost Lesson
Gmail cleanup task cost ~$22 using Sonnet for bulk operations. **Key insight:** Right model for the right job. Use Haiku for repetitive bulk work, Sonnet for quality work, Opus for complex creative tasks. This informed the model strategy for all future features.

### The Curator Vision
With infrastructure settling and cost strategy clear, next big feature emerged: **overnight geopolitics curator**. This demonstrates the full system working together — automated curation (OpenClaw cron), personalized analysis (Neo4j context), research storage (Postgres), and learning from behavior (selection patterns → refined curation).

---

## Current State (Feb 2026)

### What Works
✅ OpenClaw agent (main session, Telegram integration)  
✅ Personal-ai-agents FastAPI server (basic endpoints)  
✅ Neo4j decision traces (manual + auto-scheduled)  
✅ Postgres defined (not populated yet)  
✅ Local Ollama (gemma3:1b)  
✅ Basic workspace structure (AGENTS.md, SOUL.md, MEMORY.md)

### Recent Lessons
- **Cost management** - Gmail cleanup cost ~$22 using Sonnet for bulk work (lesson: use Haiku for repetitive tasks)
- **Batch sizes matter** - 1000 items > 100 items (fewer API calls)
- **Planning > fixing** - Structure upfront saves refactoring later
- **Model strategy matters** - Right model for the job saves money without sacrificing quality
- **Balance monitoring critical** - Feb 11: Curator cron timeout caused by low balance ($0.40). Silent failure, no alert. Need proactive monitoring before hitting limits. (See `/workspace/projects/balance-monitoring.md`)

---

## Phases

### Phase 0: Foundation ✅
**Status:** Complete  
**What:** Basic OpenClaw setup, personal-ai-agents repo, Docker Compose for databases

### Phase 1: Architecture & Cleanup 🔄
**Status:** In progress (Weekend of Feb 8-9, 2026)  
**Goals:**
1. ✅ Lock down architecture decisions (see ARCHITECTURE.md) — DONE 2026-02-07
2. ⏳ Complete Gmail cleanup task — In progress
3. ✅ Push personal-ai-agents to GitHub — DONE 2026-02-07
4. ⏳ Establish repository structure — Next after Gmail

**Architecture Decisions Made (2026-02-07):**
- ✅ **Git strategy:** Mono-repo now, multi-repo later for public version
- ✅ **Docker strategy:** Unified compose (modern container architecture)
- ✅ **Server target:** Mac Mini next, keep optionality
- ✅ **Naming:** `ai-infrastructure` repo, `ai-*` prefix for containers
- ✅ **Backups:** Daily automated, 30-day retention, monthly cloud archive

### Phase 2: Geopolitics Curator ✅🔄
**Status:** MVP Live (Feb 10, 2026) → AI Enhancement Next  
**Why this matters:** This is the first "smart agent" feature that demonstrates the full power of the integrated system — automated curation, personalized analysis, and learning from user behavior over time.

**Current Status (Feb 11, 2026):**
- ✅ MVP working (mechanical keyword scoring + source diversity)
- ✅ Telegram delivery automated (7am daily via OpenClaw cron)
- ✅ HTML bookmark system (curator_latest.html + dated archives)
- 🔄 **Next:** Add AI filtering (Haiku pre-filter → Sonnet quality ranking)
- 🔄 **Next:** Context-aware curation (read Neo4j for your recent interests)

---

#### The Vision: Overnight Geopolitics Curator

**Inputs:**
- Trusted source list (evolves over time based on selection patterns)
- Your Neo4j decision traces (what you value, why)
- News aggregators, RSS feeds, research publications
- Eventually: X posts, books, papers

**Process:**
- Overnight agent (2-3am) fetches, analyzes, scores
- Ranks top 20-40 based on relevance + quality + challenge-factor
- Morning delivery (7am) via Telegram

**Output:**
- Curated briefing with summaries
- You select what to dive into
- Selected items → Postgres for research
- Selection patterns → Neo4j (refine curation algorithm)

---

#### Model Strategy

**Fetching/parsing:** Haiku (cheap, bulk work)  
**Analysis/curation:** Sonnet (quality matters here)  
**Summary generation:** Sonnet (voice/quality matters)

**Why not Opus?** Sonnet is plenty smart for curation. Save Opus for when you're doing deep analysis on selected articles (user-initiated, not automated).

**Cost-conscious thinking:** Right model for the job. Bulk operations get cheap models, quality work gets premium models.

---

#### Architecture Fit

This plugs perfectly into the personal-ai-agents setup:

**OpenClaw:** Scheduling (cron job spawns isolated agent)  
**Neo4j:** Your interests, trusted sources, selection history  
**Postgres:** Saved articles, research artifacts  
**FastAPI:** Bridge between OpenClaw skill and your database  

**Key insight:** This replaces the APScheduler stub in your current project. OpenClaw cron → calls your FastAPI endpoint or runs skill directly.

---

#### Implementation Path

**Phase 2.1: Basic Overnight Curator (MVP)**
1. Create geopolitics-curator skill
2. Trusted sources config (`workspace/geopolitics-sources.json`)
3. Overnight cron (2am isolated agent, Sonnet)
4. Morning delivery (Telegram list, 7am)
5. Manual selection (you reply with numbers)

**Phase 2.2: Postgres Integration**
6. FastAPI endpoint (save selected articles)
7. Selection handler (parses your choices, saves to DB)
8. Neo4j integration (why you selected, patterns)

**Phase 2.3: Smart Curation**
9. Personalization (pull context from Neo4j)
10. Source scoring (track which sources you select from)
11. Auto-refine (promote/demote sources based on patterns)

**Phase 2.4: Advanced Features**
12. X integration (monitor specific accounts/topics)
13. Cross-reference (connect related articles)
14. Conflict detection (highlight opposing viewpoints)

---

#### Sequencing & Time Estimates

**Before Phase 2 starts:**
1. ✅ Infrastructure decisions (Git strategy, Docker, naming) — 1-2 hours
2. ✅ Push personal-ai-agents to GitHub — 30 min
3. ✅ Finish Gmail cleanup (we're close) — 1-2 hours

**Phase 2.1 build:**
4. Build Phase 1 curator — 4-6 hours
5. Test overnight → iterate — ongoing

**Timeline:** Could have Phase 2.1 running by Monday morning if we focus this weekend.

---

#### Open Questions (Before We Build)

1. **Starting sources?** Do you have a list, or should we research reputable geopolitics sources?
2. **Delivery time?** 7am? Earlier?
3. **Selection interface?** Telegram reactions (👍/💾) or numbered list (reply "3, 7, 12")?
4. **Conflict ratio?** What % should be "challenge your thinking" vs "trusted perspective"?
5. **Topics/regions?** Global? US-focused? Specific regions (Middle East, China, EU)?

---

#### Cost Estimate

**Overnight run (Sonnet):**
- Fetch 100 articles
- Analyze/score each
- Rank top 40
- Generate summaries

**Rough estimate:** $2-5/night depending on article length and depth of analysis  
**Monthly:** ~$60-150 if running daily

**Optimization opportunities:**
- Batch more aggressively
- Cache sources
- Use Haiku for pre-filtering (rough first pass)
- Only use Sonnet for final ranking/summaries

---

#### Why This Feature Second?

1. **Demonstrates the vision** - Shows why we built the infrastructure
2. **High personal value** - Daily use case, immediately useful
3. **Tests the system** - Exercises all components (OpenClaw, Neo4j, Postgres, FastAPI)
4. **Portfolio piece** - Clear, impressive demo of AI-human collaboration
5. **Learning opportunity** - Iterative refinement based on real usage patterns

### Phase 3: RVS Associates LLC Website 📅
**Status:** Planned (Capital Investment Project)  
**Why this matters:** Use this AI collaboration to build a functional, professional LLC website. Demonstrates capabilities to potential clients, showcases AI-human partnership, and establishes digital presence for RVS Associates LLC.

**Business Context:**
- Company: RVS Associates LLC
- Investment: Capital expense (AI infrastructure + development)
- Billing: Switch Anthropic to corporate card
- Goal: Professional portfolio + business development

**Deliverables:**
- Modern, professional website design
- Portfolio showcasing AI agent work
- Clear value proposition for consulting services
- Case studies from this collaboration
- Contact/inquiry system

**Technical Approach:**
- Built collaboratively (Robert + Mini-moi)
- Modern stack (to be determined based on needs)
- Hosted/deployed professionally
- Mobile-responsive, accessible

**Timeline:** TBD (after curator Phase 2 AI enhancement)

### Phase 4: Unified Memory Architecture 📅
**Status:** Future  
**Goals:**
- Integrate OpenClaw MEMORY.md with Neo4j context graph
- Automated memory consolidation
- Cross-reference personal context with research artifacts

### Phase 5: Production Migration & Monitoring 📅
**Status:** Planned (April 2026)  
**Goals:**
- Move from MacBook to dedicated server (Mac Mini or VPS)
- Production Docker setup
- **Security hardening:**
  - Migrate credentials (keychain → encrypted .env or Docker secrets)
  - See `PRODUCTION_SECURITY.md` for full migration plan
  - Full-disk encryption, firewall rules
  - Credential rotation procedures
- Backup automation
- **Monitoring & alerting:**
  - Balance alerts (Anthropic API usage)
  - Error pattern detection (quota, rate limits)
  - Cron job health checks
  - Security event monitoring
  - Telegram notifications for critical issues

---

## Design Principles

### Model Selection
- **Conversations:** Sonnet (quality, reasoning)
- **Bulk operations:** Haiku (cost-effective)
- **Creative/complex:** Opus (when needed)
- **Local ops:** Ollama (free, private)

### Data Strategy
- **Neo4j:** Personal context, why things matter, relationships
- **Postgres:** Structured research, artifacts, publications
- **MEMORY.md:** Curated long-term memory (OpenClaw native)
- **Daily logs:** `memory/YYYY-MM-DD.md` (raw session notes)

### Development Approach
1. **Plan before building** (architecture → structure → code)
2. **Test early** (pytest + GitHub Actions from the start)
3. **Document decisions** (this file shows the thinking)
4. **Iterate based on cost** (monitor, optimize, learn)

---

## Why This Document?

This roadmap serves three purposes:

### 1. Planning Tool
Keeps human and AI aligned on what's next, what's blocked, and what needs decisions. A shared source of truth for the project direction.

### 2. Decision Record
Captures **why** we made choices, not just what we built. Shows the evolution of thinking:
- Why this architecture?
- Why this model strategy?
- Why this sequence of features?
- What lessons informed later decisions?

### 3. Portfolio Artifact
Demonstrates AI-human strategic collaboration to future employers, investors, or collaborators:
- Shows systems thinking (not just coding)
- Documents cost-conscious engineering
- Proves ability to plan complex projects
- Illustrates how to work effectively with AI agents

**It's not just about building cool tech** — it's about showing the *thinking* behind it. How an AI and human work together to plan, build, and evolve a complex system from first principles.

**This document itself is part of the product** — proof that you can think strategically, document clearly, and collaborate with AI to solve real problems.

---

## Timeline

**Target: Production by April 2026** 🎯

**Week of Feb 10-16, 2026**
- ✅ Curator MVP live (mechanical scoring + automation)
- ⏳ AI enhancement (Haiku pre-filter + Sonnet ranking)
- ⏳ Context-aware curation (Neo4j integration)

**Week of Feb 17-23, 2026**
- Postgres integration (save selected articles)
- Source scoring (track engagement patterns)
- Balance monitoring & alerting

**Week of Feb 24 - Mar 2, 2026**
- RVS Associates LLC website (design + structure)
- Portfolio content (case studies from collaboration)

**March 2026**
- Website build & deployment
- Unified memory architecture
- Advanced curator features

**April 2026** 🚀
- Production migration (server deployment)
- Monitoring & backup automation
- System hardening & documentation
- **Go live: Professional LLC presence + AI infrastructure**

---

_Last updated: 2026-02-11 by Mini-moi_
