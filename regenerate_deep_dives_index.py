#!/usr/bin/env python3
"""
Regenerate deep dives index.html
"""

from pathlib import Path
from datetime import datetime
import json
import re

def regenerate_deep_dives_index():
    """Scan deep-dives directory and regenerate index.html"""
    
    deep_dives_dir = Path(__file__).parent / "interests" / "2026" / "deep-dives"
    
    if not deep_dives_dir.exists():
        print(f"❌ Deep dives directory not found: {deep_dives_dir}")
        return
    
    # Scan for markdown files (exclude index.html)
    dives = []
    
    for md_file in deep_dives_dir.glob("*.md"):
        # Read metadata from markdown
        with open(md_file, 'r') as f:
            content = f.read()
        
        # Extract title, source, date
        title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
        source_match = re.search(r'\*\*Source:\*\* (.+)$', content, re.MULTILINE)
        date_match = re.search(r'\*\*Date:\*\* (.+)$', content, re.MULTILINE)
        
        if title_match and source_match and date_match:
            title = title_match.group(1).strip()
            source = source_match.group(1).strip()
            date_str = date_match.group(1).strip()
            
            html_file = md_file.stem + '.html'
            
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            except:
                date_obj = datetime.now()
            
            dives.append({
                'title': title,
                'source': source,
                'date': date_obj,
                'date_str': date_str,
                'html_file': html_file
            })
    
    # Sort by date, newest first
    dives.sort(key=lambda x: x['date'], reverse=True)
    
    # Generate HTML
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Deep Dive Archive - Briefing Platform</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            font-size: 14px;
            max-width: 1400px;
            margin: 15px auto;
            padding: 12px;
            background: #f5f5f5;
            color: #333;
        }
        .header {
            margin-bottom: 15px;
            padding: 12px 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 5px;
            text-align: center;
        }
        .header h1 {
            margin: 0 0 6px 0;
            font-size: 1.5em;
            font-weight: 600;
        }
        .header-meta {
            opacity: 0.9;
            font-size: 0.88em;
            margin: 0 8px;
            display: inline-block;
        }
        .nav-buttons {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 15px;
        }
        .nav-btn {
            padding: 8px 16px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.9em;
            font-weight: 500;
        }
        .nav-btn:hover {
            background: #5568d3;
        }
        .dives-table {
            background: white;
            border-radius: 5px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        thead {
            background: #f8f9fa;
            border-bottom: 2px solid #ddd;
        }
        th {
            padding: 11px 12px;
            text-align: left;
            font-weight: 600;
            font-size: 0.9em;
            color: #495057;
        }
        td {
            padding: 12px;
            border-bottom: 1px solid #ddd;
            vertical-align: middle;
        }
        tbody tr:nth-child(even) {
            background: #fafafa;
        }
        tbody tr:last-child td {
            border-bottom: none;
        }
        tbody tr:hover {
            background: #f0f4ff;
        }
        .dive-title {
            font-weight: 500;
            color: #333;
            line-height: 1.4;
        }
        .dive-title a {
            color: #333;
            text-decoration: none;
        }
        .dive-title a:hover {
            color: #667eea;
        }
        .date-badge {
            color: #888;
            font-size: 0.9em;
        }
        .source-name {
            color: #666;
            font-size: 0.95em;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🔍 Deep Dive Archive</h1>
        <div>
            <span class="header-meta">""" + str(len(dives)) + """ deep dives</span>
            <span class="header-meta">•</span>
            <span class="header-meta">In-depth research</span>
        </div>
    </div>

    <div class="nav-buttons">
        <a href="../../../curator_briefing.html" class="nav-btn">📰 Today</a>
        <a href="../../../curator_index.html" class="nav-btn">📚 Archive</a>
        <a href="index.html" class="nav-btn">🔍 Deep Dives</a>
    </div>

    <div class="dives-table">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Source</th>
                    <th>Title</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
"""
    
    if dives:
        for dive in dives:
            formatted_date = dive['date'].strftime("%b %d, %Y")
            html += f"""                <tr>
                    <td><span class="date-badge">{formatted_date}</span></td>
                    <td><span class="source-name">{dive['source']}</span></td>
                    <td class="dive-title">
                        <a href="{dive['html_file']}">
                            {dive['title']}
                        </a>
                    </td>
                    <td><a href="{dive['html_file']}" class="nav-btn">Read Analysis →</a></td>
                </tr>
"""
    else:
        html += """                <tr>
                    <td colspan="4" style="text-align: center; padding: 30px; color: #888;">
                        No deep dives yet. Like or save an article, then click 🔖 Deep Dive!
                    </td>
                </tr>
"""
    
    html += """            </tbody>
        </table>
    </div>
</body>
</html>
"""
    
    # Write index
    index_file = deep_dives_dir / "index.html"
    with open(index_file, 'w') as f:
        f.write(html)
    
    print(f"✅ Deep dives index regenerated: {len(dives)} entries")

if __name__ == '__main__':
    regenerate_deep_dives_index()
