#!/usr/bin/env python3
"""
enrich_signals.py — Phase 3C: Bookmark enrichment pipeline.

Fetches bookmarks from the X API, follows article links, downloads
media images, analyzes tweet text with Haiku, and writes:

  curator_signals.json       — enriched signal store
  curator_url_cache.json     — destination URL cache (graph edges in waiting)
  curator_domain_registry.json — aggregated domain knowledge
  curator_media/             — downloaded chart/photo images

Safe to re-run: already-enriched signals are skipped.

Usage:
    python enrich_signals.py --dry-run                         # preview, no writes
    python enrich_signals.py --limit=20                        # default: 20 most recent bookmarks
    python enrich_signals.py --limit=100                       # larger batch
    python enrich_signals.py --all                             # all bookmarks (paginated, up to 400)
    python enrich_signals.py --folder "Finance and geopolitics" # folder-only (Premium feature)
    python enrich_signals.py --skip-analysis                   # skip Haiku text analysis
    python enrich_signals.py --skip-media                      # skip image downloads
    python enrich_signals.py --full                            # re-enrich all (ignore existing)
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import tweepy

from curator_utils import (
    extract_domain,
    classify_source_type,
    fetch_url_metadata,
    download_image,
    analyze_text_haiku,
)
from x_oauth2_authorize import get_valid_token

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR          = Path(__file__).parent
SIGNALS_FILE         = PROJECT_DIR / 'curator_signals.json'
URL_CACHE_FILE       = PROJECT_DIR / 'curator_url_cache.json'
DOMAIN_REGISTRY_FILE = PROJECT_DIR / 'curator_domain_registry.json'
MEDIA_DIR            = PROJECT_DIR / 'curator_media'
PREFS_FILE           = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'

# ── Domains to skip for article enrichment (tweet self-links, media hosts) ───
SKIP_DOMAINS = {
    'x.com', 'twitter.com', 't.co',
    'pic.twitter.com', 'pbs.twimg.com', 'ton.twimg.com',
    'bit.ly', 'tinyurl.com', 'ow.ly', 'buff.ly',
}

# ── Domains that host downloadable media (charts, photos) ─────────────────
MEDIA_DOMAINS = {'pbs.twimg.com', 'ton.twimg.com'}

# ── Polite crawl delay between HTTP requests ──────────────────────────────
CRAWL_DELAY = 0.5

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def today_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


# ── X API fetch ───────────────────────────────────────────────────────────────

def fetch_bookmarks(limit: int) -> tuple[list, dict, dict]:
    """
    Fetch up to `limit` bookmarks via the X bookmarks API, paginating as needed.
    X API max is 100 per request — fetches multiple pages for limits > 100.

    Returns:
        tweets   — list of tweet objects (up to limit)
        authors  — {author_id: username}
        media    — {media_key: {type, url, alt_text}}
    """
    token  = get_valid_token()
    client = tweepy.Client(bearer_token=token)

    tweets  = []
    authors = {}
    media   = {}
    next_token = None
    page = 0

    while len(tweets) < limit:
        page += 1
        batch_size = min(100, limit - len(tweets))  # X API max is 100 per call
        log.info(f'Fetching page {page} ({batch_size} bookmarks)...')

        resp = client.get_bookmarks(
            max_results=batch_size,
            pagination_token=next_token,
            tweet_fields=['text', 'author_id', 'created_at', 'entities', 'attachments'],
            expansions=['author_id', 'attachments.media_keys'],
            user_fields=['username'],
            media_fields=['type', 'url', 'preview_image_url', 'alt_text'],
        )

        if not resp.data:
            break  # No more bookmarks

        tweets.extend(resp.data)

        if resp.includes:
            for u in (resp.includes.get('users') or []):
                authors[str(u.id)] = u.username
            for m in (resp.includes.get('media') or []):
                media[m.media_key] = {
                    'type':    m.type,
                    'url':     getattr(m, 'url', None) or getattr(m, 'preview_image_url', None),
                    'alt_text': getattr(m, 'alt_text', None),
                }

        # Check for next page
        next_token = resp.meta.get('next_token') if resp.meta else None
        if not next_token:
            break  # Reached end of bookmarks

    log.info(f'Fetched {len(tweets)} bookmarks across {page} page(s)')
    return tweets[:limit], authors, media


# ── X API: folder mapping (Premium) ──────────────────────────────────────────

def fetch_folder_mapping() -> dict[str, str]:
    """
    Build a {tweet_id: folder_name} map across ALL bookmark folders.

    X folder API returns up to 20 tweet IDs per folder — no pagination.
    One API call per folder (typically 5–10 folders).

    Returns:
        mapping — {tweet_id: folder_name}  (65 entries across 5 folders in practice)
    """
    token = get_valid_token()
    headers = {'Authorization': f'Bearer {token}'}

    # Authenticated user ID
    me = requests.get('https://api.twitter.com/2/users/me', headers=headers, timeout=10)
    me.raise_for_status()
    user_id = me.json()['data']['id']

    # List all folders
    folders_resp = requests.get(
        f'https://api.twitter.com/2/users/{user_id}/bookmarks/folders',
        headers=headers, timeout=10
    )
    folders_resp.raise_for_status()
    folders = folders_resp.json().get('data', [])
    log.info(f'Found {len(folders)} bookmark folders')

    mapping: dict[str, str] = {}
    for f in folders:
        ids_resp = requests.get(
            f'https://api.twitter.com/2/users/{user_id}/bookmarks/folders/{f["id"]}',
            headers=headers, timeout=10
        )
        ids_resp.raise_for_status()
        ids = [t['id'] for t in (ids_resp.json().get('data') or [])]
        for tid in ids:
            mapping[tid] = f['name']
        log.info(f'  "{f["name"]}": {len(ids)} tweets')

    log.info(f'Folder mapping: {len(mapping)} total tagged tweet IDs')
    return mapping


# ── Signal building ───────────────────────────────────────────────────────────

def build_signal(tweet, authors: dict, media_map: dict,
                 url_cache: dict, domain_registry: dict,
                 dry_run: bool, skip_analysis: bool, skip_media: bool,
                 folder: str | None = None) -> dict:
    """
    Build one enriched signal from a tweet object.
    Mutates url_cache and domain_registry in place.

    folder — bookmark folder name if this tweet was filed into one, else None.
    """
    tweet_id  = str(tweet.id)
    username  = authors.get(str(tweet.author_id), str(tweet.author_id))
    source    = f'X/@{username}'
    tweet_text = tweet.text or ''

    signal = {
        'tweet_id':   tweet_id,
        'source':     source,
        'url':        f'https://x.com/{username}/status/{tweet_id}',
        'action':     'save',
        'folder':     folder,
        'fetched_at': now_iso(),
        'metadata': {
            'tweet_text':    tweet_text,
            'linked_content': None,
            'media':          [],
            'text_analysis':  None,
        },
    }

    # ── Article URL enrichment ─────────────────────────────────────────────
    entities = tweet.entities or {}
    url_list = (
        getattr(entities, 'urls', None)
        or (entities.get('urls', []) if isinstance(entities, dict) else [])
        or []
    )

    for u in url_list:
        expanded = (
            getattr(u, 'expanded_url', None)
            or (u.get('expanded_url') if isinstance(u, dict) else None)
            or ''
        )
        domain = extract_domain(expanded)
        if not domain or domain in SKIP_DOMAINS:
            continue

        # First external URL only
        if url_cache.get(expanded):
            cached = url_cache[expanded]
            signal['metadata']['linked_content'] = {
                'destination_url': expanded,
                'domain':          domain,
                'source_type':     cached['source_type'],
                'title':           cached['title'],
                'content_preview': cached.get('content_preview', ''),
                'enriched_at':     cached['enriched_at'],
                'enrichment_failed': cached.get('enrichment_failed', False),
            }
            if tweet_id not in cached.get('referenced_by_tweets', []):
                url_cache[expanded].setdefault('referenced_by_tweets', []).append(tweet_id)
        else:
            if not dry_run:
                time.sleep(CRAWL_DELAY)
            meta = {} if dry_run else fetch_url_metadata(expanded)
            source_type = classify_source_type(expanded)
            failed      = not dry_run and not meta.get('title')

            linked = {
                'destination_url':  expanded,
                'domain':           domain,
                'source_type':      source_type,
                'title':            meta.get('title', ''),
                'content_preview':  meta.get('preview', ''),
                'enriched_at':      now_iso(),
                'enrichment_failed': failed,
            }
            signal['metadata']['linked_content'] = linked

            url_cache[expanded] = {
                'domain':              domain,
                'source_type':         source_type,
                'title':               meta.get('title', ''),
                'content_preview':     meta.get('preview', ''),
                'first_seen':          now_iso(),
                'enriched_at':         now_iso(),
                'referenced_by_tweets': [tweet_id],
                'led_to_deep_dive':    False,
                'enrichment_failed':   failed,
            }

        # Update domain registry
        if domain not in domain_registry:
            domain_registry[domain] = {
                'source_type':      classify_source_type(expanded),
                'first_seen':       today_iso(),
                'times_referenced': 0,
                'confirmed_trusted': False,
            }
        domain_registry[domain]['times_referenced'] += 1

        break  # first external URL only

    # ── Media download ────────────────────────────────────────────────────
    attachments = getattr(tweet, 'attachments', None) or {}
    media_keys  = (
        attachments.get('media_keys', []) if isinstance(attachments, dict)
        else getattr(attachments, 'media_keys', []) or []
    )

    for idx, key in enumerate(media_keys or []):
        if key not in media_map:
            continue
        item      = media_map[key]
        image_url = item.get('url')

        media_entry = {
            'media_key':    key,
            'type':         item.get('type'),
            'source_url':   image_url,
            'local_path':   None,
            'alt_text':     item.get('alt_text'),
            'downloaded_at': None,
            'chart_analysis': None,   # gate for future vision re-run
        }

        if image_url and not skip_media and not dry_run:
            local_path = str(MEDIA_DIR / f'{tweet_id}_{idx}.jpg')
            ok = download_image(image_url, local_path)
            if ok:
                media_entry['local_path']   = local_path
                media_entry['downloaded_at'] = now_iso()
                log.info(f'  Downloaded: {local_path}')
            else:
                log.warning(f'  Download failed: {image_url}')

        signal['metadata']['media'].append(media_entry)

    # ── Text analysis ─────────────────────────────────────────────────────
    if not skip_analysis and not dry_run and tweet_text.strip():
        result = analyze_text_haiku(tweet_text, source)
        if result:
            result['analyzed_at'] = now_iso()
            result['backend']     = 'haiku'
            signal['metadata']['text_analysis'] = result

    return signal


# ── Learned patterns update ───────────────────────────────────────────────────

def update_learned_patterns(signals: list):
    """
    Merge content_domains, source_types, and content_topics from enriched
    signals into learned_patterns in curator_preferences.json.
    """
    if not PREFS_FILE.exists():
        log.warning(f'Preferences file not found: {PREFS_FILE}')
        return

    prefs    = json.loads(PREFS_FILE.read_text())
    patterns = prefs.setdefault('learned_patterns', {})
    domains  = patterns.setdefault('content_domains', {})
    types    = patterns.setdefault('source_types', {})
    topics   = patterns.setdefault('content_topics', {})

    def bump(bucket: dict, key: str):
        if not key:
            return
        bucket.setdefault(key, {'like': 0, 'save': 0, 'dislike': 0})
        bucket[key]['save'] += 1

    for sig in signals:
        meta = sig.get('metadata', {})

        lc = meta.get('linked_content')
        if lc and not lc.get('enrichment_failed'):
            bump(domains, lc.get('domain'))
            bump(types,   lc.get('source_type'))

        ta = meta.get('text_analysis')
        if ta:
            for topic in (ta.get('topics') or []):
                bump(topics, topic)

    PREFS_FILE.write_text(json.dumps(prefs, indent=2))
    log.info(f'learned_patterns updated: {len(domains)} domains, {len(types)} source types, {len(topics)} topics')


# ── Folder tag backfill ───────────────────────────────────────────────────────

def backfill_folder_tags(dry_run: bool = False):
    """
    Fetch the current folder mapping and stamp a `folder` field onto every
    existing signal in curator_signals.json.

    Signals already in a folder get their folder name; all others get null.
    Signals that already have a non-null folder field are left unchanged
    (so manual overrides survive a re-run).
    """
    if not SIGNALS_FILE.exists():
        log.warning('No signals file found — nothing to backfill')
        return

    print(f'\n{"="*56}')
    print('Backfilling folder tags into existing signals...')
    print(f'{"="*56}\n')

    folder_map = fetch_folder_mapping()

    signals = json.loads(SIGNALS_FILE.read_text())
    tagged = 0
    already_set = 0
    untagged = 0

    for sig in signals:
        tid = sig.get('tweet_id', '')
        if sig.get('folder') is not None:
            already_set += 1
            continue
        folder_name = folder_map.get(tid)
        sig['folder'] = folder_name
        if folder_name:
            tagged += 1
        else:
            untagged += 1

    if not dry_run:
        SIGNALS_FILE.write_text(json.dumps(signals, indent=2))

    print(f'Backfill complete:')
    print(f'  Tagged with folder:  {tagged}')
    print(f'  No folder (null):    {untagged}')
    print(f'  Already had folder:  {already_set}')
    if dry_run:
        print('  (dry run — nothing written)')

    # Show folder breakdown
    from collections import Counter
    counts = Counter(s.get('folder') for s in signals)
    print(f'\nFolder breakdown:')
    for name, count in sorted(counts.items(), key=lambda x: -(x[1])):
        label = name or '(unorganized)'
        print(f'  {label:<30} {count}')
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def enrich_signals(limit: int = 20, dry_run: bool = False,
                   skip_analysis: bool = False, skip_media: bool = False,
                   full: bool = False):

    print(f'\n{"="*56}')
    if dry_run:
        print('DRY RUN — no files will be written')
    pages = (limit + 99) // 100
    print(f'Fetching up to {limit} bookmarks (~{pages} API page{"s" if pages > 1 else ""})...')
    print(f'{"="*56}\n')

    # Load existing data
    existing_signals: list = json.loads(SIGNALS_FILE.read_text()) if SIGNALS_FILE.exists() else []
    url_cache:        dict = json.loads(URL_CACHE_FILE.read_text())        if URL_CACHE_FILE.exists()        else {}
    domain_registry:  dict = json.loads(DOMAIN_REGISTRY_FILE.read_text()) if DOMAIN_REGISTRY_FILE.exists() else {}

    existing_ids = {s['tweet_id'] for s in existing_signals} if not full else set()

    # Fetch folder mapping so new signals get tagged on arrival
    print('Fetching bookmark folder mapping...')
    try:
        folder_map = fetch_folder_mapping()
    except Exception as e:
        log.warning(f'Could not fetch folder mapping: {e} — signals will have folder=null')
        folder_map = {}

    # Fetch from X
    tweets, authors, media_map = fetch_bookmarks(limit)
    print(f'Received {len(tweets)} bookmarks\n')

    enriched = 0
    skipped  = 0
    new_signals = []

    for i, tweet in enumerate(tweets, 1):
        tweet_id = str(tweet.id)
        username = authors.get(str(tweet.author_id), str(tweet.author_id))

        if tweet_id in existing_ids:
            print(f'[{i:2d}] @{username:<20} SKIP (already enriched)')
            skipped += 1
            continue

        folder = folder_map.get(tweet_id)
        folder_label = f' [{folder}]' if folder else ''
        print(f'[{i:2d}] @{username:<20} enriching...{folder_label}')

        signal = build_signal(
            tweet, authors, media_map,
            url_cache, domain_registry,
            dry_run, skip_analysis, skip_media,
            folder=folder,
        )

        lc = signal['metadata'].get('linked_content')
        ta = signal['metadata'].get('text_analysis')
        media_count = len(signal['metadata'].get('media', []))

        if lc and not lc.get('enrichment_failed'):
            print(f'       article: {lc["domain"]} — {lc["title"][:50]!r}')
        if ta:
            print(f'       topics:  {ta.get("topics", [])}')
        if media_count:
            print(f'       media:   {media_count} item(s)')

        new_signals.append(signal)
        enriched += 1

    # Write files
    if not dry_run:
        MEDIA_DIR.mkdir(exist_ok=True)
        all_signals = existing_signals + new_signals
        SIGNALS_FILE.write_text(json.dumps(all_signals, indent=2))
        URL_CACHE_FILE.write_text(json.dumps(url_cache, indent=2))
        DOMAIN_REGISTRY_FILE.write_text(json.dumps(domain_registry, indent=2))
        update_learned_patterns(new_signals)

    print(f'\n{"="*56}')
    print(f'Enrichment complete')
    print(f'  Enriched: {enriched}')
    print(f'  Skipped:  {skipped}')
    print(f'  URLs cached:  {len(url_cache)}')
    print(f'  Domains:      {len(domain_registry)}')
    if dry_run:
        print('  (dry run — nothing written)')
    print(f'{"="*56}\n')

    return enriched, skipped


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Enrich X bookmarks with URL metadata and text analysis')
    parser.add_argument('--limit',         type=int, default=20,  help='Number of bookmarks to fetch (default 20)')
    parser.add_argument('--all',           action='store_true',   help='Fetch all bookmarks (up to 400, paginated)')
    parser.add_argument('--tag-folders',   action='store_true',   help='Backfill folder tags on existing signals (no re-enrichment)')
    parser.add_argument('--dry-run',       action='store_true',   help='Preview only — write nothing')
    parser.add_argument('--skip-analysis', action='store_true',   help='Skip Haiku text analysis')
    parser.add_argument('--skip-media',    action='store_true',   help='Skip image downloads')
    parser.add_argument('--full',          action='store_true',   help='Re-enrich all (ignore existing signals)')
    args = parser.parse_args()

    if args.tag_folders:
        backfill_folder_tags(dry_run=args.dry_run)
    else:
        limit = 400 if args.all else args.limit
        enrich_signals(
            limit=limit,
            dry_run=args.dry_run,
            skip_analysis=args.skip_analysis,
            skip_media=args.skip_media,
            full=args.full,
        )
