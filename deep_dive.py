#!/usr/bin/env python3
"""
Deep Dive Article Analysis
AI-powered analysis with contrarian perspectives and challenge factors

Usage:
  python deep_dive.py <article_url>
  
Features:
- Fetches full article content
- Claude Sonnet 4 analysis
- Contrarian perspectives
- Challenge factors ("what could go wrong")
- Connections to other topics
- Delivered to Telegram

Cost: ~$0.10-0.15 per analysis
"""

import sys
import os
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
import keyring
from anthropic import Anthropic

# Project root
PROJECT_ROOT = Path(__file__).parent


def fetch_article_content(url: str) -> Optional[str]:
    """
    Fetch and extract article content using web scraping.
    Returns markdown-formatted content.
    """
    try:
        print(f"📡 Fetching article from {url}...")
        
        # Use requests + BeautifulSoup for basic extraction
        # (In production, you'd use your OpenClaw web_fetch tool)
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        response.raise_for_status()
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove scripts, styles, etc.
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            element.decompose()
        
        # Extract text
        text = soup.get_text(separator='\n', strip=True)
        
        # Clean up excessive whitespace
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        content = '\n\n'.join(lines)
        
        # Truncate if too long (Claude has limits)
        if len(content) > 15000:
            content = content[:15000] + "\n\n[Article truncated for analysis]"
        
        print(f"✅ Fetched {len(content)} characters")
        return content
        
    except Exception as e:
        print(f"❌ Error fetching article: {e}", file=sys.stderr)
        return None


def analyze_with_sonnet(article_content: str, article_url: str) -> Optional[str]:
    """
    Analyze article using Claude Sonnet 4.
    Returns formatted analysis.
    """
    try:
        # Get API key
        api_key = keyring.get_password('anthropic', 'api_key')
        if not api_key:
            print("❌ Anthropic API key not found in keyring", file=sys.stderr)
            return None
        
        client = Anthropic(api_key=api_key)
        
        prompt = f"""Analyze this article with focus on deeper implications and contrarian perspectives.

Article URL: {article_url}

Article Content:
{article_content}

---

Please provide:

1. **Key Implications** (3-5 points)
   - What are the second-order effects?
   - Who wins/loses from this?
   - Time horizons: immediate, 6 months, 2 years

2. **Contrarian Perspectives** (2-3 points)
   - What's the mainstream consensus view?
   - What could be wrong about that consensus?
   - Alternative interpretations of the evidence

3. **Challenge Factors** (3-4 points)
   - What could go wrong with this narrative?
   - Hidden risks or unstated assumptions
   - Key metrics to watch that would invalidate this

4. **Connections** (2-3 points)
   - How does this relate to other ongoing developments?
   - Historical parallels or precedents
   - Cross-domain implications (tech → geopolitics, finance → policy, etc.)

Format as clear sections with bullet points. Be specific and analytical, not generic.
Target ~500-800 words total."""

        print("🔍 Analyzing with Claude Sonnet 4...")
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        analysis = response.content[0].text
        
        # Calculate cost (rough estimate)
        input_tokens = len(prompt.split()) * 1.3  # rough estimate
        output_tokens = len(analysis.split()) * 1.3
        cost = (input_tokens / 1000000 * 3.0) + (output_tokens / 1000000 * 15.0)
        
        print(f"✅ Analysis complete (~{output_tokens:.0f} tokens, ~${cost:.2f})")
        
        return analysis
        
    except Exception as e:
        print(f"❌ Error analyzing article: {e}", file=sys.stderr)
        return None


def format_for_telegram(analysis: str, article_url: str, article_title: str = None) -> str:
    """
    Format analysis for Telegram delivery.
    """
    title_line = f"🔍 **Deep Dive:** {article_title}\n\n" if article_title else "🔍 **Deep Dive Analysis**\n\n"
    
    telegram_msg = title_line + analysis + f"\n\n---\n📎 Source: {article_url}\n🤖 Analysis: Claude Sonnet 4"
    
    return telegram_msg


def send_to_telegram(message: str) -> bool:
    """
    Send analysis to Telegram.
    """
    try:
        telegram_token = keyring.get_password('telegram', 'bot_token')
        telegram_chat_id = os.environ.get('TELEGRAM_CHAT_ID', '8379221702')
        
        if not telegram_token:
            print("⚠️  TELEGRAM_BOT_TOKEN not found in keyring", file=sys.stderr)
            return False
        
        # Split if too long (Telegram has 4096 char limit)
        if len(message) > 4000:
            chunks = []
            current_chunk = ""
            for line in message.split('\n'):
                if len(current_chunk) + len(line) + 1 > 4000:
                    chunks.append(current_chunk)
                    current_chunk = line + '\n'
                else:
                    current_chunk += line + '\n'
            
            if current_chunk:
                chunks.append(current_chunk)
            
            # Send each chunk
            for i, chunk in enumerate(chunks, 1):
                url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                data = {
                    "chat_id": telegram_chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True
                }
                response = requests.post(url, json=data, timeout=10)
                response.raise_for_status()
                print(f"📱 Sent Telegram message part {i}/{len(chunks)}")
        else:
            url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            data = {
                "chat_id": telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
            response = requests.post(url, json=data, timeout=10)
            response.raise_for_status()
            print(f"📱 ✅ Sent to Telegram")
        
        return True
        
    except Exception as e:
        print(f"❌ Error sending to Telegram: {e}", file=sys.stderr)
        return False


def save_analysis(analysis: str, article_url: str, article_title: str = None):
    """
    Save analysis to interests/deep-dives/ directory.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    slug = article_title.lower().replace(' ', '-')[:50] if article_title else 'article'
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    
    deep_dives_dir = PROJECT_ROOT / "interests" / "deep-dives"
    deep_dives_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{today}-{slug}.md"
    filepath = deep_dives_dir / filename
    
    with open(filepath, 'w') as f:
        f.write(f"# Deep Dive Analysis\n\n")
        if article_title:
            f.write(f"**Article:** {article_title}\n\n")
        f.write(f"**URL:** {article_url}\n\n")
        f.write(f"**Analyzed:** {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n\n")
        f.write("---\n\n")
        f.write(analysis)
    
    print(f"💾 Saved to {filepath}")


def main():
    """
    Main entry point for deep dive analysis.
    """
    if len(sys.argv) < 2:
        print("Usage: python deep_dive.py <article_url> [article_title]")
        sys.exit(1)
    
    article_url = sys.argv[1]
    article_title = ' '.join(sys.argv[2:]) if len(sys.argv) > 2 else None
    
    print(f"\n🔍 Deep Dive Analysis")
    print(f"   URL: {article_url}\n")
    
    # Fetch article
    content = fetch_article_content(article_url)
    if not content:
        print("❌ Failed to fetch article content", file=sys.stderr)
        sys.exit(1)
    
    # Analyze
    analysis = analyze_with_sonnet(content, article_url)
    if not analysis:
        print("❌ Failed to analyze article", file=sys.stderr)
        sys.exit(1)
    
    # Format for Telegram
    telegram_msg = format_for_telegram(analysis, article_url, article_title)
    
    # Save locally
    save_analysis(analysis, article_url, article_title)
    
    # Send to Telegram
    if send_to_telegram(telegram_msg):
        print("\n✅ Deep dive complete!")
    else:
        print("\n⚠️  Analysis complete but Telegram delivery failed")
        print(f"   Analysis saved locally in interests/deep-dives/")


if __name__ == '__main__':
    main()
