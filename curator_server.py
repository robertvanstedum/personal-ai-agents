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

                    # Extract your words as the note
                    note = item.get('your_words', '')

                    articles[article_id] = {
                        'article_id':  article_id,
                        'hash_id':     None,           # filled below if matched
                        'type':        feedback_type,
                        'date':        item.get('timestamp', date_str)[:10],
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


@app.route('/')
def index():
    """Root URL redirects to latest briefing"""
    return send_from_directory(BASE_DIR, 'curator_latest.html')

@app.route('/curator_library.html')
def library_page():
    return send_from_directory(BASE_DIR, 'curator_library.html')

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
        result = subprocess.run(
            ['python3', str(feedback_script), action, str(rank), '--channel', 'web_ui'],
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
