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
import hashlib
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# RSS Feed Sources
FEEDS = {
    # Original sources
    "Geopolitical Futures": "https://geopoliticalfutures.com/feed/",
    "ZeroHedge": "https://cms.zerohedge.com/fullrss2.xml",
    "The Big Picture": "https://ritholtz.com/feed/",
    "Fed On The Economy": "https://www.stlouisfed.org/rss/page%20resources/publications/blog-entries",
    "Treasury MSPD": "https://www.treasurydirect.gov/rss/mspd.xml",
    
    # NEW - Feb 18, 2026: Expanded source diversity
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",  # Non-Western perspective
    "ProPublica": "https://www.propublica.org/feeds/propublica/main",  # Investigative journalism
    "Antiwar.com": "https://news.antiwar.com/feed/",  # Contrarian/realist geopolitics
    "Investing.com": "https://www.investing.com/rss/news.rss",  # Finance/markets
    "The Duran": "https://theduran.com/feed/",  # Contrarian analysis
    "O Globo": "https://oglobo.globo.com/rss.xml",  # Brazilian/Portuguese perspective
    
    # German sources (Feb 18, 2026)
    "Deutsche Welle": "https://rss.dw.com/xml/rss-en-all",  # Public broadcaster, English, no paywall
    "Spiegel International": "https://www.spiegel.de/international/index.rss",  # English, partial paywall
    "FAZ": "https://www.faz.net/rss/aktuell/",  # German, partial paywall
    "Die Welt": "https://www.welt.de/feeds/latest.rss",  # German, partial paywall
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
            
            # Generate stable hash ID from URL
            url = entry.get("link", "")
            hash_id = hashlib.md5(url.encode('utf-8')).hexdigest()[:5] if url else None
            
            entries.append({
                "hash_id": hash_id,  # Stable ID for history tracking
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

def send_telegram_alert(message: str, chat_id: str = None) -> bool:
    """
    Send error alert via Telegram
    
    Args:
        message: Alert message to send
        chat_id: Telegram chat ID (defaults to env var)
    
    Returns:
        True if sent successfully, False otherwise
    """
    if chat_id is None:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '8379221702')
    
    token = get_telegram_token()
    if not token:
        print("⚠️  No Telegram token found, skipping alert")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        print("✅ Telegram alert sent")
        return True
    except Exception as e:
        print(f"⚠️  Failed to send Telegram alert: {e}")
        return False

def log_error(error_type: str, error_msg: str, context: str = ""):
    """
    Log error to file with timestamp
    
    Args:
        error_type: Type of error (e.g., "APIError", "BillingError")
        error_msg: Error message
        context: Additional context (e.g., "Haiku API call")
    """
    log_file = "curator_errors.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_entry = f"[{timestamp}] {error_type}"
    if context:
        log_entry += f" ({context})"
    log_entry += f": {error_msg}\n"
    
    try:
        with open(log_file, "a") as f:
            f.write(log_entry)
        print(f"📝 Error logged to {log_file}")
    except Exception as e:
        print(f"⚠️  Could not write to log file: {e}")

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
        
        # Log error to file
        log_error(error_type, error_msg, context="Haiku API call")
        
        error_report = f"""
❌ Haiku API Error: {error_type}

Details: {error_msg}

Common causes:
"""
        
        # Detect billing issues
        is_billing_error = False
        telegram_alert = None
        
        # Classify error and provide specific guidance
        if "authentication" in error_msg.lower() or "api key" in error_msg.lower():
            error_report += """
  • Invalid or expired API key
  
  Fix: Update your API key
    python store_api_key.py
"""
            telegram_alert = "🔴 <b>Curator API Error</b>\n\nAuthentication failed - API key invalid or expired.\n\nFix: Update API key via store_api_key.py"
            
        elif "insufficient" in error_msg.lower() or "credit" in error_msg.lower() or "balance" in error_msg.lower() or "overloaded" in error_msg.lower():
            error_report += """
  • Insufficient credits / out of funds
  
  Fix: Add credits at https://console.anthropic.com/settings/billing
  Check balance: https://console.anthropic.com/settings/usage
"""
            is_billing_error = True
            telegram_alert = f"🔴 <b>Curator Billing Alert</b>\n\n⚠️ Out of Anthropic credits!\n\nError: {error_type}\n\n🔗 Add credits: https://console.anthropic.com/settings/billing\n\n📊 Check usage: https://console.anthropic.com/settings/usage"
            
        elif "rate limit" in error_msg.lower():
            error_report += """
  • Rate limit exceeded
  
  Fix: Wait a few minutes and try again
  Or: Upgrade plan at https://console.anthropic.com/settings/plans
"""
            telegram_alert = f"⚠️ <b>Curator Rate Limited</b>\n\nRate limit exceeded. Will retry later.\n\nError: {error_type}"
            
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
            telegram_alert = f"⚠️ <b>Curator Error</b>\n\n{error_type}: {error_msg[:200]}"
        
        error_report += """
To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical

To enable automatic fallback (for cron jobs):
  python curator_rss_v2.py --mode=ai --fallback
"""
        
        print(error_report)
        
        # Send Telegram alert for critical errors
        if telegram_alert:
            send_telegram_alert(telegram_alert)
        
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


def score_entries_xai(entries: List[Dict], fallback_on_error: bool = False) -> List[Dict]:
    """
    Score all entries using xAI Grok (batch processing)
    
    Args:
        entries: List of article entries
        fallback_on_error: If True, silently fall back to mechanical on API errors
    
    Returns list of dicts with 'score', 'category', 'method' = 'xai'
    """
    from openai import OpenAI
    import json
    
    # Get xAI API key from auth profiles
    api_key = None
    try:
        with open(os.path.expanduser('~/.openclaw/agents/main/agent/auth-profiles.json'), 'r') as f:
            config = json.load(f)
            api_key = config['profiles']['xai:default']['key']
    except Exception as e:
        error_msg = f"""
❌ xAI API key not found in OpenClaw auth profiles!

Error: {e}

To fix:
  Check ~/.openclaw/agents/main/agent/auth-profiles.json
  Ensure 'xai:default' profile exists with valid key

To test with mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical
"""
        if fallback_on_error:
            print("⚠️  xAI API key not found, falling back to mechanical")
            return [score_entry_mechanical(e) for e in entries]
        else:
            print(error_msg)
            raise ValueError("xAI API key not found")
    
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    
    # Build prompt with all articles (same format as Haiku)
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

OUTPUT FORMAT (one line per article, no explanation):
<article_index>|<category>|<score>

ARTICLES:
"""
    
    for i, entry in enumerate(entries):
        summary = entry.get('summary', '')[:200].replace('\n', ' ')
        source = entry.get('source', 'Unknown')
        prompt += f"\n{i}. [{source}] {entry['title']}\n   {summary}...\n"
    
    prompt += "\nOUTPUT (one line per article):\n"
    
    print(f"📡 Calling xAI Grok to score {len(entries)} articles...")
    
    try:
        response = client.chat.completions.create(
            model="grok-2-vision-1212",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0
        )
        
        content = response.choices[0].message.content
        usage = response.usage
        print(f"   Input: {usage.prompt_tokens} tokens, Output: {usage.completion_tokens} tokens")
        cost = usage.prompt_tokens * 5 / 1_000_000 + usage.completion_tokens * 15 / 1_000_000
        print(f"   Cost: ${cost:.4f}")
        
    except Exception as e:
        error_msg = f"""
❌ xAI API call failed!

Error: {e}

To use mechanical mode instead:
  python curator_rss_v2.py --mode=mechanical
"""
        if fallback_on_error:
            print(f"⚠️  xAI API error: {e}")
            print("   Falling back to mechanical scoring...")
            return [score_entry_mechanical(e) for e in entries]
        else:
            print(error_msg)
            raise
    
    # Parse response
    results = []
    lines = content.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or '|' not in line:
            continue
            
        try:
            parts = line.split('|')
            idx = int(parts[0].strip())
            category = parts[1].strip().lower()
            score = float(parts[2].strip())
            
            results.append({
                'score': score,
                'category': category,
                'method': 'xai',
                'raw_score': score
            })
        except (ValueError, IndexError) as e:
            print(f"⚠️  Skipping malformed line: {line}")
            results.append({
                'score': 5.0,
                'category': 'other',
                'method': 'xai',
                'raw_score': 5.0
            })
    
    # Ensure we have results for all entries
    while len(results) < len(entries):
        results.append({
            'score': 5.0,
            'category': 'other',
            'method': 'xai',
            'raw_score': 5.0
        })
    
    return results[:len(entries)]

def load_active_interests():
    """
    Load all active (non-expired) interests from interests/ directory.
    Returns dict mapping categories/keywords to score modifiers.
    """
    import re
    from pathlib import Path
    from datetime import datetime
    
    interests_dir = Path(__file__).parent / "interests"
    
    if not interests_dir.exists():
        return {}
    
    active_interests = {}
    today = datetime.now()
    
    # Read all flagged files
    for interest_file in interests_dir.glob("*-flagged.md"):
        try:
            with open(interest_file, 'r') as f:
                file_content = f.read()
            
            # Parse each flagged article
            pattern = r'\#\# \[([^\]]+)\] (.+?)\n- \*\*URL:\*\* (.+?)\n- \*\*Source:\*\* (.+?)\n- \*\*Category:\*\* (.+?)\n.*?- \*\*Expires:\*\* (.+?)\n- \*\*Score Modifier:\*\* ([+-]\d+)'
            
            for match in re.finditer(pattern, file_content, re.DOTALL):
                priority = match.group(1)
                title = match.group(2)
                category = match.group(5)
                expires_str = match.group(6)
                score_modifier = int(match.group(7))
                
                # Check if expired
                if expires_str != "No expiry":
                    try:
                        expiry_date = datetime.strptime(expires_str, '%Y-%m-%d')
                        if expiry_date < today:
                            continue  # Skip expired
                    except:
                        pass
                
                # Store by category (primary match)
                if category not in active_interests:
                    active_interests[category] = []
                
                active_interests[category].append({
                    'priority': priority,
                    'title': title,
                    'modifier': score_modifier
                })
        
        except Exception as e:
            print(f"⚠️  Error loading interests from {interest_file}: {e}")
    
    return active_interests


def apply_interest_boost(entry: Dict, active_interests: Dict) -> int:
    """
    Apply score boost/penalty based on active interests.
    Returns total modifier to add to entry score.
    """
    total_modifier = 0
    
    # Match by category (strongest signal)
    category = entry.get('category', 'other')
    if category in active_interests:
        for interest in active_interests[category]:
            total_modifier += interest['modifier']
    
    return total_modifier


def save_to_history(entries: List[Dict], output_dir: str = "."):
    """Save articles to history index and cache (with duplicate protection)"""
    from pathlib import Path
    
    # Create directories
    cache_dir = Path(output_dir) / "curator_cache"
    cache_dir.mkdir(exist_ok=True)
    
    # Load existing history
    history_file = Path(output_dir) / "curator_history.json"
    history = {}
    if history_file.exists():
        with open(history_file, 'r') as f:
            history = json.load(f)
    
    # Process each article
    today = datetime.now().strftime("%Y-%m-%d")
    
    for rank, entry in enumerate(entries, 1):
        hash_id = entry.get('hash_id')
        if not hash_id:
            continue
            
        # Add/update in history index
        if hash_id not in history:
            history[hash_id] = {
                "hash_id": hash_id,
                "first_seen": today,
                "title": entry["title"],
                "source": entry["source"],
                "url": entry["link"],
                "appearances": []
            }
        
        # Record this appearance (prevent same-day duplicates)
        existing_today = [a for a in history[hash_id]["appearances"] if a["date"] == today]
        if not existing_today:
            history[hash_id]["appearances"].append({
                "date": today,
                "rank": rank,
                "score": entry["score"]
            })
        else:
            # Update existing entry (in case scores changed)
            for appearance in history[hash_id]["appearances"]:
                if appearance["date"] == today:
                    appearance["rank"] = rank
                    appearance["score"] = entry["score"]
                    break
        
        # Save full article to cache
        cache_file = cache_dir / f"{hash_id}.json"
        with open(cache_file, 'w') as f:
            json.dump({
                "hash_id": hash_id,
                "title": entry["title"],
                "source": entry["source"],
                "url": entry["link"],
                "summary": entry["summary"],
                "published": entry["published"].isoformat() if entry["published"] else None,
                "category": entry.get("category"),
                "score": entry["score"],
                "cached_date": today
            }, f, indent=2)
    
    # Save updated history
    with open(history_file, 'w') as f:
        json.dump(history, f, indent=2)
    
    print(f"💾 History updated: {len(entries)} articles saved")


def curate(top_n: int = 20, diversity_weight: float = 0.3, mode: str = 'mechanical', 
           fallback_on_error: bool = False) -> List[Dict]:
    """
    Fetch all feeds, score, rank, return top N
    
    Args:
        top_n: Number of articles to return
        diversity_weight: How much to penalize source over-representation (0-1)
        mode: 'mechanical', 'ai', 'ai-two-stage', 'xai', or 'hybrid'
        fallback_on_error: Auto-fallback to mechanical if API fails
    
    MODES:
    - mechanical: Fast, free, keyword-based
    - ai: Single-stage Haiku scoring (~$0.20/day)
    - ai-two-stage: Haiku pre-filter + Sonnet ranking (~$0.90/day, RECOMMENDED)
    - xai: Single-stage xAI Grok scoring (~$0.15/day, cheapest LLM option)
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
    
    
    # Load active interests for score boosting
    active_interests = load_active_interests()
    if active_interests:
        total_interests = sum(len(v) for v in active_interests.values())
        print(f"📌 Loaded {total_interests} active interests from interests/ directory")
        for category, interests in active_interests.items():
            print(f"   {category}: {len(interests)} flagged articles ({sum(i['modifier'] for i in interests):+d} total boost)")
    
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
    elif mode == 'xai':
        # Single-stage xAI Grok scoring
        results = score_entries_xai(all_entries, fallback_on_error=fallback_on_error)
        for i, entry in enumerate(all_entries):
            entry["score"] = results[i]['score']
            entry["category"] = results[i]['category']
            entry["method"] = results[i]['method']
            entry["raw_score"] = results[i].get('raw_score', entry["score"])
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
            
            # Apply interest-based boosting
            interest_boost = apply_interest_boost(entry, active_interests)
            entry["final_score"] = entry["score"] - source_penalty - category_penalty + interest_boost
            if interest_boost != 0:
                entry["interest_boosted"] = True
                entry["interest_modifier"] = interest_boost
        
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
    """Format as table HTML (unified briefing platform style)"""
    from datetime import datetime
    
    today = datetime.now()
    date_str = today.strftime('%B %d, %Y')
    day_str = today.strftime('%A')
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Morning Briefing - {date_str}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            font-size: 14px;
            max-width: 1400px;
            margin: 15px auto;
            padding: 12px;
            background: #f5f5f5;
            color: #333;
        }}
        .header {{
            margin-bottom: 15px;
            padding: 12px 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 5px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0 0 6px 0;
            font-size: 1.5em;
            font-weight: 600;
        }}
        .header-meta {{
            opacity: 0.9;
            font-size: 0.88em;
            margin: 0 8px;
            display: inline-block;
        }}
        .nav-buttons {{
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 15px;
        }}
        .nav-btn {{
            padding: 6px 14px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 500;
        }}
        .nav-btn:hover {{
            background: #5568d3;
        }}
        .briefing-table {{
            background: white;
            border-radius: 5px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        thead {{
            background: #f8f9fa;
            border-bottom: 2px solid #ddd;
        }}
        th {{
            padding: 11px 12px;
            text-align: left;
            font-weight: 600;
            font-size: 0.9em;
            color: #495057;
            white-space: nowrap;
        }}
        td {{
            padding: 12px;
            border-bottom: 1px solid #ddd;
            vertical-align: middle;
        }}
        tbody tr:nth-child(even) {{
            background: #fafafa;
        }}
        tbody tr:last-child td {{
            border-bottom: none;
        }}
        tbody tr:hover {{
            background: #f0f4ff;
        }}
        .col-rank {{
            width: 40px;
            text-align: center;
        }}
        .col-category {{
            width: 110px;
        }}
        .col-source {{
            width: 140px;
        }}
        .col-title {{
            min-width: 300px;
        }}
        .col-time {{
            width: 80px;
        }}
        .col-score {{
            width: 120px;
            text-align: right;
        }}
        .col-actions {{
            width: 110px;
            text-align: center;
            white-space: nowrap;
        }}
        .action-buttons {{
            display: flex;
            gap: 6px;
            justify-content: center;
        }}
        .action-btn {{
            padding: 4px 8px;
            border: none;
            border-radius: 3px;
            cursor: pointer;
            font-size: 0.85em;
            font-weight: 500;
            transition: all 0.2s;
        }}
        .btn-like {{
            background: #d4edda;
            color: #155724;
        }}
        .btn-like:hover {{
            background: #c3e6cb;
            transform: scale(1.05);
        }}
        .btn-dislike {{
            background: #f8d7da;
            color: #721c24;
        }}
        .btn-dislike:hover {{
            background: #f5c6cb;
            transform: scale(1.05);
        }}
        .btn-save {{
            background: #cfe2ff;
            color: #084298;
        }}
        .btn-save:hover {{
            background: #b6d4fe;
            transform: scale(1.05);
        }}
        .rank-badge {{
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 3px 9px;
            border-radius: 3px;
            font-weight: 600;
            font-size: 0.9em;
        }}
        .category-badge {{
            display: inline-block;
            padding: 4px 9px;
            border-radius: 3px;
            font-size: 0.8em;
            font-weight: 600;
            white-space: nowrap;
        }}
        .cat-geo_major {{ background: #ffe5e5; color: #c00; }}
        .cat-geo_other {{ background: #fff0e5; color: #c60; }}
        .cat-monetary {{ background: #fff8e5; color: #c90; }}
        .cat-fiscal {{ background: #f0e5ff; color: #90c; }}
        .cat-technology {{ background: #e5f0ff; color: #05c; }}
        .cat-other {{ background: #f0f0f0; color: #666; }}
        .article-title {{
            font-weight: 500;
            font-size: 1.0em;
            color: #333;
            line-height: 1.4;
        }}
        .article-title a {{
            color: #333;
            text-decoration: none;
        }}
        .article-title a:hover {{
            color: #667eea;
        }}
        .source-name {{
            color: #666;
            font-size: 0.95em;
        }}
        .time-ago {{
            color: #999;
            font-size: 0.95em;
        }}
        .score-value {{
            font-weight: 500;
            font-size: 1.05em;
            color: #667eea;
        }}
        .score-details {{
            color: #999;
            font-size: 0.85em;
            white-space: nowrap;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 Your Morning Briefing</h1>
        <div>
            <span class="header-meta">{day_str}, {date_str}</span>
            <span class="header-meta">•</span>
            <span class="header-meta">📊 {len(entries)} curated articles</span>
            <span class="header-meta">•</span>
            <span class="header-meta">Category-aware curation</span>
        </div>
    </div>

    <div class="nav-buttons">
        <a href="curator_index.html" class="nav-btn">📚 Archive</a>
        <a href="curator_latest_with_buttons.html" class="nav-btn">🔝 Top 20</a>
        <a href="interests/2026/deep-dives/index.html" class="nav-btn">🔍 Deep Dives</a>
    </div>

    <div class="briefing-table">
        <table>
            <thead>
                <tr>
                    <th class="col-rank">#</th>
                    <th class="col-category">Category</th>
                    <th class="col-source">Source</th>
                    <th class="col-title">Title</th>
                    <th class="col-time">Time</th>
                    <th class="col-score">Score</th>
                    <th class="col-actions">Actions</th>
                </tr>
            </thead>
            <tbody>
"""
    
    # Generate table rows
    for i, entry in enumerate(entries, 1):
        rank = i
        category = entry.get('category', 'other').upper()
        source = entry.get('source', 'Unknown')
        title = entry.get('title', 'Untitled')
        url = entry.get('link', '#')
        published = entry.get('published', '')
        score = entry.get('final_score', 0)
        raw_score = entry.get('raw_score', 0)
        
        # Calculate time ago
        time_ago = "N/A"
        if published:
            try:
                from datetime import datetime, timezone
                import dateutil.parser
                pub_dt = dateutil.parser.parse(published)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                diff = now - pub_dt
                hours = diff.total_seconds() / 3600
                if hours < 1:
                    time_ago = f"{int(diff.total_seconds() / 60)}m ago"
                elif hours < 24:
                    time_ago = f"{int(hours)}h ago"
                else:
                    time_ago = f"{int(hours / 24)}d ago"
            except:
                pass
        
        html += f"""                <tr>
                    <td class="col-rank"><span class="rank-badge">{rank}</span></td>
                    <td class="col-category"><span class="category-badge cat-{category.lower()}">{category}</span></td>
                    <td class="col-source"><span class="source-name">{source}</span></td>
                    <td class="col-title">
                        <div class="article-title">
                            <a href="{url}" target="_blank">{title}</a>
                        </div>
                    </td>
                    <td class="col-time"><span class="time-ago">{time_ago}</span></td>
                    <td class="col-score">
                        <div class="score-value">{score:.1f}</div>
                        <div class="score-details">{raw_score:.1f} → {score:.1f}</div>
                    </td>
                    <td class="col-actions">
                        <div class="action-buttons">
                            <button class="action-btn btn-like" title="Like this article" onclick="showFeedback('like', {rank});">👍</button>
                            <button class="action-btn btn-dislike" title="Dislike this article" onclick="showFeedback('dislike', {rank});">👎</button>
                            <button class="action-btn btn-save" title="Save for deep dive" onclick="showFeedback('save', {rank});">💾</button>
                        </div>
                    </td>
                </tr>
"""
    
    html += """            </tbody>
        </table>
    </div>
    
    <script>
    function showFeedback(action, rank) {
        // Send to feedback server
        fetch(`http://localhost:8765/feedback?action=${action}&rank=${rank}`)
            .then(response => response.json())
            .then(data => {
                // Create subtle toast notification
                const toast = document.createElement('div');
                toast.textContent = data.message || `Article #${rank} ${action}d`;
                toast.style.cssText = `
                    position: fixed;
                    top: 20px;
                    right: 20px;
                    background: #4f46e5;
                    color: white;
                    padding: 12px 20px;
                    border-radius: 8px;
                    font-weight: 500;
                    font-size: 14px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                    z-index: 9999;
                    opacity: 0;
                    transform: translateX(100%);
                    transition: all 0.3s ease;
                `;
                
                document.body.appendChild(toast);
                
                // Animate in
                setTimeout(() => {
                    toast.style.opacity = '1';
                    toast.style.transform = 'translateX(0)';
                }, 100);
                
                // Animate out after 2 seconds
                setTimeout(() => {
                    toast.style.opacity = '0';
                    toast.style.transform = 'translateX(100%)';
                    setTimeout(() => document.body.removeChild(toast), 300);
                }, 2000);
                
                // If liked or saved, add deep dive button
                if (action === 'like' || action === 'save') {
                    addDeepDiveButton(rank);
                }
            })
            .catch(error => {
                console.error('Feedback error:', error);
                // Show fallback toast
                const toast = document.createElement('div');
                toast.textContent = 'Feedback server not running. Start: python curator_server.py';
                toast.style.cssText = `
                    position: fixed;
                    top: 20px;
                    right: 20px;
                    background: #ef4444;
                    color: white;
                    padding: 12px 20px;
                    border-radius: 8px;
                    font-weight: 500;
                    font-size: 14px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                    z-index: 9999;
                `;
                document.body.appendChild(toast);
                setTimeout(() => document.body.removeChild(toast), 4000);
            });
    }
    
    function addDeepDiveButton(rank) {
        // Find the action buttons container for this rank
        const rows = document.querySelectorAll('tbody tr');
        for (const row of rows) {
            const rankBadge = row.querySelector('.rank-badge');
            if (rankBadge && rankBadge.textContent === rank.toString()) {
                const actionButtons = row.querySelector('.action-buttons');
                
                // Check if deep dive button already exists
                if (actionButtons.querySelector('.btn-dive')) {
                    return;
                }
                
                // Create deep dive button
                const diveBtn = document.createElement('button');
                diveBtn.className = 'action-btn btn-dive';
                diveBtn.textContent = '🔖 Deep Dive';
                diveBtn.title = 'Request deep dive analysis (~30s, costs ~$0.15)';
                diveBtn.style.cssText = `
                    background: #f39c12;
                    color: white;
                `;
                diveBtn.onclick = () => {
                    // Disable button and show loading
                    diveBtn.disabled = true;
                    diveBtn.textContent = '⏳ Analyzing...';
                    diveBtn.style.opacity = '0.6';
                    
                    // Trigger deep dive
                    fetch(\`http://localhost:8765/deepdive?rank=\${rank}\`)
                        .then(response => response.json())
                        .then(data => {
                            if (data.success && data.html_path) {
                                // Open deep dive in new tab
                                window.open(data.html_path, '_blank');
                                diveBtn.textContent = '✅ View Again';
                                diveBtn.style.background = '#27ae60';
                                diveBtn.disabled = false;
                                diveBtn.style.opacity = '1';
                                diveBtn.onclick = () => window.open(data.html_path, '_blank');
                            } else {
                                diveBtn.textContent = '✅ Done';
                                diveBtn.disabled = false;
                                diveBtn.style.opacity = '1';
                                alert(data.message || 'Deep dive complete! Check console.');
                            }
                        })
                        .catch(error => {
                            console.error('Deep dive error:', error);
                            diveBtn.textContent = '❌ Failed';
                            diveBtn.style.background = '#e74c3c';
                            diveBtn.disabled = false;
                            diveBtn.style.opacity = '1';
                            alert('Deep dive failed. Is curator_server.py running?');
                        });
                };
                
                // Insert at the beginning
                actionButtons.insertBefore(diveBtn, actionButtons.firstChild);
                
                // Visual feedback
                diveBtn.style.animation = 'fadeIn 0.3s ease';
                break;
            }
        }
    }
    </script>
    <style>
    @keyframes fadeIn {
        from { opacity: 0; transform: scale(0.9); }
        to { opacity: 1; transform: scale(1); }
    }
    .btn-dive {
        background: #f39c12 !important;
        color: white !important;
    }
    .btn-dive:hover {
        background: #e67e22 !important;
        transform: scale(1.05);
    }
    </style>
</body>
</html>
"""
    
    return html
def generate_index_page(archive_dir: str):
    """Generate unified archive index page"""
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
            font-size: 14px;
            max-width: 1400px;
            margin: 15px auto;
            padding: 12px;
            background: #f5f5f5;
            color: #333;
        }
        .header {
            margin-bottom: 15px;
            padding: 12px 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 5px;
            text-align: center;
        }
        .header h1 {
            margin: 0 0 6px 0;
            font-size: 1.5em;
            font-weight: 600;
        }
        .header-meta {
            opacity: 0.9;
            font-size: 0.88em;
        }
        .nav-buttons {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 15px;
        }
        .nav-btn {
            padding: 6px 14px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 500;
        }
        .nav-btn:hover {
            background: #5568d3;
        }
        .archive-table {
            background: white;
            border-radius: 5px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        thead {
            background: #f8f9fa;
            border-bottom: 2px solid #ddd;
        }
        th {
            padding: 11px 12px;
            text-align: left;
            font-weight: 600;
            font-size: 0.9em;
            color: #495057;
        }
        td {
            padding: 12px;
            border-bottom: 1px solid #ddd;
            vertical-align: middle;
        }
        tbody tr:nth-child(even) {
            background: #fafafa;
        }
        tbody tr:last-child td {
            border-bottom: none;
        }
        tbody tr:hover {
            background: #f0f4ff;
        }
        .date-link {
            font-weight: 500;
            color: #667eea;
            text-decoration: none;
            font-size: 1.05em;
        }
        .date-link:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📚 Curator Archive</h1>
        <div><span class="header-meta">Daily Intelligence Briefings</span></div>
    </div>

    <div class="nav-buttons">
        <a href="curator_briefing.html" class="nav-btn">📰 Today</a>
        <a href="curator_index.html" class="nav-btn">📚 Archive</a>
        <a href="interests/2026/deep-dives/index.html" class="nav-btn">🔍 Deep Dives</a>
    </div>

    <div class="archive-table">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Day</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
"""
    
    if archive_files:
        for date_str, filename, date_obj in archive_files:
            formatted_date = date_obj.strftime("%B %d, %Y")
            day_of_week = date_obj.strftime("%A")
            html += f"""                <tr>
                    <td><a href="{archive_dir}/{filename}" class="date-link">{formatted_date}</a></td>
                    <td>{day_of_week}</td>
                    <td><a href="{archive_dir}/{filename}" class="nav-btn">View →</a></td>
                </tr>
"""
    else:
        html += """                <tr>
                    <td colspan="3" style="text-align: center; color: #999; padding: 40px 0;">
                        No archived briefings yet
                    </td>
                </tr>
"""
    
    html += """            </tbody>
        </table>
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
    
    # Save to history (with duplicate protection)
    save_to_history(top_articles)
    
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
