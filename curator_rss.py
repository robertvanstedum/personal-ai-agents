#!/usr/bin/env python3
"""
Simple RSS Curator - Fetch, score, rank geopolitics & finance feeds

MVP VERSION (mechanical scoring):
- Keyword matching + recency + source weights
- Source diversity enforcement (avoid confirmation bias)
- Simple, fast, cheap

FUTURE AI ENHANCEMENT (adaptive learning loop):
Phase 1 - Smart Filtering:
- Replace keyword scoring with LLM-based relevance analysis
- Use Haiku for cheap bulk filtering (200+ articles → 50 candidates)
- Use Sonnet for quality/challenge-factor assessment (50 → top 20)
- Incorporate Neo4j decision traces (what you've valued before)

Phase 2 - Context-Aware Curation:
- Capture daily thoughts, questions, learnings from conversations
- Store "what's on my mind today" as context in Neo4j
- Today's briefing answers questions you asked yesterday
- Tomorrow's briefing incorporates today's interests
- System learns your evolving priorities over time

Example flow:
1. You discuss gold/sanctions with agent → captured in Neo4j
2. Overnight curator sees "Robert asked about gold price drivers"
3. Morning briefing prioritizes gold/sanctions articles + analysis
4. You engage with specific pieces → feedback loop for next curation
"""

import feedparser
import requests
from datetime import datetime, timezone
from typing import List, Dict
import time

# RSS Feed Sources (refined 2026-02-09)
# Focus: Analysis over noise, trend-oriented macro content
FEEDS = {
    "ZeroHedge": "https://cms.zerohedge.com/fullrss2.xml",  # Contrarian edge, debasement/gold/dollar
    "The Big Picture": "https://ritholtz.com/feed/",  # Macro synthesis, behavioral takes
    "Fed On The Economy": "https://www.stlouisfed.org/rss/page%20resources/publications/blog-entries",  # Fed analysis layer
    "Treasury MSPD": "https://www.treasurydirect.gov/rss/mspd.xml",  # Monthly fiscal pulse (debt totals)
}

# Keywords for scoring (geopolitics + finance focus)
KEYWORDS = [
    "gold", "sanctions", "debt", "fiscal", "geopolitical", "trade war",
    "central bank", "inflation", "russia", "ukraine", "china", "treasury",
    "fed", "powell", "rates", "recession", "currency", "dollar", "euro",
    "oil", "energy", "conflict", "policy", "tariff", "deficit", "bonds"
]

def fetch_feed(name: str, url: str) -> List[Dict]:
    """Fetch and parse a single RSS feed"""
    print(f"📡 Fetching {name}...")
    try:
        # Fetch with requests first (handles SSL better)
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; RSS Reader Bot)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Parse with feedparser
        feed = feedparser.parse(response.content)
        entries = []
        
        for entry in feed.entries[:50]:  # Limit to 50 most recent per feed
            # Parse published date
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            
            entries.append({
                "source": name,
                "title": entry.get("title", "No title"),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": pub_date,
                "raw_entry": entry
            })
        
        print(f"   ✅ {len(entries)} entries from {name}")
        return entries
    
    except Exception as e:
        print(f"   ❌ Error fetching {name}: {e}")
        return []

def score_entry(entry: Dict) -> float:
    """
    Score an entry based on recency + keyword matching
    
    TODO (Phase 2 - Context-Aware):
    - Add parameter: user_context (from Neo4j: recent questions, interests, topics)
    - Use LLM to assess relevance to user_context
    - Boost score for articles that answer "what's on my mind today"
    - Example: If user asked about "gold manipulation" yesterday,
      boost articles about central bank gold reserves, manipulation allegations, etc.
    """
    score = 0.0
    
    # Recency score (decay over 7 days)
    if entry["published"]:
        age_hours = (datetime.now(timezone.utc) - entry["published"]).total_seconds() / 3600
        recency_score = max(0, 100 - (age_hours / 24) * 10)  # 10 points per day decay
        score += recency_score
    
    # Keyword matching score (MVP: static keywords)
    # TODO: Replace with LLM-based relevance check against user_context
    text = f"{entry['title']} {entry['summary']}".lower()
    keyword_matches = sum(1 for kw in KEYWORDS if kw in text)
    score += keyword_matches * 5  # 5 points per keyword
    
    # Source priority (optional: weight trusted sources higher)
    source_weights = {
        "The Big Picture": 1.2,
        "ZeroHedge": 1.1,
        "Fed On The Economy": 1.2,  # Fed analysis layer
        "Treasury MSPD": 1.3,  # Raw fiscal data = high value
    }
    weight = source_weights.get(entry["source"], 1.0)
    score *= weight
    
    return score

def curate(top_n: int = 20, diversity_weight: float = 0.3) -> List[Dict]:
    """
    Fetch all feeds, score, rank, return top N
    
    Args:
        top_n: Number of articles to return
        diversity_weight: How much to penalize source over-representation (0-1)
                         Higher = more variety, lower = pure quality ranking
    """
    print("\n🧠 Starting RSS curation...\n")
    
    all_entries = []
    
    # Fetch all feeds
    for name, url in FEEDS.items():
        entries = fetch_feed(name, url)
        all_entries.extend(entries)
        time.sleep(0.5)  # Be polite to servers
    
    print(f"\n📊 Total entries fetched: {len(all_entries)}")
    
    # Score all entries (base score)
    for entry in all_entries:
        entry["score"] = score_entry(entry)
    
    # Apply diversity-aware selection (combat confirmation bias)
    # Select items iteratively, recalculating penalties each round
    selected = []
    source_counts = {}
    candidates = all_entries.copy()
    
    while len(selected) < top_n and candidates:
        # Recalculate final scores based on current source distribution
        for entry in candidates:
            source = entry["source"]
            count = source_counts.get(source, 0)
            
            # Penalty grows exponentially with over-representation
            # 1st = 0, 2nd = -30, 3rd = -120, 4th = -270, etc.
            diversity_penalty = (count ** 2) * 30 * diversity_weight
            entry["final_score"] = entry["score"] - diversity_penalty
        
        # Pick the highest-scoring candidate
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        best = candidates.pop(0)
        
        selected.append(best)
        source = best["source"]
        source_counts[source] = source_counts.get(source, 0) + 1
    
    # Report source distribution
    print("\n📊 Source distribution in top 20:")
    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"   {source}: {count} articles")
    
    return selected

def format_output(entries: List[Dict]) -> str:
    """Format ranked entries for display"""
    output = "\n" + "="*80 + "\n"
    output += f"TOP {len(entries)} CURATED ARTICLES (Diversity-Weighted)\n"
    output += "="*80 + "\n\n"
    
    for i, entry in enumerate(entries, 1):
        pub_str = entry["published"].strftime("%Y-%m-%d %H:%M UTC") if entry["published"] else "Unknown date"
        
        # Show both base score and final score (with diversity adjustment)
        final = entry.get("final_score", entry["score"])
        output += f"#{i} [{entry['source']}] (Base: {entry['score']:.1f}, Final: {final:.1f})\n"
        output += f"   {entry['title']}\n"
        output += f"   {entry['link']}\n"
        output += f"   Published: {pub_str}\n"
        
        # Show first 150 chars of summary
        if entry['summary']:
            summary = entry['summary'][:150].replace("\n", " ")
            output += f"   {summary}...\n"
        
        output += "\n"
    
    return output

def format_telegram(entries: List[Dict]) -> str:
    """Format for Telegram delivery (with clickable links)"""
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
        
        # Telegram markdown format
        output += f"*#{i}* [{entry['source']}] _{time_str}_\n"
        output += f"{entry['title']}\n"
        output += f"🔗 {entry['link']}\n\n"
    
    return output

def format_html(entries: List[Dict]) -> str:
    """Format as HTML with clickable links and nice styling"""
    from datetime import datetime
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Morning Briefing - {datetime.now().strftime('%b %d, %Y')}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 900px;
            margin: 40px auto;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }}
        .header {{
            text-align: center;
            margin-bottom: 40px;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 2em;
        }}
        .header p {{
            margin: 10px 0 0 0;
            opacity: 0.9;
        }}
        .article {{
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .article:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }}
        .article-number {{
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 4px 10px;
            border-radius: 4px;
            font-weight: bold;
            margin-right: 10px;
        }}
        .article-source {{
            display: inline-block;
            background: #f0f0f0;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            margin-right: 10px;
        }}
        .article-time {{
            color: #888;
            font-size: 0.85em;
        }}
        .article-title {{
            font-size: 1.2em;
            font-weight: 600;
            margin: 10px 0;
            color: #333;
        }}
        .article-title a {{
            color: #333;
            text-decoration: none;
        }}
        .article-title a:hover {{
            color: #667eea;
        }}
        .article-link {{
            display: inline-block;
            margin-top: 10px;
            color: #667eea;
            text-decoration: none;
            font-size: 0.9em;
        }}
        .article-link:hover {{
            text-decoration: underline;
        }}
        .article-meta {{
            margin-top: 10px;
            font-size: 0.85em;
            color: #666;
        }}
        .score {{
            color: #888;
            font-size: 0.8em;
        }}
        .source-zerohedge {{ background: #ffe5e5; }}
        .source-bigpicture {{ background: #e5f3ff; }}
        .source-fed {{ background: #e5ffe5; }}
        .source-treasury {{ background: #fff5e5; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 Your Morning Briefing</h1>
        <p>{datetime.now().strftime('%A, %B %d, %Y')}</p>
        <p>📊 {len(entries)} curated articles from 4 sources</p>
    </div>
"""
    
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
        
        # Source class for styling
        source_class = ""
        if "ZeroHedge" in entry["source"]:
            source_class = "source-zerohedge"
        elif "Big Picture" in entry["source"]:
            source_class = "source-bigpicture"
        elif "Fed" in entry["source"]:
            source_class = "source-fed"
        elif "Treasury" in entry["source"]:
            source_class = "source-treasury"
        
        # Scores
        base_score = entry.get("score", 0)
        final_score = entry.get("final_score", base_score)
        
        html += f"""
    <div class="article {source_class}">
        <div>
            <span class="article-number">#{i}</span>
            <span class="article-source">{entry['source']}</span>
            <span class="article-time">{time_str}</span>
        </div>
        <div class="article-title">
            <a href="{entry['link']}" target="_blank">{entry['title']}</a>
        </div>
        <a href="{entry['link']}" class="article-link" target="_blank">🔗 Read article →</a>
        <div class="article-meta">
            <span class="score">Base score: {base_score:.1f} | Final score: {final_score:.1f}</span>
        </div>
    </div>
"""
    
    html += """
</body>
</html>
"""
    return html

def main():
    """Run the curator and display results"""
    import sys
    import subprocess
    
    # Check for flags
    send_telegram = "--telegram" in sys.argv
    generate_html = "--html" in sys.argv or "--open" in sys.argv
    auto_open = "--open" in sys.argv
    
    top_articles = curate(top_n=20)
    
    # Console output
    output = format_output(top_articles)
    print(output)
    
    # Save to file
    output_file = "curator_output.txt"
    with open(output_file, "w") as f:
        f.write(output)
    print(f"💾 Results saved to {output_file}")
    
    # HTML generation (default: always generate)
    html_content = format_html(top_articles)
    html_file = "curator_briefing.html"
    with open(html_file, "w") as f:
        f.write(html_content)
    print(f"🌐 HTML briefing saved to {html_file}")
    
    # Auto-open in browser
    if auto_open:
        try:
            subprocess.run(["open", html_file], check=True)
            print(f"✅ Opened {html_file} in browser")
        except Exception as e:
            print(f"⚠️  Could not auto-open: {e}")
    
    # Telegram delivery (if requested)
    if send_telegram:
        telegram_msg = format_telegram(top_articles)
        
        # Split into chunks if needed (Telegram limit: 4096 chars)
        max_len = 4000
        if len(telegram_msg) > max_len:
            chunks = []
            lines = telegram_msg.split('\n')
            current_chunk = ""
            
            for line in lines:
                if len(current_chunk) + len(line) + 1 > max_len:
                    chunks.append(current_chunk)
                    current_chunk = line + "\n"
                else:
                    current_chunk += line + "\n"
            
            if current_chunk:
                chunks.append(current_chunk)
            
            # Save chunks to files for OpenClaw to send
            for i, chunk in enumerate(chunks, 1):
                chunk_file = f"telegram_message_{i}.txt"
                with open(chunk_file, "w") as f:
                    f.write(chunk)
                print(f"📱 Telegram message part {i} saved to {chunk_file}")
        else:
            # Single message
            with open("telegram_message.txt", "w") as f:
                f.write(telegram_msg)
            print(f"📱 Telegram message saved to telegram_message.txt")

if __name__ == "__main__":
    main()
