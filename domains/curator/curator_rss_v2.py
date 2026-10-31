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
- mechanical: Deterministic keyword scoring (no model call)
- ollama: Legacy alias for mechanical scoring
- xai: Single-stage Grok 4.3 scoring (production default)
- haiku: Single-stage Claude Haiku 4.5 scoring
- sonnet: Haiku 4.5 pre-filter → Sonnet 4.5 ranking

COST NOTES:
- ollama has no per-token API charge; host compute is not included
- xAI production runs currently cost about $0.07–$0.09 per run
- cloud alternatives vary with the number and size of candidates

USAGE:
  python curator_rss_v2.py [options]

  Options:
    --model=mechanical   Use deterministic keyword scoring
    --model=ollama       Legacy alias for mechanical scoring
    --model=xai          Use xAI Grok 4.3 (production default)
    --model=grok-4.3     Explicit alias for the production xAI model
    --model=haiku        Use Anthropic Claude Haiku 4.5
    --model=sonnet       Use Anthropic Claude Sonnet 4.5
    
    --dry-run            Preview without saving (output to curator_preview.html)
                         Buttons disabled, no archive/history updates
    
    --fallback           Auto-fallback to mechanical scoring if API fails (for cron jobs)
                         Without this flag, API errors stop execution
    
    --telegram           Send to Telegram after completion
    --open               Auto-open HTML in browser
  
  Examples:
    # Free local test (dry run)
    python curator_rss_v2.py --dry-run --model=ollama
    
    # Production default (xAI Grok 4.3)
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

import sys
_BASE_DIR = Path(__file__).parent
REPO_ROOT = _BASE_DIR.parent.parent  # domains/curator -> domains -> repo root
sys.path.insert(0,str(REPO_ROOT))
from domains.curator.research.paths import configured as research_paths
_RESEARCH_DATA = research_paths().data
sys.path.insert(0, str(_BASE_DIR))    # sibling imports below (curator_config, curator_utils)
sys.path.insert(0, str(REPO_ROOT))    # utils.role import below

from curator_config import ACTIVE_DOMAIN
from curator_utils import get_telegram_token, send_telegram_alert
from core.get_secret import get_secret
from domains.curator import briefing_render as _briefing_render
from domains.curator import scoring_rules as _scoring_rules
from domains.curator import scoring_prompts as _scoring_prompts
from domains.curator import scoring_responses as _scoring_responses
from domains.curator import archive_index as _archive_index
from domains.curator import preference_profile as _preference_profile
from domains.curator.feed_records import normalize_feed_entries
from domains.curator.briefing_render import format_output, format_telegram
from utils.role import is_production as _is_production

# Curator user data directory — same convention as curator_server.py and curator_feedback.py.
# Override with CURATOR_DATA_DIR env var on EC2.
from domains.curator.data_paths import data_directory
_DATA_DIR = data_directory(REPO_ROOT)
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Cost logger — persists per-run API costs for cost_report.py
# ---------------------------------------------------------------------------
_COST_LOG = REPO_ROOT / 'curator_costs.json'

# Current xAI production model and standard token rates. Keep the requested model
# explicit: retired aliases can continue resolving while silently changing price
# and behavior at the provider boundary.
XAI_RANKING_MODEL = "grok-4.3"
XAI_INPUT_USD_PER_MILLION = 1.25
XAI_OUTPUT_USD_PER_MILLION = 2.50
HAIKU_INPUT_USD_PER_MILLION = 1.00
HAIKU_OUTPUT_USD_PER_MILLION = 5.00

SCORING_MODE_MAP = {
    'mechanical': 'mechanical',
    'ollama': 'mechanical',
    'xai': 'xai',
    XAI_RANKING_MODEL: 'xai',
    'haiku': 'ai',
    'sonnet': 'ai-two-stage',
}


def calculate_xai_cost(input_tokens: int, output_tokens: int) -> float:
    """Return the current standard token cost for the production xAI model."""
    return (
        input_tokens * XAI_INPUT_USD_PER_MILLION / 1_000_000
        + output_tokens * XAI_OUTPUT_USD_PER_MILLION / 1_000_000
    )


def calculate_haiku_cost(input_tokens: int, output_tokens: int) -> float:
    """Return Claude Haiku 4.5 cost at current standard token rates."""
    return (
        input_tokens * HAIKU_INPUT_USD_PER_MILLION / 1_000_000
        + output_tokens * HAIKU_OUTPUT_USD_PER_MILLION / 1_000_000
    )


def resolve_scoring_mode(model: str) -> str:
    """Resolve a supported CLI model name without silently accepting typos."""
    try:
        return SCORING_MODE_MAP[model]
    except KeyError as error:
        supported = ", ".join(SCORING_MODE_MAP)
        raise ValueError(
            f"Unsupported Curator model {model!r}. Supported values: {supported}"
        ) from error

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

    # Feb 18, 2026: Expanded source diversity
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",  # Non-Western perspective
    "ProPublica": "https://www.propublica.org/feeds/propublica/main",  # Investigative journalism
    "Antiwar.com": "https://news.antiwar.com/feed/",  # Contrarian/realist geopolitics
    # Investing.com removed — was DROP tier, wasted HTTP request each run
    "The Duran": "https://theduran.com/feed/",  # Contrarian analysis
    "O Globo": "https://oglobo.globo.com/rss.xml",  # Brazilian/Portuguese perspective

    # German sources (Feb 18, 2026)
    "Deutsche Welle": "https://rss.dw.com/xml/rss-en-all",  # Public broadcaster, English, no paywall
    "Spiegel International": "https://www.spiegel.de/international/index.rss",  # English, partial paywall
    "FAZ": "https://www.faz.net/rss/aktuell/",  # German, partial paywall
    "Die Welt": "https://www.welt.de/feeds/latest.rss",  # German, partial paywall

    # Institutional sources (Mar 13, 2026)
    "War on the Rocks": "https://warontherocks.com/feed/",  # Defense/security analysis
    "Foreign Affairs": "https://www.foreignaffairs.com/rss.xml",  # Premier geopolitics journal
    "arXiv q-fin": "https://rss.arxiv.org/rss/q-fin",  # Academic preprints, capped at 15
    "Just Security": "https://www.justsecurity.org/feed/",  # National security / international law
    "CEPR VoxEU": "https://cepr.org/rss/vox-content",  # Economic policy research columns

    # v1.1 additions (May 2026): source diversity + quality uplift
    "Crisis Group": "https://www.crisisgroup.org/rss.xml",  # Conflict analysis, already trusted
    # Chatham House: returns 403 on automated fetch — deferred, add when resolved
    "Adam Tooze Chartbook": "https://adamtooze.substack.com/feed",  # Macro/political economy newsletter
    "NY Fed Liberty Street": "https://libertystreeteconomics.newyorkfed.org/feed/",  # Fed research blog
    "Naked Capitalism": "https://www.nakedcapitalism.com/feed",  # Finance/macro commentary
}

# ── X post cap and account filter ─────────────────────────────────────────────
# Max X bookmark posts allowed in a single briefing (hard cap across both
# selection phases). Prevents X from dominating when handles each count
# as a distinct "source" in the diversity penalty logic.
X_POST_CAP = 4

# Minimum Grok score for any X post to be eligible for selection.
# Applied after scoring, before the selection loop.
X_POST_MIN_SCORE = 3.0

# Accounts excluded from the candidate pool regardless of score.
# Criteria: aggregators, redundant with RSS feeds already present, anonymous
# political commentary with no editorial accountability.
X_ACCOUNT_BLACKLIST = {
    "X/@ThomasSowell",   # Aggregator — not Sowell's account
    "X/@zerohedge",      # Redundant — ZeroHedge RSS already in FEEDS
    "X/@realMaalouf",    # Anonymous political commentary
    "X/@WarrenVsCCP",    # Anonymous political commentary
    "X/@A1Anduril",      # Anonymous defense commentary
    "X/@Myrmikan",       # Publishes bi-monthly PDFs by email only — wrong format
}

# Keywords for scoring (legacy mechanical mode)
from domains.curator.scoring_rules import KEYWORDS

# Deterministic category/boost/radar rules share one maintained module.
# Existing names remain available to CLI callers and the scoring pipeline.
from domains.curator.selection_rules import (
    CATEGORIES, CATEGORY_PRIORITY, assign_category, normalize_score,
    apply_interest_boost, apply_priorities_boost, find_radar_articles,
)

# ── Two-tier age penalty domains ───────────────────────────────────────────────
# SLOW: think tanks, academic, institutional, newsletters.
# These publish infrequently — a 90-day article may still be highly relevant.
# Any source domain NOT in this list is treated as FAST (news, X posts, blogs),
# where content older than 30 days is essentially dead.
from domains.curator.scoring_rules import SLOW_SOURCE_DOMAINS

def fetch_feed(name: str, url: str) -> List[Dict]:
    """Fetch and parse a single RSS feed"""
    print(f"📡 Fetching {name}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; RSS Reader Bot)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        entries = normalize_feed_entries(name, feed.entries)

        print(f"   ✅ {len(entries)} entries from {name}")
        return entries
    
    except Exception as e:
        print(f"   ❌ Error fetching {name}: {e}")
        return []


def _compute_age_multiplier(entry: Dict) -> float:
    """Compatibility adapter supplying the runtime clock and source policy."""
    return _scoring_rules.compute_age_multiplier(
        entry, now=datetime.now(timezone.utc), slow_domains=SLOW_SOURCE_DOMAINS,
    )


def score_entry_mechanical(entry: Dict) -> Dict:
    """Compatibility adapter retaining the existing category/normalizer hooks."""
    return _scoring_rules.score_entry_mechanical(
        entry, now=datetime.now(timezone.utc), keywords=KEYWORDS,
        normalizer=normalize_score, categorizer=assign_category,
    )

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
    """Resolve xAI through the platform credential contract.

    Production receives ``XAI_API_KEY`` from the authoritative
    ``/minimoi/production/xai_api_key`` SSM parameter through the deployment
    secret-sync step. Dev may use its environment or the ``xai/api_key``
    Keychain entry. No OpenClaw state file is part of this contract.
    """
    return get_secret("XAI_API_KEY", "xai", "api_key")

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
    prompt = _scoring_prompts.haiku_prompt(entries, user_profile)
    
    print(f"📡 Calling Haiku to score {len(entries)} articles...")
    
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        output = response.content[0].text.strip()
        
        # Parse output: "0|geo_major|8"
        scores = _scoring_responses.category_scores(output, len(entries))
        scores = {index: dict(score, method="haiku") for index, score in scores.items()}
        
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
        
        # Claude Haiku 4.5 standard pricing, reviewed 2026-08-17.
        cost = calculate_haiku_cost(input_tokens, output_tokens)
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
        
        # Send Telegram alert for critical errors (production only)
        if telegram_alert and _is_production():
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
    prompt = _scoring_prompts.prefilter_prompt(entries, user_profile)
    
    print(f"📡 Stage 1: Haiku pre-filter on {len(entries)} articles...")
    
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        
        output = response.content[0].text.strip()
        
        # Parse output
        scores = _scoring_responses.category_scores(output, len(entries))
        
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
        cost = calculate_haiku_cost(input_tokens, output_tokens)
        
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
    prompt = _scoring_prompts.sonnet_prompt(entries, user_profile)
    
    print(f"📡 Stage 2: Sonnet ranking on {len(entries)} candidates...")
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        
        output = response.content[0].text.strip()
        
        # Parse output
        scores = _scoring_responses.ranking_scores(output, len(entries))
        
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


def score_entries_xai(entries: List[Dict], fallback_on_error: bool = False, user_profile: str = "", model: str = XAI_RANKING_MODEL, temperature: float = 0.0) -> List[Dict]:
    """
    Score all entries using xAI Grok (batch processing)
    
    Args:
        entries: List of article entries
        fallback_on_error: If True, silently fall back to mechanical on API errors
        user_profile: User preference profile text
        model: xAI model to use. Production uses grok-4.3 explicitly.
    
    Returns list of dicts with 'score', 'category', 'method' = 'xai'
    """
    import json
    
    # Use the shared platform secret contract; Curator must not depend on an
    # OpenClaw-managed state file merely because both happen to use xAI.
    try:
        api_key = get_xai_api_key()
    except Exception as e:
        error_msg = f"""
❌ xAI API key could not be resolved through the platform credential helper!

Error: {e}

To fix:
  Production: verify /minimoi/production/xai_api_key and rerun the secret-sync step
  Dev: set XAI_API_KEY or the xai/api_key Keychain entry

To test with mechanical mode instead:
  python curator_rss_v2.py --model=ollama
"""
        if fallback_on_error:
            print("⚠️  xAI API key unavailable, falling back to mechanical")
            return [score_entry_mechanical(e) for e in entries]
        else:
            print(error_msg)
            raise ValueError("xAI API key unavailable through platform credential helper")

    # Import the optional OpenAI-compatible client only after credential
    # resolution succeeds. This keeps the documented mechanical fallback
    # available in minimal environments where the cloud client is absent.
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    # Build prompt with Grok's optimized instructions
    prompt = _scoring_prompts.xai_prompt(entries, user_profile)
    
    print(f"📡 Calling xAI {model} to score {len(entries)} articles...")
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=temperature
        )
        
        content = response.choices[0].message.content
        usage = response.usage
        effective_model = getattr(response, "model", None) or model
        print(f"   Input: {usage.prompt_tokens} tokens, Output: {usage.completion_tokens} tokens")
        cost = calculate_xai_cost(usage.prompt_tokens, usage.completion_tokens)
        print(f"   Effective model: {effective_model}")
        print(f"   Cost: ${cost:.4f}")
        log_curator_cost(f'xai-{effective_model}', 'curator', usage.prompt_tokens, usage.completion_tokens, cost)
        
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
    scores = _scoring_responses.category_scores(content, len(entries), lowercase=True)
    # Preserve xAI's existing neutral fallback, attached to the missing index.
    return [
        dict(scores.get(index, {"category": "other", "score": 5.0}),
             method="xai", raw_score=scores.get(index, {"score": 5.0})["score"])
        for index in range(len(entries))
    ]

def load_active_interests():
    """
    Load all active (non-expired) interests from interests/ directory.
    Returns dict mapping categories/keywords to score modifiers.
    """
    import re
    from pathlib import Path
    from datetime import datetime
    
    interests_dir = REPO_ROOT / "interests"
    
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

    prefs_path = _DATA_DIR / 'curator_preferences.json'

    try:
        with open(prefs_path) as f:
            prefs = json.load(f)
    except Exception:
        return ""

    return _preference_profile.format_user_profile(
        prefs, active_domain=ACTIVE_DOMAIN, now=datetime.now(timezone.utc), min_weight=min_weight,
    )


def load_priorities():
    """
    Load active (non-expired) priorities from workspace priorities.json.
    Returns list of active priority dicts.
    """
    from pathlib import Path
    from datetime import datetime
    import json
    
    priorities_file = _DATA_DIR / "priorities.json"
    
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


# ── Source trust helpers (Workstream 1) ──────────────────────────────────────

def _load_source_trust() -> dict:
    """Load curator_sources.json → {domain: trust_tier} dict."""
    path = Path(__file__).parent / 'curator_sources.json'
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text())
        return {e['domain']: e['trust'] for e in entries if 'domain' in e and 'trust' in e}
    except Exception:
        return {}

_TRUST_MULTIPLIERS = {'trusted': 1.5, 'neutral': 1.0, 'deprioritize': 0.5, 'probationary': 0.6}

def _domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith('www.') else netloc
    except Exception:
        return ''


# ── Web search enrichment helper (Workstream 2) ───────────────────────────────

def _fetch_web_search_candidates(
    seen_hashes: set,
    priority_limit: int = 3,
    results_per_priority: int = 5,
    baseline_total: int = 5,
) -> List[Dict]:
    """
    Fetch Brave web search candidates for the daily briefing pool.

    Two query sources (priority queries first, baseline fills after):
      1. Active priority keywords — up to priority_limit priorities,
         results_per_priority results each (max 15 total)
      2. Static baseline topics  — baseline_total results spread across
         6 evergreen queries (max 5 total)

    Results are domain-whitelist filtered and deduped against seen_hashes.
    Each result tagged with source_type='web_search'.
    seen_hashes is mutated in-place to prevent cross-source duplication.
    """
    # Lazy import avoids circular dependency:
    # curator_priority_feed imports curator_rss_v2 at module level.
    try:
        from curator_priority_feed import brave_search, DOMAIN_WHITELIST
    except ImportError:
        print("⚠️  Web search: curator_priority_feed not available, skipping")
        return []

    BASELINE_TOPICS = [
        'geopolitics', 'monetary policy', 'emerging markets',
        'AI economy', 'energy markets', 'US foreign policy',
    ]

    candidates = []
    seen_urls: set = set()

    def _to_entry(r: dict, query_label: str):
        url = r.get('url', '')
        if not url:
            return None
        domain = _domain_from_url(url)
        if domain not in DOMAIN_WHITELIST:
            return None
        h = hashlib.md5(url.encode()).hexdigest()[:5]
        if h in seen_hashes or url in seen_urls:
            return None
        seen_urls.add(url)
        return {
            'hash_id':     h,
            'source':      domain,
            'source_type': 'web_search',
            'title':       r.get('title', '').strip(),
            'link':        url,
            'summary':     r.get('description', '').strip(),
            'published':   None,
            'query_label': query_label,
        }

    # 1. Priority queries (load_priorities() already filters active + non-expired)
    for priority in load_priorities()[:priority_limit]:
        keywords = priority.get('keywords', [])
        if not keywords:
            continue
        label = priority.get('label', priority.get('id', ''))
        raw = brave_search(' '.join(keywords), count=results_per_priority * 2)
        time.sleep(1)  # avoid Brave rate-limit between queries
        added = 0
        for r in raw:
            if added >= results_per_priority:
                break
            entry = _to_entry(r, f"priority:{label}")
            if entry:
                candidates.append(entry)
                seen_hashes.add(entry['hash_id'])
                added += 1
        print(f"  🔍 Web [{label}]: {added} candidates")

    # 2. Baseline topics (spread baseline_total across all topics)
    per_topic = max(1, (baseline_total + len(BASELINE_TOPICS) - 1) // len(BASELINE_TOPICS))
    baseline_added = 0
    for topic in BASELINE_TOPICS:
        if baseline_added >= baseline_total:
            break
        raw = brave_search(topic, count=per_topic * 2)
        time.sleep(1)  # avoid Brave rate-limit between queries
        for r in raw:
            if baseline_added >= baseline_total:
                break
            entry = _to_entry(r, f"baseline:{topic}")
            if entry:
                candidates.append(entry)
                seen_hashes.add(entry['hash_id'])
                baseline_added += 1
    if baseline_added:
        print(f"  🌐 Web baseline: {baseline_added} candidates")

    return candidates


def get_active_topic_tags() -> dict:
    """Return {slug: [match_terms]} for all active-pull Topics. Reads JSON — no DB needed.
    Used for dormant section routing: string-match remaining candidates against these terms.
    Returns empty dict gracefully if the threads directory doesn't exist.

    Match terms are derived from:
      1. Explicit tags on the thread record (when populated)
      2. Slug tokens (split on '-', drop noise words) — reliable fallback since
         slugs like 'gold-geopolitics' or 'china-rise' are descriptive by design.
    """
    _NOISE = {"not", "and", "or", "the", "a", "an", "of", "in", "for", "to", "vs"}
    threads_dir = _RESEARCH_DATA / "threads"
    if not threads_dir.exists():
        return {}
    result = {}
    for thread_file in threads_dir.glob("*/thread.json"):
        try:
            t = json.loads(thread_file.read_text())
            if t.get("status") in ("active", "active-pull"):
                slug = t.get("slug", thread_file.parent.name)
                explicit_tags = [tag.lower() for tag in t.get("tags", []) if tag]
                slug_terms = [w for w in slug.lower().split("-") if w and w not in _NOISE]
                result[slug] = explicit_tags if explicit_tags else slug_terms
        except Exception:
            pass
    return result


def curate(top_n: int = 20, diversity_weight: float = 0.3, mode: str = 'mechanical',
           fallback_on_error: bool = False, xai_model: str = XAI_RANKING_MODEL,
           temperature: float = 0.0, return_pool: bool = False):
    """
    Fetch all feeds, score, rank, return top N.

    Args:
        top_n: Number of articles to return
        diversity_weight: How much to penalize source over-representation (0-1)
        mode: 'mechanical', 'ai', 'ai-two-stage', 'xai', or 'hybrid'
        fallback_on_error: Auto-fallback to mechanical if API fails
        return_pool: If True, return (top_articles, all_scored_entries) tuple
                     instead of just top_articles. Used by dormant-section routing.
        xai_model: Which xAI model to use. Production uses grok-4.3.
    
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

    # Merge X bookmark articles into candidate pool (Phase 3C.6)
    # Dedup by hash_id (MD5 of URL) — RSS articles take precedence on collision
    # Apply account blacklist: aggregators, redundant, anonymous commentary (v1.1)
    from scripts.x.x_to_article import load_x_bookmark_articles
    seen_hashes = {e['hash_id'] for e in all_entries if e.get('hash_id')}
    x_raw = load_x_bookmark_articles()
    x_after_dedup = [a for a in x_raw if a['hash_id'] not in seen_hashes]
    x_blacklisted_count = sum(1 for a in x_after_dedup if a.get('source') in X_ACCOUNT_BLACKLIST)
    x_articles = [a for a in x_after_dedup if a.get('source') not in X_ACCOUNT_BLACKLIST]
    all_entries.extend(x_articles)
    print(f"📎 X bookmarks merged: {len(x_articles)} articles "
          f"({len(x_raw)} signals → {len(x_after_dedup)} after dedup "
          f"→ {len(x_articles)} after blacklist [{x_blacklisted_count} removed])")

    # Web search enrichment — active priority keywords + static baseline topics
    print(f"\n🔍 Web search enrichment:")
    _ws_seen = {e['hash_id'] for e in all_entries if e.get('hash_id')}
    web_candidates = _fetch_web_search_candidates(seen_hashes=_ws_seen)
    if web_candidates:
        all_entries.extend(web_candidates)
        print(f"   Pool after web search: {len(all_entries)} candidates")
    else:
        print(f"   No web candidates added (Brave unavailable or all filtered)")

    # Apply source trust: load trust table and drop 'drop'-tier domains before scoring
    _source_trust = _load_source_trust()
    if _source_trust:
        before_drop = len(all_entries)
        all_entries = [
            e for e in all_entries
            if _source_trust.get(_domain_from_url(e.get('link', '')), 'neutral') != 'drop'
        ]
        dropped = before_drop - len(all_entries)
        if dropped:
            print(f"🚫 Source trust: dropped {dropped} entries (drop tier)")

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
        # Single-stage xAI Grok scoring.
        results = score_entries_xai(all_entries, fallback_on_error=fallback_on_error, user_profile=user_profile, model=xai_model, temperature=temperature)
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

    # Apply source trust multipliers to scores (post-scoring, all modes)
    if _source_trust:
        boosted = 0
        for entry in all_entries:
            domain = _domain_from_url(entry.get('link', ''))
            tier = _source_trust.get(domain, 'neutral')
            multiplier = _TRUST_MULTIPLIERS.get(tier, 1.0)
            if multiplier != 1.0:
                entry['raw_score'] = entry.get('raw_score', entry['score'])
                entry['score'] = round(entry['score'] * multiplier, 2)
                entry['trust_tier'] = tier
                boosted += 1
        if boosted:
            print(f"⚖️  Source trust: applied multipliers to {boosted} entries")

    # Apply two-tier age penalty (SLOW: think tanks/academic/institutional vs FAST: news/X/blogs)
    # Applied after trust multipliers so the combined score is adjusted together.
    age_penalized = 0
    for entry in all_entries:
        multiplier = _compute_age_multiplier(entry)
        if multiplier < 1.0:
            entry['score'] = round(entry['score'] * multiplier, 2)
            entry['age_multiplier'] = multiplier
            age_penalized += 1
    if age_penalized:
        print(f"⏰  Age penalty applied to {age_penalized} entries")

    # Filter X posts below minimum score threshold (after scoring + all multipliers)
    x_before_filter = len(all_entries)
    all_entries = [
        e for e in all_entries
        if not (e.get('content_type') == 'x_bookmark' and e.get('score', 0) < X_POST_MIN_SCORE)
    ]
    x_score_filtered = x_before_filter - len(all_entries)
    if x_score_filtered:
        print(f"✂️  Filtered {x_score_filtered} X posts below min score ({X_POST_MIN_SCORE})")

    # Load serendipity reserve setting
    serendipity_reserve = 0.20  # Default
    try:
        prefs_path = _DATA_DIR / 'curator_preferences.json'
        with open(prefs_path) as f:
            prefs = json.load(f)
            serendipity_reserve = prefs.get('curation_settings', {}).get('serendipity_reserve', 0.20)
    except Exception:
        pass  # Use default if file not found
    
    from domains.curator.selection_rules import select_articles
    selected = select_articles(all_entries, top_n, diversity_weight, serendipity_reserve,
                               active_interests=active_interests, active_priorities=active_priorities,
                               source_trust=_source_trust, x_post_cap=X_POST_CAP,
                               domain_from_url=_domain_from_url, interest_boost_fn=apply_interest_boost,
                               priorities_boost_fn=apply_priorities_boost, report=print)

    if return_pool:
        return selected, all_entries
    return selected


def format_html(entries: List[Dict], model: str = "xai", run_mode: str = "production",
                radar_articles: List[Dict] = None) -> str:
    """Compatibility entrypoint; the runtime owns the configured model label."""
    return _briefing_render.format_html(
        entries, model=model, run_mode=run_mode, radar_articles=radar_articles,
        xai_ranking_model=XAI_RANKING_MODEL,
    )
def generate_index_page(archive_dir: str):
    """Compatibility entrypoint for archive-index filesystem generation."""
    return _archive_index.generate_index_page(archive_dir)
def main():
    """Run the curator and display results"""
    import sys
    import subprocess
    from signal_store import get_session_id, log_article_scored, log_priority_match
    from utils.role import is_production, role_label

    role = role_label()
    print(f"[curator_rss_v2] Starting as {role}")

    if not is_production():
        print(f"[curator_rss_v2] STANDBY — scheduled run suppressed (MINIMOI_ROLE={role})")
        return

    # Parse command line args first (dry_run needed before session init)
    send_telegram = "--telegram" in sys.argv
    auto_open = "--open" in sys.argv
    fallback_on_error = "--fallback" in sys.argv
    dry_run = "--dry-run" in sys.argv

    # Model selection (default: xai)
    # --model=[mechanical|ollama|xai|grok-4.3|haiku|sonnet] controls which scorer is used.
    model = 'xai'
    for arg in sys.argv:
        if arg.startswith('--model='):
            model = arg.split('=')[1]
            break

    # Resolve explicitly so a typo cannot silently select a different model.
    try:
        mode = resolve_scoring_mode(model)
        if model == 'ollama':
            print('--model=ollama is a legacy alias for mechanical keyword scoring; no Ollama model is called')
    except ValueError as error:
        print(f"\n💥 {error}")
        sys.exit(2)
    
    xai_model_variant = XAI_RANKING_MODEL

    # Temperature setting (default: 0 for deterministic production runs)
    # Temperature 1.0 — wider selection, A/B test vs 0.7 (week of Mar 11, 2026)
    temperature = 1.0
    for arg in sys.argv:
        if arg.startswith('--temperature='):
            temperature = float(arg.split('=')[1])
            break

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
        top_articles, all_scored = curate(top_n=20, mode=mode, fallback_on_error=fallback_on_error,
                                           xai_model=xai_model_variant, temperature=temperature,
                                           return_pool=True)
    except (ValueError, RuntimeError) as e:
        # API error with no fallback - exit with error
        print(f"\n💥 Curation failed: {e}")
        print("\nTip: Run with --model=mechanical to use keyword scoring without a model API")
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
                model=xai_model_variant if mode == 'xai' else model,
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
        # Production sets CURATOR_OUTPUT_PATH into the mounted data/curator
        # folder so the day's briefing text survives container replacement (#305).
        output_file = os.environ.get("CURATOR_OUTPUT_PATH") or "curator_output.txt"
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
    # ── Dormant / On Radar pass ───────────────────────────────────────────────
    # Deterministic tag-match against remaining candidates. No AI, no scoring.
    # One item, one place — top_articles excluded by hash_id.
    topic_tags   = get_active_topic_tags()
    top_ids      = {a.get("hash_id") or a.get("link", "") for a in top_articles}
    radar_articles = find_radar_articles(all_scored, top_ids, topic_tags, cap=10)
    if radar_articles:
        print(f"📡 On Radar: {len(radar_articles)} article(s) matching active Topics")
    # ─────────────────────────────────────────────────────────────────────────

    html_content = format_html(top_articles, model=model, run_mode=run_mode,
                               radar_articles=radar_articles or None)
    
    # Create dated archive (skip in dry run). `os` is the module-level import:
    # a local `import os` here would make `os` local to all of main() and break
    # the CURATOR_OUTPUT_PATH read above with UnboundLocalError (#305).
    from datetime import datetime
    
    if not dry_run:
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        archive_dir = "curator_archive"
        os.makedirs(archive_dir, exist_ok=True)
        
        archive_path = os.path.join(archive_dir, f"curator_{timestamp}.html")
        with open(archive_path, "w") as f:
            f.write(html_content)
        print(f"📁 Archive saved to {archive_path}")
        
        # Save as "latest" — anchor to repo root so cwd never matters
        _repo_root = Path(__file__).parent
        latest_file = str(_repo_root / "curator_latest.html")
        latest_html = html_content.replace('href="../', 'href="')
        with open(latest_file, "w") as f:
            f.write(latest_html)
        print(f"🔖 Latest briefing: {latest_file}")

        # Backward compatibility — static pre-render at repo root
        # NOTE: this is curator_briefing.html (root), NOT templates/curator_briefing.html
        # The Jinja2 template in templates/ is never touched by this script.
        with open(_repo_root / "curator_briefing.html", "w") as f:
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
    
    # Write today's briefing pool for intelligence layer (WS5 Phase B)
    if not dry_run:
        try:
            if top_articles:
                top_articles[0]['briefing_model'] = xai_model_variant if mode == 'xai' else model
                # Persist the generation date with the briefing itself. Host
                # wrappers may still stamp it for compatibility, but the web UI
                # and AI Observations must not depend on a later Telegram step.
                from datetime import timezone as _timezone
                top_articles[0]['briefing_date'] = datetime.now(_timezone.utc).date().isoformat()
            with open(_DATA_DIR / 'curator_latest.json', 'w') as f:
                json.dump(top_articles, f, indent=2, default=str)
            print(f"💾 Wrote {len(top_articles)} articles to curator_latest.json")
        except Exception as e:
            print(f"⚠️  Could not write curator_latest.json: {e}")

        # Write radar articles so the Flask server can surface them in the briefing
        try:
            with open(_DATA_DIR / 'curator_radar.json', 'w') as f:
                json.dump(radar_articles, f, indent=2, default=str)
            if radar_articles:
                print(f"📡 Wrote {len(radar_articles)} radar article(s) to curator_radar.json")
        except Exception as e:
            print(f"⚠️  Could not write curator_radar.json: {e}")

        # Also save a dated JSON copy so the web UI can serve today/yesterday toggle
        try:
            from pathlib import Path as _Path
            archive_dir = _Path('curator_archive')
            archive_dir.mkdir(exist_ok=True)
            dated_json = archive_dir / f"curator_{datetime.now().strftime('%Y-%m-%d')}.json"
            with open(dated_json, 'w') as f:
                json.dump(top_articles, f, indent=2, default=str)
            print(f"💾 Wrote JSON archive to {dated_json}")
        except Exception as e:
            print(f"⚠️  Could not write JSON archive: {e}")

    # Final dry run reminder
    if dry_run:
        print()
        print("="*60)
        print("🧪 DRY RUN COMPLETE - No archive or history saved")
        print("="*60)

if __name__ == "__main__":
    main()
