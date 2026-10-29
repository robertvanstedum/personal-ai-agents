"""Render a loaded Scan/Dive catalog; no filesystem or application imports."""

from html import escape
from urllib.parse import quote


def render_catalog(scans, dives):
    # Render copies: callers retain raw metadata for persistence and other views.
    scans = [{**item, 'title': escape(str(item['title']), quote=False),
              'source': escape(str(item['source']), quote=False),
              'hash_id': quote(str(item['hash_id']), safe='')} for item in scans]
    dives = [{**item, 'topic': escape(str(item['topic']), quote=False),
              'excerpt': escape(str(item['excerpt']), quote=False),
              'stats': escape(str(item['stats']), quote=False),
              'stem': quote(str(item['stem']), safe='')} for item in dives]
    # ── Build rows (new layout: collapsed sections, hover-reveal actions) ────────

    THREAD_LIMIT  = 3
    ARTICLE_LIMIT = 5

    # Thread rows
    thread_rows_html = ''
    for i, dd in enumerate(dives):
        formatted_date = dd['date'].strftime("%b %-d, %Y")
        url = f'/research/dive-result/{dd["stem"]}'
        excerpt_html = f'<span class="row-excerpt">{dd["excerpt"]}</span>' if dd['excerpt'] else ''
        hidden_cls = ' row-hidden' if i >= THREAD_LIMIT else ''
        thread_rows_html += f'        <a class="row row-thread{hidden_cls}" href="{url}"><span class="row-badge">Thread</span><span class="row-main"><span class="row-title">{dd["topic"]}</span>{excerpt_html}</span><span class="row-meta">{dd["stats"]}</span><span class="row-date">{formatted_date}</span><span class="row-action">Read &#8594;</span></a>\n'

    thread_more = len(dives) - THREAD_LIMIT
    thread_toggle_style = ' style="display:none"' if len(dives) <= THREAD_LIMIT else ''
    thread_footer_html = ''
    if thread_more > 0:
        label = f'+ {thread_more} more thread{"s" if thread_more != 1 else ""}'
        thread_footer_html = f'        <div class="expand-footer" onclick="expandSection(\'section-threads\')">{label}</div>\n'

    # Article rows
    article_rows_html = ''
    for i, dive in enumerate(scans):
        formatted_date = dive['date'].strftime("%b %-d, %Y")
        url = f'/research/scan/{dive["hash_id"]}'
        hidden_cls = ' row-hidden' if i >= ARTICLE_LIMIT else ''
        article_rows_html += f'        <a class="row row-article{hidden_cls}" href="{url}"><span class="row-date-col">{formatted_date}</span><span class="row-source">{dive["source"]}</span><span class="row-title">{dive["title"]}</span><span class="row-action">Read analysis &#8594;</span></a>\n'

    if not article_rows_html:
        article_rows_html = '        <div class="row-empty">No scans yet. Like or save an article, then click 🔭 Scan!</div>\n'

    article_more = len(scans) - ARTICLE_LIMIT
    article_toggle_style = ' style="display:none"' if len(scans) <= ARTICLE_LIMIT else ''
    article_footer_html = ''
    if article_more > 0:
        label = f'+ {article_more} more scan{"s" if article_more != 1 else ""}'
        article_footer_html = f'        <div class="expand-footer" onclick="expandSection(\'section-articles\')">{label}</div>\n'

    # Count line
    count_parts = []
    if dives:
        count_parts.append(f'{len(dives)} dive{"s" if len(dives) != 1 else ""}')
    count_parts.append(f'{len(scans)} scan{"s" if len(scans) != 1 else ""}')
    count_label = ' · '.join(count_parts)

    # Deeper Dives section block (only if entries exist)
    threads_section_html = ''
    if dives:
        threads_section_html = f'''    <div class="section" id="section-threads">
      <div class="section-hdr">
        <span class="section-name">Dives <span class="section-count">{len(dives)}</span></span>
        <button class="toggle-btn"{thread_toggle_style} onclick="toggleSection(\'section-threads\', this)">show all</button>
      </div>
      <div class="section-body">
{thread_rows_html}{thread_footer_html}      </div>
    </div>
'''

    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
    <title>Scans &amp; Dives</title>
    <style>
        :root {{
            --bg: #f5f0e8;
            --bg-texture: #ede8df;
            --surface: #faf7f2;
            --surface2: #f0ebe0;
            --border: #ddd6c8;
            --text: #2a2418;
            --text-muted: #6b5f4e;
            --text-dim: #9e9080;
            --accent: #8b5e2a;
            --accent-dim: rgba(139,94,42,0.08);
            --shadow: rgba(42,36,24,0.08);
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
        }}

        header {{
            border-bottom: 0.5px solid var(--border);
            padding: 0 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 60px;
            position: sticky;
            top: 0;
            background: rgba(245,240,232,0.95);
            backdrop-filter: blur(12px);
            z-index: 100;
        }}

        .header-left {{ display: flex; align-items: baseline; gap: 16px; }}

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

        .header-nav {{ display: flex; gap: 4px; }}

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

        .nav-link:hover {{ color: var(--text); background: var(--surface2); }}

        .nav-link.active {{
            color: var(--accent);
            background: var(--accent-dim);
            border-color: rgba(139,94,42,0.2);
        }}

        main {{
            max-width: 900px;
            margin: 0 auto;
            padding: 36px 32px 60px;
        }}

        .page-header {{ margin-bottom: 36px; }}

        .page-title {{
            font-family: 'Playfair Display', serif;
            font-size: 28px;
            font-weight: 400;
            color: var(--text);
            letter-spacing: -0.03em;
            line-height: 1.1;
            margin-bottom: 6px;
        }}

        .page-meta {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            letter-spacing: 0.04em;
        }}

        /* ── Section ── */
        .section {{
            margin-bottom: 36px;
        }}

        .section-hdr {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            padding-bottom: 9px;
            border-bottom: 0.5px solid var(--border);
            margin-bottom: 0;
        }}

        .section-name {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--text-dim);
        }}

        .section-count {{
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            color: var(--text-dim);
            opacity: 0.6;
            margin-left: 6px;
        }}

        .toggle-btn {{
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            letter-spacing: 0.06em;
            color: var(--accent);
            background: none;
            border: none;
            cursor: pointer;
            padding: 0;
        }}

        .toggle-btn:hover {{ text-decoration: underline; }}

        /* ── Rows ── */
        .row {{
            display: flex;
            align-items: baseline;
            gap: 14px;
            padding: 11px 6px;
            border-bottom: 0.5px solid var(--border);
            text-decoration: none;
            color: inherit;
            transition: background 0.1s;
        }}

        .row:last-of-type {{ border-bottom: none; }}
        .row:hover {{ background: rgba(139,94,42,0.04); }}
        .row-hidden {{ display: none !important; }}

        /* Thread row parts */
        .row-badge {{
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            background: #fef3c7;
            color: #92400e;
            border: 0.5px solid #f59e0b;
            border-radius: 3px;
            padding: 2px 6px;
            white-space: nowrap;
            flex-shrink: 0;
        }}

        .row-main {{
            flex: 1;
            min-width: 0;
        }}

        .row-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text);
            display: block;
            line-height: 1.4;
        }}

        .row:hover .row-title {{ color: var(--accent); }}

        .row-excerpt {{
            font-size: 12px;
            color: var(--text-dim);
            font-style: italic;
            display: block;
            margin-top: 2px;
            line-height: 1.4;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 520px;
        }}

        .row-meta {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            white-space: nowrap;
            flex-shrink: 0;
        }}

        /* Article row parts */
        .row-date-col {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            white-space: nowrap;
            flex-shrink: 0;
            width: 88px;
        }}

        .row-source {{
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 500;
            white-space: nowrap;
            flex-shrink: 0;
            width: 150px;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        /* Date on thread rows */
        .row-thread .row-date {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            white-space: nowrap;
            flex-shrink: 0;
        }}

        /* Hover-reveal action */
        .row-action {{
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--accent);
            white-space: nowrap;
            flex-shrink: 0;
            opacity: 0;
            transition: opacity 0.15s;
            font-weight: 500;
        }}

        .row:hover .row-action {{ opacity: 1; }}

        /* Expand footer */
        .expand-footer {{
            padding: 10px 6px;
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--accent);
            cursor: pointer;
            border-top: 0.5px solid var(--border);
            transition: color 0.1s;
        }}

        .expand-footer:hover {{ color: var(--text); }}

        .section-body.expanded .expand-footer {{ display: none; }}
        .section-body.expanded .row-hidden {{ display: flex !important; }}

        .row-empty {{
            padding: 20px 6px;
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
        }}
    </style>
</head>
<body>
<header>
  <div class="header-left">
    <a href="../../../" class="logo">📚 Curator</a>
    <span class="logo-sub">Scans &amp; Dives</span>
  </div>
  <nav class="header-nav">
    <a href="../../../" class="nav-link">Daily</a>
    <a href="../../../curator_library.html" class="nav-link">Library</a>
    <a href="../../../interests/2026/scans/index.html" class="nav-link active">Scans &amp; Dives</a>
    <a href="../../../curator_priorities.html" class="nav-link">Priorities</a>
    <a href="../../../curator_intelligence.html" class="nav-link">AI Observations</a>
  </nav>
</header>

<main>
    <div class="page-header">
        <h1 class="page-title">Scans &amp; Dives</h1>
        <p class="page-meta">{count_label}</p>
    </div>

{threads_section_html}
    <div class="section" id="section-articles">
      <div class="section-hdr">
        <span class="section-name">Scans <span class="section-count">{len(scans)}</span></span>
        <button class="toggle-btn"{article_toggle_style} onclick="toggleSection(\'section-articles\', this)">show all</button>
      </div>
      <div class="section-body">
{article_rows_html}{article_footer_html}      </div>
    </div>
</main>

<script>
function toggleSection(sectionId, btn) {{
    const body = document.getElementById(sectionId).querySelector('.section-body');
    const expanded = body.classList.toggle('expanded');
    btn.textContent = expanded ? 'collapse' : 'show all';
}}
function expandSection(sectionId) {{
    const body = document.getElementById(sectionId).querySelector('.section-body');
    const btn  = document.getElementById(sectionId).querySelector('.toggle-btn');
    body.classList.add('expanded');
    if (btn) btn.textContent = 'collapse';
}}
</script>
</body>
</html>'''

    return html
