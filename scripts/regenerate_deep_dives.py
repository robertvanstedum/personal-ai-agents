#!/usr/bin/env python3
"""
Regenerate all deep dive HTML files with the new parchment template
"""
import re
import json
from pathlib import Path

def regenerate_deep_dive_html(md_path):
    """Regenerate HTML from markdown file using new template"""
    
    # Read the markdown
    md_content = md_path.read_text()
    
    # Extract metadata from the markdown
    # Format: # Article Title
    # Source: ...
    # URL: ...
    # etc.
    
    lines = md_content.split('\n')
    title = lines[0].replace('# ', '').strip() if lines else 'Untitled'
    
    metadata = {}
    for line in lines[1:20]:  # Check first 20 lines for metadata
        if line.startswith('**Source:**'):
            metadata['source'] = line.replace('**Source:**', '').strip()
        elif line.startswith('**URL:**'):
            metadata['url'] = line.replace('**URL:**', '').strip()
        elif line.startswith('**Hash ID:**'):
            metadata['hash_id'] = line.replace('**Hash ID:**', '').strip()
        elif line.startswith('**Your Interest:**'):
            metadata['interest'] = line.replace('**Your Interest:**', '').strip()
        elif line.startswith('**Focus:**'):
            metadata['focus'] = line.replace('**Focus:**', '').strip()
    
    # Extract the analysis content (everything after metadata section)
    analysis_start = 0
    for i, line in enumerate(lines):
        if line.startswith('## '):
            analysis_start = i
            break
    
    analysis_content = '\n'.join(lines[analysis_start:])
    
    # Extract cost info from the last line if present
    cost_match = re.search(r'(\d+) input \+ (\d+) output tokens • \$(\d+\.\d+)', md_content)
    if cost_match:
        input_tokens = cost_match.group(1)
        output_tokens = cost_match.group(2)
        cost = cost_match.group(3)
    else:
        input_tokens = "N/A"
        output_tokens = "N/A"
        cost = "0.00"
    
    # Build the new HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
    <title>{title} - Deep Dive</title>
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
            background: var(--bg-texture);
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            font-size: 0.95em;
            border: 1px solid var(--border);
        }}

        .meta strong {{
            color: var(--text);
        }}

        .interest-box {{
            background: rgba(255, 193, 7, 0.1);
            border-left: 4px solid #ffc107;
            padding: 12px 15px;
            margin: 15px 0;
            font-size: 0.9em;
            border-radius: 4px;
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
            color: var(--text);
            font-size: 1.4em;
            margin-top: 30px;
            border-bottom: 2px solid var(--border);
            padding-bottom: 8px;
            font-family: 'Playfair Display', serif;
            font-weight: 400;
        }}

        .analysis h3 {{
            color: var(--text-muted);
            font-size: 1.15em;
            margin-top: 20px;
        }}

        .bibliography {{
            background: var(--bg-texture);
            border-left: 4px solid var(--accent);
            padding: 20px;
            margin: 30px 0;
            border-radius: 8px;
            border: 1px solid var(--border);
        }}

        .bibliography h2 {{
            margin-top: 0;
            color: var(--text);
            font-size: 1.3em;
            font-family: 'Playfair Display', serif;
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
            color: var(--accent);
            word-break: break-all;
        }}

        .bibliography code {{
            background: var(--surface2);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}

        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border);
            font-size: 0.9em;
            color: var(--text-dim);
            text-align: center;
        }}

        a {{
            color: var(--accent);
            text-decoration: none;
        }}

        a:hover {{
            text-decoration: underline;
        }}

        .analysis p {{
            margin-bottom: 1em;
        }}

        .analysis ul {{
            margin: 1em 0;
            padding-left: 2em;
        }}

        .analysis li {{
            margin-bottom: 0.5em;
        }}
    </style>
</head>
<body>
<header>
  <div class="header-left">
    <a href="../../../" class="logo">📚 Curator</a>
    <span class="logo-sub">Deep Dive</span>
  </div>
  <nav class="header-nav">
    <a href="../../../" class="nav-link">Daily</a>
    <a href="../../../curator_library.html" class="nav-link">Library</a>
    <a href="../../../interests/2026/deep-dives/index.html" class="nav-link active">Deep Dives</a>
  </nav>
</header>
<div style="padding: 32px;">
    <div class="container">
        <h1>{title}</h1>
        
        <div class="meta">
            <strong>Source:</strong> {metadata.get('source', 'N/A')}<br>
            <strong>URL:</strong> <a href="{metadata.get('url', '#')}" target="_blank">{metadata.get('url', 'N/A')}</a><br>
            <strong>Hash ID:</strong> {metadata.get('hash_id', 'N/A')}
        </div>
"""
    
    if metadata.get('interest'):
        html += f"""        
        <div class="interest-box">
            <h2>Your Interest</h2>
            <p>{metadata['interest']}</p>
"""
        if metadata.get('focus'):
            html += f"            <p><strong>Focus:</strong> {metadata['focus']}</p>\n"
        html += "        </div>\n"
    
    html += """        
        <div class="analysis">
            <h2>Analysis</h2>
"""
    
    # Convert markdown to basic HTML
    # Strip ALL "Deep Dive Analysis" headings (we add it in template)
    analysis_content = re.sub(r'^##\s+Deep Dive Analysis\s*\n', '', analysis_content, flags=re.IGNORECASE | re.MULTILINE)
    analysis_content = re.sub(r'^##\s+Deep Dive Analysis\s*\n', '', analysis_content, flags=re.IGNORECASE | re.MULTILINE)
    
    analysis_html = analysis_content
    
    # Check for Sources section
    has_sources = bool(re.search(r'^## (Sources|Bibliography|Further Reading|References)', analysis_content, flags=re.MULTILINE))
    
    if has_sources:
        parts = re.split(r'(^## (?:Sources|Bibliography|Further Reading|References)[^\n]*\n)', analysis_content, maxsplit=1, flags=re.MULTILINE)
        main_content = parts[0] if len(parts) > 0 else analysis_content
        sources_heading = parts[1] if len(parts) > 1 else ''
        sources_content = parts[2] if len(parts) > 2 else ''
    else:
        main_content = analysis_content
        sources_heading = ''
        sources_content = ''
    
    # Convert main content
    analysis_html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', main_content, flags=re.MULTILINE)
    analysis_html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', analysis_html, flags=re.MULTILINE)
    analysis_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', analysis_html)
    analysis_html = re.sub(r'^- (.+)$', r'<li>\1</li>', analysis_html, flags=re.MULTILINE)
    
    # Wrap consecutive li tags in ul
    analysis_html = re.sub(r'(<li>.*?</li>\n)+', lambda m: '<ul>\n' + m.group(0) + '</ul>\n', analysis_html, flags=re.DOTALL)
    
    # Paragraphs
    paragraphs = analysis_html.split('\n\n')
    analysis_html = '\n'.join([f'<p>{p}</p>' if not p.startswith('<') else p for p in paragraphs if p.strip()])
    
    html += analysis_html
    
    # Add sources section if present
    if has_sources and sources_content.strip():
        sources_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', sources_content)
        sources_html = re.sub(r'^- (.+)$', r'<li>\1</li>', sources_html, flags=re.MULTILINE)
        sources_html = re.sub(r'(<li>.*?</li>\n)+', lambda m: '<ul>\n' + m.group(0) + '</ul>\n', sources_html, flags=re.DOTALL)
        
        sources_title = re.search(r'## (.+)', sources_heading)
        title_text = sources_title.group(1) if sources_title else 'Sources & Further Reading'
        
        html += f"""
        </div>
        
        <div class="bibliography">
            <h2>📚 {title_text}</h2>
{sources_html}
        </div>
        
        <div class="analysis">
"""
    
    html += f"""
        </div>
        
        <div class="footer">
            Generated by Claude Sonnet<br>
            {input_tokens} input + {output_tokens} output tokens • ${cost}
        </div>
    </div>
</div>
</body>
</html>
"""
    
    return html


if __name__ == '__main__':
    deep_dives_dir = Path('interests/2026/deep-dives')
    
    count = 0
    for md_file in deep_dives_dir.glob('*.md'):
        html_file = md_file.with_suffix('.html')
        
        print(f"Regenerating {html_file.name}...")
        
        try:
            new_html = regenerate_deep_dive_html(md_file)
            html_file.write_text(new_html)
            count += 1
            print(f"  ✅ Updated")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    print(f"\n✅ Regenerated {count} deep dive HTML files with parchment design")
