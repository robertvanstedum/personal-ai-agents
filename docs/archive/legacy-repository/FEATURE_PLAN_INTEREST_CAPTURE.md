# Feature Plan: Interest Capture + Deep Dive

**Status:** Ready to implement (starting after 5 PM CST, Feb 16, 2026)

**Goal:** Build interactive article tagging and on-demand AI analysis

---

## Phase 1: Interest Capture System (1.5-2 hours)

### What We're Building
User can flag articles from morning briefings with priority levels that affect future curation.

### Implementation

**1. Telegram Command Handler** (`flag_article.py`)
```
Usage: /flag <article_number> <priority>

Priorities:
- DEEP-DIVE: Trigger immediate analysis (+50 score, 3 days)
- THIS-WEEK: Boost in briefings (+30 score, 7 days)
- BACKLOG: Save for later (+10 score, no expiry)
- MUTE: Reduce this topic (-20 penalty, 7 days)
```

**2. Interest Storage** (`interests/YYYY-MM-DD-flagged.md`)
```markdown
# Flagged Articles - 2026-02-16

## [DEEP-DIVE] Article Title
- URL: https://...
- Flagged: 2026-02-16 07:35 AM
- Expires: 2026-02-19
- Topics: china, trade, sanctions
- Reason: Potential impact on tech supply chains

## [THIS-WEEK] Another Article
...
```

**3. Curator Integration** (modify `curator_rss_v2.py`)
- Read `interests/` directory on startup
- Parse active interests (non-expired)
- Apply score modifiers based on topic matches
- Boost/penalize articles accordingly

**4. Command Handler Integration**
- Set up Telegram bot command listener
- Parse article number from briefing
- Extract metadata (title, URL, topics)
- Store in interests file
- Trigger deep dive if priority is DEEP-DIVE

---

## Phase 2: Ad-hoc Deep Dive (1.5-2 hours)

### What We're Building
AI-powered deep analysis of flagged articles with contrarian perspectives.

### Implementation

**1. Article Fetcher** (`deep_dive.py`)
- Use `web_fetch` tool to get full article content
- Extract main text (markdown)
- Prepare for LLM analysis

**2. Analysis Prompt** (Sonnet 4)
```
Analyze this article with focus on:

1. Key Implications
   - What are the second-order effects?
   - Who wins/loses?
   - Time horizons (immediate, 6mo, 2yr)

2. Contrarian Perspectives
   - What's the consensus view?
   - What could be wrong about it?
   - Alternative interpretations

3. Challenge Factors
   - What could go wrong with this narrative?
   - Hidden risks or assumptions
   - Metrics to watch

4. Connections
   - Related to what other topics?
   - Historical parallels
   - Cross-domain implications

Format: Clear sections, bullet points, ~500-800 words
```

**3. Delivery Format** (Telegram)
```markdown
🔍 Deep Dive: [Article Title]

📌 Key Implications
• [Point 1]
• [Point 2]

🤔 Contrarian Take
• [Alternative view]

⚠️ Challenge Factors
• [What could go wrong]

🔗 Connections
• [Related topics]

---
Source: [URL]
Analysis: Claude Sonnet 4
Cost: ~$0.15
```

**4. Auto-trigger Integration**
- When user flags with DEEP-DIVE
- Immediately call deep_dive.py
- Send results to Telegram
- Store analysis in `interests/deep-dives/YYYY-MM-DD-article-slug.md`

---

## Integration Flow

```
Morning Briefing (7 AM)
    ↓
User sees article #3 interesting
    ↓
Telegram: /flag 3 DEEP-DIVE
    ↓
1. Store interest (interests/2026-02-16-flagged.md)
2. Trigger deep_dive.py
    ↓
3. Fetch full article
4. Sonnet analyzes (~30 seconds)
5. Format results
    ↓
Deliver to Telegram
    ↓
Future briefings boost similar topics
```

---

## File Structure

```
personal-ai-agents/
├── flag_article.py          # NEW: Command handler
├── deep_dive.py             # NEW: Article analyzer
├── interests/               # NEW: Directory
│   ├── YYYY-MM-DD-flagged.md
│   └── deep-dives/
│       └── YYYY-MM-DD-article-slug.md
├── curator_rss_v2.py        # MODIFY: Add interest scoring
├── CURATOR_README.md        # UPDATE: Document features
└── requirements.txt         # UPDATE: Add dependencies
```

---

## Technical Details

### Dependencies
- OpenClaw `web_fetch` tool (already available)
- Anthropic API (Sonnet for analysis)
- Telegram bot commands (already configured)

### Cost Estimates
- Interest capture: Free (just storage)
- Deep dive: ~$0.10-0.15 per article (Sonnet)
- Budget: ~15-30 deep dives/month = $3-6/month

### Configuration
Store in `.env`:
```
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=8379221702
ANTHROPIC_API_KEY=<from keyring>
```

---

## Testing Plan

### Phase 1 Test
1. Send test briefing to Telegram
2. Run: `/flag 3 DEEP-DIVE`
3. Verify interest stored in `interests/`
4. Check next curator run boosts similar topics

### Phase 2 Test
1. Flag article triggers analysis
2. Verify web fetch works
3. Check Sonnet analysis quality
4. Confirm Telegram delivery
5. Validate storage in deep-dives/

---

## Success Metrics

**User Experience:**
- ✅ Can flag article in <5 seconds
- ✅ Deep dive delivered in <60 seconds
- ✅ Future briefings adapt to interests

**Technical:**
- ✅ No API timeouts
- ✅ Cost per analysis <$0.20
- ✅ Storage organized and searchable

**Portfolio Value:**
- ✅ Interactive AI curation demo
- ✅ Shows personalization capability
- ✅ Clear before/after improvement

---

## Next Steps (After 5 PM)

1. Create file structure
2. Build `flag_article.py`
3. Implement interest storage
4. Modify curator to read interests
5. Build `deep_dive.py`
6. Test end-to-end
7. Update documentation

**Estimated time:** 3.5-4.5 hours focused work

---

**Status:** Plan documented, ready to start after 5 PM CST

**Last updated:** 2026-02-16 13:23 CST
