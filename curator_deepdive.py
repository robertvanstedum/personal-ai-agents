#!/usr/bin/env python3
"""
Curator Deep Dive - Ad-hoc Sonnet analysis on flagged articles

USAGE:
  # Analyze article by rank from today's briefing
  python curator_deepdive.py --rank=6
  
  # Or by URL
  python curator_deepdive.py --url="https://example.com/article"
  
  # Save to interests directory
  python curator_deepdive.py --rank=6 --save-interest

COST: ~$0.10-0.20 per deep dive (Sonnet analysis)

OUTPUT:
  - Markdown file: interests/deep-dive-YYYY-MM-DD-topic.md
  - Console: formatted analysis
  - Optional: Send to Telegram
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from curator_rss_v2 import get_anthropic_api_key

def get_article_from_briefing(rank: int) -> Optional[Dict]:
    """
    Get article from today's briefing by rank (1-20)
    Reads from curator output files
    """
    # Try to find today's briefing
    today = datetime.now().strftime("%Y-%m-%d")
    archive_file = f"curator_archive/curator_{today}.html"
    
    # TODO: Parse HTML to extract article data
    # For now, return None - implement parsing logic
    print(f"⚠️  Parsing briefing HTML not yet implemented")
    print(f"   For now, use --url flag to analyze specific articles")
    return None

def fetch_article_content(url: str) -> str:
    """
    Fetch full article text from URL
    Uses requests + readability or browser automation
    """
    # TODO: Implement web scraping
    # Options:
    #   1. requests + BeautifulSoup + readability-lxml
    #   2. OpenClaw web_fetch tool (if available)
    #   3. Playwright/browser automation for JS-heavy sites
    
    print(f"📥 Fetching article from {url}...")
    print(f"⚠️  Article fetching not yet implemented")
    print(f"   Will use article title + summary for now")
    return ""

def load_dive_ratings(max_examples: int = 3) -> str:
    """
    Read interests/ratings.json and build a quality guidance block.

    Returns empty string if:
    - File missing or unreadable
    - Fewer than 2 ratings (not enough signal yet)

    max_examples: how many entries to show per tier (high / low)
    """
    ratings_path = Path(__file__).parent / 'interests' / 'ratings.json'

    try:
        with open(ratings_path) as f:
            data = json.load(f)
    except Exception:
        return ""

    ratings = data.get('ratings', [])

    if len(ratings) < 2:
        return ""  # Not enough signal yet

    high = [r for r in ratings if r.get('stars', 0) >= 3]
    low  = [r for r in ratings if 1 <= r.get('stars', 0) <= 2]

    if not high and not low:
        return ""

    sections = []

    if high:
        sections.append("High-rated dives (3-4★) — what worked:")
        for r in high[-max_examples:]:
            line = f"  • {r.get('article_title', 'Unknown')[:60]}"
            if r.get('user_comment'):
                line += f" → \"{r['user_comment']}\""
            if r.get('ai_themes'):
                line += f" [{', '.join(r['ai_themes'])}]"
            sections.append(line)

    if low:
        sections.append("Low-rated dives (1-2★) — what didn't work:")
        for r in low[-max_examples:]:
            line = f"  • {r.get('article_title', 'Unknown')[:60]}"
            if r.get('user_comment'):
                line += f" → \"{r['user_comment']}\""
            if r.get('ai_themes'):
                line += f" [{', '.join(r['ai_themes'])}]"
            sections.append(line)

    block  = f"\nPAST DEEP DIVE QUALITY REFERENCE (from {len(ratings)} rated dives):\n"
    block += "\n".join(sections)
    block += "\nApply these as quality benchmarks — replicate what worked, avoid what didn't.\n"

    return block


def build_deepdive_prompt(article_data: Dict, full_content: str = "") -> str:
    """
    Build Sonnet prompt for deep analysis
    
    Focuses on:
    - Key implications (what this really means)
    - Contrarian angles (what others miss)
    - Geopolitical context (connections)
    - Investment/policy angles (actionable insights)
    """
    
    title = article_data.get('title', 'Unknown')
    source = article_data.get('source', 'Unknown')
    url = article_data.get('url', 'Unknown')
    summary = article_data.get('summary', '')
    category = article_data.get('category', 'other')
    
    ratings_guidance = load_dive_ratings()

    prompt = f"""You are a geopolitics & finance analyst doing DEEP ANALYSIS on a flagged article.

ARTICLE METADATA:
Title: {title}
Source: {source}
Category: {category}
URL: {url}

SUMMARY/EXCERPT:
{summary[:1000]}
{ratings_guidance}
YOUR TASK:
Provide a comprehensive analysis covering:

## 1. KEY IMPLICATIONS
What does this really mean? What are the second-order effects?
Go beyond the surface narrative.

## 2. CONTRARIAN ANGLES
What is the mainstream missing here?
What alternative interpretations or risks exist?
What would a contrarian investor/analyst think?

## 3. GEOPOLITICAL CONTEXT
How does this connect to broader trends?
What other developments does this relate to?
Who wins/loses from this?

## 4. INVESTMENT & POLICY IMPLICATIONS
If you were managing money or advising policymakers:
- What assets/positions does this affect?
- What policy responses might follow?
- What should be monitored next?

## 5. WHAT TO WATCH
What specific indicators or events would confirm/refute this analysis?
What should I track going forward?

OUTPUT FORMAT:
Use markdown with clear headers (##).
Be specific, not generic.
Challenge assumptions.
Connect non-obvious dots.
"""
    
    return prompt

def analyze_with_sonnet(prompt: str) -> str:
    """
    Send prompt to Sonnet and get analysis
    Returns markdown-formatted analysis
    """
    from anthropic import Anthropic
    
    api_key = get_anthropic_api_key()
    if not api_key:
        print("❌ Anthropic API key not found")
        print("   Run: python setup_keys.py")
        sys.exit(1)
    
    client = Anthropic(api_key=api_key)
    
    print("🧠 Analyzing with Sonnet (this may take 30-60 seconds)...")
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        analysis = response.content[0].text.strip()
        
        # Report cost
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens / 1_000_000 * 3.00) + (output_tokens / 1_000_000 * 15.00)
        
        print(f"✅ Analysis complete")
        print(f"💰 Cost: ${cost:.4f} ({input_tokens:,} in + {output_tokens:,} out tokens)")
        
        return analysis
        
    except Exception as e:
        print(f"❌ Sonnet API error: {e}")
        sys.exit(1)

def save_analysis(article_data: Dict, analysis: str, interests_dir: Path) -> str:
    """
    Save analysis to markdown file in interests directory
    Returns filename
    """
    interests_dir.mkdir(exist_ok=True)
    
    # Generate filename from title
    today = datetime.now().strftime("%Y-%m-%d")
    title = article_data.get('title', 'article')
    # Clean title for filename
    clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-')).strip()
    clean_title = clean_title.replace(' ', '-')[:50]  # Max 50 chars
    
    filename = f"deep-dive-{today}-{clean_title}.md"
    filepath = interests_dir / filename
    
    # Build markdown document
    content = f"""# Deep Dive: {article_data.get('title')}

**Date:** {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Source:** {article_data.get('source', 'Unknown')}
**Category:** {article_data.get('category', 'other')}
**URL:** {article_data.get('url', 'N/A')}

---

{analysis}

---

*Generated by Curator Deep Dive*
*Cost: ~$0.10-0.20 (Sonnet 4 analysis)*
"""
    
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"💾 Saved to: {filepath}")
    return str(filepath)

def main():
    parser = argparse.ArgumentParser(description="Deep dive analysis on flagged articles")
    parser.add_argument('--rank', type=int, help="Article rank from today's briefing (1-20)")
    parser.add_argument('--url', type=str, help="Article URL")
    parser.add_argument('--save-interest', action='store_true', help="Save to interests/ directory")
    parser.add_argument('--telegram', action='store_true', help="Send analysis to Telegram")
    
    args = parser.parse_args()
    
    if not args.rank and not args.url:
        print("❌ Must specify either --rank or --url")
        print("\nExamples:")
        print("  python curator_deepdive.py --rank=6")
        print("  python curator_deepdive.py --url='https://example.com/article'")
        sys.exit(1)
    
    # Get article data
    if args.rank:
        article_data = get_article_from_briefing(args.rank)
        if not article_data:
            print(f"❌ Could not find article rank {args.rank} in today's briefing")
            sys.exit(1)
    else:
        # Manual URL input
        article_data = {
            'url': args.url,
            'title': 'Manual Article',  # Will be updated if we fetch content
            'source': 'Unknown',
            'category': 'other',
            'summary': 'Manual deep dive analysis'
        }
    
    # Fetch full content (optional, may not be implemented yet)
    full_content = fetch_article_content(article_data['url']) if article_data.get('url') else ""
    
    # Build prompt
    prompt = build_deepdive_prompt(article_data, full_content)
    
    # Analyze with Sonnet
    analysis = analyze_with_sonnet(prompt)
    
    # Display
    print("\n" + "="*80)
    print(analysis)
    print("="*80 + "\n")
    
    # Save if requested
    if args.save_interest:
        interests_dir = Path.home() / ".openclaw" / "workspace" / "interests"
        filepath = save_analysis(article_data, analysis, interests_dir)
    
    # TODO: Telegram sending if requested
    if args.telegram:
        print("⚠️  Telegram sending not yet implemented")
        print("   Analysis saved to file for now")

if __name__ == "__main__":
    main()
