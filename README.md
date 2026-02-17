# RVS Personal AI Agents

**Production-grade AI automation system** for daily intelligence briefings, cost monitoring, and personalized task automation.

Built on [OpenClaw](https://openclaw.ai/) - a personal AI assistant framework that runs locally and connects to your preferred messaging channels.

---

## 🎯 What This Does

- **Morning Intelligence Briefing** (7 AM daily) - AI-curated RSS digest from geopolitics, finance, and tech sources
- **Usage Tracking** (8 AM daily) - Token consumption and cost monitoring with budget alerts
- **Balance Monitoring** (every 4 hours) - Automated low-balance warnings
- **System Cron Integration** - Production-grade reliability without LLM dependency

**Philosophy:** Scripts contain the intelligence; execution should be fast, reliable, and deterministic.

---

## 📁 Repository Structure

### Public Repository (this repo)
**Contains:**
- Working automation scripts (curator, usage tracker)
- Production cron wrapper (`scripts/cron_wrapper.sh`)
- Strategy documents (architecture, roadmaps, analysis)
- Setup documentation

**Purpose:** Showcase technical work, share automation patterns, enable replication

### Private Workspace (separate repo)
**Contains:**
- Agent personality configuration (SOUL.md, AGENTS.md, USER.md)
- Personal memory and context (MEMORY.md, daily logs)
- Private notes and brainstorming
- Environment-specific secrets (API keys via keyring)

**Why separate?** Personal knowledge graph and agent personality are private; automation code and strategy are shareable.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    System Cron                          │
│         (Reliable, no LLM dependency)                   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
         ┌───────────────────────┐
         │   cron_wrapper.sh     │
         │  (captures output)    │
         └───────────┬───────────┘
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
┌──────────────┐          ┌──────────────┐
│ Your Scripts │          │  OpenClaw    │
│ (intelligent │  ──────► │  Messaging   │
│  output)     │          │     API      │
└──────────────┘          └──────┬───────┘
                                 │
                                 ▼
                          ┌──────────────┐
                          │   Telegram   │
                          │  (delivery)  │
                          └──────────────┘
```

**Key Design Decision:** Scripts generate formatted output; wrapper simply delivers it. No agent interpretation = 100% reliability.

---

## 🚀 Quick Start

### Prerequisites

1. **OpenClaw installed and configured**
   ```bash
   npm install -g openclaw@latest
   openclaw onboard
   ```

2. **Messaging channel connected** (Telegram, WhatsApp, etc.)
   - Follow OpenClaw wizard to pair your channel
   - Get your chat ID

3. **Python 3.9+** with venv support

---

### Installation

```bash
# Clone this repository
git clone https://github.com/robertvanstedum/personal-ai-agents.git
cd personal-ai-agents

# Set up Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure credentials (see CREDENTIALS_SETUP.md)
python setup_credentials.py

# Test the wrapper
./scripts/cron_wrapper.sh ./track_usage_wrapper.sh YOUR_CHAT_ID
```

### Set Up Workspace (First Time)

**Create your private workspace:**

```bash
cd ~/.openclaw/workspace

# Create agent personality files
# See templates in docs/workspace-templates/
touch SOUL.md      # Who your agent is
touch USER.md      # About you
touch AGENTS.md    # Agent guidelines
touch MEMORY.md    # Long-term memory
mkdir memory       # Daily logs (YYYY-MM-DD.md)
```

**Template examples:**
- See `docs/workspace-templates/` for starter templates
- Customize SOUL.md to define your agent's personality
- Update USER.md with your timezone, preferences, goals

**Security:**
- Keep workspace in a PRIVATE git repository
- Use `.gitignore` to exclude API keys (already configured in OpenClaw)
- Store credentials in system keyring (not git)

---

### Install Cron Jobs

```bash
# Edit crontab
crontab -e

# Add jobs (adjust paths and chat ID)
0 7 * * * /path/to/personal-ai-agents/scripts/cron_wrapper.sh /path/to/personal-ai-agents/run_curator_cron.sh YOUR_CHAT_ID
0 8 * * * /path/to/personal-ai-agents/scripts/cron_wrapper.sh /path/to/personal-ai-agents/track_usage_wrapper.sh YOUR_CHAT_ID
0 */4 * * * /path/to/personal-ai-agents/scripts/cron_wrapper.sh /path/to/.openclaw/workspace/scripts/check_balance_alert.sh YOUR_CHAT_ID
```

**Full setup guide:** See [CRON-SETUP.md](CRON-SETUP.md)

---

## 📊 Features

### 1. AI-Powered RSS Curator
**File:** `curator_rss_v2.py`

- Fetches from geopolitics, finance, tech, and treasury sources
- AI scoring (Haiku) for relevance (~$0.20/day)
- Category weighting + diversity boosting
- Automatic Telegram delivery

**Modes:**
- `--mode=mechanical` - Free keyword-based
- `--mode=ai` - Single-stage Haiku ($6/month)
- `--mode=ai-two-stage` - Haiku + Sonnet ranking ($27/month)

**See:** [CURATOR_README.md](CURATOR_README.md)

### 2. Usage & Cost Tracking
**File:** `track_usage.py`

- Parses Anthropic API usage from gateway logs
- Daily/weekly/monthly token and cost summaries
- Formatted Markdown for messaging
- Budget projection calculations

### 3. Balance Monitoring
**File:** `check_balance_alert.py`

- Checks Anthropic account balance via API
- Three alert levels (warning, critical, severe)
- Silent when balance is OK
- Automatic notifications

### 4. Production Cron Wrapper
**File:** `scripts/cron_wrapper.sh`

- Runs any script via system cron
- Captures stdout/stderr
- Delivers to messaging channel via OpenClaw
- Error handling and logging
- **Zero LLM dependency** = reliable execution

---

## 📚 Documentation

### Architecture & Design
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design and technical decisions
- [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md) - Development phases and milestones
- [PRODUCTION_SECURITY.md](PRODUCTION_SECURITY.md) - Security best practices
- [CRON-SETUP.md](CRON-SETUP.md) - System cron installation guide

### Feature Documentation
- [CURATOR_ROADMAP.md](CURATOR_ROADMAP.md) - RSS curator feature evolution
- [CURATOR_ENHANCEMENT_ANALYSIS.md](CURATOR_ENHANCEMENT_ANALYSIS.md) - AI curation cost/benefit analysis
- [INTEREST_CAPTURE_README.md](INTEREST_CAPTURE_README.md) - Deep dive analysis system
- [AI_TOOLS_EVALUATION.md](AI_TOOLS_EVALUATION.md) - Tool selection rationale

### Learning & Methodology
- [📖 Case Study: Human-AI Co-Building](docs/CASE-STUDY-DEEP-DIVE-FEATURE.md) - **How we built the deep dive feature through collaborative exploration** (3-hour session, Feb 16 2026)
  - Methodology: Learning-focused development vs "vibe coding"
  - Technical learning moments (client pattern, abstractions, dependencies)
  - Emergent discoveries (natural language interface)
  - Lessons for humans and AI agents working together

---

## 🔐 Security

**What's in git:**
- ✅ Code and scripts
- ✅ Documentation
- ✅ Configuration templates
- ❌ API keys (use system keyring)
- ❌ Personal memory/logs (separate private repo)
- ❌ Credentials or tokens

**Credential Management:**
- Use Python `keyring` for API keys
- Environment variables for non-sensitive config
- `.gitignore` excludes secrets by default
- See [CREDENTIALS_SETUP.md](CREDENTIALS_SETUP.md)

---

## 🎓 Learning from This Project

**Key Lessons:**

1. **Batch Aggressively** - 1000-item batches beat 100-item for cost/speed
2. **Put Intelligence in Scripts** - Don't force agent mediation where it's not needed
3. **Use Cheaper Models for Mechanical Tasks** - Haiku for filtering, Sonnet for quality
4. **System Cron > Agent Cron** - For production reliability on scheduled tasks
5. **Separate Public and Private** - Code is shareable, personal context is not

**Mistakes and Fixes:**
- Initial OpenClaw cron design caused LLM timeout failures → migrated to system cron
- See [MEMORY.md](docs/lessons-learned.md) for detailed postmortems

---

## 🛠️ Customization

### Add Your Own Sources
Edit `curator_rss_v2.py`:
```python
FEEDS = {
    'your_source': {
        'url': 'https://example.com/rss',
        'categories': ['geo_major', 'technology']
    }
}
```

### Adjust Scoring Categories
Modify `CATEGORY_SCORES` in `curator_rss_v2.py` to match your interests.

### Create New Automation
1. Write script with formatted output
2. Test standalone: `bash your_script.sh`
3. Test with wrapper: `./scripts/cron_wrapper.sh your_script.sh CHAT_ID`
4. Add to crontab

---

## 🤝 Contributing

This is a personal project, but ideas and improvements are welcome!

**If you build something similar:**
- Share your automation patterns
- Document what worked/what didn't
- File OpenClaw feature requests for missing pieces

**Related OpenClaw Feature Request:**
- [#18160: Direct Exec Mode for Cron Jobs](https://github.com/openclaw/openclaw/issues/18160)

---

## 📈 Roadmap

**Phase 1: Foundation** ✅
- RSS curator with AI scoring
- Usage tracking and monitoring
- System cron integration

**Phase 2: Intelligence** (In Progress)
- Interest capture system
- Context-aware briefing scoring
- Ad-hoc deep dives on flagged articles

**Phase 3: Expansion** (Future)
- Email/calendar integration
- Multi-agent task delegation
- Proactive intelligence gathering

See [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md) for details.

---

## 💡 Philosophy

**"Faster, Cheaper, Better - All Three Simultaneously"**

This project proves you don't have to pick two. With the right architecture:
- AI makes it smarter than keyword filters
- Batching + model selection keeps costs low ($6-27/month)
- System cron + no-LLM execution makes it reliable

**Personal AI should be:**
- 🏃 Fast enough to use daily
- 💰 Cheap enough to run constantly  
- 🎯 Smart enough to save time
- 🔒 Private enough to trust
- 🔧 Hackable enough to customize

---

## 📞 Contact

**Author:** Robert van Stedum  
**Repository:** [personal-ai-agents](https://github.com/robertvanstedum/personal-ai-agents)  
**Built with:** [OpenClaw](https://openclaw.ai/)

---

## 📄 License

MIT License - See [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- **OpenClaw Team** - For building an extensible personal AI framework
- **Anthropic** - Claude models power the curation intelligence
- **Community** - For inspiration and feature requests

---

**Status:** Production (daily use since Feb 2026)  
**Last Updated:** 2026-02-16
