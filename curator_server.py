#!/usr/bin/env python3
"""
curator_server.py - Flask server for curator feedback and library

Usage:
    python curator_server.py
    
Then open curator_latest.html or curator_library.html in browser
"""

from flask import Flask, request, jsonify, send_from_directory, render_template, redirect
from flask_cors import CORS
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import re
import html as _html

BASE_DIR = Path(__file__).parent

# Curator user data directory — preferences, history, priorities.
# Defaults to data/curator/ within the repo so data lives under version control
# boundaries (but is gitignored as personal data).
# Override with CURATOR_DATA_DIR env var on EC2 or any non-default layout.
_DATA_DIR = Path(os.environ.get("CURATOR_DATA_DIR", str(BASE_DIR / "data" / "curator")))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__,
            template_folder=str(BASE_DIR / 'templates'),
            static_folder=str(BASE_DIR / 'static'),
            static_url_path='/static')
CORS(app)


def _init_sentry():
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        dsn = os.environ.get('SENTRY_DSN')
        if not dsn:
            try:
                sys.path.insert(0, str(BASE_DIR))
                from core.get_secret import get_secret
                dsn = get_secret('SENTRY_DSN')
            except Exception:
                return
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            environment=os.environ.get('FLASK_ENV', 'production'),
        )
    except ImportError:
        pass
_init_sentry()


# ─────────────────────────────────────────────────────────────────────────────
# Briefing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _calc_time_ago(published_str, now):
    """Return a human-readable 'Xh ago' / 'Xd ago' string for a published timestamp."""
    if not published_str:
        return 'N/A'
    try:
        import dateutil.parser
        pub_dt = dateutil.parser.parse(published_str)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        diff = now - pub_dt
        hours = diff.total_seconds() / 3600
        if hours < 1:
            return f"{int(diff.total_seconds() / 60)}m ago"
        elif hours < 24:
            return f"{int(hours)}h ago"
        else:
            return f"{int(hours / 24)}d ago"
    except Exception:
        return 'N/A'


def _strip_summary(text: str, max_len: int = 200) -> str:
    """Strip HTML tags, decode entities, trim RSS footer boilerplate, cap length."""
    if not text:
        return ''
    clean = _html.unescape(re.sub(r'<[^>]+>', ' ', text))
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Drop common RSS footer lines ("The post X appeared first on Y.")
    clean = re.sub(r'\s*The post .+ appeared first on .+\.$', '', clean, flags=re.DOTALL).strip()
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(' ', 1)[0] + '…'
    return clean


def _load_briefing_articles():
    """Read curator_latest.json and enrich each article with time_ago and score_pct.

    Returns (articles, day_str, date_str, model_display, briefing_date) or None
    if the JSON file does not exist.
    """
    json_path = BASE_DIR / 'curator_latest.json'
    if not json_path.exists():
        return None

    try:
        raw = json.loads(json_path.read_text())
    except Exception:
        return None

    now = datetime.now(timezone.utc)
    today = datetime.now()
    day_str = today.strftime('%A')
    date_str = today.strftime('%B %d, %Y')

    # Derive briefing_date and model_display from first article if available
    briefing_date = raw[0].get('briefing_date', today.strftime('%Y-%m-%d')) if raw else today.strftime('%Y-%m-%d')
    model_display = raw[0].get('briefing_model', 'grok-4-1') if raw else 'grok-4-1'

    articles = []
    for entry in raw:
        score = entry.get('final_score', 0) or 0
        score_pct = min(100, max(0, (score / 20.0) * 100)) if score > 0 else 0
        enriched = dict(entry)
        enriched['time_ago'] = _calc_time_ago(entry.get('published', ''), now)
        enriched['score_pct'] = score_pct
        enriched['summary'] = _strip_summary(entry.get('summary', ''))
        articles.append(enriched)

    return articles, day_str, date_str, model_display, briefing_date


def _load_radar_articles():
    """Load on-radar articles from curator_radar.json. Returns [] if missing or empty."""
    radar_path = BASE_DIR / 'curator_radar.json'
    if not radar_path.exists():
        return []
    try:
        data = json.loads(radar_path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ── Landing page data helpers ─────────────────────────────────────────────────

def _get_latest_briefing_date() -> str:
    """Return formatted date string from latest briefing JSON. Falls back to dash."""
    try:
        raw = json.loads((BASE_DIR / 'curator_latest.json').read_text())
        if raw:
            d = raw[0].get('briefing_date') or raw[0].get('date', '')
            if d:
                from datetime import datetime as _dt
                return _dt.strptime(d[:10], '%Y-%m-%d').strftime('%A, %B %-d, %Y')
    except Exception:
        pass
    return '—'


def _get_latest_article_count() -> int:
    """Return count of articles in latest briefing. Falls back to 0."""
    try:
        raw = json.loads((BASE_DIR / 'curator_latest.json').read_text())
        return len(raw)
    except Exception:
        return 0


def _get_topics_summary() -> dict:
    """Count topics by status from threads directory. Falls back to all zeros."""
    threads_dir = BASE_DIR / '_NewDomains' / 'research-intelligence' / 'data' / 'threads'
    result = {'total': 0, 'active': 0, 'dormant': 0}
    try:
        for f in threads_dir.glob('*/thread.json'):
            t = json.loads(f.read_text())
            status = t.get('status', '')
            result['total'] += 1
            if status in ('active', 'active-pull'):
                result['active'] += 1
            elif status in ('dormant', 'paused'):
                result['dormant'] += 1
    except Exception:
        pass
    return result


def _get_leanings_summary() -> dict:
    """Count leanings by state from leanings.json. Falls back to all zeros."""
    leanings_path = (BASE_DIR / '_NewDomains' / 'research-intelligence' /
                     'data' / 'leanings' / 'leanings.json')
    result = {'total': 0, 'question': 0, 'leaning': 0, 'hold': 0}
    try:
        raw = json.loads(leanings_path.read_text())
        leanings = raw.get('leanings', raw) if isinstance(raw, dict) else raw
        for lean in leanings:
            result['total'] += 1
            state = lean.get('state', 'question')
            if state in result:
                result[state] += 1
    except Exception:
        pass
    return result

# Shared navigation HTML
SHARED_NAV_HTML = """
  <nav class="header-nav">
    <a href="/" class="nav-link {briefing_active}">Daily</a>
    <a href="/curator_library.html" class="nav-link {library_active}">Library</a>
    <a href="/scans-dives" class="nav-link {deepdives_active}">Scans &amp; Dives</a>
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
    workspace = _DATA_DIR
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
                        'date':        item.get('timestamp') or (re.sub(r'^[^0-9]+', '', date_str) + 'T12:00:00'),
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
                # Strip x_bootstrap_ prefixes from cold-start signals so dates parse correctly
                raw_date = item.get('bookmark_date') or item.get('first_seen', '')
                bookmark_date = re.sub(r'^[^0-9]+', '', raw_date) if raw_date else ''
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

    # ── 3. Resolve deep_dive_url for each article ─────────────────────────────
    # Scan scans/ directory to catch dives not recorded in curator_history.json
    scans_dir = BASE_DIR / 'interests' / '2026' / 'scans'
    dive_url_map = {}  # hash_id -> web URL
    if scans_dir.exists():
        for f in scans_dir.glob('*.md'):
            parts = f.name.split('-', 1)
            if len(parts) >= 1 and len(parts[0]) == 5:
                # Route to Flask scan view, not the old static HTML
                dive_url_map[parts[0]] = f'/research/scan/{parts[0]}'

    for art in articles.values():
        hash_id = art.get('hash_id')
        dive_path = art.get('deep_dive_path')
        dive_url = None
        if dive_path:
            # Rewrite history-recorded path to Flask scan view if it's a scan hash
            hash_from_path = re.search(r'/scans/([0-9a-f]{5})-', str(dive_path))
            if hash_from_path:
                dive_url = f'/research/scan/{hash_from_path.group(1)}'
            else:
                # Fallback: convert .md path to static web URL (legacy dives)
                path_match = re.search(r'interests/(.+\.md)$', str(dive_path))
                if path_match:
                    dive_url = '/interests/' + path_match.group(1).replace('.md', '.html')
        elif hash_id and hash_id in dive_url_map:
            # Dive file exists on disk but wasn't recorded in history
            dive_url = dive_url_map[hash_id]
        art['deep_dive_url'] = dive_url

    # ── 4. Sort: most recently liked/saved first ──────────────────────────────
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
    DEPRECATED — Priority creation retired in Step 2 of Leaning tier.
    Boosting now flows through Topic activation. Existing priorities expire naturally.
    """
    return jsonify({
        'success': False,
        'message': 'Priority creation is retired. Activate a Topic to boost matching articles.',
        'retired': True,
    }), 410

def _api_add_priority_legacy():
    """Kept for reference — no longer reachable."""
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
    workspace = _DATA_DIR
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
    workspace = _DATA_DIR
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
    workspace = _DATA_DIR
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
    workspace = _DATA_DIR
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
    workspace = _DATA_DIR
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

    workspace = _DATA_DIR
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

    prefs_path = _DATA_DIR / 'curator_preferences.json'
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

    prefs_path = _DATA_DIR / 'curator_preferences.json'
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
    workspace = _DATA_DIR

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
@app.route('/landing')
def index():
    """Root URL — Curator landing page (masthead + card catalog)."""
    from flask import session as _session
    return render_template(
        'curator_landing.html',
        briefing_date=_get_latest_briefing_date(),
        article_count=_get_latest_article_count(),
        topics=_get_topics_summary(),
        leanings=_get_leanings_summary(),
        username=_session.get('username', 'Robert'),
    )


@app.route('/briefing')
def briefing():
    """Daily briefing — previously served at /."""
    from flask import request as _req
    tier = _req.headers.get("X-Minimoi-User-Tier", "guest")  # least-privilege default (H1)
    result = _load_briefing_articles()
    if result is not None:
        articles, day_str, date_str, model_display, briefing_date = result
        tips_file = BASE_DIR / 'config' / 'curator' / 'tips.json'
        try:
            tips = json.loads(tips_file.read_text()) if tips_file.exists() else {}
        except Exception:
            tips = {}
        entry = tips.get('briefing.main', {})
        tip = entry.get('text') if entry.get('active') else None
        return render_template(
            'curator_briefing.html',
            articles=articles,
            day_str=day_str,
            date_str=date_str,
            model_display=model_display,
            run_mode='production',
            briefing_date=briefing_date,
            radar_articles=_load_radar_articles(),
            tier=tier,
            tip=tip,
        )
    return send_from_directory(BASE_DIR, 'curator_latest.html')

@app.route('/curator_library.html')
@app.route('/reading-room')
def library_page():
    tips_file = BASE_DIR / 'config' / 'curator' / 'tips.json'
    try:
        tips = json.loads(tips_file.read_text()) if tips_file.exists() else {}
    except Exception:
        tips = {}
    entry = tips.get('curator.reading_room', {})
    tip = entry.get('text') if entry.get('active') else None
    return render_template('curator_library.html', tip=tip)

# ── Archive data helpers (Phase 5) ────────────────────────────────────────────

def _archive_daily_editions():
    """Sorted list of daily editions from curator_archive/. Most recent first."""
    archive_dir = BASE_DIR / 'curator_archive'
    editions = []
    seen = set()
    try:
        for f in sorted(archive_dir.glob('curator_*.html'), reverse=True):
            parts = f.stem.replace('curator_', '').split('-')
            if len(parts) >= 3:
                date_str = '-'.join(parts[:3])
                if date_str in seen:
                    continue
                seen.add(date_str)
                count = 0
                json_f = archive_dir / f'curator_{date_str}.json'
                if json_f.exists():
                    try:
                        count = len(json.loads(json_f.read_text()))
                    except Exception:
                        pass
                editions.append({'date': date_str, 'count': count, 'file': f.name})
    except Exception:
        pass
    return editions


def _archive_sources():
    """Bookmarked articles from curator_history.json. Most recent first."""
    hist_path = BASE_DIR / 'curator_history.json'
    articles = []
    try:
        hist = json.loads(hist_path.read_text())
        for art in hist.values():
            if art.get('bookmarked'):
                articles.append({
                    'date':   art.get('bookmark_date') or art.get('first_seen', ''),
                    'title':  art.get('title', ''),
                    'source': art.get('source', ''),
                    'url':    art.get('url', ''),
                })
        articles.sort(key=lambda x: x['date'], reverse=True)
    except Exception:
        pass
    return articles


def _scan_md_meta(md_path):
    """Extract title, source, date, hash_id from a scan .md file."""
    text = md_path.read_text()
    title_m = re.match(r'^# (.+)', text)
    title   = title_m.group(1).strip() if title_m else md_path.stem
    source_m = re.search(r'\*\*Source:\*\*\s*(.+)', text)
    source   = source_m.group(1).strip() if source_m else ''
    date_m   = re.search(r'\*\*Date:\*\*\s*(.+)', text)
    date     = date_m.group(1).strip() if date_m else ''
    hash_id  = md_path.stem.split('-')[0]
    return {'title': title, 'source': source, 'date': date, 'hash_id': hash_id}


def _archive_scans():
    """Scan list read directly from interests/2026/scans/*.md files."""
    scans_dir = BASE_DIR / 'interests' / '2026' / 'scans'
    scans = []
    try:
        raw = []
        for md in scans_dir.glob('*.md'):
            m = _scan_md_meta(md)
            raw.append({
                'date':   m['date'],
                'title':  m['title'],
                'source': m['source'],
                'url':    f'research/scan/{m["hash_id"]}',
            })
        scans = sorted(raw, key=lambda s: s['date'], reverse=True)
    except Exception:
        pass
    return scans


def _archive_dives():
    """Deeper-dive summaries from the dives data directory."""
    dives_dir = BASE_DIR / '_NewDomains' / 'research-intelligence' / 'data' / 'dives'
    dives = []
    try:
        for f in sorted(dives_dir.glob('*.md'), reverse=True):
            stem  = f.stem  # e.g. hellscape-taiwan-porcupine-deeper-dive-001
            parts = stem.rsplit('-deeper-dive-', 1)
            topic = parts[0] if len(parts) == 2 else stem
            title = ' '.join(w.capitalize() for w in topic.split('-'))
            dives.append({'topic': topic, 'title': title, 'file': f.name, 'slug': stem})
    except Exception:
        pass
    return dives


def _archive_observations():
    """Daily intelligence observation dates from ~/.openclaw/workspace/."""
    workspace = _DATA_DIR
    entries = []
    try:
        for f in sorted(workspace.glob('intelligence_*.json'), reverse=True):
            stem = f.stem
            if 'weekly' in stem:
                continue
            raw = stem.replace('intelligence_', '')
            if len(raw) == 8:
                date_str = f'{raw[:4]}-{raw[4:6]}-{raw[6:]}'
                try:
                    data = json.loads(f.read_text())
                    count = len(data.get('observations', []))
                except Exception:
                    count = 0
                entries.append({'date': date_str, 'count': count})
    except Exception:
        pass
    return entries


@app.route('/archive')
def archive_page():
    """Archive — live browse page (Phase 5)."""
    tips_file = BASE_DIR / 'config' / 'curator' / 'tips.json'
    try:
        tips = json.loads(tips_file.read_text()) if tips_file.exists() else {}
    except Exception:
        tips = {}
    entry = tips.get('curator.archive', {})
    tip = entry.get('text') if entry.get('active') else None
    return render_template('curator_archive.html',
        daily_editions = _archive_daily_editions(),
        sources        = _archive_sources(),
        scans          = _archive_scans(),
        dives          = _archive_dives(),
        observations   = _archive_observations(),
        tip            = tip,
    )


# ── Scans & Dives helpers (Flask route) ───────────────────────────────────────

def _scans_dives_data():
    """Build scans and dives lists directly from .md files — no index.html dependency."""
    scans, dives = [], []

    # ── Scans from interests/2026/scans/*.md ─────────────────────────────────
    scans_dir = BASE_DIR / 'interests' / '2026' / 'scans'
    try:
        raw_scans = []
        for md in scans_dir.glob('*.md'):
            m = _scan_md_meta(md)
            raw_scans.append({
                'href':   f'/research/scan/{m["hash_id"]}',
                'date':   m['date'],
                'source': m['source'],
                'title':  m['title'],
            })
        scans = sorted(raw_scans, key=lambda s: s['date'], reverse=True)
    except Exception:
        pass

    # ── Dives from data/dives/*.md ────────────────────────────────────────────
    dives_dir = BASE_DIR / '_NewDomains' / 'research-intelligence' / 'data' / 'dives'
    try:
        raw_dives = []
        for f in dives_dir.glob('*.md'):
            text    = f.read_text()
            title_m = re.match(r'^#\s+DEEPER DIVE:\s*(.+)', text, re.IGNORECASE)
            title   = title_m.group(1).strip() if title_m else ' '.join(
                w.capitalize() for w in f.stem.split('-')
            )
            date_m  = re.search(r'Generated:\s*(\d{4}-\d{2}-\d{2})', text)
            date    = date_m.group(1) if date_m else ''
            sort_key = date if date else str(f.stat().st_mtime)
            raw_dives.append({
                'href':     f'/research/dive-result/{f.stem}',
                'title':    title,
                'excerpt':  '',
                'meta':     date,
                'date':     date,
                '_sort_key': sort_key,
            })
        dives = sorted(raw_dives, key=lambda d: d['_sort_key'], reverse=True)
        for d in dives:
            del d['_sort_key']
    except Exception:
        pass

    return dives, scans


@app.route('/scans-dives')
def scans_dives_page():
    """Scans & Dives — Flask-rendered page."""
    tips_file = BASE_DIR / 'config' / 'curator' / 'tips.json'
    try:
        tips = json.loads(tips_file.read_text()) if tips_file.exists() else {}
    except Exception:
        tips = {}
    entry = tips.get('curator.scans-dives', {})
    tip = entry.get('text') if entry.get('active') else None
    dives, scans = _scans_dives_data()
    return render_template('curator_scans_dives.html', dives=dives, scans=scans, tip=tip)


# ── (legacy placeholder kept below for reference — removed Phase 5) ──────────
def _archive_page_placeholder():
    """Archive — placeholder page (Phase 5 will add full browse UI)."""
    from flask import Response
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Archive — Curator</title>
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root { --bg:#F5F0E8; --text:#2A1F14; --accent:#C68A5E; --rule:#C4B49A; --muted:#8A7060; --dim:#A89880; --card-bg:#EDE7DC; }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: Georgia, serif; min-height: 100vh; }
    nav.curator-subnav { position: sticky; top: 0; z-index: 100; background: rgba(245,240,232,0.96); backdrop-filter: blur(8px); border-bottom: 1px solid var(--rule); display: flex; align-items: center; padding: 0 1.5rem; height: 44px; }
    .subnav-tab { font-family: 'DM Mono', monospace; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); text-decoration: none; padding: 0 14px; height: 44px; display: flex; align-items: center; border-bottom: 2px solid transparent; transition: color 0.15s; }
    .subnav-tab:hover { color: var(--text); }
    .subnav-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
    .subnav-focus { margin-left: auto; }
    .placeholder { max-width: 520px; margin: 6rem auto; text-align: center; }
    .placeholder-label { font-family: 'DM Mono', monospace; font-size: 10px; text-transform: uppercase; letter-spacing: 0.14em; color: var(--dim); margin-bottom: 1.2rem; }
    .placeholder-title { font-size: 2.2rem; letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 1rem; }
    .placeholder-rule { border: none; border-top: 1px solid var(--rule); width: 60%; margin: 0 auto 1.2rem; }
    .placeholder-body { font-size: 0.92rem; line-height: 1.7; color: var(--muted); font-style: italic; }
    .placeholder-back { display: inline-block; margin-top: 2rem; font-family: 'DM Mono', monospace; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--accent); text-decoration: none; }
  </style>
</head>
<body>
<nav class="curator-subnav">
  <a href="/briefing" class="subnav-tab">Daily</a>
  <a href="/curator_library.html" class="subnav-tab">Reading Room</a>
  <a href="/scans-dives" class="subnav-tab">Scans &amp; Dives</a>
  <a href="/research/leanings" class="subnav-tab">Leanings</a>
  <a href="/archive" class="subnav-tab active">Archive</a>
  <a href="/research/dashboard" class="subnav-tab subnav-focus">Desk</a>
</nav>
<div class="placeholder">
  <div class="placeholder-label">Coming — Phase 5</div>
  <h1 class="placeholder-title">Archive</h1>
  <hr class="placeholder-rule">
  <p class="placeholder-body">Everything kept, and searchable over time.<br>Daily editions &middot; Scans &middot; Dives &middot; Observations &middot; Sources.</p>
  <a href="/" class="placeholder-back">&#8592; Back to Curator</a>
</div>
</body>
</html>"""
    return Response(html, mimetype='text/html')
# ────────────────────────────────────────────────────────────────────────────


@app.route('/curator_briefing.html')
def briefing_page():
    """Serve briefing from Jinja2 template when JSON exists."""
    from flask import request as _req
    tier = _req.headers.get("X-Minimoi-User-Tier", "guest")  # least-privilege default (H1)
    result = _load_briefing_articles()
    if result is not None:
        articles, day_str, date_str, model_display, briefing_date = result
        return render_template(
            'curator_briefing.html',
            articles=articles,
            day_str=day_str,
            date_str=date_str,
            model_display=model_display,
            run_mode='production',
            briefing_date=briefing_date,
            tier=tier,
        )
    return send_from_directory(BASE_DIR, 'curator_briefing.html')

@app.route('/api/briefing')
def api_briefing():
    """Return briefing articles as JSON for a given date.

    Query params:
      date=YYYY-MM-DD  (default: today, i.e. curator_latest.json)

    Reads from curator_archive/curator_YYYY-MM-DD.json when a date is supplied.
    Falls back to curator_latest.json for today or when archive file is missing.
    """
    from datetime import date as _date
    req_date = request.args.get('date', '')
    today_str = datetime.now().strftime('%Y-%m-%d')

    # Resolve which JSON file to read
    if req_date and req_date != today_str:
        json_path = BASE_DIR / 'curator_archive' / f'curator_{req_date}.json'
        if not json_path.exists():
            return jsonify({'error': f'No archive for {req_date}'}), 404
    else:
        json_path = BASE_DIR / 'curator_latest.json'
        req_date = today_str

    try:
        raw = json.loads(json_path.read_text())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    now = datetime.now(timezone.utc)
    articles = []
    for i, entry in enumerate(raw, 1):
        score = entry.get('final_score', 0) or 0
        score_pct = min(100, max(0, (score / 20.0) * 100)) if score > 0 else 0
        enriched = dict(entry)
        enriched['time_ago'] = _calc_time_ago(entry.get('published', ''), now)
        enriched['score_pct'] = score_pct
        enriched['summary'] = _strip_summary(entry.get('summary', ''))
        enriched['rank'] = i
        articles.append(enriched)

    return jsonify({'date': req_date, 'articles': articles})


@app.route('/curator_latest.html')
def latest_page():
    """Serve briefing from Jinja2 template when JSON exists."""
    result = _load_briefing_articles()
    if result is not None:
        articles, day_str, date_str, model_display, briefing_date = result
        return render_template(
            'curator_briefing.html',
            articles=articles,
            day_str=day_str,
            date_str=date_str,
            model_display=model_display,
            run_mode='production',
            briefing_date=briefing_date,
        )
    return send_from_directory(BASE_DIR, 'curator_latest.html')

@app.route('/curator_index.html')
def index_page():
    return send_from_directory(BASE_DIR, 'curator_index.html')

@app.route('/language')
def language_coming():
    return send_from_directory(BASE_DIR, 'language_coming.html')

@app.route('/jobs')
def career_focus_redirect():
    """Career Focus moved to Guild domain."""
    return redirect('/guild/career', 301)

@app.route('/interests/2026/scans/<path:filename>')
def redirect_old_scan(filename):
    """
    Redirect old static scan HTML files to the Flask scan view.
    Registered before serve_interests so it takes priority for scan paths.
    """
    if filename == 'index.html':
        return redirect('/scans-dives', 301)
    m = re.match(r'^([0-9a-f]{5})-', filename)
    if m:
        return redirect(f'/research/scan/{m.group(1)}', 301)
    # Non-HTML files (.md, etc.) — serve as-is
    return send_from_directory(BASE_DIR / 'interests' / '2026' / 'scans', filename)


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

@app.route('/health')
def health():
    return {'status': 'ok', 'service': 'curator'}


# ─────────────────────────────────────────────────────────────────────────────
# Research Intelligence — Blueprint
# All /research/* routes live in research_routes.py.
# The try/except guard ensures any failure there cannot crash this server.
# ─────────────────────────────────────────────────────────────────────────────

try:
    from research_routes import research_bp
    app.register_blueprint(research_bp)
    print("✓ Research Intelligence routes loaded")
except Exception as _research_import_err:
    print(f"⚠️  Research routes not loaded: {_research_import_err}")


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
    feedback_script = BASE_DIR / 'curator_feedback.py'
    
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
                        'html_path': '/' + str(html_rel_path)
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
    PORT = int(os.environ.get('PORT', 8766))
    
    print(f"""
🌐 Curator Server Starting (Flask)...

📍 Server running at: http://localhost:{PORT}
📄 curator_latest.html - Daily briefing with feedback buttons
📚 curator_library.html - Reading library (liked/saved/bookmarked)
🔌 /api/library - Library data endpoint

Press Ctrl+C to stop
""")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == '__main__':
    main()
