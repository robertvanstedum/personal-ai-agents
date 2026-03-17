#!/usr/bin/env python3
"""
curator_server.py - Flask server for curator feedback and library

Usage:
    python curator_server.py
    
Then open curator_latest.html or curator_library.html in browser
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
import re

BASE_DIR = Path(__file__).parent
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Shared navigation HTML
SHARED_NAV_HTML = """
  <nav class="header-nav">
    <a href="/" class="nav-link {briefing_active}">Daily</a>
    <a href="/curator_library.html" class="nav-link {library_active}">Library</a>
    <a href="/interests/2026/deep-dives/index.html" class="nav-link {deepdives_active}">Deep Dives</a>
    <a href="/curator_priorities.html" class="nav-link {priorities_active}">Priorities</a>
    <a href="/curator_intelligence.html" class="nav-link {intelligence_active}">AI Observations</a>
  </nav>
"""

# ─────────────────────────────────────────────────────────────────────────────
# Legacy feedback endpoints (converted from old HTTP server)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    """Handle like/dislike/save feedback (both old GET and new POST)"""
    
    # New POST endpoint with article data
    if request.method == 'POST':
        data = request.get_json()
        action = data.get('action', '')
        rank = data.get('rank', '')
        article_data = data.get('article', {})
        
        if not action or not rank or not article_data:
            return jsonify({'success': False, 'message': 'Missing action, rank, or article data'}), 400
        
        print(f"\n📥 Feedback received (POST): {action} for article #{rank}")
        print(f"   Article: {article_data.get('title', 'Unknown')[:60]}...")
        
        result = record_feedback_with_article(action, rank, article_data)
        return jsonify(result)
    
    # Legacy GET endpoint (keep for compatibility)
    else:
        action = request.args.get('action', '')
        rank = request.args.get('rank', '')
        
        if not action or not rank:
            return jsonify({'success': False, 'message': 'Missing action or rank'}), 400
        
        print(f"\n📥 Feedback received (GET): {action} for article #{rank}")
        
        # For 'save', no prompt needed - just mark it
        if action == 'save':
            result = record_feedback(action, rank, "Saved from web UI")
        else:
            # For like/dislike, use default message
            result = record_feedback(action, rank, f"{action}d from web UI - see article for details")
        
        return jsonify(result)


@app.route('/deepdive')
def deepdive():
    """Trigger deep dive analysis"""
    hash_id = request.args.get('hash_id', '')
    interest = request.args.get('interest', '')
    focus = request.args.get('focus', '')
    
    if not hash_id:
        return jsonify({'success': False, 'message': 'Missing hash_id'}), 400
    
    if not interest:
        return jsonify({'success': False, 'message': 'Missing interest'}), 400
    
    print(f"\n🔍 Deep dive requested for article {hash_id}")
    print(f"   Interest: {interest[:100]}...")
    if focus:
        print(f"   Focus: {focus[:100]}...")
    
    result = trigger_deepdive(hash_id, interest, focus)
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# New library endpoints (from Claude)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/library')
def api_library():
    """
    Serve merged liked, saved, and bookmarked articles for the Reading Library page.
    
    Merges data from:
      - curator_preferences.json  (liked / saved with your notes)
      - curator_history.json      (bookmarked flag + appearances + deep_dive_path)
    
    Returns JSON: { "articles": [...], "generated_at": "..." }
    
    Future: swap JSON reads for Postgres queries with no changes to the frontend.
    """
    articles = {}  # keyed by article_id to deduplicate

    # ── 1. Load liked + saved from curator_preferences.json ──────────────────
    workspace = Path.home() / '.openclaw' / 'workspace'
    prefs_path = workspace / 'curator_preferences.json'
    if prefs_path.exists():
        prefs = json.loads(prefs_path.read_text())
        feedback_history = prefs.get('feedback_history', {})

        for date_str, day_data in feedback_history.items():
            for feedback_type in ('liked', 'saved'):
                for item in day_data.get(feedback_type, []):
                    article_id = item.get('article_id') or item.get('url', '')
                    if not article_id:
                        continue

                    # Use article_id directly as hash_id if it's a valid 5-char hex hash
                    # (curator pipeline stores articles as <hash>.json in curator_cache/)
                    import re as _re
                    hash_id_direct = article_id if _re.match(r'^[0-9a-f]{5}$', str(article_id)) else None

                    # Extract your_words — may be raw JSON wrapper, extract inner note
                    note_raw = item.get('your_words', '') or ''
                    if isinstance(note_raw, str) and note_raw.startswith('{'):
                        try:
                            note_data = json.loads(note_raw)
                            note = note_data.get('your_words', '') or note_data.get('note', '') or ''
                        except Exception:
                            note = note_raw
                    else:
                        note = note_raw

                    articles[article_id] = {
                        'article_id':  article_id,
                        'hash_id':     hash_id_direct,  # set directly from article_id
                        'type':        feedback_type,
                        'date':        item.get('timestamp') or (date_str + 'T12:00:00'),
                        'timestamp':   item.get('timestamp', ''),
                        'title':       item.get('title', ''),
                        'url':         item.get('url', ''),
                        'source':      item.get('source', ''),
                        'category':    item.get('category', 'other'),
                        'score':       item.get('score', None),
                        'note':        note,
                        'appearances': [],
                        'deep_dive_path': None,
                    }

    # ── 2. Load bookmarked from curator_history.json + enrich existing ────────
    history_path = workspace / 'curator_history.json'
    if history_path.exists():
        history = json.loads(history_path.read_text())

        for hash_id, item in history.items():
            url = item.get('url', '')
            bookmarked = item.get('bookmarked', False)

            # Build lookup key — try to match to existing article by URL
            matched_id = None
            for aid, art in articles.items():
                if art['url'] and art['url'] == url:
                    matched_id = aid
                    break

            appearances = item.get('appearances', [])
            deep_dive   = item.get('deep_dive_path')

            # Get best score from appearances
            scores = [a.get('score', 0) for a in appearances if a.get('score')]
            best_score = max(scores) if scores else None

            if matched_id:
                # Enrich existing liked/saved record with history data
                articles[matched_id]['hash_id']        = hash_id
                articles[matched_id]['appearances']    = appearances
                articles[matched_id]['deep_dive_path'] = deep_dive
                if best_score and not articles[matched_id]['score']:
                    articles[matched_id]['score'] = best_score

            elif bookmarked:
                # Pure bookmark (not in liked/saved) — add it
                bookmark_date = item.get('bookmark_date') or item.get('first_seen', '')
                articles[url] = {
                    'article_id':     url,
                    'hash_id':        hash_id,
                    'type':           'saved',
                    'date':           bookmark_date,
                    'timestamp':      bookmark_date,
                    'title':          item.get('title', ''),
                    'url':            url,
                    'source':         item.get('source', ''),
                    'category':       item.get('category', 'other'),
                    'score':          best_score,
                    'note':           None,
                    'appearances':    appearances,
                    'deep_dive_path': deep_dive,
                }

    # ── 3. Sort: most recently liked/saved first ──────────────────────────────
    result = sorted(
        articles.values(),
        key=lambda a: a.get('timestamp') or a.get('date') or '',
        reverse=True
    )

    return jsonify({
        'articles':     result,
        'count':        len(result),
        'generated_at': datetime.now().isoformat(),
    })


@app.route('/api/priority', methods=['POST'])
def api_add_priority():
    """
    Add a new priority to priorities.json.
    
    Expects JSON:
    {
      "label": "Tigray Conflict",
      "keywords": ["Tigray", "Ethiopia"],
      "boost": 2.0,
      "expires_days": 3  // optional, days from now
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    label = data.get('label', '').strip()
    keywords_raw = data.get('keywords', '')
    boost = data.get('boost', 2.0)
    expires_days = data.get('expires_days')
    
    if not label:
        return jsonify({'success': False, 'message': 'Label is required'}), 400
    
    # Parse keywords (comma-separated string or list)
    if isinstance(keywords_raw, str):
        keywords = [k.strip() for k in keywords_raw.split(',') if k.strip()]
    else:
        keywords = keywords_raw
    
    if not keywords:
        return jsonify({'success': False, 'message': 'At least one keyword is required'}), 400
    
    # Load existing priorities
    workspace = Path.home() / '.openclaw' / 'workspace'
    priorities_file = workspace / 'priorities.json'
    
    if priorities_file.exists():
        with open(priorities_file, 'r') as f:
            priorities_data = json.load(f)
    else:
        priorities_data = {'version': 1, 'priorities': []}
    
    # Generate ID
    existing_ids = [p['id'] for p in priorities_data['priorities']]
    max_num = 0
    for pid in existing_ids:
        if pid.startswith('p_'):
            try:
                num = int(pid.split('_')[1])
                max_num = max(max_num, num)
            except:
                pass
    new_id = f"p_{max_num + 1:03d}"
    
    # Calculate expiry
    expires_at = None
    if expires_days:
        expiry_date = datetime.now() + timedelta(days=int(expires_days))
        expires_at = expiry_date.isoformat() + 'Z'
    
    # Create priority
    new_priority = {
        'id': new_id,
        'label': label,
        'keywords': keywords,
        'boost': float(boost),
        'created_at': datetime.now().isoformat() + 'Z',
        'expires_at': expires_at,
        'active': True,
        'match_count': 0
    }
    
    # Add and save
    priorities_data['priorities'].append(new_priority)
    
    with open(priorities_file, 'w') as f:
        json.dump(priorities_data, f, indent=2)
    
    print(f"✅ Added priority: {label} (ID: {new_id}, boost: {boost:+.1f})")
    
    # Log to Signal Store
    from signal_store import log_priority_added
    log_priority_added(
        concern=label,
        boost=float(boost),
        expires=expires_at.split('T')[0] if expires_at else None,
        metadata={'id': new_id, 'keywords': keywords}
    )
    
    return jsonify({
        'success': True,
        'message': f'Priority "{label}" added',
        'priority': new_priority
    })


@app.route('/api/priorities', methods=['GET'])
def api_list_priorities():
    """List all priorities from priorities.json with computed expiry fields."""
    workspace = Path.home() / '.openclaw' / 'workspace'
    priorities_file = workspace / 'priorities.json'

    if not priorities_file.exists():
        return jsonify({'priorities': [], 'count': 0, 'active_count': 0})

    with open(priorities_file, 'r') as f:
        priorities_data = json.load(f)

    priorities = priorities_data.get('priorities', [])
    now = datetime.now()
    file_updated = False

    for p in priorities:
        if p.get('expires_at'):
            try:
                exp = datetime.fromisoformat(p['expires_at'].rstrip('Z'))
                p['expired'] = exp < now
                delta = exp - now
                p['days_remaining'] = delta.days
                # Auto-deactivate expired priorities so active flag stays accurate
                if p['expired'] and p.get('active'):
                    p['active'] = False
                    file_updated = True
            except Exception:
                p['expired'] = False
                p['days_remaining'] = None
        else:
            p['expired'] = False
            p['days_remaining'] = None

    # Save back if any priorities were deactivated (strip computed fields before writing)
    if file_updated:
        save_priorities = [
            {k: v for k, v in p.items() if k not in ('expired', 'days_remaining')}
            for p in priorities
        ]
        priorities_data['priorities'] = save_priorities
        with open(priorities_file, 'w') as f:
            json.dump(priorities_data, f, indent=2)
        deactivated = sum(1 for p in priorities if p.get('expired'))
        print(f"🧹 Deactivated {deactivated} expired priorities")

    active_count = sum(
        1 for p in priorities
        if p.get('active') and not p.get('expired')
    )

    return jsonify({
        'priorities': priorities,
        'count': len(priorities),
        'active_count': active_count,
    })


@app.route('/api/priority/<string:priority_id>', methods=['DELETE'])
def api_delete_priority(priority_id):
    """Delete a priority by ID."""
    workspace = Path.home() / '.openclaw' / 'workspace'
    priorities_file = workspace / 'priorities.json'

    if not priorities_file.exists():
        return jsonify({'success': False, 'message': 'priorities.json not found'}), 404

    with open(priorities_file, 'r') as f:
        priorities_data = json.load(f)

    original = priorities_data['priorities']
    filtered = [p for p in original if p['id'] != priority_id]

    if len(filtered) == len(original):
        return jsonify({'success': False, 'message': f'Priority {priority_id} not found'}), 404

    priorities_data['priorities'] = filtered

    with open(priorities_file, 'w') as f:
        json.dump(priorities_data, f, indent=2)

    print(f"🗑️  Deleted priority: {priority_id}")
    return jsonify({'success': True, 'message': f'Priority {priority_id} deleted'})


@app.route('/api/priority/<string:priority_id>', methods=['PATCH'])
def api_edit_priority(priority_id):
    """
    Edit or extend a priority.
    Accepts JSON fields: label, keywords (list or comma string),
    boost (float), active (bool), expires_days (int — sets expiry from now).
    """
    data = request.get_json()
    workspace = Path.home() / '.openclaw' / 'workspace'
    priorities_file = workspace / 'priorities.json'

    if not priorities_file.exists():
        return jsonify({'success': False, 'message': 'priorities.json not found'}), 404

    with open(priorities_file, 'r') as f:
        priorities_data = json.load(f)

    target = next(
        (p for p in priorities_data['priorities'] if p['id'] == priority_id),
        None
    )
    if not target:
        return jsonify({'success': False, 'message': f'Priority {priority_id} not found'}), 404

    if 'label' in data:
        target['label'] = data['label'].strip()
    if 'keywords' in data:
        kw = data['keywords']
        if isinstance(kw, str):
            target['keywords'] = [k.strip() for k in kw.split(',') if k.strip()]
        else:
            target['keywords'] = kw
    if 'boost' in data:
        target['boost'] = float(data['boost'])
    if 'active' in data:
        target['active'] = bool(data['active'])
    if 'expires_days' in data:
        expiry_date = datetime.now() + timedelta(days=int(data['expires_days']))
        target['expires_at'] = expiry_date.isoformat() + 'Z'

    with open(priorities_file, 'w') as f:
        json.dump(priorities_data, f, indent=2)

    print(f"✏️  Updated priority: {priority_id}")
    return jsonify({
        'success': True,
        'message': f'Priority {priority_id} updated',
        'priority': target
    })


@app.route('/api/priority/<string:priority_id>/feed', methods=['GET'])
def api_priority_feed(priority_id):
    """Return the feed array for a specific priority."""
    workspace = Path.home() / '.openclaw' / 'workspace'
    priorities_file = workspace / 'priorities.json'

    if not priorities_file.exists():
        return jsonify({'success': False, 'message': 'priorities.json not found'}), 404

    with open(priorities_file, 'r') as f:
        priorities_data = json.load(f)

    target = next(
        (p for p in priorities_data['priorities'] if p['id'] == priority_id),
        None
    )
    if not target:
        return jsonify({'success': False, 'message': f'Priority {priority_id} not found'}), 404

    return jsonify({
        'priority_id': priority_id,
        'feed': target.get('feed', []),
        'feed_last_updated': target.get('feed_last_updated'),
        'count': len(target.get('feed', [])),
    })


@app.route('/api/priority/<string:priority_id>/refresh', methods=['POST'])
def api_priority_refresh(priority_id):
    """Trigger an immediate web search run for a single priority."""
    import curator_priority_feed as cpf
    from datetime import timezone

    workspace = Path.home() / '.openclaw' / 'workspace'
    priorities_file = workspace / 'priorities.json'

    if not priorities_file.exists():
        return jsonify({'success': False, 'message': 'priorities.json not found'}), 404

    with open(priorities_file, 'r') as f:
        priorities_data = json.load(f)

    priority = next(
        (p for p in priorities_data['priorities'] if p['id'] == priority_id),
        None
    )
    if not priority:
        return jsonify({'success': False, 'message': f'Priority {priority_id} not found'}), 404

    # Block refresh on expired priorities
    if priority.get('expires_at'):
        try:
            exp = datetime.fromisoformat(priority['expires_at'].rstrip('Z'))
            if exp < datetime.now():
                return jsonify({'success': False, 'message': 'Priority is expired — feed paused'}), 400
        except Exception:
            pass

    user_profile = cpf.load_user_profile()
    fetched_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    try:
        added = cpf.run_priority(priority, user_profile, dry_run=False, fetched_at=fetched_at)
    except Exception as e:
        print(f'Priority refresh error for {priority_id}: {e}')
        return jsonify({'success': False, 'message': f'Refresh failed: {e}'}), 500

    if added > 0:
        cpf.save_priorities([priority])

    feed = priority.get('feed', [])
    print(f'↻ Feed refresh: {priority_id} — {added} new articles')
    return jsonify({
        'success': True,
        'priority_id': priority_id,
        'new_articles': added,
        'feed': feed,
        'feed_last_updated': priority.get('feed_last_updated'),
        'count': len(feed),
    })


@app.route('/api/priority-feed/save', methods=['POST'])
def api_priority_feed_save():
    """Save a priority feed article to curator_preferences.json (no signal extraction)."""
    import hashlib

    data = request.get_json() or {}
    url   = (data.get('url')   or '').strip()
    title = (data.get('title') or '').strip()
    source = (data.get('source') or '').strip()
    score = data.get('score')
    priority_id    = (data.get('priority_id')    or '').strip()
    priority_label = (data.get('priority_label') or '').strip()

    if not (url and title and source):
        return jsonify({'success': False, 'message': 'Missing url, title, or source'}), 400

    prefs_path = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
    if prefs_path.exists():
        prefs = json.loads(prefs_path.read_text())
    else:
        prefs = {'version': '1.0', 'feedback_history': {}}

    feedback_history = prefs.setdefault('feedback_history', {})

    # Idempotency check — scan all dates for matching URL
    for day_data in feedback_history.values():
        for entry in day_data.get('saved', []):
            if entry.get('url') == url:
                return jsonify({'success': True, 'already_saved': True})

    # Build article_id from priority_id + url hash
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    article_id = f'priority-{priority_id}-{url_hash}' if priority_id else f'priority-feed-{url_hash}'

    today = datetime.now().strftime('%Y-%m-%d')
    day_bucket = feedback_history.setdefault(today, {'liked': [], 'saved': []})
    day_bucket.setdefault('saved', []).append({
        'article_id':     article_id,
        'url':            url,
        'title':          title,
        'source':         source,
        'score':          float(score) if score is not None else None,
        'category':       'priority_feed',
        'timestamp':      datetime.now().isoformat(),
        'your_words':     '',
        'saved_from':     'priority_feed',
        'priority_id':    priority_id,
        'priority_label': priority_label,
    })

    prefs_path.write_text(json.dumps(prefs, indent=2))
    print(f'🔖 Priority feed save: [{priority_id}] {title[:60]}')
    return jsonify({'success': True, 'already_saved': False})


@app.route('/api/priority-feed/feedback', methods=['POST'])
def api_priority_feed_feedback():
    """Like, Dislike, or Save a priority feed article — lightweight write, no Haiku."""
    import hashlib

    data = request.get_json() or {}
    action = (data.get('action') or '').strip().lower()
    url    = (data.get('url')    or '').strip()
    title  = (data.get('title')  or '').strip()
    source = (data.get('source') or '').strip()
    score  = data.get('score')
    priority_id    = (data.get('priority_id')    or '').strip()
    priority_label = (data.get('priority_label') or '').strip()

    if action not in ('like', 'dislike', 'save'):
        return jsonify({'success': False, 'message': 'action must be like, dislike, or save'}), 400
    if not (url and title and source):
        return jsonify({'success': False, 'message': 'Missing url, title, or source'}), 400

    # Map action → storage key
    storage_key = {'like': 'liked', 'dislike': 'disliked', 'save': 'saved'}[action]

    prefs_path = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
    if prefs_path.exists():
        prefs = json.loads(prefs_path.read_text())
    else:
        prefs = {'version': '1.0', 'feedback_history': {}}

    feedback_history = prefs.setdefault('feedback_history', {})

    # Idempotency — scan all dates for matching URL in this action's bucket
    for day_data in feedback_history.values():
        for entry in day_data.get(storage_key, []):
            if entry.get('url') == url:
                return jsonify({'success': True, 'already_saved': True, 'action': action})

    # Build entry
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    article_id = f'priority-{priority_id}-{url_hash}' if priority_id else f'priority-feed-{url_hash}'

    today = datetime.now().strftime('%Y-%m-%d')
    day_bucket = feedback_history.setdefault(today, {'liked': [], 'saved': []})
    day_bucket.setdefault(storage_key, []).append({
        'article_id':     article_id,
        'url':            url,
        'title':          title,
        'source':         source,
        'score':          float(score) if score is not None else None,
        'category':       'priority_feed',
        'timestamp':      datetime.now().isoformat(),
        'your_words':     '',
        'saved_from':     'priority_feed',
        'priority_id':    priority_id,
        'priority_label': priority_label,
    })

    prefs_path.write_text(json.dumps(prefs, indent=2))
    icons = {'like': '👍', 'dislike': '👎', 'save': '🔖'}
    print(f'{icons[action]} Priority feed {action}: [{priority_id}] {title[:60]}')
    return jsonify({'success': True, 'already_saved': False, 'action': action})


@app.route('/api/check-url')
def api_check_url():
    """Proxy-check an article URL before the browser opens it — catches 4xx/5xx and XML error pages."""
    import urllib.request, urllib.error
    url = request.args.get('url', '').strip()
    if not url or not url.startswith('http'):
        return jsonify({'ok': False, 'status': 0, 'error': 'Invalid URL'}), 400
    try:
        req = urllib.request.Request(url, method='HEAD', headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            ct = r.headers.get('Content-Type', '')
            # Flag XML content-type as bad (catches S3 error pages served as application/xml)
            if 'xml' in ct and 'html' not in ct:
                return jsonify({'ok': False, 'status': r.status,
                                'error': f'Unexpected content type: {ct}'})
            return jsonify({'ok': True, 'status': r.status})
    except urllib.error.HTTPError as e:
        return jsonify({'ok': False, 'status': e.code, 'error': e.reason})
    except Exception as e:
        return jsonify({'ok': False, 'status': 0, 'error': str(e)})


@app.route('/api/intelligence/latest', methods=['GET'])
def api_intelligence_latest():
    """Serve intelligence observations for a given date (defaults to today) + responses."""
    workspace = Path.home() / '.openclaw' / 'workspace'

    date_param = request.args.get('date', '').strip()
    if date_param:
        try:
            req_date = datetime.strptime(date_param, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
    else:
        req_date = datetime.now()

    date_str  = req_date.strftime('%Y%m%d')    # e.g. "20260315"
    date_iso  = req_date.strftime('%Y-%m-%d')  # e.g. "2026-03-15"
    today_str = datetime.now().strftime('%Y%m%d')

    # Daily
    daily_path = workspace / f'intelligence_{date_str}.json'
    daily = json.loads(daily_path.read_text()) if daily_path.exists() else None

    # Most recent weekly on or before requested date
    weekly = None
    for wf in sorted(workspace.glob('intelligence_weekly_*.json'), reverse=True):
        wdate = wf.stem.replace('intelligence_weekly_', '')
        if wdate <= date_str:
            weekly = json.loads(wf.read_text())
            break

    # All responses
    responses_path = workspace / 'intelligence_responses.json'
    responses = json.loads(responses_path.read_text()) if responses_path.exists() else {"responses": []}

    prev_date = (req_date - timedelta(days=1)).strftime('%Y%m%d')
    has_prev  = (workspace / f'intelligence_{prev_date}.json').exists()

    return jsonify({
        'daily':     daily,
        'weekly':    weekly,
        'responses': responses.get('responses', []),
        'today':     date_iso,
        'is_today':  date_str == today_str,
        'has_prev':  has_prev,
    })


@app.route('/api/intelligence/respond', methods=['POST'])
def api_intelligence_respond():
    """Capture a response to an intelligence observation."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400

    reaction = (data.get('reaction') or '').strip()
    topic    = (data.get('topic')    or '').strip()

    if not reaction:
        return jsonify({'success': False, 'message': 'reaction is required'}), 400
    valid_reactions = ('agree', 'disagree', 'already_tracking', 'not_relevant', 'want_more', 'note')
    if reaction not in valid_reactions:
        return jsonify({'success': False, 'message': f'Invalid reaction: {reaction}'}), 400

    try:
        from curator_intelligence import save_response
        response = save_response(data)
        print(f"💬 Intelligence response saved: {response['id']} [{reaction}] {topic[:60]}")
        return jsonify({'success': True, 'response': response})
    except Exception as e:
        print(f'Intelligence respond error: {e}')
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/')
def index():
    """Root URL redirects to latest briefing"""
    return send_from_directory(BASE_DIR, 'curator_latest.html')

@app.route('/curator_library.html')
def library_page():
    return send_from_directory(BASE_DIR, 'curator_library.html')

@app.route('/curator_priorities.html')
def priorities_page():
    return send_from_directory(BASE_DIR, 'curator_priorities.html')

@app.route('/curator_intelligence.html')
def intelligence_page():
    return send_from_directory(BASE_DIR, 'curator_intelligence.html')

@app.route('/curator_briefing.html')
def briefing_page():
    return send_from_directory(BASE_DIR, 'curator_briefing.html')

@app.route('/curator_latest.html')
def latest_page():
    return send_from_directory(BASE_DIR, 'curator_latest.html')

@app.route('/curator_index.html')
def index_page():
    return send_from_directory(BASE_DIR, 'curator_index.html')

@app.route('/interests/<path:filepath>')
def serve_interests(filepath):
    """Serve deep dive markdown and HTML files from interests directory"""
    interests_dir = BASE_DIR / 'interests'
    return send_from_directory(interests_dir, filepath)

@app.route('/curator_archive/<path:filename>')
def serve_archive(filename):
    """Serve archived briefing HTML files"""
    archive_dir = BASE_DIR / 'curator_archive'
    return send_from_directory(archive_dir, filename)


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def record_feedback(action, rank, reason):
    """Call curator_feedback.py to record feedback (legacy GET endpoint)"""
    try:
        # Build command based on action
        if action == 'like':
            cmd = ['python', 'curator_feedback.py', 'like', rank]
        elif action == 'dislike':
            cmd = ['python', 'curator_feedback.py', 'dislike', rank]
        elif action == 'save':
            cmd = ['python', 'curator_feedback.py', 'save', rank]
        else:
            return {'success': False, 'message': f'Unknown action: {action}'}
        
        # Run in virtual environment
        venv_python = BASE_DIR / 'venv' / 'bin' / 'python'
        if venv_python.exists():
            cmd[0] = str(venv_python)
        
        # Execute with auto-response
        result = subprocess.run(
            cmd,
            input=reason.encode(),
            capture_output=True,
            cwd=BASE_DIR,
            timeout=30
        )
        
        if result.returncode == 0:
            print(f"✅ Feedback recorded successfully")
            return {
                'success': True,
                'message': f'Article #{rank} {action}d!'
            }
        else:
            error = result.stderr.decode()
            print(f"❌ Error: {error}")
            return {
                'success': False,
                'message': f'Error recording feedback: {error[:100]}'
            }
    
    except subprocess.TimeoutExpired:
        return {'success': False, 'message': 'Timeout waiting for feedback script'}
    except Exception as e:
        return {'success': False, 'message': f'Error: {str(e)}'}


def record_feedback_with_article(action, rank, article_data):
    """Call curator_feedback.py in workspace with full article metadata (new POST endpoint)"""
    workspace = Path.home() / '.openclaw' / 'workspace'
    feedback_script = workspace / 'curator_feedback.py'
    
    if not feedback_script.exists():
        return {'success': False, 'message': f'curator_feedback.py not found at {feedback_script}'}
    
    # Prepare JSON payload with article data
    payload = {
        'article': article_data,
        'your_words': f'{action}d from web UI'
    }
    
    try:
        # Use venv Python so workspace curator_feedback.py has access to anthropic + other deps
        venv_python = BASE_DIR / 'venv' / 'bin' / 'python3'
        python_cmd = str(venv_python) if venv_python.exists() else 'python3'

        result = subprocess.run(
            [python_cmd, str(feedback_script), action, str(rank), '--channel', 'web_ui'],
            input=json.dumps(payload).encode(),
            capture_output=True,
            cwd=BASE_DIR,
            env={**os.environ, 'PYTHONPATH': str(BASE_DIR)},
            timeout=30
        )
        
        if result.returncode == 0:
            print(f"✅ Feedback recorded to workspace preferences")
            return {
                'success': True,
                'message': f'Article #{rank} {action}d!'
            }
        else:
            stdout = result.stdout.decode()
            stderr = result.stderr.decode()
            print(f"❌ Subprocess failed with return code {result.returncode}")
            print(f"   stdout: {stdout}")
            print(f"   stderr: {stderr}")
            return {
                'success': False,
                'message': f'Error (code {result.returncode}): {stderr[:100] or stdout[:100] or "No output"}'
            }
    
    except subprocess.TimeoutExpired:
        return {'success': False, 'message': 'Timeout waiting for feedback script'}
    except Exception as e:
        return {'success': False, 'message': f'Error: {str(e)}'}


def trigger_deepdive(hash_id, interest, focus=''):
    """Trigger deep dive analysis for an article"""
    try:
        # Use curator_feedback.py bookmark command (which triggers deep dive)
        cmd = ['python', 'curator_feedback.py', 'bookmark', hash_id]
        
        # Run in virtual environment
        venv_python = BASE_DIR / 'venv' / 'bin' / 'python'
        if venv_python.exists():
            cmd[0] = str(venv_python)
        
        # Input: interest reason + optional focus areas
        input_text = interest + "\n" + focus + "\n"
        
        result = subprocess.run(
            cmd,
            input=input_text.encode(),
            capture_output=True,
            cwd=BASE_DIR,
            timeout=60  # Deep dives take longer
        )
        
        if result.returncode == 0:
            output = result.stdout.decode()
            print(f"Deep dive output:\n{output}")
            
            # Extract file path - look for MD path (absolute path)
            md_match = re.search(r'Saved to:\s+(.+\.md)', output)
            
            if md_match:
                md_full_path = Path(md_match.group(1).strip())
                html_full_path = Path(str(md_full_path).replace('.md', '.html'))
                
                # Convert to relative path for browser
                html_rel_path = html_full_path.relative_to(BASE_DIR)
                
                # Verify HTML file exists
                if html_full_path.exists():
                    print(f"✅ Deep dive HTML found: {html_rel_path}")
                    return {
                        'success': True,
                        'message': 'Deep dive complete!',
                        'html_path': str(html_rel_path)
                    }
                else:
                    print(f"⚠️  HTML not found at {html_full_path}")
                    return {
                        'success': True,
                        'message': f'Deep dive saved (HTML not generated)'
                    }
            else:
                print(f"⚠️  Could not extract path from output")
                print(f"Output was: {output[:200]}")
                return {
                    'success': True,
                    'message': 'Deep dive complete (check terminal)'
                }
        else:
            error = result.stderr.decode()
            print(f"❌ Deep dive error: {error}")
            return {
                'success': False,
                'message': f'Failed: {error[:100]}'
            }
    
    except subprocess.TimeoutExpired:
        return {'success': False, 'message': 'Timeout (>60s)'}
    except Exception as e:
        return {'success': False, 'message': f'Error: {str(e)}'}


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    PORT = 8765
    
    print(f"""
🌐 Curator Server Starting (Flask)...

📍 Server running at: http://localhost:{PORT}
📄 curator_latest.html - Daily briefing with feedback buttons
📚 curator_library.html - Reading library (liked/saved/bookmarked)
🔌 /api/library - Library data endpoint

Press Ctrl+C to stop
""")
    
    app.run(host='localhost', port=PORT, debug=False)

if __name__ == '__main__':
    main()
