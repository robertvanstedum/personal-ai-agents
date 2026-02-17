#!/usr/bin/env python3
"""
Article Interest Flagging System
Capture user interest in articles from curator briefings

Usage (via Telegram):
  /flag 3 DEEP-DIVE    # Trigger immediate analysis
  /flag 5 THIS-WEEK    # Boost similar articles this week
  /flag 7 BACKLOG      # Save for later exploration
  /flag 2 MUTE         # Reduce this topic temporarily

Priority Levels:
  DEEP-DIVE: Immediate AI analysis + boost (+50, 3 days)
  THIS-WEEK: Boost in briefings (+30, 7 days)
  BACKLOG: Low priority tracking (+10, no expiry)
  MUTE: Temporarily reduce topic (-20, 7 days)
"""

import sys
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict
import json

# Project root
PROJECT_ROOT = Path(__file__).parent

# Priority levels configuration
PRIORITIES = {
    'DEEP-DIVE': {'score': 50, 'days': 3, 'description': 'Immediate analysis + strong boost'},
    'THIS-WEEK': {'score': 30, 'days': 7, 'description': 'Boost similar articles'},
    'BACKLOG': {'score': 10, 'days': None, 'description': 'Save for later'},
    'MUTE': {'score': -20, 'days': 7, 'description': 'Reduce this topic'}
}


def parse_latest_briefing() -> Optional[Dict]:
    """
    Parse the most recent curator briefing to extract article metadata.
    Returns dict mapping article numbers to metadata.
    """
    briefing_file = PROJECT_ROOT / "curator_output.txt"
    
    if not briefing_file.exists():
        print("Error: No curator briefing found (curator_output.txt)", file=sys.stderr)
        return None
    
    articles = {}
    current_article = None
    
    with open(briefing_file, 'r') as f:
        for line in f:
            # Match article headers: #1 [Source] 🏷️ category
            match = re.match(r'^#(\d+)\s+\[([^\]]+)\]\s+🏷️\s+(\w+)', line)
            if match:
                num = int(match.group(1))
                source = match.group(2)
                category = match.group(3)
                current_article = num
                articles[num] = {
                    'number': num,
                    'source': source,
                    'category': category,
                    'title': None,
                    'url': None,
                    'published': None
                }
                continue
            
            # Extract title (indented line after header)
            if current_article and articles[current_article]['title'] is None:
                title_match = re.match(r'^\s{3}(.+)$', line)
                if title_match:
                    articles[current_article]['title'] = title_match.group(1).strip()
                    continue
            
            # Extract URL
            if current_article and 'https://' in line:
                url_match = re.search(r'(https://[^\s]+)', line)
                if url_match:
                    articles[current_article]['url'] = url_match.group(1)
                    continue
            
            # Extract published date
            if current_article and 'Published:' in line:
                pub_match = re.search(r'Published:\s+(.+)$', line)
                if pub_match:
                    articles[current_article]['published'] = pub_match.group(1).strip()
    
    return articles if articles else None


def store_interest(article_num: int, priority: str, article_data: Dict, reason: Optional[str] = None) -> bool:
    """
    Store flagged article interest to interests/ directory.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    interests_dir = PROJECT_ROOT / "interests"
    interests_file = interests_dir / f"{today}-flagged.md"
    
    # Calculate expiry
    priority_config = PRIORITIES[priority]
    if priority_config['days']:
        expiry = (datetime.now() + timedelta(days=priority_config['days'])).strftime('%Y-%m-%d')
    else:
        expiry = "No expiry"
    
    # Format entry
    entry = f"""
## [{priority}] {article_data['title']}
- **URL:** {article_data['url']}
- **Source:** {article_data['source']}
- **Category:** {article_data['category']}
- **Published:** {article_data['published']}
- **Flagged:** {datetime.now().strftime('%Y-%m-%d %I:%M %p')}
- **Expires:** {expiry}
- **Score Modifier:** {priority_config['score']:+d}
"""
    
    if reason:
        entry += f"- **Reason:** {reason}\n"
    
    entry += "\n"
    
    # Append to file
    with open(interests_file, 'a') as f:
        if interests_file.stat().st_size == 0:
            f.write(f"# Flagged Articles - {today}\n\n")
            f.write(f"*Articles flagged for interest tracking and curation boosting*\n")
        f.write(entry)
    
    print(f"✅ Stored interest in {interests_file}")
    return True


def trigger_deep_dive(article_data: Dict) -> bool:
    """
    Trigger deep dive analysis for DEEP-DIVE flagged articles.
    Calls deep_dive.py with article URL.
    """
    deep_dive_script = PROJECT_ROOT / "deep_dive.py"
    
    if not deep_dive_script.exists():
        print("⚠️  deep_dive.py not yet implemented, skipping analysis", file=sys.stderr)
        return False
    
    import subprocess
    
    try:
        print(f"🔍 Triggering deep dive analysis for: {article_data['title']}")
        # Use venv python if available
        python_bin = PROJECT_ROOT / 'venv' / 'bin' / 'python3'
        if not python_bin.exists():
            python_bin = 'python3'
        
        result = subprocess.run(
            [str(python_bin), str(deep_dive_script), article_data['url'], article_data['title']],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            print("✅ Deep dive analysis completed")
            return True
        else:
            print(f"❌ Deep dive failed: {result.stderr}", file=sys.stderr)
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Deep dive timed out (>2 minutes)", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Deep dive error: {e}", file=sys.stderr)
        return False


def main():
    """
    Main entry point for article flagging.
    
    Usage: python flag_article.py <article_number> <priority> [reason]
    """
    if len(sys.argv) < 3:
        print("Usage: python flag_article.py <article_number> <priority> [reason]")
        print("\nPriorities:")
        for pri, config in PRIORITIES.items():
            print(f"  {pri:12} - {config['description']}")
        sys.exit(1)
    
    try:
        article_num = int(sys.argv[1])
    except ValueError:
        print(f"Error: Article number must be integer, got: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)
    
    priority = sys.argv[2].upper()
    if priority not in PRIORITIES:
        print(f"Error: Invalid priority '{priority}'", file=sys.stderr)
        print(f"Valid priorities: {', '.join(PRIORITIES.keys())}")
        sys.exit(1)
    
    reason = ' '.join(sys.argv[3:]) if len(sys.argv) > 3 else None
    
    # Parse briefing
    print(f"📰 Parsing latest briefing...")
    articles = parse_latest_briefing()
    
    if not articles:
        print("❌ No articles found in briefing", file=sys.stderr)
        sys.exit(1)
    
    if article_num not in articles:
        print(f"❌ Article #{article_num} not found in briefing", file=sys.stderr)
        print(f"Available articles: {', '.join(f'#{n}' for n in sorted(articles.keys()))}")
        sys.exit(1)
    
    article_data = articles[article_num]
    
    # Validate article has required data
    if not article_data['url'] or not article_data['title']:
        print(f"❌ Article #{article_num} missing required data (title/URL)", file=sys.stderr)
        sys.exit(1)
    
    # Store interest
    print(f"\n📌 Flagging article #{article_num} as [{priority}]")
    print(f"   Title: {article_data['title']}")
    print(f"   Source: {article_data['source']}")
    print(f"   Category: {article_data['category']}")
    
    if not store_interest(article_num, priority, article_data, reason):
        print("❌ Failed to store interest", file=sys.stderr)
        sys.exit(1)
    
    # Trigger deep dive if requested
    if priority == 'DEEP-DIVE':
        print("\n🔍 DEEP-DIVE flagged - triggering analysis...")
        trigger_deep_dive(article_data)
    
    print(f"\n✅ Article flagged successfully!")
    print(f"   Priority: {priority}")
    print(f"   Score modifier: {PRIORITIES[priority]['score']:+d}")
    if PRIORITIES[priority]['days']:
        print(f"   Active for: {PRIORITIES[priority]['days']} days")


if __name__ == '__main__':
    main()
