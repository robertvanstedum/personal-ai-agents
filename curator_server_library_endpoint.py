
# ─────────────────────────────────────────────────────────────────────────────
# ADD THIS ENDPOINT TO curator_server.py
# Place it alongside your other route handlers
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
    import json
    from datetime import datetime
    from flask import jsonify  # or use your existing import

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


# ─────────────────────────────────────────────────────────────────────────────
# Also add this static file route if not already present in curator_server.py:
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/curator_library.html')
def library_page():
    return send_from_directory(BASE_DIR, 'curator_library.html')
