"""Curator briefing presentation, separate from feed/scoring and delivery effects.

Render supplied articles as console text, Telegram text or HTML. The runtime
passes its configured model label explicitly. Rendering never fetches feeds,
resolves secrets, creates state directories or sends a message.
"""
from datetime import timezone
from typing import Dict, List
from html import escape as _escape
from urllib.parse import urlsplit


def _safe_href(value: str) -> str:
    """Render only absolute web navigation; keep other references non-clickable."""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return "#"
        # Accessing the port validates malformed and out-of-range port syntax.
        parts.port
    except (TypeError, ValueError):
        return "#"
    return _escape(value, quote=True)


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


def format_html(entries: List[Dict], model: str = "xai", run_mode: str = "production",
                radar_articles: List[Dict] = None, *, xai_ranking_model: str) -> str:
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
        'xai': xai_ranking_model,
        'grok-4.3': xai_ranking_model,
        'haiku': 'claude-haiku-4-5',
        'sonnet': 'claude-sonnet-4-5',
    }
    model_display = _escape(str(model_display_map.get(model, model)))

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="curator-model" content="{model_display}">
    <meta name="curator-mode" content="{_escape(str(run_mode))}">
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
            display: flex;
            align-items: flex-start;
        }}

        /* ── Domain Rail ── */
        .domain-rail {{
            width: 200px;
            min-width: 200px;
            background: rgba(245,240,232,0.6);
            border-right: 1px solid var(--border);
            min-height: 100vh;
            padding: 24px 0;
            position: sticky;
            top: 0;
            display: flex;
            flex-direction: column;
            gap: 4px;
            z-index: 50;
        }}
        .rail-domain {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 20px;
            cursor: pointer;
            text-decoration: none;
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--text-muted);
            transition: all 0.15s;
        }}
        .rail-domain:hover {{ background: var(--surface2); color: var(--text); }}
        .rail-domain.active {{
            background: var(--accent-dim);
            color: var(--accent);
            font-weight: 600;
            border-right: 2px solid var(--accent);
        }}
        .rail-domain.coming-soon {{ opacity: 0.4; cursor: default; pointer-events: none; }}
        .rail-icon {{ font-size: 14px; }}
        .main-content {{
            flex: 1;
            min-width: 0;
            overflow-x: hidden;
            min-height: 100vh;
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

        /* ── Briefing Date ── */
        .briefing-date {{
            font-family: var(--font-mono);
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 1rem;
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
<body data-run-mode="{_escape(str(run_mode))}">
<div class="domain-rail">
  <a href="/" class="rail-domain active"><span class="rail-icon">📚</span><span>Curator</span></a>
  <a href="/research/dashboard" class="rail-domain"><span class="rail-icon">🔬</span><span>Research</span></a>
  <span class="rail-domain coming-soon"><span class="rail-icon">💬</span><span>Language</span></span>
  <span class="rail-domain coming-soon"><span class="rail-icon">💼</span><span>Jobs</span></span>
</div>
<div class="main-content">
<header class="curator-header">
  <div class="header-left">
    <a href="/" class="logo">📚 Curator</a>
    <span class="logo-sub">Morning Briefing</span>
  </div>
  <nav class="header-nav">
    <a href="/" class="nav-link active">Daily</a>
    <a href="curator_library.html" class="nav-link">Library</a>
    <a href="interests/2026/deep-dives/index.html" class="nav-link">Deep Dives</a>
    <a href="curator_intelligence.html" class="nav-link">Observations</a>
    <a href="curator_priorities.html" class="nav-link">🎯</a>
  </nav>
</header>

<main>
    <div class="briefing-date">{day_str}, {date_str}</div>
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

        # HTML attributes decode back to the original article strings. They
        # are not JavaScript literals; JSON.stringify handles feedback later.
        title_escaped = _escape(title)
        url_escaped = _escape(url)
        source_escaped = _escape(source)
        hash_id_escaped = _escape(str(hash_id))
        category_escaped = _escape(category)
        category_label = _escape(category.lower())
        href = _safe_href(url)

        html += f"""                <tr data-hash-id="{hash_id_escaped}" 
                        data-rank="{rank}"
                        data-title="{title_escaped}"
                        data-url="{url_escaped}"
                        data-source="{source_escaped}"
                        data-category="{category_escaped}">
                    <td class="col-rank"><span class="rank-badge">{rank}</span></td>
                    <td class="col-category"><span class="cat-badge cat-{category_label}">{category_label}</span></td>
                    <td class="col-source"><span class="source-name">{source_escaped}</span></td>
                    <td class="col-title">
                        <div class="article-title">
                            <a href="{href}" target="_blank">{title_escaped}</a>
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
        fetch('http://localhost:8766/feedback', {
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
            var url = 'http://localhost:8766/deepdive?hash_id=' + hashId + '&interest=' + encodeURIComponent(interest);
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
"""

    # ── Dormant / On Radar section ────────────────────────────────────────────
    # Collapsed by default. Only shown when active Topics produced tag matches
    # in the remaining candidate pool. No AI — deterministic string-match only.
    if radar_articles:
        radar_rows = ""
        for a in radar_articles:
            title   = _escape(str(a.get("title", "Untitled")))
            source  = _escape(str(a.get("source", "")))
            link    = _safe_href(a.get("link", "#"))
            topics  = _escape(", ".join(a.get("_radar_topics", [])))
            radar_rows += f"""
            <tr class="radar-row">
                <td class="radar-title"><a href="{link}" target="_blank" rel="noopener">{title}</a></td>
                <td class="radar-source">{source}</td>
                <td class="radar-topic">📡 {topics}</td>
            </tr>"""

        html += f"""
<details class="radar-section">
  <summary class="radar-summary">
    📡 On Radar — {len(radar_articles)} article{"s" if len(radar_articles) != 1 else ""} matching active Topics
    <span class="radar-hint">(not in today's top 20)</span>
  </summary>
  <div class="radar-body">
    <table class="radar-table">
      <thead>
        <tr>
          <th>Article</th>
          <th>Source</th>
          <th>Topic</th>
        </tr>
      </thead>
      <tbody>{radar_rows}
      </tbody>
    </table>
  </div>
</details>
<style>
.radar-section {{
  margin: 2rem 0 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
}}
.radar-summary {{
  padding: 0.75rem 1rem;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-muted);
  user-select: none;
  list-style: none;
}}
.radar-summary::-webkit-details-marker {{ display: none; }}
.radar-hint {{
  font-weight: 400;
  font-size: 0.8rem;
  color: var(--text-dim);
  margin-left: 0.5rem;
}}
.radar-body {{ padding: 0 0.5rem 0.75rem; }}
.radar-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
.radar-table th {{
  text-align: left; padding: 0.4rem 0.75rem;
  color: var(--text-dim); font-weight: 500;
  border-bottom: 1px solid var(--border);
}}
.radar-row td {{ padding: 0.4rem 0.75rem; border-bottom: 1px solid var(--border2); }}
.radar-title a {{ color: var(--text); text-decoration: none; }}
.radar-title a:hover {{ color: var(--accent); text-decoration: underline; }}
.radar-source {{ color: var(--text-muted); white-space: nowrap; }}
.radar-topic {{ color: var(--text-dim); font-size: 0.8rem; white-space: nowrap; }}
</style>
"""

    html += """
</div><!-- /.main-content -->
</body>
</html>
"""

    return html
