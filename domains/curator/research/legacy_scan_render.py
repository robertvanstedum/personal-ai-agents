"""Legacy generated Scan page, distinct from the current web Scan renderer."""
import re
from html import escape
from utils.html_links import safe_web_href

def render_scan(hash_id, article_data, initial_interest, dive_focus, analysis_content, cost, input_tokens, output_tokens, *, today):
    """Generate HTML version of a Scan (formerly generate_deep_dive_html)"""
    
    
    # Raw document fields are text, not trusted HTML. Keep the legacy Markdown
    # formatting rules after escaping rather than admitting generated tags.
    source_href = safe_web_href(article_data['url'])
    article_data = {**article_data, **{
        key: escape(str(article_data[key]), quote=False) for key in ('title', 'source', 'url')}}
    hash_id = escape(str(hash_id), quote=False)
    initial_interest = escape(str(initial_interest), quote=False)
    if dive_focus:
        dive_focus = escape(str(dive_focus), quote=False)
    analysis_content = escape(analysis_content, quote=False)
    today = escape(str(today), quote=False)
    input_tokens = escape(str(input_tokens), quote=False)
    output_tokens = escape(str(output_tokens), quote=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
    <title>{article_data['title']} - Scan</title>
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
            font-size: 15px;
            line-height: 1.6;
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
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            padding: 32px;
            background: var(--surface);
            border-radius: 12px;
            box-shadow: 0 2px 12px var(--shadow);
        }}
        h1 {{
            font-family: 'Playfair Display', serif;
            color: var(--text);
            font-size: 2em;
            font-weight: 400;
            border-bottom: 2px solid var(--border);
            padding-bottom: 12px;
            margin-bottom: 24px;
            letter-spacing: -0.02em;
        }}
        .meta {{
            background: #ecf0f1;
            padding: 15px;
            border-radius: 4px;
            margin: 20px 0;
            font-size: 0.95em;
        }}
        .meta strong {{
            color: #2c3e50;
        }}
        .interest-box {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 12px 15px;
            margin: 15px 0;
            font-size: 0.9em;
        }}
        .interest-box h2 {{
            margin-top: 0;
            margin-bottom: 8px;
            color: #856404;
            font-size: 1.1em;
        }}
        .interest-box p {{
            margin: 6px 0;
        }}
        .analysis {{
            margin-top: 30px;
        }}
        .analysis h2 {{
            color: #2c3e50;
            font-size: 1.4em;
            margin-top: 30px;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 8px;
        }}
        .analysis h3 {{
            color: #34495e;
            font-size: 1.15em;
            margin-top: 20px;
        }}
        .bibliography {{
            background: #f8f9fa;
            border-left: 4px solid #6c757d;
            padding: 20px;
            margin: 30px 0;
            border-radius: 4px;
        }}
        .bibliography h2 {{
            margin-top: 0;
            color: #495057;
            font-size: 1.3em;
        }}
        .bibliography ul {{
            margin-top: 15px;
            list-style-type: none;
            padding-left: 0;
        }}
        .bibliography li {{
            margin-bottom: 15px;
            line-height: 1.6;
            padding-left: 20px;
            text-indent: -20px;
        }}
        .bibliography a {{
            color: #0066cc;
            word-break: break-all;
        }}
        .bibliography code {{
            background: #e9ecef;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #ecf0f1;
            font-size: 0.9em;
            color: #7f8c8d;
            text-align: center;
        }}
        a {{
            color: var(--accent);
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
<header>
  <div class="header-left">
    <a href="../../../" class="logo">📚 Curator</a>
    <span class="logo-sub">Scan</span>
  </div>
  <nav class="header-nav">
    <a href="../../../" class="nav-link">Daily</a>
    <a href="../../../curator_library.html" class="nav-link">Library</a>
    <a href="../../../interests/2026/scans/index.html" class="nav-link active">Scans &amp; Dives</a>
    <a href="../../../curator_priorities.html" class="nav-link">Priorities</a>
    <a href="../../../curator_intelligence.html" class="nav-link">AI Observations</a>
  </nav>
</header>
<div style="padding: 32px;">
    <div class="container">
        
        <h1>{article_data['title']}</h1>
        
        <div class="meta">
            <strong>Source:</strong> {article_data['source']}<br>
            <strong>URL:</strong> <a href="{source_href}" target="_blank">{article_data['url']}</a><br>
            <strong>Date:</strong> {today}<br>
            <strong>Hash ID:</strong> {hash_id}
        </div>
        
        <div class="interest-box">
            <h2>Your Interest</h2>
            <p>{initial_interest}</p>
"""
    
    if dive_focus:
        html += f"            <p><strong>Focus:</strong> {dive_focus}</p>\n"
    
    html += """        </div>
        
        <div class="analysis">
            <h2>Scan Analysis</h2>
"""
    
    # Convert markdown analysis to basic HTML
    # Simple conversion: h2, h3, bold, lists
    
    # Strip ALL "Deep Dive Analysis" headings from AI output (we add it in template)
    # Run twice to handle duplicate headings that sometimes appear
    analysis_content = re.sub(r'^##\s+(Deep Dive|Scan) Analysis\s*\n', '', analysis_content, flags=re.IGNORECASE | re.MULTILINE)
    analysis_content = re.sub(r'^##\s+(Deep Dive|Scan) Analysis\s*\n', '', analysis_content, flags=re.IGNORECASE | re.MULTILINE)
    
    # Check if there's a Sources/Bibliography section
    has_sources = bool(re.search(r'^## (Sources|Bibliography|Further Reading|References)', analysis_content, flags=re.MULTILINE))
    
    if has_sources:
        # Split at Sources section
        parts = re.split(r'(^## (?:Sources|Bibliography|Further Reading|References)[^\n]*\n)', analysis_content, maxsplit=1, flags=re.MULTILINE)
        main_content = parts[0] if len(parts) > 0 else analysis_content
        sources_heading = parts[1] if len(parts) > 1 else ''
        sources_content = parts[2] if len(parts) > 2 else ''
    else:
        main_content = analysis_content
        sources_heading = ''
        sources_content = ''
    
    # Convert main content
    analysis_html = re.sub(r'^## (.+)$', '<h2>\\1</h2>', main_content, flags=re.MULTILINE)
    analysis_html = re.sub(r'^### (.+)$', '<h3>\\1</h3>', analysis_html, flags=re.MULTILINE)
    analysis_html = re.sub(r'\*\*(.+?)\*\*', '<strong>\\1</strong>', analysis_html)
    analysis_html = re.sub(r'^- (.+)$', '<li>\\1</li>', analysis_html, flags=re.MULTILINE)
    analysis_html = re.sub(r'(<li>.*</li>)', '<ul>\\1</ul>', analysis_html, flags=re.DOTALL)
    
    # Paragraphs
    paragraphs = analysis_html.split('\n\n')
    analysis_html = '\n'.join([f'<p>{p}</p>' if not p.startswith('<') else p for p in paragraphs if p.strip()])
    
    html += analysis_html
    
    # Add sources section if present
    if has_sources and sources_content.strip():
        sources_html = re.sub(r'\*\*(.+?)\*\*', '<strong>\\1</strong>', sources_content)
        sources_html = re.sub(r'^- (.+)$', '<li>\\1</li>', sources_html, flags=re.MULTILINE)
        sources_html = re.sub(r'(<li>.*</li>)', '<ul>\\1</ul>', sources_html, flags=re.DOTALL)
        
        # Extract title from heading
        sources_title = re.search(r'## (.+)', sources_heading)
        title = sources_title.group(1) if sources_title else 'Sources & Further Reading'
        
        html += f"""
        </div>
        
        <div class="bibliography">
            <h2>📚 {title}</h2>
{sources_html}
        </div>
        
        <div class="analysis">
"""
    
    html += f"""
        </div>
        
        <div class="footer">
            Generated by Claude Sonnet<br>
            {input_tokens} input + {output_tokens} output tokens • ${cost:.4f}
        </div>
    </div>
</div>
</body>
</html>
"""
    
    return html
