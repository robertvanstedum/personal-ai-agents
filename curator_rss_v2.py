#!/usr/bin/env python3
"""
Simple RSS Curator - Fetch, score, rank geopolitics & finance feeds

VERSION 2.0 - Category-aware mechanical scoring (foundation for AI hybrid)

CHANGES from V1:
- All modes use consistent category|score structure
- Mechanical mode assigns categories based on keyword matching
- Scores normalized to 0-10 scale (was 0-200+)
- Ready for AI enhancement (Haiku pre-filter → Sonnet ranking)

MODES:
- mechanical: Keyword-based categories + scoring (FREE, always works)
- ai: Single-stage Haiku scoring (~$0.20/day = $6/month)
- ai-two-stage: Haiku pre-filter → Sonnet ranking (~$0.90/day = $27/month)
- hybrid: Blend mechanical + AI with confidence weighting (Phase 2.2, not yet implemented)

COST COMPARISON:
- mechanical: $0/month (free, keyword-based)
- ai: $6/month (single Haiku pass, good quality)
- ai-two-stage: $27/month (Haiku + Sonnet, best quality with challenge-factor scoring)

USAGE:
  python curator_rss_v2.py [options]

  Options:
    --mode=mechanical    Use keyword-based scoring (default, FREE)
    --mode=ai            Use single-stage Haiku scoring ($6/month)
    --mode=ai-two-stage  Use Haiku pre-filter + Sonnet ranking ($27/month)
    --mode=hybrid        Use blended scoring (not yet implemented)
    
    --fallback           Auto-fallback to mechanical if API fails (for cron jobs)
                         Without this flag, API errors stop execution (recommended for manual runs)
    
    --telegram           Send to Telegram after completion
    --open               Auto-open HTML in browser
  
  Examples:
    # Free mechanical mode (test/fallback)
    python curator_rss_v2.py --mode=mechanical --open
    
    # Single-stage AI (good quality, $6/month - DAILY PRODUCTION)
    python curator_rss_v2.py --mode=ai --telegram
    
    # Two-stage AI (best quality, $27/month - manual deep analysis)
    python curator_rss_v2.py --mode=ai-two-stage --telegram
    
    # AI with fallback (for cron jobs)
    python curator_rss_v2.py --mode=ai --fallback --telegram

ERROR HANDLING:
- By default, API errors STOP execution (fail fast)
- This lets you diagnose problems: out of credits? bad key? rate limit?
- Use --mode=mechanical to test everything except the API
- Use --fallback only for automated/cron runs where you want best-effort
"""

import feedparser
import requests
from datetime import datetime, timezone
from typing import List, Dict, Tuple
import time
import os
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# RSS Feed Sources
FEEDS = {
    "Geopolitical Futures": "https://geopoliticalfutures.com/feed/",
    "ZeroHedge": "https://cms.zerohedge.com/fullrss2.xml",
    "The Big Picture": "https://ritholtz.com/feed/",
    "Fed On The Economy": "https://www.stlouisfed.org/rss/page%20resources/publications/blog-entries",
    "Treasury MSPD": "https://www.treasurydirect.gov/rss/mspd.xml",
}

# Keywords for scoring (legacy mechanical mode)
KEYWORDS = [
    "gold", "sanctions", "debt", "fiscal", "geopolitical", "trade war",
    "central bank", "inflation", "russia", "ukraine", "china", "treasury",
    "fed", "powell", "rates", "recession", "currency", "dollar", "euro",
    "oil", "energy", "conflict", "policy", "tariff", "deficit", "bonds"
]

# Categories for topic diversity (used by mechanical + AI modes)
# Aligned with geopolitical/finance focus
#
# KEY DISTINCTION (technology vs geopolitics):
# - Technology: R&D, manufacturing, dual-use DEVELOPMENT, future capabilities (e.g., Anduril, AI research)
# - Geopolitics: IN USE, deployed, current operations (e.g., drones in Ukraine, active conflicts)
#
# Mechanical mode limitation: Can't distinguish "drone development" from "drone strikes" by keywords alone.
# This will be resolved in AI mode (Phase 2.1) where LLM understands context.
CATEGORIES = {
    'geo_major': ['china', 'beijing', 'xi jinping', 'russia', 'putin', 'moscow', 'europe', 'eu', 'european union', 
                  'japan', 'tokyo', 'korea', 'seoul', 'united states', 'washington', 'us policy',
                  'ukraine', 'war', 'conflict', 'military operation', 'invasion', 'strike', 'attack'],
    'geo_other': ['middle east', 'iran', 'israel', 'saudi', 'africa', 'latin america', 'brazil', 'mexico', 
                  'india', 'pakistan', 'southeast asia', 'vietnam', 'indonesia', 'turkey',
                  'terrorism', 'insurgency', 'civil war'],
    'monetary': ['gold', 'silver', 'bitcoin', 'crypto', 'currency', 'dollar', 'euro', 'yuan', 'yen',
                 'precious metal', 'commodity', 'bullion', 'forex', 'exchange rate', 'devaluation'],
    'fiscal': ['debt', 'deficit', 'treasury', 'bonds', 'fiscal', 'spending', 'budget', 'government spending',
               'national debt', 'sovereign debt', 'fiscal policy', 'austerity'],
    'technology': ['ai development', 'ai research', 'artificial intelligence', 'machine learning', 'robotics', 
                   'autonomous systems', 'defense industry', 'weapons development', 'military tech',
                   'anduril', 'palantir', 'manufacturing capacity', 'dual-use technology',
                   'hypersonic', 'quantum computing', 'semiconductor', '5g', '6g'],
    'other': []  # Catch-all for articles that don't fit above
}

# Priority when multiple categories match (highest first)
# Geo categories win over technology when both match (operational context trumps R&D)
# Monetary/fiscal are specific finance categories
CATEGORY_PRIORITY = ['geo_major', 'geo_other', 'monetary', 'fiscal', 'technology', 'other']

def fetch_feed(name: str, url: str) -> List[Dict]:
    """Fetch and parse a single RSS feed"""
    print(f"📡 Fetching {name}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; RSS Reader Bot)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        entries = []
        
        for entry in feed.entries[:50]:
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            
            entries.append({
                "source": name,
                "title": entry.get("title", "No title"),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": pub_date,
                "raw_entry": entry
            })
        
        print(f"   ✅ {len(entries)} entries from {name}")
        return entries
    
    except Exception as e:
        print(f"   ❌ Error fetching {name}: {e}")
        return []

def assign_category(entry: Dict) -> str:
    """
    Assign a category based on keyword matching (mechanical mode)
    
    Returns one of: geo_major, geo_other, monetary, fiscal, technology, other
    
    KEY DECISION: When multiple categories match (e.g., "China gold reserves"),
    we use CATEGORY_PRIORITY to pick the most specific/important one.
    Priority: technology > geo_major > monetary > fiscal > geo_other > other
    This prevents over-representation and maintains diversity.
    """
    text = f"{entry['title']} {entry['summary']}".lower()
    
    # Check which categories match
    matches = []
    for category, keywords in CATEGORIES.items():
        if category == 'other':
            continue
        if any(kw in text for kw in keywords):
            matches.append(category)
    
    # Return highest-priority match, or 'other' if none
    if matches:
        for cat in CATEGORY_PRIORITY:
            if cat in matches:
                return cat
    
    return 'other'

def normalize_score(raw_score: float, max_score: float = 200.0) -> float:
    """
    Normalize raw mechanical score to 0-10 scale
    
    Args:
        raw_score: Mechanical score (typically 0-200+)
        max_score: Expected maximum for scaling (200 = recency + keywords + weights)
    
    Returns:
        Score between 0-10 (clamped)
    
    KEY DECISION: We normalize to 0-10 so mechanical and AI scores are comparable.
    This enables hybrid mode to blend both scoring methods meaningfully.
    """
    normalized = (raw_score / max_score) * 10
    return min(10.0, max(0.0, normalized))

def score_entry_mechanical(entry: Dict) -> Dict:
    """
    Score an entry using mechanical keyword matching (V2: category-aware)
    
    Returns:
        {
            'score': float (0-10),       # Normalized score
            'category': str,              # Assigned category
            'raw_score': float,           # Original score (for debugging)
            'method': 'mechanical'        # Scoring method used
        }
    
    KEY DECISION: Return a dict instead of just a float.
    This consistent structure works across mechanical/ai/hybrid modes.
    """
    raw_score = 0.0
    
    # Recency score (decay over 7 days)
    if entry["published"]:
        age_hours = (datetime.now(timezone.utc) - entry["published"]).total_seconds() / 3600
        recency_score = max(0, 100 - (age_hours / 24) * 10)  # 10 points per day decay
        raw_score += recency_score
    
    # Keyword matching score
    text = f"{entry['title']} {entry['summary']}".lower()
    keyword_matches = sum(1 for kw in KEYWORDS if kw in text)
    raw_score += keyword_matches * 5
    
    # Source priority weights
    source_weights = {
        "Geopolitical Futures": 1.4,
        "The Big Picture": 1.2,
        "ZeroHedge": 1.1,
        "Fed On The Economy": 1.2,
        "Treasury MSPD": 1.3,
    }
    weight = source_weights.get(entry["source"], 1.0)
    raw_score *= weight
    
    # Normalize to 0-10
    normalized = normalize_score(raw_score)
    
    # Assign category
    category = assign_category(entry)
    
    return {
        'score': normalized,
        'category': category,
        'raw_score': raw_score,
        'method': 'mechanical'
    }

def get_anthropic_api_key() -> str:
    """
    Get Anthropic API key from (in priority order):
    1. macOS Keychain (via keyring)
    2. Environment variable ANTHROPIC_API_KEY
    3. .env file
    
    Returns empty string if not found.
    """
    # Try keychain first (most secure)
    try:
        import keyring
        api_key = keyring.get_password("anthropic", "api_key")
        if api_key:
            return api_key
    except Exception as e:
        pass  # Keychain not available or error
    
    # Try environment variable (from .env or shell)
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        return api_key
    
    return ""

def get_telegram_token() -> str:
    """
    Get Telegram bot token from (in priority order):
    1. macOS Keychain (via keyring)
    2. Environment variable TELEGRAM_BOT_TOKEN
    3. .env file
    
    Returns empty string if not found.
    """
    # Try keychain first (most secure)
    try:
        import keyring
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception as e:
        pass  # Keychain not available or error
    
    # Try environment variable (from .env or shell)
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if token:
        return token
    
    return ""

def score_entries_haiku(entries: List[Dict], fallback_on_error: bool = False) -> List[Dict]:
    """
    Score all entries using Haiku LLM (batch processing)
    
    Args:
        entries: List of article entries
        fallback_on_error: If True, silently fall back to mechanical on API errors.
                          If False (default), raise exception and let user decide.
    
    Returns list of dicts with 'score', 'category', 'method' = 'haiku'
    
    KEY DECISION: Single batch call to minimize API overhead.
    Haiku can handle ~140 articles easily in one prompt.
    
    ERROR HANDLING:
    - No fallback by default (fail fast, let user decide)
    - Use --fallback flag to enable automatic fallback (for cron jobs)
    """
    from anthropic import Anthropic
    
    # Get API key (keychain → env → .env)
    api_key = get_anthropic_api_key()
    if not api_key:
        error_msg = """
❌ Anthropic API key not found!

Checked:
  1. macOS Keychain (service: anthropic, account: api_key)
  2. Environment variable: ANTHROPIC_API_KEY
  3. .env file

To fix:
  1. Store key in Keychain:
     python store_api_key.py
  
  2. OR set environment variable:
     export ANTHROPIC_API_KEY='your-key-here'
  
  3. OR add to .env file:
     echo "ANTHROPIC_API_KEY=your-key-here" > .env

To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical
"""
        if fallback_on_error:
            print("⚠️  API key not found, falling back to mechanical")
            return [score_entry_mechanical(e) for e in entries]
        else:
            print(error_msg)
            raise ValueError("Anthropic API key not found")
    
    client = Anthropic(api_key=api_key)
    
    # Build prompt with all articles
    prompt = """You are a geopolitics & finance curator. For each article below, assign:
1. Category (ONE of: geo_major, geo_other, monetary, fiscal, technology, other)
2. Score (0-10): relevance for a geopolitics/finance professional

CATEGORIES:
- geo_major: US, China, Russia, Europe, Japan, Korea (deployed/operational context)
- geo_other: Middle East, Africa, Latin America, South/Southeast Asia
- monetary: Gold, Bitcoin, currencies, commodities, exchange rates
- fiscal: Government debt, spending, budgets, deficits
- technology: R&D, manufacturing, dual-use DEVELOPMENT (not yet deployed)
- other: Everything else

SCORE GUIDANCE:
9-10: Critical developments, major policy shifts, must-read analysis
7-8: Important trends, significant geopolitical/financial analysis
5-6: Relevant but not urgent, decent background
3-4: Tangential interest, minor relevance
0-2: Skip (noise, spam, off-topic, pure entertainment)

KEY DISTINCTION (technology vs geopolitics):
- If discussing DEPLOYED systems, active operations, current conflicts → geo category
- If discussing R&D, manufacturing capacity, future capabilities → technology
- Example: "Drones used in Ukraine" = geo_major, "Anduril developing new drones" = technology

OUTPUT FORMAT (one line per article, no explanation):
<article_index>|<category>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        # Include title + first 200 chars of summary for context
        summary = entry.get('summary', '')[:200].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        prompt += f"\n{i}. [{source}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT (one line per article):\n"
    
    print(f"📡 Calling Haiku to score {len(entries)} articles...")
    
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        output = response.content[0].text.strip()
        
        # Parse output: "0|geo_major|8"
        scores = {}
        for line in output.split('\n'):
            line = line.strip()
            if not line or '|' not in line:
                continue
            
            parts = line.split('|')
            if len(parts) != 3:
                continue
            
            try:
                idx = int(parts[0])
                category = parts[1].strip()
                score = float(parts[2])
                scores[idx] = {'category': category, 'score': score, 'method': 'haiku'}
            except (ValueError, IndexError):
                continue
        
        print(f"   ✅ Haiku scored {len(scores)}/{len(entries)} articles")
        
        # Build results list
        results = []
        for i, entry in enumerate(entries):
            if i in scores:
                results.append(scores[i])
            else:
                # Fallback to mechanical if Haiku didn't score this one
                print(f"   ⚠️  Article {i} not scored by Haiku, using mechanical fallback")
                results.append(score_entry_mechanical(entry))
        
        # Report token usage
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        
        # Haiku pricing: $0.80/MTok input, $4.00/MTok output (as of Dec 2024)
        cost = (input_tokens / 1_000_000 * 0.80) + (output_tokens / 1_000_000 * 4.00)
        print(f"   💰 Haiku cost: ${cost:.4f} ({input_tokens:,} in + {output_tokens:,} out tokens)")
        
        return results
        
    except Exception as e:
        # Detailed error reporting based on exception type
        error_type = type(e).__name__
        error_msg = str(e)
        
        error_report = f"""
❌ Haiku API Error: {error_type}

Details: {error_msg}

Common causes:
"""
        
        # Classify error and provide specific guidance
        if "authentication" in error_msg.lower() or "api key" in error_msg.lower():
            error_report += """
  • Invalid or expired API key
  
  Fix: Update your API key
    python store_api_key.py
"""
        elif "insufficient" in error_msg.lower() or "credit" in error_msg.lower() or "balance" in error_msg.lower():
            error_report += """
  • Insufficient credits / out of funds
  
  Fix: Add credits at https://console.anthropic.com/settings/billing
  Check balance: https://console.anthropic.com/settings/usage
"""
        elif "rate limit" in error_msg.lower():
            error_report += """
  • Rate limit exceeded
  
  Fix: Wait a few minutes and try again
  Or: Upgrade plan at https://console.anthropic.com/settings/plans
"""
        elif "timeout" in error_msg.lower() or "connection" in error_msg.lower():
            error_report += """
  • Network/connection error
  
  Fix: Check internet connection and try again
"""
        else:
            error_report += """
  • Unknown error (see details above)
  
  Check: https://console.anthropic.com/settings/keys
  Check: https://status.anthropic.com/
"""
        
        error_report += """
To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical

To enable automatic fallback (for cron jobs):
  python curator_rss_v2.py --mode=ai --fallback
"""
        
        print(error_report)
        
        if fallback_on_error:
            print("⚠️  Falling back to mechanical scoring...")
            return [score_entry_mechanical(e) for e in entries]
        else:
            raise RuntimeError(f"Haiku API failed: {error_type}: {error_msg}")

def score_entries_haiku_prefilter(entries: List[Dict], top_n: int = 50, fallback_on_error: bool = False) -> List[Dict]:
    """
    STAGE 1: Haiku Pre-Filter (150 → 50)
    Fast relevance check + basic categorization
    
    Returns top N entries with preliminary scores
    Cost: ~$0.15 per run
    """
    from anthropic import Anthropic
    
    api_key = get_anthropic_api_key()
    if not api_key:
        if fallback_on_error:
            print("⚠️  API key not found, falling back to mechanical")
            return entries  # Return all entries unfiltered
        else:
            raise ValueError("Anthropic API key not found")
    
    client = Anthropic(api_key=api_key)
    
    # Build simple relevance filter prompt
    prompt = """You are a geopolitics & finance curator doing a QUICK RELEVANCE FILTER.

For each article below, give:
1. Category (ONE of: geo_major, geo_other, monetary, fiscal, technology, other)
2. Score (0-10): Simple relevance check

CATEGORIES:
- geo_major: US, China, Russia, Europe, Japan, Korea
- geo_other: Middle East, Africa, Latin America, South/Southeast Asia
- monetary: Gold, Bitcoin, currencies, commodities
- fiscal: Government debt/spending (US, Japan, Europe)
- technology: AI in physical world (robotics, drones, warfare, autonomous systems)
- other: Everything else

SCORING (KEEP IT SIMPLE):
8-10: Clearly relevant geopolitics/finance
5-7: Probably relevant, needs closer look
3-4: Marginal relevance
0-2: Off-topic noise (entertainment, sports, local news)

OUTPUT FORMAT (one line per article):
<index>|<category>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        summary = entry.get('summary', '')[:200].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        prompt += f"\n{i}. [{source}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT:\n"
    
    print(f"📡 Stage 1: Haiku pre-filter on {len(entries)} articles...")
    
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        
        output = response.content[0].text.strip()
        
        # Parse output
        scores = {}
        for line in output.split('\n'):
            line = line.strip()
            if not line or '|' not in line:
                continue
            
            parts = line.split('|')
            if len(parts) != 3:
                continue
            
            try:
                idx = int(parts[0])
                category = parts[1].strip()
                score = float(parts[2])
                scores[idx] = {'category': category, 'score': score}
            except (ValueError, IndexError):
                continue
        
        # Assign scores to entries
        for i, entry in enumerate(entries):
            if i in scores:
                entry['category'] = scores[i]['category']
                entry['score'] = scores[i]['score']
                entry['method'] = 'haiku-prefilter'
            else:
                # Fallback for missing scores
                entry['score'] = 0.0
                entry['category'] = 'other'
                entry['method'] = 'haiku-prefilter'
        
        # Sort by score and take top N
        entries.sort(key=lambda x: x.get('score', 0), reverse=True)
        filtered = entries[:top_n]
        
        # Report costs
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens / 1_000_000 * 0.80) + (output_tokens / 1_000_000 * 4.00)
        
        print(f"   ✅ Stage 1 complete: {len(filtered)} candidates selected")
        print(f"   💰 Stage 1 cost: ${cost:.4f} ({input_tokens:,} in + {output_tokens:,} out)")
        
        return filtered
        
    except Exception as e:
        print(f"❌ Stage 1 (Haiku) failed: {e}")
        if fallback_on_error:
            print("⚠️  Falling back to mechanical")
            return entries
        else:
            raise

def score_entries_sonnet_ranking(entries: List[Dict], fallback_on_error: bool = False) -> List[Dict]:
    """
    STAGE 2: Sonnet Final Ranking (50 → 20)
    Deep quality analysis + challenge-factor scoring
    
    Re-scores entries with focus on quality and contrarian insight
    Cost: ~$0.75 per run
    """
    from anthropic import Anthropic
    
    api_key = get_anthropic_api_key()
    if not api_key:
        if fallback_on_error:
            print("⚠️  API key not found, keeping Stage 1 scores")
            return entries
        else:
            raise ValueError("Anthropic API key not found")
    
    client = Anthropic(api_key=api_key)
    
    # Build quality assessment prompt
    prompt = """You are an expert geopolitics & finance curator. These articles passed initial relevance filter. Now rank them by QUALITY and CHALLENGE-FACTOR.

For each article, give a SINGLE score (0-10) based on:

**QUALITY SIGNALS (50%):**
- Original analysis vs. news aggregation
- Data-driven insights
- Expert credibility
- Depth of understanding

**CHALLENGE-FACTOR (50%):**
- Challenges conventional wisdom
- Offers contrarian perspective
- Presents underappreciated risks/opportunities
- Connects non-obvious dots

**SCORING GUIDE:**
9-10: Must-read. Deep analysis + challenges assumptions. Changes how you think.
7-8: Strong quality. Either excellent analysis OR interesting challenge perspective.
5-6: Solid but standard. Good information, no surprises.
3-4: Mediocre. Superficial or consensus rehash.
0-2: Weak. Skip.

**EXAMPLES:**
- "Fed raises rates 0.25%" (standard news) → 4
- "Why Fed's rate policy may backfire given fiscal dominance" (challenge) → 8
- "China economy slowing" (consensus) → 5
- "China's regional bank crisis: hidden stress test for Xi's power" (contrarian depth) → 9

OUTPUT FORMAT (one line per article):
<index>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        summary = entry.get('summary', '')[:300].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        category = entry.get('category', 'other')
        prompt += f"\n{i}. [{source}] [{category}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT:\n"
    
    print(f"📡 Stage 2: Sonnet ranking on {len(entries)} candidates...")
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        
        output = response.content[0].text.strip()
        
        # Parse output
        scores = {}
        for line in output.split('\n'):
            line = line.strip()
            if not line or '|' not in line:
                continue
            
            parts = line.split('|')
            if len(parts) != 2:
                continue
            
            try:
                idx = int(parts[0])
                score = float(parts[1])
                scores[idx] = score
            except (ValueError, IndexError):
                continue
        
        # Update scores
        for i, entry in enumerate(entries):
            if i in scores:
                entry['score'] = scores[i]
                entry['method'] = 'sonnet-ranking'
            # Keep Stage 1 score if Sonnet didn't score it
        
        # Report costs
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens / 1_000_000 * 3.00) + (output_tokens / 1_000_000 * 15.00)
        
        print(f"   ✅ Stage 2 complete: {len([e for e in entries if e.get('method') == 'sonnet-ranking'])} articles ranked")
        print(f"   💰 Stage 2 cost: ${cost:.4f} ({input_tokens:,} in + {output_tokens:,} out)")
        
        return entries
        
    except Exception as e:
        print(f"❌ Stage 2 (Sonnet) failed: {e}")
        if fallback_on_error:
            print("⚠️  Keeping Stage 1 scores")
            return entries
        else:
            raise

def curate(top_n: int = 20, diversity_weight: float = 0.3, mode: str = 'mechanical', 
           fallback_on_error: bool = False) -> List[Dict]:
    """
    Fetch all feeds, score, rank, return top N
    
    Args:
        top_n: Number of articles to return
        diversity_weight: How much to penalize source over-representation (0-1)
        mode: 'mechanical', 'ai', 'ai-two-stage', or 'hybrid'
        fallback_on_error: Auto-fallback to mechanical if API fails
    
    MODES:
    - mechanical: Fast, free, keyword-based
    - ai: Single-stage Haiku scoring (~$0.20/day)
    - ai-two-stage: Haiku pre-filter + Sonnet ranking (~$0.90/day, RECOMMENDED)
    - hybrid: Blend mechanical + AI (Phase 2.2, not yet implemented)
    """
    print(f"\n🧠 Starting RSS curation (mode: {mode})...\n")
    
    if mode == 'hybrid':
        print(f"⚠️  Mode 'hybrid' not yet implemented, falling back to ai")
        mode = 'ai'
    
    all_entries = []
    
    # Fetch all feeds
    for name, url in FEEDS.items():
        entries = fetch_feed(name, url)
        all_entries.extend(entries)
        time.sleep(0.5)
    
    print(f"\n📊 Total entries fetched: {len(all_entries)}")
    
    # Score all entries using selected mode
    if mode == 'ai-two-stage':
        # TWO-STAGE: Haiku pre-filter → Sonnet ranking
        print("\n🎯 Two-stage AI curation:")
        print(f"   Stage 1: Haiku filters {len(all_entries)} → 50 candidates")
        print(f"   Stage 2: Sonnet ranks 50 → top {top_n}")
        print()
        
        # Stage 1: Haiku pre-filter (150 → 50)
        candidates = score_entries_haiku_prefilter(all_entries, top_n=50, fallback_on_error=fallback_on_error)
        
        # Stage 2: Sonnet ranking (50 → scored)
        all_entries = score_entries_sonnet_ranking(candidates, fallback_on_error=fallback_on_error)
        
    elif mode == 'ai':
        # Single-stage Haiku scoring (original implementation)
        results = score_entries_haiku(all_entries, fallback_on_error=fallback_on_error)
        for i, entry in enumerate(all_entries):
            entry["score"] = results[i]['score']
            entry["category"] = results[i]['category']
            entry["method"] = results[i]['method']
            entry["raw_score"] = results[i].get('raw_score', entry["score"])  # AI doesn't have raw_score
    else:
        # Mechanical scoring (one by one)
        for entry in all_entries:
            result = score_entry_mechanical(entry)
            entry["score"] = result['score']
            entry["category"] = result['category']
            entry["raw_score"] = result['raw_score']
            entry["method"] = result['method']
    
    # Apply diversity-aware selection
    selected = []
    source_counts = {}
    category_counts = {}  # NEW: Track category distribution
    candidates = all_entries.copy()
    
    while len(selected) < top_n and candidates:
        # Recalculate final scores based on current distribution
        for entry in candidates:
            source = entry["source"]
            category = entry.get("category", "other")
            
            source_count = source_counts.get(source, 0)
            category_count = category_counts.get(category, 0)
            
            # Source diversity penalty (existing logic)
            source_penalty = (source_count ** 2) * 30 * diversity_weight
            
            # Category diversity penalty (NEW: avoid topic echo chambers)
            # Less aggressive than source penalty (we want some depth per topic)
            category_penalty = (category_count ** 2) * 15 * diversity_weight
            
            entry["final_score"] = entry["score"] - source_penalty - category_penalty
        
        # Pick the highest-scoring candidate
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        best = candidates.pop(0)
        
        selected.append(best)
        
        source = best["source"]
        category = best.get("category", "other")
        
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    
    # Report distributions
    print("\n📊 Source distribution in top 20:")
    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"   {source}: {count} articles")
    
    print("\n🏷️  Category distribution in top 20:")
    for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"   {category}: {count} articles")
    
    return selected

def format_output(entries: List[Dict]) -> str:
    """Format ranked entries for display (now shows categories)"""
    output = "\n" + "="*80 + "\n"
    output += f"TOP {len(entries)} CURATED ARTICLES (Category + Diversity Weighted)\n"
    output += "="*80 + "\n\n"
    
    for i, entry in enumerate(entries, 1):
        pub_str = entry["published"].strftime("%Y-%m-%d %H:%M UTC") if entry["published"] else "Unknown date"
        
        category = entry.get("category", "other")
        method = entry.get("method", "unknown")
        raw = entry.get("raw_score", 0)
        final = entry.get("final_score", entry["score"])
        
        output += f"#{i} [{entry['source']}] 🏷️  {category} ({method})\n"
        output += f"   {entry['title']}\n"
        output += f"   {entry['link']}\n"
        output += f"   Published: {pub_str}\n"
        output += f"   Scores: {entry['score']:.1f}/10 (raw: {raw:.1f}, final: {final:.1f})\n"
        
        if entry['summary']:
            summary = entry['summary'][:150].replace("\n", " ")
            output += f"   {summary}...\n"
        
        output += "\n"
    
    return output

def format_telegram(entries: List[Dict]) -> str:
    """Format for Telegram delivery (with clickable links + categories)"""
    from datetime import datetime
    
    output = f"🧠 *Your Morning Briefing* - {datetime.now().strftime('%b %d, %Y')}\n\n"
    output += f"📊 {len(entries)} curated articles\n"
    output += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, entry in enumerate(entries, 1):
        # Time ago string
        if entry["published"]:
            age_hours = (datetime.now(timezone.utc) - entry["published"]).total_seconds() / 3600
            if age_hours < 1:
                time_str = f"{int(age_hours * 60)}m ago"
            elif age_hours < 24:
                time_str = f"{int(age_hours)}h ago"
            else:
                time_str = f"{int(age_hours / 24)}d ago"
        else:
            time_str = "unknown"
        
        category = entry.get("category", "other")
        category_emoji = {
            'geo_major': '🌍',
            'geo_other': '🗺️',
            'monetary': '🪙',
            'fiscal': '💸',
            'technology': '🤖',
            'other': '📰'
        }.get(category, '📰')
        
        # Telegram markdown format
        output += f"*#{i}* {category_emoji} [{entry['source']}] _{time_str}_\n"
        output += f"{entry['title']}\n"
        output += f"🔗 {entry['link']}\n\n"
    
    return output

def format_html(entries: List[Dict]) -> str:
    """Format as HTML with clickable links and category badges"""
    from datetime import datetime
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Morning Briefing - {datetime.now().strftime('%b %d, %Y')}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 900px;
            margin: 40px auto;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }}
        .header {{
            text-align: center;
            margin-bottom: 40px;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 2em;
        }}
        .header p {{
            margin: 10px 0 0 0;
            opacity: 0.9;
        }}
        .article {{
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .article:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }}
        .article-number {{
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 4px 10px;
            border-radius: 4px;
            font-weight: bold;
            margin-right: 10px;
        }}
        .article-source {{
            display: inline-block;
            background: #f0f0f0;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            margin-right: 10px;
        }}
        .category-badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: 600;
            margin-right: 10px;
        }}
        .cat-geo_major {{ background: #ffe5e5; color: #c00; }}
        .cat-geo_other {{ background: #fff0e5; color: #c60; }}
        .cat-monetary {{ background: #fff8e5; color: #c90; }}
        .cat-fiscal {{ background: #f0e5ff; color: #90c; }}
        .cat-technology {{ background: #e5f0ff; color: #05c; }}
        .cat-other {{ background: #f0f0f0; color: #666; }}
        .article-time {{
            color: #888;
            font-size: 0.85em;
        }}
        .article-title {{
            font-size: 1.2em;
            font-weight: 600;
            margin: 10px 0;
            color: #333;
        }}
        .article-title a {{
            color: #333;
            text-decoration: none;
        }}
        .article-title a:hover {{
            color: #667eea;
        }}
        .article-link {{
            display: inline-block;
            margin-top: 10px;
            color: #667eea;
            text-decoration: none;
            font-size: 0.9em;
        }}
        .article-link:hover {{
            text-decoration: underline;
        }}
        .article-meta {{
            margin-top: 10px;
            font-size: 0.85em;
            color: #666;
        }}
        .score {{
            color: #888;
            font-size: 0.8em;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 Your Morning Briefing</h1>
        <p>{datetime.now().strftime('%A, %B %d, %Y')}</p>
        <p>📊 {len(entries)} curated articles • Category-aware curation</p>
    </div>
"""
    
    for i, entry in enumerate(entries, 1):
        # Time ago string
        if entry["published"]:
            age_hours = (datetime.now(timezone.utc) - entry["published"]).total_seconds() / 3600
            if age_hours < 1:
                time_str = f"{int(age_hours * 60)}m ago"
            elif age_hours < 24:
                time_str = f"{int(age_hours)}h ago"
            else:
                time_str = f"{int(age_hours / 24)}d ago"
        else:
            time_str = "unknown"
        
        # Category styling
        category = entry.get("category", "other")
        cat_class = f"cat-{category}"
        
        # Scores
        score = entry.get("score", 0)
        raw_score = entry.get("raw_score", 0)
        final_score = entry.get("final_score", score)
        method = entry.get("method", "unknown")
        
        html += f"""
    <div class="article">
        <div>
            <span class="article-number">#{i}</span>
            <span class="category-badge {cat_class}">{category.upper()}</span>
            <span class="article-source">{entry['source']}</span>
            <span class="article-time">{time_str}</span>
        </div>
        <div class="article-title">
            <a href="{entry['link']}" target="_blank">{entry['title']}</a>
        </div>
        <a href="{entry['link']}" class="article-link" target="_blank">🔗 Read article →</a>
        <div class="article-meta">
            <span class="score">Score: {score:.1f}/10 ({method}) | Raw: {raw_score:.1f} | Final: {final_score:.1f}</span>
        </div>
    </div>
"""
    
    html += """
</body>
</html>
"""
    return html

def generate_index_page(archive_dir: str):
    """Generate an index.html page listing all archived briefings"""
    import os
    from datetime import datetime
    
    archive_files = []
    if os.path.exists(archive_dir):
        for filename in os.listdir(archive_dir):
            if filename.startswith("curator_") and filename.endswith(".html"):
                date_str = filename.replace("curator_", "").replace(".html", "")
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    archive_files.append((date_str, filename, date_obj))
                except ValueError:
                    pass
    
    archive_files.sort(key=lambda x: x[2], reverse=True)
    
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Curator Archive</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
            background: #f5f5f5;
        }
        .header {
            text-align: center;
            margin-bottom: 40px;
            padding: 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
        }
        .header h1 {
            margin: 0;
            font-size: 2.5em;
        }
        .header p {
            margin: 10px 0 0 0;
            opacity: 0.9;
        }
        .quick-links {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 30px;
        }
        .quick-links a {
            padding: 10px 20px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 500;
        }
        .quick-links a:hover {
            background: #5568d3;
        }
        .archive-list {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .archive-item {
            padding: 15px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .archive-item:last-child {
            border-bottom: none;
        }
        .archive-item:hover {
            background: #f8f8f8;
        }
        .archive-date {
            font-weight: 600;
            color: #333;
        }
        .archive-link {
            color: #667eea;
            text-decoration: none;
            font-weight: 500;
        }
        .archive-link:hover {
            text-decoration: underline;
        }
        .stats {
            text-align: center;
            color: #666;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📚 Curator Archive</h1>
        <p>Your daily geopolitics & finance briefings</p>
    </div>
    
    <div class="quick-links">
        <a href="curator_latest.html">📰 Today's Briefing</a>
    </div>
    
    <div class="archive-list">
        <h2>Past Briefings</h2>
"""
    
    if archive_files:
        for date_str, filename, date_obj in archive_files:
            formatted_date = date_obj.strftime("%B %d, %Y")
            day_of_week = date_obj.strftime("%A")
            html += f"""
        <div class="archive-item">
            <div>
                <span class="archive-date">{formatted_date}</span>
                <span style="color: #999; margin-left: 10px;">{day_of_week}</span>
            </div>
            <a href="{archive_dir}/{filename}" class="archive-link">View Briefing →</a>
        </div>
"""
    else:
        html += """
        <p style="text-align: center; color: #999; padding: 40px 0;">
            No archived briefings yet. Check back after your first automated run!
        </p>
"""
    
    html += f"""
    </div>
    
    <div class="stats">
        <p>{len(archive_files)} briefing(s) archived</p>
    </div>
</body>
</html>
"""
    
    with open("curator_index.html", "w") as f:
        f.write(html)
    print(f"📑 Index page updated: curator_index.html")

def main():
    """Run the curator and display results"""
    import sys
    import subprocess
    
    # Parse command line args
    send_telegram = "--telegram" in sys.argv
    auto_open = "--open" in sys.argv
    fallback_on_error = "--fallback" in sys.argv
    
    # Mode selection (default: mechanical)
    mode = 'mechanical'
    for arg in sys.argv:
        if arg.startswith('--mode='):
            mode = arg.split('=')[1]
            break
    
    # Run curation
    try:
        top_articles = curate(top_n=20, mode=mode, fallback_on_error=fallback_on_error)
    except (ValueError, RuntimeError) as e:
        # API error with no fallback - exit with error
        print(f"\n💥 Curation failed: {e}")
        print("\nTip: Run with --mode=mechanical to test everything except API")
        sys.exit(1)
    
    # Console output
    output = format_output(top_articles)
    print(output)
    
    # Save to file
    output_file = "curator_output.txt"
    with open(output_file, "w") as f:
        f.write(output)
    print(f"💾 Results saved to {output_file}")
    
    # HTML generation
    html_content = format_html(top_articles)
    
    # Create dated archive
    import os
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    archive_dir = "curator_archive"
    os.makedirs(archive_dir, exist_ok=True)
    
    archive_path = os.path.join(archive_dir, f"curator_{today}.html")
    with open(archive_path, "w") as f:
        f.write(html_content)
    print(f"📁 Archive saved to {archive_path}")
    
    # Save as "latest"
    latest_file = "curator_latest.html"
    with open(latest_file, "w") as f:
        f.write(html_content)
    print(f"🔖 Latest briefing: {latest_file}")
    
    # Backward compatibility
    with open("curator_briefing.html", "w") as f:
        f.write(html_content)
    
    # Generate index
    generate_index_page(archive_dir)
    
    # Auto-open
    if auto_open:
        try:
            subprocess.run(["open", latest_file], check=True)
            print(f"✅ Opened {latest_file} in browser")
        except Exception as e:
            print(f"⚠️  Could not auto-open: {e}")
    
    # Telegram delivery
    if send_telegram:
        telegram_msg = format_telegram(top_articles)
        
        # Save to file (for cron compatibility)
        with open("telegram_message.txt", "w") as f:
            f.write(telegram_msg)
        print(f"📱 Telegram message saved to telegram_message.txt")
        
        # Actually send to Telegram
        telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID', '8379221702')
        
        # Get bot token (keychain → env → .env)
        telegram_token = get_telegram_token()
        
        if telegram_token:
            try:
                # Telegram API max message length
                max_len = 4000
                
                if len(telegram_msg) > max_len:
                    # Split into chunks
                    chunks = []
                    lines = telegram_msg.split('\n')
                    current_chunk = ""
                    
                    for line in lines:
                        if len(current_chunk) + len(line) + 1 > max_len:
                            chunks.append(current_chunk)
                            current_chunk = line + "\n"
                        else:
                            current_chunk += line + "\n"
                    
                    if current_chunk:
                        chunks.append(current_chunk)
                    
                    # Send each chunk
                    for i, chunk in enumerate(chunks, 1):
                        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                        data = {
                            "chat_id": telegram_chat_id,
                            "text": chunk,
                            "parse_mode": "Markdown",
                            "disable_web_page_preview": True
                        }
                        response = requests.post(url, json=data, timeout=10)
                        response.raise_for_status()
                        print(f"📱 Sent Telegram message part {i}/{len(chunks)}")
                        time.sleep(1)  # Avoid rate limits
                else:
                    # Send single message
                    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                    data = {
                        "chat_id": telegram_chat_id,
                        "text": telegram_msg,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True
                    }
                    response = requests.post(url, json=data, timeout=10)
                    response.raise_for_status()
                    print(f"📱 ✅ Sent to Telegram chat {telegram_chat_id}")
                    
            except Exception as e:
                print(f"⚠️  Failed to send Telegram message: {e}")
                print(f"   Message saved to telegram_message.txt")
        else:
            print(f"⚠️  TELEGRAM_BOT_TOKEN not set, message saved to file only")
            print(f"   To enable auto-send: export TELEGRAM_BOT_TOKEN='your-token'")

if __name__ == "__main__":
    main()
