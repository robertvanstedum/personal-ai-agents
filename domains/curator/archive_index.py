"""Read archive metadata and write its HTML index in the caller's directory."""
from html import escape

def generate_index_page(archive_dir: str):
    """Generate unified archive index page with timestamp and model info"""
    import os
    import re
    from datetime import datetime
    
    archive_files = []
    if os.path.exists(archive_dir):
        for filename in os.listdir(archive_dir):
            if filename.startswith("curator_") and filename.endswith(".html"):
                timestamp_str = filename.replace("curator_", "").replace(".html", "")
                
                # Try new format first: YYYY-MM-DD-HHMM
                try:
                    datetime_obj = datetime.strptime(timestamp_str, "%Y-%m-%d-%H%M")
                except ValueError:
                    # Fallback to old format: YYYY-MM-DD
                    try:
                        datetime_obj = datetime.strptime(timestamp_str, "%Y-%m-%d")
                    except ValueError:
                        continue
                
                # Extract metadata from HTML file
                filepath = os.path.join(archive_dir, filename)
                model = "unknown"
                article_count = "?"
                try:
                    with open(filepath, 'r') as f:
                        content = f.read(2000)  # Only read first 2KB for metadata
                        model_match = re.search(r'<meta name="curator-model" content="([^"]+)"', content)
                        articles_match = re.search(r'<meta name="curator-articles" content="([^"]+)"', content)
                        if model_match:
                            model = model_match.group(1)
                        if articles_match:
                            article_count = articles_match.group(1)
                except Exception:
                    pass
                
                archive_files.append((timestamp_str, filename, datetime_obj, model, article_count))
    
    archive_files.sort(key=lambda x: x[2], reverse=True)
    
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
    <title>Curator Archive</title>
    <style>
        :root {
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
        }

        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Source Sans 3', sans-serif;
            background: var(--bg);
            background-image:
                radial-gradient(ellipse at 20% 0%, rgba(210,190,160,0.4) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 100%, rgba(200,180,150,0.3) 0%, transparent 50%);
            color: var(--text);
            min-height: 100vh;
            font-size: 14px;
            line-height: 1.5;
        }

        /* ── Header ── */
        header {
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
        }

        .header-left {
            display: flex;
            align-items: baseline;
            gap: 16px;
        }

        .logo {
            font-family: 'Playfair Display', serif;
            font-size: 20px;
            color: var(--accent);
            letter-spacing: -0.02em;
            text-decoration: none;
        }

        .logo-sub {
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-dim);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .header-nav {
            display: flex;
            gap: 4px;
        }

        .nav-link {
            font-family: 'DM Mono', monospace;
            font-size: 11px;
            color: var(--text-muted);
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 6px;
            letter-spacing: 0.05em;
            transition: all 0.15s;
            border: 1px solid transparent;
        }

        .nav-link:hover {
            color: var(--text);
            background: var(--surface2);
        }

        .nav-link.active {
            color: var(--accent);
            background: var(--accent-dim);
            border-color: rgba(139,94,42,0.2);
        }

        /* ── Main ── */
        main {
            max-width: 1400px;
            margin: 0 auto;
            padding: 32px;
        }

        .archive-table {
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 12px var(--shadow);
            background: var(--surface);
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        thead {
            background: var(--bg-texture);
            border-bottom: 2px solid var(--border);
        }

        th {
            padding: 11px 16px;
            text-align: left;
            font-family: 'DM Mono', monospace;
            font-size: 10px;
            font-weight: 500;
            color: var(--text-dim);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        tbody tr {
            border-bottom: 1px solid var(--border);
            transition: background 0.1s;
            background: var(--surface);
        }

        tbody tr:last-child {
            border-bottom: none;
        }

        tbody tr:hover {
            background: rgba(139,94,42,0.04);
        }

        td {
            padding: 14px 16px;
            vertical-align: middle;
        }

        .date-link {
            font-weight: 600;
            color: var(--accent);
            text-decoration: none;
            font-size: 13px;
        }

        .date-link:hover {
            text-decoration: underline;
        }

        .nav-btn {
            color: var(--accent);
            text-decoration: none;
            font-size: 13px;
        }

        .nav-btn:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
<header>
  <div class="header-left">
    <a href="/" class="logo">📚 Curator</a>
    <span class="logo-sub">Archive</span>
  </div>
  <nav class="header-nav">
    <a href="/" class="nav-link">Daily</a>
    <a href="curator_library.html" class="nav-link">Library</a>
    <a href="interests/2026/deep-dives/index.html" class="nav-link">Deep Dives</a>
  </nav>
</header>

<main>
    <div class="archive-table">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Time</th>
                    <th>Model</th>
                    <th>Articles</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
"""
    
    if archive_files:
        for timestamp_str, filename, datetime_obj, model, article_count in archive_files:
            formatted_date = datetime_obj.strftime("%b %d, %Y")
            
            # Check if this is a legacy entry (old date-only format, no timestamp)
            is_legacy = '-' not in timestamp_str or timestamp_str.count('-') == 2
            
            if is_legacy:
                # Legacy entry: show "-" for time and articles
                formatted_time = "-"
                display_article_count = "-"
                # Map old model names or show "legacy" for unknown
                if model == "unknown":
                    model_display = "legacy"
                elif model == "xai":
                    model_display = "grok-beta"  # Old xai entries
                elif model == "mechanical":
                    model_display = "mechanical (keyword)"  # Deterministic keyword scoring
                else:
                    model_display = "legacy" if model == "?" else model
            else:
                # New entry: show full metadata
                formatted_time = datetime_obj.strftime("%I:%M %p")
                display_article_count = article_count
                # Describe the scorer without inventing a provider/model
                if model == "mechanical":
                    model_display = "mechanical (keyword)"
                elif model == "unknown":
                    model_display = "legacy"
                else:
                    model_display = model  # Use as-is from metadata (already formatted)
            
            html += f"""                <tr>
                    <td><a href="{escape(str(archive_dir), quote=True)}/{escape(filename, quote=True)}" class="date-link">{formatted_date}</a></td>
                    <td style="text-align: center;">{formatted_time}</td>
                    <td><span style="font-family: monospace; font-size: 0.9em;">{escape(str(model_display))}</span></td>
                    <td style="text-align: center;">{escape(str(display_article_count))}</td>
                    <td><a href="{escape(str(archive_dir), quote=True)}/{escape(filename, quote=True)}" class="nav-btn">View →</a></td>
                </tr>
"""
    else:
        html += """                <tr>
                    <td colspan="5" style="text-align: center; color: #999; padding: 40px 0;">
                        No archived briefings yet
                    </td>
                </tr>
"""
    
    html += """            </tbody>
        </table>
    </div>
</main>
</body>
</html>
"""
    
    with open("curator_index.html", "w") as f:
        f.write(html)
    print(f"📑 Index page updated: curator_index.html")
