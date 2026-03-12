#!/usr/bin/env python3
"""
x_to_article.py — Phase 3C.6: X bookmark signal normalizer.

Reads curator_signals.json and converts X bookmark signals into the same
article schema used by RSS entries in curator_rss_v2.py, so they can be
merged into the curate() candidate pool and scored alongside RSS articles.

Signal routing:
  - destination_text_source='fetched'  → article title + body text
  - destination_text_source='fallback' → tweet text (URL existed, body failed)
  - tweet-only, >= 50 chars            → tweet text
  - tweet-only, < 50 chars             → excluded

  # ~23-char signals are bare t.co URLs not resolved at ingest
  # Excluded by 50-char filter — future pass could resolve these
  # See Issue #4 notes for context

Output schema (matches fetch_feed() in curator_rss_v2.py):
    hash_id      — 5-char MD5 of destination_url or tweet URL
    source       — 'X/@username'
    title        — article title (if fetched) or tweet_text[:80]
    link         — destination_url or tweet URL
    summary      — destination_text or tweet_text (scorer uses first 200 chars)
    published    — None (X bookmarks have no reliable publication date)
    content_type — 'x_bookmark' (identifies origin for dedup and tracking)

Usage:
    python x_to_article.py               # summary + 3 sample articles
    python x_to_article.py --show=10     # more samples
    python x_to_article.py --json        # full JSON dump of all articles
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR  = Path(__file__).parent
SIGNALS_FILE = PROJECT_DIR / 'curator_signals.json'

# Minimum tweet text length to include a tweet-only signal in the scoring pool.
# ~23-char signals are bare t.co URLs not resolved at ingest.
# Excluded by this filter — future pass could resolve these. See Issue #4.
TWEET_TEXT_MIN_CHARS = 50


# ── Core normalizer ───────────────────────────────────────────────────────────

def _hash_id(url: str) -> str:
    """5-char MD5 of URL — matches hash_id format used by fetch_feed()."""
    return hashlib.md5(url.encode('utf-8')).hexdigest()[:5]


def signal_to_article(sig: dict) -> Optional[dict]:
    """
    Convert one X bookmark signal to RSS article schema.
    Returns None if the signal should be excluded from the scoring pool.
    """
    meta       = sig.get('metadata', {})
    lc         = meta.get('linked_content')
    tweet_text = meta.get('tweet_text', '')
    source     = sig.get('source', 'X/@unknown')
    tweet_url  = sig.get('url', '')

    if lc and not lc.get('enrichment_failed'):
        dest_url = lc.get('destination_url', tweet_url)
        dest_src = lc.get('destination_text_source', '')

        if dest_src == 'fetched':
            # Real article body retrieved — use article title + body text
            title   = lc.get('title') or tweet_text[:80] or 'X bookmark'
            summary = lc['destination_text']
        else:
            # Destination URL exists but body wasn't fetchable (paywall, 403, etc.)
            # Use tweet text as the scoring signal
            if len(tweet_text) < TWEET_TEXT_MIN_CHARS:
                return None
            title   = tweet_text[:80]
            summary = tweet_text

        return {
            'hash_id':      _hash_id(dest_url),
            'source':       source,
            'title':        title,
            'link':         dest_url,
            'summary':      summary,
            'published':    None,
            'content_type': 'x_bookmark',
        }

    else:
        # Tweet-only signal — no external URL resolved at ingest
        if len(tweet_text) < TWEET_TEXT_MIN_CHARS:
            # ~23-char signals are bare t.co URLs not resolved at ingest.
            # Excluded — future pass could resolve these. See Issue #4.
            return None
        return {
            'hash_id':      _hash_id(tweet_url),
            'source':       source,
            'title':        tweet_text[:80],
            'link':         tweet_url,
            'summary':      tweet_text,
            'published':    None,
            'content_type': 'x_bookmark',
        }


def load_x_bookmark_articles() -> list[dict]:
    """
    Load all X bookmark signals and return them as article dicts.

    Called by curate() in curator_rss_v2.py to merge X bookmarks into
    the scoring candidate pool. Excluded signals are silently dropped.

    Returns:
        List of article dicts ready for scoring alongside RSS articles.
    """
    if not SIGNALS_FILE.exists():
        return []
    signals = json.loads(SIGNALS_FILE.read_text())
    return [a for sig in signals if (a := signal_to_article(sig)) is not None]


# ── Standalone inspection ─────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='X bookmark normalizer — inspect output')
    parser.add_argument('--show', type=int, default=3, help='Sample articles to display (default 3)')
    parser.add_argument('--json', action='store_true',  help='Dump full JSON output for all articles')
    args = parser.parse_args()

    if not SIGNALS_FILE.exists():
        print(f'ERROR: signals file not found: {SIGNALS_FILE}')
        raise SystemExit(1)

    signals  = json.loads(SIGNALS_FILE.read_text())
    articles = load_x_bookmark_articles()

    # Compute breakdown stats from raw signals (articles list loses the routing info)
    fetched_count    = 0
    fallback_count   = 0
    tweet_only_count = 0
    excluded_count   = 0

    for sig in signals:
        meta = sig.get('metadata', {})
        lc   = meta.get('linked_content')
        tw   = meta.get('tweet_text', '')

        if lc and not lc.get('enrichment_failed'):
            if lc.get('destination_text_source') == 'fetched':
                fetched_count += 1
            elif len(tw) >= TWEET_TEXT_MIN_CHARS:
                fallback_count += 1
            else:
                excluded_count += 1
        else:
            if len(tw) >= TWEET_TEXT_MIN_CHARS:
                tweet_only_count += 1
            else:
                excluded_count += 1

    if args.json:
        print(json.dumps(articles, indent=2, default=str))
        raise SystemExit(0)

    print(f'\n{"="*56}')
    print('X Bookmark Articles — Normalizer Summary')
    print(f'{"="*56}')
    print(f'Input signals:            {len(signals)}')
    print(f'Articles produced:        {len(articles)}')
    print(f'Excluded (< {TWEET_TEXT_MIN_CHARS} chars):       {excluded_count}')
    print()
    print('Breakdown by content source:')
    print(f'  Fetched article text:   {fetched_count}')
    print(f'  Tweet fallback (URL):   {fallback_count}')
    print(f'  Tweet-only text:        {tweet_only_count}')
    print()

    show_n = min(args.show, len(articles))
    print(f'Sample articles ({show_n}):')
    for i, a in enumerate(articles[:show_n], 1):
        print(f'\n[{i}] source:   {a["source"]}')
        print(f'    hash_id:  {a["hash_id"]}')
        print(f'    title:    {a["title"][:70]!r}')
        print(f'    link:     {a["link"][:70]}')
        print(f'    summary:  {a["summary"][:120]!r}...')
    print()
