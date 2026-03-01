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
- ollama: $0/month (free, local Ollama/phi)
- xai: $0.18/day ($5.40/month, grok-3-mini)
- sonnet: $0.90/day ($27/month, claude-sonnet-4, premium quality)

USAGE:
  python curator_rss_v2.py [options]

  Options:
    --model=ollama       Use local Ollama/phi (default: FREE)
    --model=xai          Use xAI grok-3-mini ($0.18/day - RECOMMENDED)
    --model=sonnet       Use Anthropic claude-sonnet-4 (premium, $0.90/day)
    
    --dry-run            Preview without saving (output to curator_preview.html)
                         Buttons disabled, no archive/history updates
    
    --fallback           Auto-fallback to ollama if API fails (for cron jobs)
                         Without this flag, API errors stop execution
    
    --telegram           Send to Telegram after completion
    --open               Auto-open HTML in browser
  
  Examples:
    # Free local test (dry run)
    python curator_rss_v2.py --dry-run --model=ollama
    
    # Production default (xAI grok-3-mini)
    python curator_rss_v2.py --model=xai --telegram
    
    # Evaluate Sonnet quality (dry run)
    python curator_rss_v2.py --dry-run --model=sonnet --open
    
    # Save local run to archive
    python curator_rss_v2.py --model=ollama --telegram

ERROR HANDLING:
- By default, API errors STOP execution (fail fast)
- This lets you diagnose problems: out of credits? bad key? rate limit?
- Use --model=ollama to test everything locally (free)
- Use --fallback only for automated/cron runs where you want best-effort
"""

import feedparser
import requests
from datetime import datetime, timezone
from typing import List, Dict, Tuple
from pathlib import Path
import time
import os
import json
import hashlib
from dotenv import load_dotenv
from curator_config import ACTIVE_DOMAIN

# ---------------------------------------------------------------------------
# Cost logger — persists per-run API costs for cost_report.py
# ---------------------------------------------------------------------------
_COST_LOG = Path(__file__).parent / 'curator_costs.json'

def log_curator_cost(model: str, use_type: str, input_tokens: int, output_tokens: int, cost_usd: float):
    """Append one cost record to the curator cost log."""
    try:
        _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(_COST_LOG.read_text()) if _COST_LOG.exists() else {"runs": []}
        data["runs"].append({
            "date":          datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "model":         model,
            "use_type":      use_type,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "cost_usd":      round(cost_usd, 6),
        })
        _COST_LOG.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"   [cost_log] Warning: could not write cost record: {e}")

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

def get_xai_api_key() -> str:
    """
    Get xAI API key from (in priority order):
    1. macOS Keychain (via keyring)
    2. Environment variable XAI_API_KEY
    3. .env file
    
    Returns empty string if not found.
    """
    # Try keychain first (most secure)
    try:
        import keyring
        api_key = keyring.get_password("xai", "api_key")
        if api_key:
            return api_key
    except Exception as e:
        pass  # Keychain not available or error
    
    # Try environment variable (from .env or shell)
    api_key = os.environ.get('XAI_API_KEY')
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
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', 'YOUR_TELEGRAM_CHAT_ID')
    
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

def score_entries_haiku(entries: List[Dict], fallback_on_error: bool = False, user_profile: str = "") -> List[Dict]:
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
  python curator_rss_v2.py --model=ollama
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
""" + user_profile + """
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
        log_curator_cost('claude-haiku', 'curator', input_tokens, output_tokens, cost)
        
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
  python curator_rss_v2.py --model=ollama

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

def score_entries_haiku_prefilter(entries: List[Dict], top_n: int = 50, fallback_on_error: bool = False, user_profile: str = "") -> List[Dict]:
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
""" + user_profile + """
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
        log_curator_cost('claude-haiku', 'curator-prefilter', input_tokens, output_tokens, cost)
        return filtered
        
    except Exception as e:
        print(f"❌ Stage 1 (Haiku) failed: {e}")
        if fallback_on_error:
            print("⚠️  Falling back to mechanical")
            return entries
        else:
            raise

def score_entries_sonnet_ranking(entries: List[Dict], fallback_on_error: bool = False, user_profile: str = "") -> List[Dict]:
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
""" + user_profile + """
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
        log_curator_cost('claude-sonnet', 'curator-ranking', input_tokens, output_tokens, cost)
        return entries
        
    except Exception as e:
        print(f"❌ Stage 2 (Sonnet) failed: {e}")
        if fallback_on_error:
            print("⚠️  Keeping Stage 1 scores")
            return entries
        else:
            raise


def score_entries_xai(entries: List[Dict], fallback_on_error: bool = False, user_profile: str = "") -> List[Dict]:
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
  python curator_rss_v2.py --model=ollama
"""
        if fallback_on_error:
            print("⚠️  xAI API key not found, falling back to mechanical")
            return [score_entry_mechanical(e) for e in entries]
        else:
            print(error_msg)
            raise ValueError("xAI API key not found")
    
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

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
""" + user_profile + """
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
            model="grok-3-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0
        )
        
        content = response.choices[0].message.content
        usage = response.usage
        print(f"   Input: {usage.prompt_tokens} tokens, Output: {usage.completion_tokens} tokens")
        cost = usage.prompt_tokens * 5 / 1_000_000 + usage.completion_tokens * 15 / 1_000_000
        print(f"   Cost: ${cost:.4f}")
        log_curator_cost('xai-grok-3-mini', 'curator', usage.prompt_tokens, usage.completion_tokens, cost)
        
    except Exception as e:
        error_msg = f"""
❌ xAI API call failed!

Error: {e}

To use mechanical mode instead:
  python curator_rss_v2.py --model=ollama
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


def load_user_profile(min_weight: int = 2) -> str:
    """
    Build a user-preferences prompt section from learned_patterns in curator_preferences.json.

    Returns an empty string if:
      - The file is missing or unreadable (graceful fallback)
      - sample_size < 3 (too few data points to trust yet)
      - No signals meet the min_weight threshold

    min_weight: minimum absolute score before a signal is included (filters noise).
    """
    from pathlib import Path

    prefs_path = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'

    try:
        with open(prefs_path) as f:
            prefs = json.load(f)
    except Exception:
        return ""

    lp = prefs.get('learned_patterns', {})
    sample_size = lp.get('sample_size', 0)

    # Decay gate: raise the signal threshold as data gets stale.
    # Old feedback shouldn't lock in forever — weak signals fade first.
    # Formula: > 30 days → min_weight 3, > 60 days → min_weight 4
    last_updated_str = lp.get('last_updated', '')
    if last_updated_str:
        try:
            from datetime import datetime, timezone
            last_updated = datetime.fromisoformat(last_updated_str)
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            days_stale = (datetime.now(timezone.utc) - last_updated).days
            if days_stale > 60:
                min_weight = max(min_weight, 4)
                print(f"   ⏳ Feedback is {days_stale}d old — decay gate active (min_weight={min_weight})")
            elif days_stale > 30:
                min_weight = max(min_weight, 3)
                print(f"   ⏳ Feedback is {days_stale}d old — decay gate active (min_weight={min_weight})")
        except Exception:
            pass  # Bad timestamp — continue with default min_weight


    if sample_size < 3:
        return ""  # Not enough data yet

    sections = []

    # ── Themes (most reliable signal from Claude metadata extraction) ──
    themes = {k: v for k, v in lp.get('preferred_themes', {}).items() if abs(v) >= min_weight}
    if themes:
        pos = sorted([(k, v) for k, v in themes.items() if v > 0], key=lambda x: -x[1])
        neg = sorted([(k, v) for k, v in themes.items() if v < 0], key=lambda x: x[1])
        if pos:
            sections.append("Strong interest in themes: " + ", ".join(k for k, _ in pos[:5]))
        if neg:
            sections.append("Low interest in themes: " + ", ".join(k for k, _ in neg[:3]))

    # ── Sources ──
    sources = {k: v for k, v in lp.get('preferred_sources', {}).items() if abs(v) >= min_weight}
    if sources:
        pos = sorted([(k, v) for k, v in sources.items() if v > 0], key=lambda x: -x[1])
        neg = sorted([(k, v) for k, v in sources.items() if v < 0], key=lambda x: x[1])
        if pos:
            sections.append("Preferred sources: " + ", ".join(k for k, _ in pos[:5]))
        if neg:
            sections.append("Penalize sources: " + ", ".join(k for k, _ in neg[:3]))

    # ── Domain signals (inferred from X bookmark link ecosystems — Phase 3C) ──
    # Reads only signals for ACTIVE_DOMAIN — keeps briefing focused on one topic cluster.
    # domain_signals structure: { "Finance and Geopolitics": { "ft.com": 14, ... }, ... }
    all_domain_signals = lp.get('domain_signals', {})
    active_signals     = all_domain_signals.get(ACTIVE_DOMAIN, {})
    top_domains = [(d, s) for d, s in sorted(active_signals.items(), key=lambda x: -x[1]) if s >= 2][:8]
    if top_domains:
        domains_str = ', '.join(f"{d}(+{s})" for d, s in top_domains)
        sections.append(f"Content domains from trusted X curators [{ACTIVE_DOMAIN}]: {domains_str}")

    # ── Content types ('descriptive' excluded — known co-tag artifact, not a standalone signal) ──
    CO_TAG_EXCLUDE = {'descriptive'}
    content = {k: v for k, v in lp.get('preferred_content_types', {}).items()
               if abs(v) >= min_weight and k not in CO_TAG_EXCLUDE}
    if content:
        pos = sorted([(k, v) for k, v in content.items() if v > 0], key=lambda x: -x[1])
        neg = sorted([(k, v) for k, v in content.items() if v < 0], key=lambda x: x[1])
        if pos:
            sections.append("Preferred content style: " + ", ".join(k for k, _ in pos[:4]))
        if neg:
            sections.append("Penalize content style: " + ", ".join(k for k, _ in neg[:3]))

    # ── Avoid patterns (extracted from disliked articles) ──
    avoid = sorted(lp.get('avoid_patterns', {}).items(), key=lambda x: -x[1])
    top_avoid = [k for k, v in avoid[:5] if v >= 1]
    if top_avoid:
        sections.append("Avoid signals: " + ", ".join(top_avoid))

    if not sections:
        return ""

    profile = f"\nPERSONALIZATION (from {sample_size} user interactions — adjust base score by +1 to +2 for strong matches, -1 to -2 for avoids):\n"
    profile += "\n".join(f"- {s}" for s in sections)
    profile += "\n"
    return profile


def load_priorities():
    """
    Load active (non-expired) priorities from workspace priorities.json.
    Returns list of active priority dicts.
    """
    from pathlib import Path
    from datetime import datetime
    import json
    
    priorities_file = Path.home() / ".openclaw" / "workspace" / "priorities.json"
    
    if not priorities_file.exists():
        return []
    
    try:
        with open(priorities_file, 'r') as f:
            data = json.load(f)
        
        priorities = data.get('priorities', [])
        active_priorities = []
        now = datetime.now()
        
        for priority in priorities:
            # Skip if not active
            if not priority.get('active', True):
                continue
            
            # Skip if expired
            expires_at = priority.get('expires_at')
            if expires_at:
                try:
                    expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    if expiry < now:
                        continue
                except:
                    pass
            
            active_priorities.append(priority)
        
        return active_priorities
    
    except Exception as e:
        print(f"⚠️  Error loading priorities: {e}")
        return []


def apply_priorities_boost(entry: Dict, priorities: List[Dict]) -> float:
    """
    Apply score boost based on active priorities.
    Matches article title/summary against priority keywords (case-insensitive).
    Returns total boost (capped at +3.0).
    """
    if not priorities:
        return 0.0
    
    total_boost = 0.0
    matched_priorities = []
    
    # Get searchable text
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    searchable = f"{title} {summary}"
    
    for priority in priorities:
        keywords = priority.get('keywords', [])
        boost = priority.get('boost', 0.0)
        priority_id = priority.get('id', 'unknown')
        
        # Check if any keyword matches
        matched = False
        for keyword in keywords:
            if keyword.lower() in searchable:
                matched = True
                break
        
        if matched:
            total_boost += boost
            matched_priorities.append({
                'priority_id': priority_id,
                'label': priority.get('label', 'Unknown'),
                'boost': boost
            })
    
    # Cap at +3.0
    total_boost = min(total_boost, 3.0)
    
    # Store matched priorities in entry for logging
    if matched_priorities:
        entry['matched_priorities'] = matched_priorities
    
    return total_boost


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
    
    # Load active priorities for score boosting
    active_priorities = load_priorities()
    if active_priorities:
        print(f"🎯 Loaded {len(active_priorities)} active priorities from workspace")
        for priority in active_priorities:
            label = priority.get('label', 'Unknown')
            boost = priority.get('boost', 0.0)
            keywords = priority.get('keywords', [])
            print(f"   {label}: {boost:+.1f} boost (keywords: {', '.join(keywords[:3])}{'...' if len(keywords) > 3 else ''})")
    
    # Load user profile once here — passed to all scorers regardless of model.
    # The profile is a dispatcher concern, not a model concern.
    user_profile = load_user_profile()
    if user_profile:
        print(f"🧠 User profile loaded ({len(user_profile)} chars) — personalizing scores")
    else:
        print("   No user profile yet — using static scoring")

    # Score all entries using selected mode
    if mode == 'ai-two-stage':
        # TWO-STAGE: Haiku pre-filter → Sonnet ranking
        print("\n🎯 Two-stage AI curation:")
        print(f"   Stage 1: Haiku filters {len(all_entries)} → 50 candidates")
        print(f"   Stage 2: Sonnet ranks 50 → top {top_n}")
        print()

        # Stage 1: Haiku pre-filter (150 → 50)
        candidates = score_entries_haiku_prefilter(all_entries, top_n=50, fallback_on_error=fallback_on_error, user_profile=user_profile)

        # Stage 2: Sonnet ranking (50 → scored)
        all_entries = score_entries_sonnet_ranking(candidates, fallback_on_error=fallback_on_error, user_profile=user_profile)

    elif mode == 'ai':
        # Single-stage Haiku scoring (original implementation)
        results = score_entries_haiku(all_entries, fallback_on_error=fallback_on_error, user_profile=user_profile)
        for i, entry in enumerate(all_entries):
            entry["score"] = results[i]['score']
            entry["category"] = results[i]['category']
            entry["method"] = results[i]['method']
            entry["raw_score"] = results[i].get('raw_score', entry["score"])  # AI doesn't have raw_score
    elif mode == 'xai':
        # Single-stage xAI Grok scoring
        results = score_entries_xai(all_entries, fallback_on_error=fallback_on_error, user_profile=user_profile)
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
    
    # Load serendipity reserve setting
    serendipity_reserve = 0.20  # Default
    try:
        prefs_path = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
        with open(prefs_path) as f:
            prefs = json.load(f)
            serendipity_reserve = prefs.get('curation_settings', {}).get('serendipity_reserve', 0.20)
    except Exception:
        pass  # Use default if file not found
    
    # Calculate split between personalized and serendipity articles
    serendipity_count = int(top_n * serendipity_reserve)
    personalized_count = top_n - serendipity_count
    
    if serendipity_count > 0:
        print(f"\n🎲 Serendipity reserve: {serendipity_count}/{top_n} articles ({int(serendipity_reserve*100)}%) from outside learned patterns")
    
    # Apply diversity-aware selection
    selected = []
    source_counts = {}
    category_counts = {}  # NEW: Track category distribution
    candidates = all_entries.copy()
    
    # PHASE 1: Select personalized articles (with interest/priority boosts)
    while len(selected) < personalized_count and candidates:
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
            
            # Apply priorities-based boosting
            priorities_boost = apply_priorities_boost(entry, active_priorities)
            
            entry["final_score"] = entry["score"] - source_penalty - category_penalty + interest_boost + priorities_boost
            if interest_boost != 0:
                entry["interest_boosted"] = True
                entry["interest_modifier"] = interest_boost
            if priorities_boost > 0:
                entry["priorities_boosted"] = True
                entry["priorities_modifier"] = priorities_boost
        
        # Pick the highest-scoring candidate
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        best = candidates.pop(0)
        
        selected.append(best)
        
        source = best["source"]
        category = best.get("category", "other")
        
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    
    # PHASE 2: Select serendipity articles (NO interest/priority boosts, only base score + diversity)
    if serendipity_count > 0 and candidates:
        print(f"   Selecting {serendipity_count} serendipity articles from {len(candidates)} remaining candidates...")
        serendipity_selected = []
        
        while len(serendipity_selected) < serendipity_count and candidates:
            # Recalculate scores WITHOUT personalization boosts
            for entry in candidates:
                source = entry["source"]
                category = entry.get("category", "other")
                
                source_count = source_counts.get(source, 0)
                category_count = category_counts.get(category, 0)
                
                source_penalty = (source_count ** 2) * 30 * diversity_weight
                category_penalty = (category_count ** 2) * 15 * diversity_weight
                
                # Serendipity score = base score + diversity penalties ONLY (no boosts)
                entry["final_score"] = entry["score"] - source_penalty - category_penalty
                entry["serendipity_pick"] = True
            
            # Pick highest base-scoring candidate
            candidates.sort(key=lambda x: x["final_score"], reverse=True)
            best = candidates.pop(0)
            
            serendipity_selected.append(best)
            
            source = best["source"]
            category = best.get("category", "other")
            
            source_counts[source] = source_counts.get(source, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
        
        # Merge serendipity articles into main selection
        selected.extend(serendipity_selected)
        print(f"   ✅ Added {len(serendipity_selected)} serendipity articles")
    
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
        output += f"   ID: {entry.get('hash_id', 'unknown')}\n"
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

def format_html(entries: List[Dict], model: str = "xai", run_mode: str = "production") -> str:
    """Format as table HTML (unified briefing platform style)
    
    Args:
        entries: List of article entries
        model: Model name (ollama|xai|sonnet)
        run_mode: Run mode (production|dry-run)
    """
    from datetime import datetime
    
    today = datetime.now()
    date_str = today.strftime('%B %d, %Y')
    day_str = today.strftime('%A')
    timestamp_str = today.strftime('%Y-%m-%d %H:%M:%S')
    
    # Map model names to display names
    model_display_map = {
        'ollama': 'ollama/phi',
        'xai': 'grok-3-mini',
        'sonnet': 'claude-sonnet-4'
    }
    model_display = model_display_map.get(model, model)
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="curator-model" content="{model_display}">
    <meta name="curator-mode" content="{run_mode}">
    <meta name="curator-timestamp" content="{timestamp_str}">
    <meta name="curator-articles" content="{len(entries)}">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
    <title>Morning Briefing - {date_str}</title>
    <style>
        :root {{
            --bg: #f5f0e8;
            --bg-texture: #ede8df;
            --surface: #faf7f2;
            --surface2: #f0ebe0;
            --border: #ddd6c8;
            --border2: #c8bfaf;
            --text: #2a2418;
            --text-muted: #6b5f4e;
            --text-dim: #9e9080;
            --accent: #8b5e2a;
            --accent-dim: rgba(139,94,42,0.08);
            --accent-glow: rgba(139,94,42,0.18);
            --shadow: rgba(42,36,24,0.08);
            --geo: #7a3d0a;
            --fiscal: #1a4a7a;
            --monetary: #4a2a7a;
            --other: #4a4035;
        }}

        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Source Sans 3', sans-serif;
            background: var(--bg);
            background-image:
                radial-gradient(ellipse at 20% 0%, rgba(210,190,160,0.4) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 100%, rgba(200,180,150,0.3) 0%, transparent 50%);
            color: var(--text);
            min-height: 100vh;
            font-size: 14px;
            line-height: 1.5;
        }}

        /* ── Header ── */
        header {{
            border-bottom: 1px solid var(--border);
            padding: 0 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 60px;
            position: sticky;
            top: 0;
            background: rgba(245,240,232,0.94);
            backdrop-filter: blur(12px);
            z-index: 100;
            box-shadow: 0 1px 0 var(--border), 0 2px 8px var(--shadow);
        }}

        .header-left {{
            display: flex;
            align-items: baseline;
            gap: 16px;
        }}

        .logo {{
            font-family: 'Playfair Display', serif;
            font-size: 20px;
            color: var(--accent);
            letter-spacing: -0.02em;
            text-decoration: none;
        }}

        .logo-sub {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}

        .header-nav {{
            display: flex;
            gap: 4px;
        }}

        .nav-link {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-muted);
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 6px;
            letter-spacing: 0.05em;
            transition: all 0.15s;
            border: 1px solid transparent;
        }}

        .nav-link:hover {{
            color: var(--text);
            background: var(--surface2);
        }}

        .nav-link.active {{
            color: var(--accent);
            background: var(--accent-dim);
            border-color: rgba(139,94,42,0.2);
        }}

        /* ── Main Content ── */
        main {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 32px;
        }}

        /* ── Table ── */
        .briefing-table {{
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 12px var(--shadow);
            background: var(--surface);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        thead {{
            background: var(--bg-texture);
            border-bottom: 2px solid var(--border);
        }}

        th {{
            padding: 11px 16px;
            text-align: left;
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            font-weight: 500;
            color: var(--text-dim);
            letter-spacing: 0.08em;
            text-transform: uppercase;
            white-space: nowrap;
        }}

        tbody tr {{
            border-bottom: 1px solid var(--border);
            transition: background 0.1s;
            background: var(--surface);
        }}

        tbody tr:last-child {{
            border-bottom: none;
        }}

        tbody tr:hover {{
            background: rgba(139,94,42,0.04);
        }}

        td {{
            padding: 14px 16px;
            vertical-align: top;
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
            max-width: 380px;
        }}

        .col-time {{
            width: 80px;
        }}

        .col-score {{
            width: 120px;
        }}

        .col-actions {{
            width: 110px;
            text-align: center;
            white-space: nowrap;
        }}

        .rank-badge {{
            display: inline-block;
            background: var(--accent);
            color: white;
            padding: 3px 9px;
            border-radius: 4px;
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            font-weight: 500;
        }}

        .cat-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            letter-spacing: 0.04em;
            font-weight: 500;
            white-space: nowrap;
            border: 1px solid transparent;
        }}

        .cat-geo_major, .cat-geo_minor {{
            background: rgba(139,69,19,0.08);
            color: var(--geo);
            border-color: rgba(139,69,19,0.2);
        }}

        .cat-fiscal {{
            background: rgba(26,74,122,0.08);
            color: var(--fiscal);
            border-color: rgba(26,74,122,0.2);
        }}

        .cat-monetary {{
            background: rgba(74,42,122,0.08);
            color: var(--monetary);
            border-color: rgba(74,42,122,0.2);
        }}

        .cat-geo_other,
        .cat-technology,
        .cat-other,
        .cat-unknown {{
            background: rgba(107,95,78,0.08);
            color: var(--other);
            border-color: rgba(107,95,78,0.2);
        }}

        .article-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text);
            line-height: 1.4;
            margin-bottom: 0;
        }}

        .article-title a {{
            color: var(--text);
            text-decoration: none;
        }}

        .article-title a:hover {{
            color: var(--accent);
        }}

        .source-name {{
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 500;
        }}

        .time-ago {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
        }}

        .score-val {{
            font-family: 'DM Mono', monospace;
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
        }}

        .score-bar {{
            height: 2px;
            background: var(--border);
            border-radius: 2px;
            margin-top: 4px;
            width: 48px;
            overflow: hidden;
        }}

        .score-fill {{
            height: 100%;
            border-radius: 2px;
            background: var(--accent);
        }}

        .score-details {{
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            color: var(--text-dim);
            margin-top: 2px;
        }}

        .action-buttons {{
            display: flex;
            gap: 6px;
            justify-content: center;
        }}

        .action-btn {{
            padding: 4px 8px;
            border: 1px solid transparent;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            font-weight: 500;
            transition: all 0.15s;
            background: var(--surface2);
            color: var(--text-muted);
        }}

        .btn-like {{
            background: rgba(46,125,82,0.08);
            color: #2e7d52;
            border-color: rgba(46,125,82,0.2);
        }}

        .btn-like:hover {{
            background: rgba(46,125,82,0.15);
            transform: scale(1.05);
        }}

        .btn-dislike {{
            background: rgba(180,60,60,0.08);
            color: #b43c3c;
            border-color: rgba(180,60,60,0.2);
        }}

        .btn-dislike:hover {{
            background: rgba(180,60,60,0.15);
            transform: scale(1.05);
        }}

        .btn-save {{
            background: rgba(26,92,138,0.08);
            color: #1a5c8a;
            border-color: rgba(26,92,138,0.2);
        }}

        .btn-save:hover {{
            background: rgba(26,92,138,0.15);
            transform: scale(1.05);
        }}
    </style>
</head>
<body data-run-mode="{run_mode}">
<header class="curator-header">
  <div class="header-left">
    <a href="/" class="logo">📚 Curator</a>
    <span class="logo-sub">Morning Briefing</span>
  </div>
  <nav class="header-nav">
    <a href="/" class="nav-link active">Daily</a>
    <a href="curator_library.html" class="nav-link">Library</a>
    <a href="curator_priorities.html" class="nav-link">🎯 Priorities</a>
    <a href="interests/2026/deep-dives/index.html" class="nav-link">Deep Dives</a>
  </nav>
</header>

<main>
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
        hash_id = entry.get('hash_id', '')
        category = entry.get('category', 'other')
        source = entry.get('source', 'Unknown')
        title = entry.get('title', 'Untitled')
        url = entry.get('link', '#')
        published = entry.get('published', '')
        score = entry.get('final_score', 0)
        raw_score = entry.get('raw_score', 0)
        
        # Calculate score percentage for visual bar (normalize to 0-100)
        # Assume max score of 20 for full bar
        score_pct = min(100, max(0, (score / 20.0) * 100)) if score > 0 else 0
        
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
        
        # Escape article data for JSON embedding
        import html as html_module
        title_escaped = html_module.escape(title.replace("'", "\\'"))
        url_escaped = html_module.escape(url)
        source_escaped = html_module.escape(source.replace("'", "\\'"))
        
        html += f"""                <tr data-hash-id="{hash_id}" 
                        data-rank="{rank}"
                        data-title="{title_escaped}"
                        data-url="{url_escaped}"
                        data-source="{source_escaped}"
                        data-category="{category}">
                    <td class="col-rank"><span class="rank-badge">{rank}</span></td>
                    <td class="col-category"><span class="cat-badge cat-{category.lower()}">{category.lower()}</span></td>
                    <td class="col-source"><span class="source-name">{source}</span></td>
                    <td class="col-title">
                        <div class="article-title">
                            <a href="{url}" target="_blank">{title}</a>
                        </div>
                    </td>
                    <td class="col-time"><span class="time-ago">{time_ago}</span></td>
                    <td class="col-score">
                        <div class="score-val">{score:.1f}</div>
                        <div class="score-bar"><div class="score-fill" style="width:{score_pct:.0f}%"></div></div>
                    </td>
                    <td class="col-actions">
                        <div class="action-buttons">"""
        
        # Add disabled buttons in dry-run mode
        if run_mode == "dry-run":
            html += f"""
                            <button class="action-btn btn-like" title="Dry run — buttons disabled" disabled style="opacity: 0.5; cursor: not-allowed;">👍</button>
                            <button class="action-btn btn-dislike" title="Dry run — buttons disabled" disabled style="opacity: 0.5; cursor: not-allowed;">👎</button>
                            <button class="action-btn btn-save" title="Dry run — buttons disabled" disabled style="opacity: 0.5; cursor: not-allowed;">💾</button>"""
        else:
            html += f"""
                            <button class="action-btn btn-like" title="Like this article" onclick="showFeedback(this, 'like', {rank});">👍</button>
                            <button class="action-btn btn-dislike" title="Dislike this article" onclick="showFeedback(this, 'dislike', {rank});">👎</button>
                            <button class="action-btn btn-save" title="Save for deep dive" onclick="showFeedback(this, 'save', {rank});">💾</button>"""
        
        html += """
                        </div>
                    </td>
                </tr>
"""
    
    html += """            </tbody>
        </table>
    </div>
</main>
    
    <script>
    function showFeedback(button, action, rank) {
        console.log('showFeedback called:', action, rank);
        
        // Get article data from row data attributes
        const row = button.closest('tr');
        const articleData = {
            id: row.dataset.hashId || `row-${rank}`,
            title: row.dataset.title || 'Unknown',
            link: row.dataset.url || '#',
            source: row.dataset.source || 'Unknown',
            category: row.dataset.category || 'other'
        };
        
        console.log('Article data:', articleData);
        
        // Immediate visual feedback
        button.style.opacity = '0.3';
        button.style.cursor = 'not-allowed';
        button.disabled = true;
        
        // Send to feedback server with full article data (POST)
        fetch('http://localhost:8765/feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                action: action,
                rank: rank,
                article: articleData
            })
        })
            .then(response => {
                console.log('Response status:', response.status);
                return response.json();
            })
            .then(data => {
                console.log('Response data:', data);
                
                if (data.success) {
                    // Lock ALL three buttons in this row - can't change mind after submitting
                    const row = button.closest('tr');
                    const allBtns = row.querySelectorAll('.action-btn');
                    allBtns.forEach(function(btn) {
                        btn.disabled = true;
                        btn.style.cursor = 'not-allowed';
                        if (btn === button) {
                            // Activated button: colored checkmark, full opacity
                            btn.innerHTML = btn.textContent + ' ✓';
                            btn.style.opacity = '1';
                            btn.style.fontWeight = '700';
                            btn.style.boxShadow = '0 0 0 2px currentColor';
                        } else {
                            // Sibling buttons: faded out so you can see which was chosen
                            btn.style.opacity = '0.2';
                        }
                    });

                    // Show toast notification
                    showToast(data.message || 'Article #' + rank + ' ' + action + 'd', 'success');

                    // If liked or saved, add deep dive button
                    if (action === 'like' || action === 'save') {
                        const hashId = button.closest('tr').dataset.hashId;
                        addDeepDiveButton(rank, hashId);
                    }
                } else {
                    // Re-enable on error
                    button.style.opacity = '1';
                    button.style.cursor = 'pointer';
                    button.disabled = false;
                    showToast(data.message || 'Error saving feedback', 'error');
                }
            })
            .catch(error => {
                console.error('Feedback error:', error);
                button.style.opacity = '1';
                button.style.cursor = 'pointer';
                button.disabled = false;
                showToast('Server error - is curator_server.py running?', 'error');
            });
    }
    
    function showToast(message, type) {
        type = type || 'success';
        console.log('showToast:', message, type);
        
        const toast = document.createElement('div');
        toast.textContent = message;
        
        var bgColor = (type === 'error') ? '#ef4444' : '#10b981';
        toast.style.cssText = 'position: fixed; top: 20px; right: 20px; background: ' + bgColor + '; color: white; padding: 12px 20px; border-radius: 8px; font-weight: 500; font-size: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); z-index: 9999; opacity: 0; transform: translateY(-20px); transition: all 0.3s ease;';
        
        document.body.appendChild(toast);
        console.log('Toast added to body');
        
        // Animate in
        requestAnimationFrame(function() {
            requestAnimationFrame(function() {
                toast.style.opacity = '1';
                toast.style.transform = 'translateY(0)';
            });
        });
        
        // Animate out after 2.5 seconds
        setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transform = 'translateY(-20px)';
            setTimeout(function() {
                if (toast.parentNode) {
                    document.body.removeChild(toast);
                }
            }, 300);
        }, 2500);
    }
    
    function showDeepDiveModal(rank, hashId, diveBtn) {
        // Create modal overlay
        var overlay = document.createElement('div');
        overlay.style.cssText = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 10000; display: flex; align-items: center; justify-content: center;';
        
        // Create modal
        var modal = document.createElement('div');
        modal.style.cssText = 'background: white; padding: 30px; border-radius: 12px; max-width: 500px; width: 90%; box-shadow: 0 4px 20px rgba(0,0,0,0.3);';
        
        modal.innerHTML = '<h2 style="margin: 0 0 20px 0; color: #2c3e50;">Deep Dive Analysis</h2>' +
            '<p style="color: #666; margin-bottom: 20px;">This generates a research briefing with bibliography tailored to YOUR interests.</p>' +
            '<label style="display: block; margin-bottom: 8px; font-weight: 600; color: #2c3e50;">What interests you about this article?</label>' +
            '<textarea id="interest-input" placeholder="e.g., Geopolitical implications, economic impact, contrarian perspectives..." style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; min-height: 80px; font-family: inherit; margin-bottom: 20px; box-sizing: border-box;"></textarea>' +
            '<label style="display: block; margin-bottom: 8px; font-weight: 600; color: #2c3e50;">Specific focus areas? (optional)</label>' +
            '<input id="focus-input" type="text" placeholder="e.g., Historical precedents, key players, data sources..." style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; font-family: inherit; margin-bottom: 20px; box-sizing: border-box;">' +
            '<div style="display: flex; gap: 10px; justify-content: flex-end;">' +
            '<button id="modal-cancel" style="padding: 10px 20px; border: 1px solid #ddd; background: white; border-radius: 6px; cursor: pointer; font-weight: 500;">Cancel</button>' +
            '<button id="modal-submit" style="padding: 10px 20px; border: none; background: #f39c12; color: white; border-radius: 6px; cursor: pointer; font-weight: 500;">Generate (~30s, $0.15)</button>' +
            '</div>';
        
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        
        // Focus on interest input
        document.getElementById('interest-input').focus();
        
        // Handle cancel
        document.getElementById('modal-cancel').onclick = function() {
            document.body.removeChild(overlay);
        };
        
        // Handle submit
        document.getElementById('modal-submit').onclick = function() {
            var interest = document.getElementById('interest-input').value.trim();
            var focus = document.getElementById('focus-input').value.trim();
            
            if (!interest) {
                alert('Please describe what interests you about this article.');
                return;
            }
            
            // Close modal
            document.body.removeChild(overlay);
            
            // Update button and trigger deep dive
            diveBtn.disabled = true;
            diveBtn.textContent = '⏳ Analyzing...';
            diveBtn.style.opacity = '0.6';
            
            // Send to server with hash_id, interest and focus
            var url = 'http://localhost:8765/deepdive?hash_id=' + hashId + '&interest=' + encodeURIComponent(interest);
            if (focus) {
                url += '&focus=' + encodeURIComponent(focus);
            }
            
            fetch(url)
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.html_path) {
                        window.open(data.html_path, '_blank');
                        diveBtn.textContent = '✅ View Again';
                        diveBtn.style.background = '#27ae60';
                        diveBtn.disabled = false;
                        diveBtn.style.opacity = '1';
                        diveBtn.onclick = function() { window.open(data.html_path, '_blank'); };
                    } else {
                        diveBtn.textContent = '✅ Done';
                        diveBtn.disabled = false;
                        diveBtn.style.opacity = '1';
                        showToast(data.message || 'Deep dive complete!', 'success');
                    }
                })
                .catch(function(error) {
                    console.error('Deep dive error:', error);
                    diveBtn.textContent = '❌ Failed';
                    diveBtn.style.background = '#e74c3c';
                    diveBtn.disabled = false;
                    diveBtn.style.opacity = '1';
                    showToast('Deep dive failed. Is curator_server.py running?', 'error');
                });
        };
        
        // Close on overlay click
        overlay.onclick = function(e) {
            if (e.target === overlay) {
                document.body.removeChild(overlay);
            }
        };
    }
    
    function addDeepDiveButton(rank, hashId) {
        // Find the action buttons container for this rank
        var rows = document.querySelectorAll('tbody tr');
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var rankBadge = row.querySelector('.rank-badge');
            if (rankBadge && rankBadge.textContent === rank.toString()) {
                var actionButtons = row.querySelector('.action-buttons');
                
                // Check if deep dive button already exists
                if (actionButtons.querySelector('.btn-dive')) {
                    return;
                }
                
                // Create deep dive button
                var diveBtn = document.createElement('button');
                diveBtn.className = 'action-btn btn-dive';
                diveBtn.textContent = '🔖 Deep Dive';
                
                // Check if this is a dry run
                var isDryRun = document.body.getAttribute('data-run-mode') === 'dry-run';
                if (isDryRun) {
                    diveBtn.title = 'Dry run — buttons disabled';
                    diveBtn.disabled = true;
                    diveBtn.style.cssText = 'background: #95a5a6; color: white; cursor: not-allowed; opacity: 0.6;';
                } else {
                    diveBtn.title = 'Request deep dive analysis (~30s, costs ~$0.15)';
                    diveBtn.style.cssText = 'background: #f39c12; color: white;';
                    // Use IIFE to capture rank, hashId and diveBtn by value (not reference)
                    diveBtn.onclick = (function(capturedRank, capturedHashId, capturedBtn) {
                        return function() {
                            showDeepDiveModal(capturedRank, capturedHashId, capturedBtn);
                        };
                    })(rank, hashId, diveBtn);
                }
                
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
    """Generate unified archive index page with timestamp and model info"""
    import os
    import re
    from datetime import datetime
    
    archive_files = []
    if os.path.exists(archive_dir):
        for filename in os.listdir(archive_dir):
            if filename.startswith("curator_") and filename.endswith(".html"):
                timestamp_str = filename.replace("curator_", "").replace(".html", "")
                
                # Try new format first: YYYY-MM-DD-HHMM
                try:
                    datetime_obj = datetime.strptime(timestamp_str, "%Y-%m-%d-%H%M")
                except ValueError:
                    # Fallback to old format: YYYY-MM-DD
                    try:
                        datetime_obj = datetime.strptime(timestamp_str, "%Y-%m-%d")
                    except ValueError:
                        continue
                
                # Extract metadata from HTML file
                filepath = os.path.join(archive_dir, filename)
                model = "unknown"
                article_count = "?"
                try:
                    with open(filepath, 'r') as f:
                        content = f.read(2000)  # Only read first 2KB for metadata
                        model_match = re.search(r'<meta name="curator-model" content="([^"]+)"', content)
                        articles_match = re.search(r'<meta name="curator-articles" content="([^"]+)"', content)
                        if model_match:
                            model = model_match.group(1)
                        if articles_match:
                            article_count = articles_match.group(1)
                except Exception:
                    pass
                
                archive_files.append((timestamp_str, filename, datetime_obj, model, article_count))
    
    archive_files.sort(key=lambda x: x[2], reverse=True)
    
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
    <title>Curator Archive</title>
    <style>
        :root {
            --bg: #f5f0e8;
            --bg-texture: #ede8df;
            --surface: #faf7f2;
            --surface2: #f0ebe0;
            --border: #ddd6c8;
            --border2: #c8bfaf;
            --text: #2a2418;
            --text-muted: #6b5f4e;
            --text-dim: #9e9080;
            --accent: #8b5e2a;
            --accent-dim: rgba(139,94,42,0.08);
            --accent-glow: rgba(139,94,42,0.18);
            --shadow: rgba(42,36,24,0.08);
        }

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Source Sans 3', sans-serif;
            background: var(--bg);
            background-image:
                radial-gradient(ellipse at 20% 0%, rgba(210,190,160,0.4) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 100%, rgba(200,180,150,0.3) 0%, transparent 50%);
            color: var(--text);
            min-height: 100vh;
            font-size: 14px;
            line-height: 1.5;
        }

        /* ── Header ── */
        header {
            border-bottom: 1px solid var(--border);
            padding: 0 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 60px;
            position: sticky;
            top: 0;
            background: rgba(245,240,232,0.94);
            backdrop-filter: blur(12px);
            z-index: 100;
            box-shadow: 0 1px 0 var(--border), 0 2px 8px var(--shadow);
        }

        .header-left {
            display: flex;
            align-items: baseline;
            gap: 16px;
        }

        .logo {
            font-family: 'Playfair Display', serif;
            font-size: 20px;
            color: var(--accent);
            letter-spacing: -0.02em;
            text-decoration: none;
        }

        .logo-sub {
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .header-nav {
            display: flex;
            gap: 4px;
        }

        .nav-link {
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-muted);
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 6px;
            letter-spacing: 0.05em;
            transition: all 0.15s;
            border: 1px solid transparent;
        }

        .nav-link:hover {
            color: var(--text);
            background: var(--surface2);
        }

        .nav-link.active {
            color: var(--accent);
            background: var(--accent-dim);
            border-color: rgba(139,94,42,0.2);
        }

        /* ── Main ── */
        main {
            max-width: 1400px;
            margin: 0 auto;
            padding: 32px;
        }

        .archive-table {
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 12px var(--shadow);
            background: var(--surface);
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        thead {
            background: var(--bg-texture);
            border-bottom: 2px solid var(--border);
        }

        th {
            padding: 11px 16px;
            text-align: left;
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            font-weight: 500;
            color: var(--text-dim);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        tbody tr {
            border-bottom: 1px solid var(--border);
            transition: background 0.1s;
            background: var(--surface);
        }

        tbody tr:last-child {
            border-bottom: none;
        }

        tbody tr:hover {
            background: rgba(139,94,42,0.04);
        }

        td {
            padding: 14px 16px;
            vertical-align: middle;
        }

        .date-link {
            font-weight: 600;
            color: var(--accent);
            text-decoration: none;
            font-size: 13px;
        }

        .date-link:hover {
            text-decoration: underline;
        }

        .nav-btn {
            color: var(--accent);
            text-decoration: none;
            font-size: 13px;
        }

        .nav-btn:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
<header>
  <div class="header-left">
    <a href="/" class="logo">📚 Curator</a>
    <span class="logo-sub">Archive</span>
  </div>
  <nav class="header-nav">
    <a href="/" class="nav-link">Daily</a>
    <a href="curator_library.html" class="nav-link">Library</a>
    <a href="interests/2026/deep-dives/index.html" class="nav-link">Deep Dives</a>
  </nav>
</header>

<main>
    <div class="archive-table">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Time</th>
                    <th>Model</th>
                    <th>Articles</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
"""
    
    if archive_files:
        for timestamp_str, filename, datetime_obj, model, article_count in archive_files:
            formatted_date = datetime_obj.strftime("%b %d, %Y")
            
            # Check if this is a legacy entry (old date-only format, no timestamp)
            is_legacy = '-' not in timestamp_str or timestamp_str.count('-') == 2
            
            if is_legacy:
                # Legacy entry: show "-" for time and articles
                formatted_time = "-"
                display_article_count = "-"
                # Map old model names or show "legacy" for unknown
                if model == "unknown":
                    model_display = "legacy"
                elif model == "xai":
                    model_display = "grok-beta"  # Old xai entries
                elif model == "mechanical":
                    model_display = "ollama/phi"  # Old mechanical entries
                else:
                    model_display = "legacy" if model == "?" else model
            else:
                # New entry: show full metadata
                formatted_time = datetime_obj.strftime("%I:%M %p")
                display_article_count = article_count
                # Map old "mechanical" to new "ollama/phi" for consistency
                if model == "mechanical":
                    model_display = "ollama/phi"
                elif model == "unknown":
                    model_display = "legacy"
                else:
                    model_display = model  # Use as-is from metadata (already formatted)
            
            html += f"""                <tr>
                    <td><a href="{archive_dir}/{filename}" class="date-link">{formatted_date}</a></td>
                    <td style="text-align: center;">{formatted_time}</td>
                    <td><span style="font-family: monospace; font-size: 0.9em;">{model_display}</span></td>
                    <td style="text-align: center;">{display_article_count}</td>
                    <td><a href="{archive_dir}/{filename}" class="nav-btn">View →</a></td>
                </tr>
"""
    else:
        html += """                <tr>
                    <td colspan="5" style="text-align: center; color: #999; padding: 40px 0;">
                        No archived briefings yet
                    </td>
                </tr>
"""
    
    html += """            </tbody>
        </table>
    </div>
</main>
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
    from signal_store import get_session_id, log_article_scored, log_priority_match

    # Parse command line args first (dry_run needed before session init)
    send_telegram = "--telegram" in sys.argv
    auto_open = "--open" in sys.argv
    fallback_on_error = "--fallback" in sys.argv
    dry_run = "--dry-run" in sys.argv

    # Model selection (default: xai)
    # --model=[ollama|xai|sonnet] controls which LLM is used
    model = 'xai'
    for arg in sys.argv:
        if arg.startswith('--model='):
            model = arg.split('=')[1]
            break

    # Map --model= flag to internal scoring mode
    mode_map = {
        'ollama': 'mechanical',   # Free local, keyword-based
        'xai':    'xai',          # xAI Grok (~$0.15/day) — production default
        'haiku':  'ai',           # Anthropic Haiku (~$0.20/day) — single-stage
        'sonnet': 'ai-two-stage', # Anthropic Haiku pre-filter + Sonnet ranking (~$0.90/day)
    }
    mode = mode_map.get(model, 'xai')  # Default: xai

    # Session ID (in-memory only; no I/O until log calls below)
    session_id = get_session_id()
    run_id = 'r_' + session_id[:7]

    # Banner
    if dry_run:
        print("="*60)
        print(f"🧪 DRY RUN MODE  [{run_id}]")
        print("   No archive, history, or Signal Store writes.")
        print("="*60)
        print()
    else:
        print(f"🚀 Production run  [{run_id}]")
    
    # Run curation
    try:
        top_articles = curate(top_n=20, mode=mode, fallback_on_error=fallback_on_error)
    except (ValueError, RuntimeError) as e:
        # API error with no fallback - exit with error
        print(f"\n💥 Curation failed: {e}")
        print("\nTip: Run with --model=ollama to test everything except API")
        sys.exit(1)
    
    # Log scored articles to Signal Store (production only)
    if not dry_run:
        print(f"📝 Logging {len(top_articles)} articles to Signal Store...")
        priority_matches_count = 0
        for rank, article in enumerate(top_articles, 1):
            log_article_scored(
                article_id=article.get("hash_id", f"article-{rank}"),
                title=article["title"],
                source=article["source"],
                category=article.get("category", "other"),
                score=article["score"],
                model=model,
                url=article.get("link"),
                rank=rank,
                metadata={
                    "run_id": run_id,
                    "method": article.get("method", "unknown"),
                    "final_score": article.get("final_score", article["score"]),
                    "interest_boosted": article.get("interest_boosted", False),
                    "priorities_boosted": article.get("priorities_boosted", False),
                    "priorities_modifier": article.get("priorities_modifier", 0.0)
                }
            )

            # Log priority matches
            matched_priorities = article.get("matched_priorities", [])
            for match in matched_priorities:
                log_priority_match(
                    priority_id=match["priority_id"],
                    priority_label=match["label"],
                    article_id=article.get("hash_id", f"article-{rank}"),
                    article_title=article["title"],
                    boost=match["boost"],
                    metadata={"run_id": run_id, "rank": rank, "article_score": article["score"]}
                )
                priority_matches_count += 1

        if priority_matches_count > 0:
            print(f"✅ Signal Store updated ({priority_matches_count} priority matches logged)")
        else:
            print(f"✅ Signal Store updated")
    else:
        print(f"🧪 Signal Store writes suppressed (dry run)")
    
    # Save to history (with duplicate protection)
    if not dry_run:
        save_to_history(top_articles)
    else:
        print("🧪 Skipping history update (dry run)")
    
    # Console output
    output = format_output(top_articles)
    print(output)
    
    # Save text output (production → curator_output.txt, dry run → curator_preview.txt)
    if not dry_run:
        output_file = "curator_output.txt"
        with open(output_file, "w") as f:
            f.write(output)
        print(f"💾 Results saved to {output_file}")
    else:
        output_file = "curator_preview.txt"
        with open(output_file, "w") as f:
            f.write(output)
        print(f"🧪 Preview text saved to {output_file}")
    
    # HTML generation
    run_mode = "dry-run" if dry_run else "production"
    html_content = format_html(top_articles, model=model, run_mode=run_mode)
    
    # Create dated archive (skip in dry run)
    import os
    from datetime import datetime
    
    if not dry_run:
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        archive_dir = "curator_archive"
        os.makedirs(archive_dir, exist_ok=True)
        
        archive_path = os.path.join(archive_dir, f"curator_{timestamp}.html")
        with open(archive_path, "w") as f:
            f.write(html_content)
        print(f"📁 Archive saved to {archive_path}")
        
        # Save as "latest" (fix relative paths for root directory)
        latest_file = "curator_latest.html"
        latest_html = html_content.replace('href="../', 'href="')
        with open(latest_file, "w") as f:
            f.write(latest_html)
        print(f"🔖 Latest briefing: {latest_file}")
        
        # Backward compatibility
        with open("curator_briefing.html", "w") as f:
            f.write(latest_html)
        
        # Generate index
        generate_index_page(archive_dir)
    else:
        # Dry run: save to preview file only (fix relative paths for root directory)
        preview_file = "curator_preview.html"
        preview_html = html_content.replace('href="../', 'href="')
        with open(preview_file, "w") as f:
            f.write(preview_html)
        print(f"🧪 Preview saved to {preview_file}")
        latest_file = preview_file  # For auto-open to work
    
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
        telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID', 'YOUR_TELEGRAM_CHAT_ID')
        
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
    
    # Final dry run reminder
    if dry_run:
        print()
        print("="*60)
        print("🧪 DRY RUN COMPLETE - No archive or history saved")
        print("="*60)

if __name__ == "__main__":
    main()
