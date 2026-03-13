#!/usr/bin/env python3
"""
x_pull_incremental.py - Incremental X bookmark pull (Phase 3C.7).

Fetches bookmarks added since the last pull, enriches each with destination
text where possible, and appends new signals to curator_signals.json.

Usage:
    python x_pull_incremental.py              # real run, all new bookmarks
    python x_pull_incremental.py --dry-run    # print what would be fetched, touch nothing
    python x_pull_incremental.py --limit=5    # real run, cap at 5 new signals
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import tweepy

from curator_utils import (
    classify_source_type,
    extract_domain,
    extract_tco_urls,
    fetch_destination_text,
    fetch_url_metadata,
    follow_redirect,
)
from x_oauth2_authorize import get_valid_token

# ── Paths ─────────────────────────────────────────────────────────────────────

SIGNALS_FILE = Path(__file__).parent / 'curator_signals.json'
STATE_FILE   = Path(__file__).parent / 'x_pull_state.json'

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
log = logging.getLogger(__name__)


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load x_pull_state.json, or return default dict if file missing."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        'last_pull_at':             None,
        'last_pull_count':          0,
        'total_incremental_pulled': 0,
    }


def save_state(state: dict) -> None:
    """Write state atomically via tmp-rename."""
    tmp = STATE_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(STATE_FILE)


# ── Signals ───────────────────────────────────────────────────────────────────

def load_existing_signals() -> list:
    """Load curator_signals.json. Returns [] if file missing."""
    if not SIGNALS_FILE.exists():
        return []
    return json.loads(SIGNALS_FILE.read_text())


def save_signals(signals: list) -> None:
    """Write signal list atomically via tmp-rename."""
    tmp = SIGNALS_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(signals, indent=2))
    tmp.rename(SIGNALS_FILE)


# ── Enrichment ────────────────────────────────────────────────────────────────

def _resolve_destination_url(tweet_text: str, entities) -> str | None:
    """
    Find the destination URL for a tweet.

    Strategy:
    1. Check tweet entities.urls for an already-expanded URL (non-X)
    2. Fall back to following the first t.co URL in tweet text ourselves
    3. Return None if no destination URL found
    """
    # entities is a dict when returned by tweepy v4 (e.g. {'urls': [...], ...})
    if entities:
        raw_urls = []
        if isinstance(entities, dict):
            raw_urls = entities.get('urls', [])
        elif hasattr(entities, 'urls') and entities.urls:
            raw_urls = entities.urls

        for u in raw_urls:
            expanded = u.get('expanded_url', '') if isinstance(u, dict) else getattr(u, 'expanded_url', '')
            # Skip t.co wrappers, x.com tweet self-links, and pic.twitter.com
            if (expanded
                    and 'x.com' not in expanded
                    and 'twitter.com' not in expanded
                    and 'pic.twitter.com' not in expanded):
                return expanded

    # Fall back: follow t.co redirect from tweet text
    tco_urls = extract_tco_urls(tweet_text)
    if tco_urls:
        try:
            resolved = follow_redirect(tco_urls[0])
            # Make sure it's not just a t.co or x.com self-link
            if 'x.com' not in resolved and 'twitter.com' not in resolved:
                return resolved
        except Exception as e:
            log.warning(f'follow_redirect failed for {tco_urls[0]}: {e}')

    return None


def enrich_signal(tweet_text: str, entities) -> dict | None:
    """
    Resolve destination URL and fetch metadata + body text.

    Returns a linked_content dict (destination_text_source always set),
    or None if no destination URL is found (tweet-only signal).
    """
    dest_url = _resolve_destination_url(tweet_text, entities)
    if dest_url is None:
        return None

    domain      = extract_domain(dest_url)
    source_type = classify_source_type(dest_url)
    now_iso     = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

    # og:title + og:description
    meta = fetch_url_metadata(dest_url)

    # Article body text (always returns source + error, never raises)
    body = fetch_destination_text(dest_url, tweet_text)

    return {
        'destination_url':         dest_url,
        'domain':                  domain,
        'source_type':             source_type,
        'title':                   meta['title'],
        'content_preview':         meta['preview'],
        'enriched_at':             now_iso,
        'enrichment_failed':       False,
        'destination_text':        body['text'],
        'destination_text_source': body['source'],
        'destination_text_error':  body['error'],
        'destination_text_at':     now_iso,
    }


def build_signal(tweet, authors: dict, fetched_at: str) -> dict:
    """
    Build a signal dict matching the exact schema of the 398 historical signals.

    Top-level fields: tweet_id, source, url, action, fetched_at, metadata, folder
    metadata fields:  tweet_text, linked_content, media, text_analysis
    """
    username  = authors.get(tweet.author_id, str(tweet.author_id))
    tweet_url = f'https://x.com/{username}/status/{tweet.id}'
    text      = tweet.text.strip()
    entities  = getattr(tweet, 'entities', None)

    linked_content = enrich_signal(text, entities)

    return {
        'tweet_id':   str(tweet.id),
        'source':     f'X/@{username}',
        'url':        tweet_url,
        'action':     'save',
        'fetched_at': fetched_at,
        'metadata': {
            'tweet_text':     text,
            'linked_content': linked_content,
            'media':          [],    # no download in incremental pull
            'text_analysis':  None,  # no Haiku analysis in incremental pull
        },
        'folder': None,
    }


# ── Pull ──────────────────────────────────────────────────────────────────────

def pull(dry_run: bool = False, limit: int | None = None) -> None:
    """Fetch new bookmarks and append to curator_signals.json."""

    state   = load_state()
    last_at = state.get('last_pull_at')
    print(f"State: last_pull_at={last_at}, "
          f"total_incremental_pulled={state['total_incremental_pulled']}")

    # Auth
    token  = get_valid_token()
    client = tweepy.Client(bearer_token=token)

    # Load existing signals
    existing_signals   = load_existing_signals()
    existing_tweet_ids = {s['tweet_id'] for s in existing_signals}
    print(f"Existing signals: {len(existing_signals)} "
          f"(dedup set: {len(existing_tweet_ids)} tweet IDs)")

    me = client.get_me(user_auth=False)
    print(f"Pulling bookmarks for @{me.data.username}...\n")

    # Parse last_pull_at for early-stop optimization
    last_pull_dt = None
    if last_at:
        try:
            last_pull_dt = datetime.fromisoformat(last_at.replace('Z', '+00:00'))
            if last_pull_dt.tzinfo is None:
                last_pull_dt = last_pull_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            log.warning(f'Could not parse last_pull_at: {last_at!r}')

    fetched_at      = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    new_signals     = []
    authors         = {}
    page            = 0
    next_token      = None
    stop_early      = False
    hit_limit_flag  = False   # True if --limit was reached (incomplete run)
    tweets_seen     = 0

    while not stop_early:
        page += 1
        kwargs = dict(
            max_results=100,
            tweet_fields=['text', 'author_id', 'created_at', 'entities'],
            expansions=['author_id'],
            user_fields=['username'],
        )
        if next_token:
            kwargs['pagination_token'] = next_token

        resp = client.get_bookmarks(**kwargs)

        # Collect authors from this page's expansions
        if resp.includes and 'users' in resp.includes:
            for u in resp.includes['users']:
                authors[u.id] = u.username

        if not resp.data:
            print(f"  Page {page}: no data — end of bookmarks")
            break

        tweets_seen += len(resp.data)
        print(f"  Page {page}: {len(resp.data)} bookmarks "
              f"(running total seen: {tweets_seen})")

        for tweet in resp.data:
            tid = str(tweet.id)

            # Early-stop: X returns bookmarks newest-first; if this tweet
            # predates last_pull_at the rest of the page will too
            if last_pull_dt and tweet.created_at:
                tweet_dt = tweet.created_at
                if tweet_dt.tzinfo is None:
                    tweet_dt = tweet_dt.replace(tzinfo=timezone.utc)
                if tweet_dt < last_pull_dt:
                    print(f"  → tweet {tid} predates last_pull_at ({last_at})"
                          f" — stopping pagination early")
                    stop_early = True
                    break

            # Dedup — covers historical 398 + any prior incremental pulls
            if tid in existing_tweet_ids:
                continue

            # Limit cap
            if limit is not None and len(new_signals) >= limit:
                print(f"  → --limit={limit} reached, stopping")
                stop_early     = True
                hit_limit_flag = True   # don't set last_pull_at — run is incomplete
                break

            # Preview for progress output
            username = authors.get(tweet.author_id, str(tweet.author_id))
            preview  = tweet.text.strip()[:70]
            print(f"  + @{username}: {preview!r}")

            if not dry_run:
                sig = build_signal(tweet, authors, fetched_at)
                new_signals.append(sig)
                existing_tweet_ids.add(tid)   # prevent double-add within same run

            else:
                # Dry-run: record intent only, touch nothing
                new_signals.append({'tweet_id': tid, '_dry_run': True,
                                    '_username': username, '_preview': preview})

        # Paginate if more results available
        if not stop_early and resp.meta and resp.meta.get('next_token'):
            next_token = resp.meta['next_token']
            time.sleep(1)   # courtesy pause for rate limits
        else:
            break

    # ── Results ───────────────────────────────────────────────────────────────

    label = 'DRY-RUN' if dry_run else 'DONE'
    print(f"\n[{label}] Tweets seen on API: {tweets_seen}")
    print(f"[{label}] New signals {'would add' if dry_run else 'added'}: {len(new_signals)}")

    if dry_run:
        print("\n--- DRY-RUN COMPLETE — nothing written ---")
        return

    if not new_signals:
        print("No new signals. Updating state timestamp.")
        if not hit_limit_flag:
            state['last_pull_at'] = fetched_at
        state['last_pull_count'] = 0
        save_state(state)
        return

    # Write new signals
    all_signals = existing_signals + new_signals
    save_signals(all_signals)
    print(f"  curator_signals.json: {len(existing_signals)} → {len(all_signals)} signals")

    # Enrichment breakdown
    fetched_count  = sum(
        1 for s in new_signals
        if s.get('metadata', {}).get('linked_content') and
        s['metadata']['linked_content'].get('destination_text_source') == 'fetched'
    )
    fallback_count = sum(
        1 for s in new_signals
        if s.get('metadata', {}).get('linked_content') and
        s['metadata']['linked_content'].get('destination_text_source') == 'tweet_fallback'
    )
    tweet_only_count = sum(
        1 for s in new_signals
        if not s.get('metadata', {}).get('linked_content')
    )
    print(f"  Enrichment breakdown:")
    print(f"    fetched body text:  {fetched_count}")
    print(f"    tweet fallback:     {fallback_count}")
    print(f"    tweet-only (no URL): {tweet_only_count}")

    # Update state
    # Don't advance last_pull_at on an incomplete run (--limit used) — the next
    # full run must not stop early before picking up the remaining new signals.
    if not hit_limit_flag:
        state['last_pull_at'] = fetched_at
    state['last_pull_count']          = len(new_signals)
    state['total_incremental_pulled'] = (
        state.get('total_incremental_pulled', 0) + len(new_signals)
    )
    save_state(state)
    print(f"  x_pull_state.json updated: "
          f"last_pull_at={state['last_pull_at']}, "
          f"total={state['total_incremental_pulled']}"
          + (' (last_pull_at not advanced — incomplete run)' if hit_limit_flag else ''))
    print("\nDone.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Incremental X bookmark pull — Phase 3C.7'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print what would be fetched/added; touch nothing',
    )
    parser.add_argument(
        '--limit', type=int, default=None, metavar='N',
        help='Cap at N new signals (real run; incompatible with --dry-run)',
    )
    args = parser.parse_args()

    if args.dry_run and args.limit is not None:
        parser.error('--dry-run and --limit are mutually exclusive')

    pull(dry_run=args.dry_run, limit=args.limit)


if __name__ == '__main__':
    main()
