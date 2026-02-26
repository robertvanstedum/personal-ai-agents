# RSS Curator - AI-Enhanced Geopolitics & Finance Briefings

## 🎯 Learning Feedback Loop (NEW - Feb 26, 2026)

**The curator now learns from your feedback and personalizes article scoring.**

### How It Works

1. **Interact with articles** - Use like (👍), save (🔖), or dislike (👎) buttons in web UI or Telegram
2. **System learns patterns** - Tracks your preferred sources, themes, content styles, and avoid signals
3. **Personalizes scoring** - After 3+ interactions, injects your preferences into AI scoring prompts
4. **Articles rank higher** - Content matching your preferences gets boosted (+1 to +2), avoids get penalized (-1 to -2)

### Feedback Weights

- **Like (+2)** - Strong quality signal: "More like this, exactly what I want"
- **Save (+1)** - Bookmark/curiosity: "Interesting, maybe relevant later"
- **Dislike (-1)** - Avoid signal: "Less like this, not interested"

### Verified Results

After just 6 interactions:
- Preferred source (Geopolitical Futures) jumped to #1
- All 3 liked sources landed in top 4
- Disliked sources scored lower and pushed down
- **Personalization working on first run** - quality improves as feedback accumulates

### Privacy

- All learning data stored locally in `~/.openclaw/workspace/curator_preferences.json`
- Never leaves your machine
- You control what gets learned (explicit feedback only)

---

## Four Modes, Four Price Points

| Mode | Cost | Quality | Use Case |
|------|------|---------|----------|
| `mechanical` | **$0/month** | Good | Free fallback, testing, or no API access |
| `xai` | **$5.40/month** | Better | Daily production briefing (Grok, personalized) ⭐ **Current** |
| `ai` | **$6/month** | Better | Alternative with Claude Haiku |
| `ai-two-stage` | **$27/month** | Best | Manual deep analysis when it matters most |

### Mode Details

**Mechanical Mode (FREE)**
- Keyword-based scoring and categorization
- Instant, no API calls
- Good for testing or when API unavailable
- Command: `python curator_rss_v2.py --mode=mechanical --open`

**xAI Mode ($5.40/month) ⭐ CURRENT PRODUCTION**
- Grok scoring with personalized feedback (~$0.18/day)
- **Learns from your feedback** (like/save/dislike)
- **Personalizes article scoring** based on accumulated preferences
- 80% cheaper than Claude Haiku
- Fast, cost-effective, adaptive
- Command: `python curator_rss_v2.py --mode=xai --telegram`

**AI Mode ($6/month)**
- Single-stage Haiku scoring (~$0.20/day)
- Good relevance filtering and categorization
- Fast, cost-effective for daily use
- No personalization (static scoring)
- Command: `python curator_rss_v2.py --mode=ai --telegram`

**AI Two-Stage Mode ($27/month)**
- Stage 1: Haiku pre-filter (150 → 50 articles) ~$0.15
- Stage 2: Sonnet ranking with challenge-factor scoring ~$0.75
- Best quality: Surfaces contrarian insights
- Use manually for important analysis days
- Command: `python curator_rss_v2.py --mode=ai-two-stage --telegram`

## Production Configuration

**Daily 7am Briefing:**
- Mode: `--mode=xai` (Grok with personalization)
- Cost: $5.40/month (~$0.18/day)
- **Learning enabled** - Adapts to your feedback over time
- Auto-sends to Telegram
- Fallback to mechanical if API fails

**Manual Deep Analysis:**
- Mode: `--mode=ai-two-stage` (run when needed)
- Cost: ~$0.90 per run
- Use for important geopolitical events or when you want deeper analysis

**Deep Dive Articles:**
- Cost: ~$0.02 per deep dive (Claude Sonnet)
- Triggered manually via web UI or Telegram buttons
- Generates concise research launchpad (6 sections + bibliography)

**Total Monthly Cost: ~$5-10/month** (well under $300 budget)

## Setup

1. **Install dependencies:**
   ```bash
   cd ~/Projects/personal-ai-agents
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Store API keys in keychain:**
   ```bash
   python setup_keys.py
   ```
   - Enter Anthropic API key
   - Enter Telegram bot token

3. **Test it:**
   ```bash
   python curator_rss_v2.py --mode=ai --telegram
   ```

## Commands

**Daily briefing (production mode with learning):**
```bash
python curator_rss_v2.py --mode=xai --telegram --fallback
```

**Alternative (Claude Haiku, no learning):**
```bash
python curator_rss_v2.py --mode=ai --telegram --fallback
```

**Deep analysis (when needed):**
```bash
python curator_rss_v2.py --mode=ai-two-stage --open --telegram
```

**Test/free mode:**
```bash
python curator_rss_v2.py --mode=mechanical --open
```

## Categories

- **geo_major**: US, China, Russia, Europe, Japan, Korea
- **geo_other**: Middle East, Africa, Latin America, South/Southeast Asia
- **monetary**: Gold, Bitcoin, currencies, commodities
- **fiscal**: Government debt/spending (US, Japan, Europe)
- **technology**: AI in physical world (robotics, drones, warfare, autonomous systems)
- **other**: Everything else

## Roadmap

- ✅ Phase 1: Mechanical foundation
- ✅ Phase 2.1: Two-stage AI enhancement
- ✅ Phase 2.2: Ad-hoc deep dive on flagged articles ($0.02 per article)
- ✅ Phase 2.3: Learning feedback loop (personalized based on user feedback) 🎯 **Achieved Feb 26, 2026**
- 🔮 Phase 2.4: Serendipity + decay factors (avoid filter bubbles, fade old preferences)
- 🚀 Phase 3: Multi-source intelligence (Reddit, Twitter, Substack, academic papers)

## Cost Breakdown

**Current spend (as of Feb 26, 2026):**
- Daily xAI briefing: $5.40/month ($0.18/day × 30 days) 🎯 **With learning**
- Deep dive articles: ~$0.60/month (30 articles @ $0.02 each)
- Balance monitoring: ~$0/month (minimal API calls)
- Manual two-stage runs: Only when you trigger them (~$0.90 each)

**Total current: ~$6/month** (98% under $300 budget)

**With learning feedback:**
- System gets smarter over time (no additional cost)
- Preferred sources rank higher automatically
- Reduces noise from irrelevant articles

## Files

- `curator_rss_v2.py` - Main curator script
- `setup_keys.py` - Store API keys in keychain
- `check_balance_alert.py` - Balance monitoring
- `track_usage.py` - Daily usage reporting
- `run_curator_cron.sh` - Cron wrapper for 7am briefing
