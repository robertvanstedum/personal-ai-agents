#!/usr/bin/env python3
"""
curator_utils.py - Utility functions for curator system

Includes validation, diagnostics, URL enrichment, media download,
and LLM text analysis helpers.
"""

import json
import logging
import os
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

log = logging.getLogger(__name__)


# ── URL Enrichment ────────────────────────────────────────────────────────────

def extract_tco_urls(text: str) -> List[str]:
    """Extract all t.co URLs from tweet text."""
    return re.findall(r'https://t\.co/[A-Za-z0-9]+', text)


def follow_redirect(tco_url: str, timeout: int = 5) -> str:
    """Follow t.co redirect chain to final destination URL. Raises on failure."""
    resp = requests.head(
        tco_url,
        allow_redirects=True,
        timeout=timeout,
        headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
    )
    return resp.url


def fetch_url_metadata(url: str, timeout: int = 10) -> Dict[str, str]:
    """
    Fetch og:title and og:description from a destination URL.
    Returns {'title': ..., 'preview': ...}. Never raises — empty strings on failure.
    """
    if not _BS4_AVAILABLE:
        log.warning('beautifulsoup4 not installed — skipping metadata fetch')
        return {'title': '', 'preview': ''}
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
        )
        soup = BeautifulSoup(resp.content, 'html.parser')

        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
        else:
            tag = soup.find('title')
            title = tag.string if tag else ''

        og_desc = soup.find('meta', property='og:description')
        preview = og_desc['content'] if og_desc and og_desc.get('content') else ''

        return {'title': str(title or '')[:200], 'preview': str(preview or '')[:500]}
    except Exception as e:
        log.warning(f'fetch_url_metadata failed for {url}: {e}')
        return {'title': '', 'preview': ''}


def fetch_destination_text(url: str, tweet_text: str, timeout: int = 15) -> dict:
    """
    Fetch readable article body from a destination URL using <p> tag extraction.

    Falls back to tweet_text on any failure:
    - HTTP error (4xx, 5xx), timeout, connection error
    - Extracted body too short (likely paywall or JS-rendered page)

    Returns:
        {
            'text':       str,        # article body or tweet_text fallback
            'source':     str,        # 'fetched' | 'tweet_fallback'
            'char_count': int,
            'error':      str | None, # None on success, error message on fallback
        }
    """
    if not _BS4_AVAILABLE:
        log.warning('beautifulsoup4 not installed — using tweet fallback')
        return {'text': tweet_text, 'source': 'tweet_fallback',
                'char_count': len(tweet_text), 'error': 'bs4 not available'}
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')

        # Strip boilerplate before extracting paragraphs
        for tag in soup(['nav', 'header', 'footer', 'script', 'style', 'aside']):
            tag.decompose()

        # Collect meaningful paragraph text (skip very short snippets)
        paragraphs = [
            p.get_text(separator=' ', strip=True)
            for p in soup.find_all('p')
            if len(p.get_text(strip=True)) > 40
        ]
        body_text = ' '.join(paragraphs)[:2000]  # cap for scoring context

        if len(body_text) < 100:
            error = f'Body too short ({len(body_text)} chars) — likely paywall or JS-rendered'
            log.warning(f'fetch_destination_text: {error} — {url}')
            return {'text': tweet_text, 'source': 'tweet_fallback',
                    'char_count': len(tweet_text), 'error': error}

        return {'text': body_text, 'source': 'fetched',
                'char_count': len(body_text), 'error': None}

    except requests.exceptions.Timeout:
        err = f'Timeout after {timeout}s'
    except requests.exceptions.ConnectionError as e:
        err = f'Connection error: {e}'
    except requests.exceptions.HTTPError as e:
        err = f'HTTP {e.response.status_code}'
    except Exception as e:
        err = f'Unexpected: {e}'

    log.warning(f'fetch_destination_text failed for {url}: {err} — using tweet fallback')
    return {'text': tweet_text, 'source': 'tweet_fallback',
            'char_count': len(tweet_text), 'error': err}


def extract_domain(url: str) -> str:
    """Return clean domain from URL, e.g. 'https://www.ft.com/...' → 'ft.com'."""
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain
    except Exception:
        return ''


def classify_source_type(url: str) -> str:
    """
    Classify URL into a source type based on domain/path patterns.
    Returns: academic_paper | pdf_document | news_article | video | substack | web_article
    """
    domain = extract_domain(url)
    url_lower = url.lower()

    if any(x in domain for x in [
        'ssrn.com', 'arxiv.org', 'nber.org', 'bis.org',
        'brookings.edu', 'imf.org', 'worldbank.org',
        'federalreserve.gov', 'ecb.europa.eu', 'stlouisfed.org',
    ]):
        return 'academic_paper'

    if url_lower.endswith('.pdf') or '/pdf/' in url_lower:
        return 'pdf_document'

    if any(x in domain for x in [
        'ft.com', 'wsj.com', 'bloomberg.com', 'reuters.com',
        'economist.com', 'nytimes.com', 'theguardian.com',
        'washingtonpost.com', 'politico.com', 'foreignaffairs.com',
        'foreignpolicy.com', 'theatlantic.com', 'zerohedge.com',
        'marketwatch.com', 'barrons.com',
    ]):
        return 'news_article'

    if 'youtube.com' in domain or 'youtu.be' in domain:
        return 'video'

    if 'substack.com' in domain or domain.endswith('.substack.com'):
        return 'substack'

    return 'web_article'


# ── Media Download ────────────────────────────────────────────────────────────

# Domains that host tweet media — images are downloadable directly
_TWITTER_MEDIA_DOMAINS = {'pbs.twimg.com', 'ton.twimg.com'}


def download_image(source_url: str, local_path: str, timeout: int = 15) -> bool:
    """
    Download an image from source_url to local_path.
    Creates parent directories as needed.
    Returns True on success, False on failure (never raises).
    """
    try:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(
            source_url,
            timeout=timeout,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
            stream=True,
        )
        resp.raise_for_status()
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        log.warning(f'download_image failed for {source_url}: {e}')
        return False


# ── LLM Text Analysis ─────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = """\
You are extracting structured signals from a bookmarked tweet.

Tweet by {source}:
{tweet_text}

Return a JSON object with these fields:
- topics: list of 3-5 specific topic tags (e.g. "treasury_bonds", "dollar_strength", "labor_market", "ai_regulation")
- entities: list of named entities mentioned (institutions, assets, people, policies)
- signal_type: one of: macro_chart_commentary | market_analysis | geopolitical | academic_reference | opinion | news_summary | tech_announcement | other

Return only valid JSON, no explanation."""


def analyze_text_haiku(tweet_text: str, source: str) -> Optional[Dict]:
    """
    Send tweet text to Claude Haiku for topic/entity extraction.
    Returns dict with {topics, entities, signal_type} or None on failure.
    """
    try:
        import keyring
        from anthropic import Anthropic

        api_key = keyring.get_password('anthropic', 'api_key') or os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            log.warning('No Anthropic API key — skipping text analysis')
            return None

        client = Anthropic(api_key=api_key)
        prompt = _ANALYSIS_PROMPT.format(source=source, tweet_text=tweet_text)

        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=256,
            messages=[{'role': 'user', 'content': prompt}],
        )

        raw = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = re.sub(r'^```[a-z]*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        return json.loads(raw)

    except Exception as e:
        log.warning(f'analyze_text_haiku failed: {e}')
        return None


def validate_signal_store_correlation(n: int = 10, signal_store_path: str = None) -> bool:
    """
    Validate that article_id values correlate correctly between scored and feedback events.
    
    This is a sanity check to catch ID format mismatches that would break Stage 3 learning.
    
    Args:
        n: Number of recent events to check (default 10)
        signal_store_path: Path to signal_store.jsonl (defaults to ./signal_store.jsonl)
    
    Returns:
        True if validation passes, False otherwise
    
    Prints loud, clear diagnostics to stdout.
    """
    if signal_store_path is None:
        signal_store_path = Path(__file__).parent / "signal_store.jsonl"
    else:
        signal_store_path = Path(signal_store_path)
    
    if not signal_store_path.exists():
        print(f"⚠️  Signal Store not found: {signal_store_path}")
        print(f"   This is OK if curator hasn't run yet.")
        return True
    
    # Read events
    scored_ids: Set[str] = set()
    feedback_ids: Set[str] = set()
    scored_by_id: Dict[str, dict] = {}
    feedback_by_id: Dict[str, List[dict]] = defaultdict(list)
    
    print(f"\n📊 Signal Store Correlation Validator")
    print(f"=" * 60)
    print(f"Reading from: {signal_store_path}")
    
    with open(signal_store_path, 'r') as f:
        for line in f:
            try:
                event = json.loads(line.strip())
                event_type = event.get('event')
                article_id = event.get('article_id')
                
                if not article_id:
                    continue
                
                if event_type == 'article_scored':
                    scored_ids.add(article_id)
                    scored_by_id[article_id] = event
                elif event_type == 'feedback':
                    feedback_ids.add(article_id)
                    feedback_by_id[article_id].append(event)
                    
            except json.JSONDecodeError:
                continue
    
    print(f"\n📈 Event Summary:")
    print(f"   Scored events: {len(scored_ids)} unique article IDs")
    print(f"   Feedback events: {len(feedback_ids)} unique article IDs")
    
    # Check for correlation
    matched = scored_ids & feedback_ids
    unmatched_feedback = feedback_ids - scored_ids
    unmatched_scored = scored_ids - feedback_ids
    
    print(f"\n🔗 Correlation Check:")
    print(f"   Matched IDs (scored + feedback): {len(matched)}")
    print(f"   Feedback with no scored event: {len(unmatched_feedback)}")
    print(f"   Scored with no feedback: {len(unmatched_scored)}")
    
    # Detailed diagnostics if problems found
    validation_passed = True
    
    if unmatched_feedback:
        print(f"\n❌ CORRELATION FAILURE: {len(unmatched_feedback)} feedback events have no matching scored event")
        print(f"\n   This means Stage 3 learning will fail to correlate feedback with articles.")
        print(f"\n   Unmatched feedback article_ids (first {min(5, len(unmatched_feedback))}):")
        
        for i, article_id in enumerate(list(unmatched_feedback)[:5], 1):
            feedback_event = feedback_by_id[article_id][0]
            print(f"      {i}. ID: {article_id}")
            print(f"         Title: {feedback_event.get('title', 'N/A')}")
            print(f"         Source: {feedback_event.get('source', 'N/A')}")
            print(f"         Channel: {feedback_event.get('channel', 'N/A')}")
        
        print(f"\n   Format analysis:")
        print(f"      Scored IDs sample: {list(scored_ids)[:3]}")
        print(f"      Feedback IDs sample: {list(unmatched_feedback)[:3]}")
        
        validation_passed = False
    
    if matched:
        print(f"\n✅ {len(matched)} article(s) have both scored and feedback events")
        print(f"\n   Sample matched articles (first {min(3, len(matched))}):")
        
        for i, article_id in enumerate(list(matched)[:3], 1):
            scored = scored_by_id[article_id]
            feedback_list = feedback_by_id[article_id]
            print(f"      {i}. ID: {article_id}")
            print(f"         Title: {scored.get('title', 'N/A')[:60]}...")
            print(f"         Scored: {scored.get('score')} ({scored.get('model')})")
            print(f"         Feedback: {len(feedback_list)} event(s) - {[f.get('action') for f in feedback_list]}")
    
    # Format consistency check
    print(f"\n🔍 Format Consistency Check:")
    
    scored_formats = analyze_id_formats(scored_ids)
    feedback_formats = analyze_id_formats(feedback_ids)
    
    print(f"   Scored ID formats:")
    for fmt, count in scored_formats.items():
        print(f"      {fmt}: {count} IDs")
    
    print(f"   Feedback ID formats:")
    for fmt, count in feedback_formats.items():
        print(f"      {fmt}: {count} IDs")
    
    # Check if formats match
    if scored_formats.keys() != feedback_formats.keys() and feedback_ids:
        print(f"\n   ⚠️  WARNING: Scored and feedback events use different ID formats!")
        print(f"      This suggests a schema mismatch that will break correlation.")
        validation_passed = False
    
    # Final verdict
    print(f"\n" + "=" * 60)
    if validation_passed:
        print(f"✅ VALIDATION PASSED: Signal Store correlation is healthy")
    else:
        print(f"❌ VALIDATION FAILED: Fix ID correlation before Stage 3")
    print(f"=" * 60 + "\n")
    
    return validation_passed


def analyze_id_formats(ids: Set[str]) -> Dict[str, int]:
    """
    Analyze article_id formats to detect patterns.
    
    Returns dict mapping format description to count.
    """
    formats = defaultdict(int)
    
    for article_id in ids:
        if not article_id:
            formats['empty'] += 1
        elif article_id.startswith('fallback-'):
            formats['fallback-{source}-{date}-{rank}'] += 1
        elif article_id.startswith('test_'):
            formats['test_*'] += 1
        elif article_id.startswith('article-'):
            formats['article-{rank}'] += 1
        elif len(article_id) == 5 and all(c in '0123456789abcdef' for c in article_id):
            formats['hash5 (MD5 URL)'] += 1
        elif '-' in article_id and len(article_id.split('-')) >= 4:
            formats['{source}-{date}-{rank}'] += 1
        else:
            formats['other/unknown'] += 1
    
    return dict(formats)


# ── Telegram helpers (Workstream 5 — shared by curator_rss_v2 and curator_intelligence) ──

def get_telegram_token() -> str:
    """
    Get Telegram bot token from (in priority order):
    1. macOS Keychain (via keyring)
    2. Environment variable TELEGRAM_BOT_TOKEN

    Returns empty string if not found.
    """
    try:
        import keyring
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get('TELEGRAM_BOT_TOKEN', '')


def send_telegram_alert(message: str, chat_id: str = None) -> bool:
    """
    Send a message via Telegram (HTML parse_mode).

    Args:
        message:  Message text (HTML formatting supported)
        chat_id:  Telegram chat ID (defaults to TELEGRAM_CHAT_ID env var)

    Returns:
        True if sent successfully, False otherwise
    """
    if chat_id is None:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    token = get_telegram_token()
    if not token:
        print("⚠️  No Telegram token found, skipping alert")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        print("✅ Telegram alert sent")
        return True
    except Exception as e:
        print(f"⚠️  Failed to send Telegram alert: {e}")
        return False


if __name__ == '__main__':
    # Self-test: run validation on current Signal Store
    print("🧪 Running Signal Store correlation validator...")
    validate_signal_store_correlation(n=10)
