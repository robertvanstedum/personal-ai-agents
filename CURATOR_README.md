# RSS Curator - AI-Enhanced Geopolitics & Finance Briefings

## Three Modes, Three Price Points

| Mode | Cost | Quality | Use Case |
|------|------|---------|----------|
| `mechanical` | **$0/month** | Good | Free fallback, testing, or no API access |
| `ai` | **$6/month** | Better | Daily production briefing (single Haiku pass) |
| `ai-two-stage` | **$27/month** | Best | Manual deep analysis when it matters most |

### Mode Details

**Mechanical Mode (FREE)**
- Keyword-based scoring and categorization
- Instant, no API calls
- Good for testing or when API unavailable
- Command: `python curator_rss_v2.py --mode=mechanical --open`

**AI Mode ($6/month)**
- Single-stage Haiku scoring (~$0.20/day)
- Good relevance filtering and categorization
- Fast, cost-effective for daily use
- **Current production setup for 7am briefing**
- Command: `python curator_rss_v2.py --mode=ai --telegram`

**AI Two-Stage Mode ($27/month)**
- Stage 1: Haiku pre-filter (150 → 50 articles) ~$0.15
- Stage 2: Sonnet ranking with challenge-factor scoring ~$0.75
- Best quality: Surfaces contrarian insights
- Use manually for important analysis days
- Command: `python curator_rss_v2.py --mode=ai-two-stage --telegram`

## Production Configuration

**Daily 7am Briefing:**
- Mode: `--mode=ai` (single-stage Haiku)
- Cost: $6/month
- Auto-sends to Telegram
- Fallback to mechanical if API fails

**Manual Deep Analysis:**
- Mode: `--mode=ai-two-stage` (run when needed)
- Cost: ~$0.90 per run
- Use for important geopolitical events or when you want deeper analysis

**Total Monthly Cost: ~$6-10/month** (well under $300 budget)

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

**Daily briefing (production mode):**
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
- 🎯 Phase 2.2: Ad-hoc deep dive on flagged articles (~$0.10-0.20 per article)
- 🔮 Phase 2.3: Interest-aware curation (personalized based on your focus areas)
- 🚀 Phase 3: Multi-source intelligence (Reddit, Twitter, Substack, academic papers)

## Cost Breakdown

**Current spend:**
- Daily AI briefing: $6/month ($0.20/day × 30 days)
- Balance monitoring: ~$0/month (minimal API calls)
- Manual two-stage runs: Only when you trigger them

**Future (Phase 2.2):**
- Ad-hoc deep dives: ~$3-6/month (15-30 articles @ $0.10-0.20 each)
- Total projected: **$9-12/month** (97% under $300 budget)

## Files

- `curator_rss_v2.py` - Main curator script
- `setup_keys.py` - Store API keys in keychain
- `check_balance_alert.py` - Balance monitoring
- `track_usage.py` - Daily usage reporting
- `run_curator_cron.sh` - Cron wrapper for 7am briefing
