#!/usr/bin/env python3
"""
curator_feedback.py - Interactive feedback system for curator articles

Usage:
    python curator_feedback.py like 3
    python curator_feedback.py dislike 8
    python curator_feedback.py save 5
    python curator_feedback.py show         # Show recent feedback
"""

import json
import sys
import re
import os
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# File paths
CURATOR_OUTPUT = Path(__file__).parent / "curator_output.txt"
PREFERENCES_FILE = Path(__file__).parent / "curator_preferences.json"

def get_anthropic_api_key():
    """Get Anthropic API key from keychain, env, or .env file"""
    # Try keychain first (most secure)
    try:
        import keyring
        api_key = keyring.get_password("anthropic", "api_key")
        if api_key:
            return api_key
    except Exception:
        pass
    
    # Try environment variable
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if api_key:
        return api_key
    
    return None

def load_preferences():
    """Load existing preferences or create new structure"""
    if PREFERENCES_FILE.exists():
        with open(PREFERENCES_FILE, 'r') as f:
            return json.load(f)
    return {
        "version": "1.0",
        "feedback_history": {},
        "learned_patterns": {
            "last_updated": None,
            "sample_size": 0,
            "preferred_content_types": {},
            "preferred_themes": {},
            "preferred_sources": {},
            "avoid_patterns": {}
        }
    }

def save_preferences(prefs):
    """Save preferences to JSON"""
    with open(PREFERENCES_FILE, 'w') as f:
        json.dump(prefs, f, indent=2)
    print(f"💾 Preferences saved to {PREFERENCES_FILE}")

def parse_curator_output():
    """Parse curator_output.txt to extract article details"""
    if not CURATOR_OUTPUT.exists():
        print(f"❌ Error: {CURATOR_OUTPUT} not found. Run curator first.")
        sys.exit(1)
    
    articles = {}
    current_article = None
    
    with open(CURATOR_OUTPUT, 'r') as f:
        content = f.read()
    
    # Find the articles section
    lines = content.split('\n')
    in_articles = False
    
    for line in lines:
        # Detect start of articles list
        if 'TOP 20 CURATED ARTICLES' in line or 'TOP 15 CURATED ARTICLES' in line:
            in_articles = True
            continue
        
        # Parse article entries
        if in_articles and line.startswith('#'):
            # Extract rank and source
            match = re.match(r'#(\d+)\s+\[([^\]]+)\].*?🏷️\s+(\w+)', line)
            if match:
                rank = int(match.group(1))
                source = match.group(2)
                category = match.group(3)
                current_article = rank
                articles[rank] = {
                    'rank': rank,
                    'source': source,
                    'category': category,
                    'title': None,
                    'url': None,
                    'scores': None
                }
        
        # Extract title (next non-empty line after rank)
        elif current_article and articles[current_article]['title'] is None:
            title = line.strip()
            if title and not title.startswith('http') and not title.startswith('Published'):
                articles[current_article]['title'] = title
        
        # Extract URL
        elif current_article and line.strip().startswith('http'):
            articles[current_article]['url'] = line.strip()
        
        # Extract scores
        elif current_article and 'Scores:' in line:
            articles[current_article]['scores'] = line.strip()
            current_article = None  # Done with this article
    
    return articles

def resolve_article_reference(ref):
    """
    Resolve article reference to hash_id
    
    Accepts:
    - hash ID: "90610"
    - date-rank: "2026-02-19-1"
    - yesterday-N: "yesterday-1"
    
    Returns: (hash_id, article_data) or (None, None) if not found
    """
    from datetime import datetime, timedelta
    
    history_file = Path(__file__).parent / "curator_history.json"
    cache_dir = Path(__file__).parent / "curator_cache"
    
    if not history_file.exists():
        print("❌ History file not found. Run curator first to build history.")
        return None, None
    
    with open(history_file, 'r') as f:
        history = json.load(f)
    
    hash_id = None
    
    # Case 1: Direct hash ID
    if ref in history:
        hash_id = ref
    
    # Case 2: yesterday-N format
    elif ref.startswith('yesterday-'):
        try:
            rank = int(ref.split('-')[1])
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Find article with matching date and rank
            for hid, data in history.items():
                for appearance in data.get('appearances', []):
                    if appearance['date'] == yesterday and appearance['rank'] == rank:
                        hash_id = hid
                        break
                if hash_id:
                    break
        except (ValueError, IndexError):
            pass
    
    # Case 3: date-rank format (YYYY-MM-DD-N)
    elif '-' in ref:
        parts = ref.rsplit('-', 1)
        if len(parts) == 2:
            date_str, rank_str = parts
            try:
                rank = int(rank_str)
                # Find article with matching date and rank
                for hid, data in history.items():
                    for appearance in data.get('appearances', []):
                        if appearance['date'] == date_str and appearance['rank'] == rank:
                            hash_id = hid
                            break
                    if hash_id:
                        break
            except ValueError:
                pass
    
    if not hash_id:
        print(f"❌ Could not resolve reference: {ref}")
        print("\nValid formats:")
        print("  - Hash ID: 90610")
        print("  - Date-rank: 2026-02-19-1")
        print("  - Yesterday: yesterday-1")
        return None, None
    
    # Load article from cache
    cache_file = cache_dir / f"{hash_id}.json"
    if not cache_file.exists():
        print(f"❌ Cache file not found for {hash_id}")
        return None, None
    
    with open(cache_file, 'r') as f:
        article_data = json.load(f)
    
    return hash_id, article_data

def generate_deep_dive(hash_id, article_data, initial_interest, dive_focus=None):
    """
    Generate deep dive analysis using Sonnet
    
    Returns: (markdown_content, cost, output_path) or (None, None, None) on error
    """
    from datetime import datetime
    
    api_key = get_anthropic_api_key()
    if not api_key:
        print("❌ No Anthropic API key found")
        return None, None, None
    
    client = Anthropic(api_key=api_key)
    
    # Build prompt
    context_parts = [f"Your initial interest: \"{initial_interest}\""]
    if dive_focus:
        context_parts.append(f"Deep dive focus: \"{dive_focus}\"")
    
    context = "\n".join(context_parts)
    
    prompt = f"""Provide a concise deep dive analysis of this article. This is a point of departure for further research, not a complete explanation.

ARTICLE:
Title: {article_data['title']}
Source: {article_data['source']}
URL: {article_data['url']}
Published: {article_data.get('published', 'Unknown')}

Summary:
{article_data.get('summary', 'No summary available')[:1000]}

USER CONTEXT:
{context}

Provide CONCISE analysis covering:

1. **Why This Matters** - Key implications and connections (2-3 sentences max)
2. **Core Argument** - Central claim and evidence (brief)
3. **Contrarian Take** - What critics would say (1-2 points)
4. **Connections** - Related trends or patterns (brief)
5. **Key Data** - Specific numbers or quotes worth remembering
6. **Next Questions** - What to investigate further (3-4 questions)

7. **Sources & Further Reading** - THE MOST IMPORTANT SECTION. List 5-8 sources for deeper research. Format as proper citations:
   - Author/Organization. "Title" or Report Name. Publisher/Journal, Year. URL if available.
   - Focus on PRIMARY sources: research papers, official reports, data sources
   - Include URLs, DOIs, or specific search terms
   - Skip explanations - just provide clean citations the user can look up

Keep sections 1-6 concise. Make section 7 (Sources) the most detailed and useful.
Write directly - no preamble."""

    print("🧠 Calling Sonnet for deep dive analysis...")
    
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = response.content[0].text
        
        # Calculate cost
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens * 0.000003) + (output_tokens * 0.000015)
        
        # Create output path
        slug = re.sub(r'[^a-z0-9]+', '-', article_data['title'].lower())[:50].strip('-')
        today = datetime.now().strftime("%Y-%m-%d")
        output_dir = Path(__file__).parent / "interests" / "2026" / "deep-dives"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = output_dir / f"{hash_id}-{slug}.md"
        
        # Build markdown document
        markdown = f"""# {article_data['title']}

**Source:** {article_data['source']}  
**URL:** {article_data['url']}  
**Date:** {today}  
**Hash ID:** {hash_id}

## Your Interest

{initial_interest}
"""
        
        if dive_focus:
            markdown += f"\n**Focus:** {dive_focus}\n"
        
        markdown += f"""
---

## Deep Dive Analysis

{content}

---

*Generated by Claude Sonnet • {input_tokens} input + {output_tokens} output tokens • ${cost:.4f}*
"""
        
        # Save markdown
        with open(output_path, 'w') as f:
            f.write(markdown)
        
        # Also save HTML version
        html = generate_deep_dive_html(
            hash_id, article_data, initial_interest, dive_focus,
            content, cost, input_tokens, output_tokens
        )
        html_path = output_path.replace('.md', '.html')
        with open(html_path, 'w') as f:
            f.write(html)
        
        return markdown, cost, str(output_path)
        
    except Exception as e:
        print(f"❌ Error generating deep dive: {e}")
        return None, None, None

def generate_deep_dive_html(hash_id, article_data, initial_interest, dive_focus, analysis_content, cost, input_tokens, output_tokens):
    """Generate HTML version of deep dive analysis"""
    from datetime import datetime
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{article_data['title']} - Deep Dive</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
            font-size: 15px;
            max-width: 1000px;
            margin: 40px auto;
            padding: 0 20px;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }}
        .container {{
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            font-size: 1.8em;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-bottom: 20px;
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
            color: #3498db;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .nav-bar {{
            display: flex;
            gap: 8px;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }}
        .nav-link {{
            display: inline-block;
            padding: 5px 12px;
            background: #667eea;
            color: white;
            border-radius: 3px;
            font-size: 0.85em;
            text-decoration: none;
            font-weight: 500;
        }}
        .nav-link:hover {{
            background: #5568d3;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav-bar">
            <a href="../../curator_briefing.html" class="nav-link">📰 Today's Briefing</a>
            <a href="../../curator_index.html" class="nav-link">📚 Archive</a>
            <a href="index.html" class="nav-link">🔍 Deep Dives</a>
        </div>
        
        <h1>{article_data['title']}</h1>
        
        <div class="meta">
            <strong>Source:</strong> {article_data['source']}<br>
            <strong>URL:</strong> <a href="{article_data['url']}" target="_blank">{article_data['url']}</a><br>
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
            <h2>Deep Dive Analysis</h2>
"""
    
    # Convert markdown analysis to basic HTML
    # Simple conversion: h2, h3, bold, lists
    
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
</body>
</html>
"""
    
    return html

def extract_metadata(article, user_words, feedback_type):
    """Use Claude to extract metadata from user feedback"""
    api_key = get_anthropic_api_key()
    if not api_key:
        print("⚠️  No Anthropic API key found - skipping metadata extraction")
        return {
            "content_type": ["manual_entry"],
            "appeal": [],
            "style": [],
            "themes": [],
            "depth": "unknown",
            "signals": []
        }
    
    client = Anthropic(api_key=api_key)
    
    prompt = f"""Analyze this user feedback about an article and extract structured metadata.

Article:
- Title: {article['title']}
- Source: {article['source']}
- Category: {article['category']}

User feedback ({feedback_type}):
"{user_words}"

Extract and return ONLY a JSON object with these fields:
{{
  "content_type": ["list of content types: argumentative, analytical, descriptive, statistical, narrative, investigative"],
  "appeal": ["what appealed or didn't: evidence_based, institutional_tension, contrarian, depth, clarity, originality"],
  "style": ["writing style: challenge_not_summary, data_driven, opinion_based, technical, accessible"],
  "themes": ["themes: fiscal_policy, monetary_policy, geopolitics, institutional_debates, market_analysis"],
  "depth": "one of: surface_summary, moderate_analysis, deep_dive, original_research",
  "signals": ["positive signals if liked, negative if disliked"]
}}

Return ONLY valid JSON, no explanation."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Parse JSON from response
    try:
        response_text = response.content[0].text.strip()
        # Handle markdown code blocks if present
        if response_text.startswith('```'):
            # Extract JSON from code block
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
            response_text = response_text.strip()
        
        metadata = json.loads(response_text)
        return metadata
    except (json.JSONDecodeError, IndexError, AttributeError) as e:
        # Fallback if parsing fails
        print(f"⚠️  Metadata extraction failed: {e}")
        print(f"   Response was: {response.content[0].text[:200] if response.content else 'empty'}")
        return {
            "content_type": ["unknown"],
            "appeal": [],
            "style": [],
            "themes": [],
            "depth": "unknown",
            "signals": []
        }

def record_feedback(rank, feedback_type, user_words, article):
    """Record feedback and update learned patterns"""
    prefs = load_preferences()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Ensure today's entry exists
    if today not in prefs['feedback_history']:
        prefs['feedback_history'][today] = {
            'liked': [],
            'disliked': [],
            'saved': []
        }
    
    # Extract metadata using Claude
    print("🧠 Analyzing your feedback...")
    metadata = extract_metadata(article, user_words, feedback_type)
    
    # Create feedback entry
    feedback_entry = {
        'rank': rank,
        'article_id': f"{article['source'].lower().replace(' ', '-')}-{today}-{rank}",
        'url': article['url'],
        'title': article['title'],
        'source': article['source'],
        'category': article['category'],
        'timestamp': datetime.now().isoformat(),
        'your_words': user_words,
        'extracted_signals': metadata
    }
    
    # Add to appropriate list
    prefs['feedback_history'][today][feedback_type].append(feedback_entry)
    
    # Update learned patterns
    update_learned_patterns(prefs, metadata, feedback_type)
    
    # Save
    save_preferences(prefs)
    
    # Show what was detected
    print(f"\n✅ Feedback recorded for article #{rank}")
    print(f"📊 Detected patterns:")
    print(f"   Content: {', '.join(metadata.get('content_type', []))}")
    print(f"   Appeal: {', '.join(metadata.get('appeal', []))}")
    print(f"   Style: {', '.join(metadata.get('style', []))}")
    if metadata.get('themes'):
        print(f"   Themes: {', '.join(metadata['themes'])}")

def update_learned_patterns(prefs, metadata, feedback_type):
    """Update aggregate patterns based on new feedback"""
    patterns = prefs['learned_patterns']
    weight = 1 if feedback_type == 'liked' else -1
    
    # Update content types
    for ct in metadata.get('content_type', []):
        patterns['preferred_content_types'][ct] = patterns['preferred_content_types'].get(ct, 0) + weight
    
    # Update themes
    for theme in metadata.get('themes', []):
        patterns['preferred_themes'][theme] = patterns['preferred_themes'].get(theme, 0) + weight
    
    # Update avoid patterns if disliked
    if feedback_type == 'disliked':
        for signal in metadata.get('signals', []):
            patterns['avoid_patterns'][signal] = patterns['avoid_patterns'].get(signal, 0) + 1
    
    # Update metadata
    patterns['last_updated'] = datetime.now().isoformat()
    patterns['sample_size'] = patterns.get('sample_size', 0) + 1

def show_recent_feedback():
    """Show recent feedback summary"""
    prefs = load_preferences()
    
    if not prefs['feedback_history']:
        print("📭 No feedback recorded yet.")
        return
    
    print("\n📊 Recent Feedback Summary\n")
    
    # Show last 3 days
    dates = sorted(prefs['feedback_history'].keys(), reverse=True)[:3]
    
    for date in dates:
        day = prefs['feedback_history'][date]
        total = len(day['liked']) + len(day['disliked']) + len(day['saved'])
        
        if total == 0:
            continue
        
        print(f"📅 {date}")
        
        if day['liked']:
            print(f"   👍 Liked ({len(day['liked'])}):")
            for item in day['liked'][:3]:
                print(f"      #{item['rank']}: {item['title'][:60]}...")
        
        if day['disliked']:
            print(f"   👎 Disliked ({len(day['disliked'])}):")
            for item in day['disliked'][:3]:
                print(f"      #{item['rank']}: {item['title'][:60]}...")
        
        if day['saved']:
            print(f"   🔖 Saved ({len(day['saved'])}):")
            for item in day['saved'][:3]:
                print(f"      #{item['rank']}: {item['title'][:60]}...")
        
        print()
    
    # Show learned patterns
    patterns = prefs['learned_patterns']
    if patterns['sample_size'] > 0:
        print(f"🧠 Learned Patterns (n={patterns['sample_size']})\n")
        
        if patterns['preferred_content_types']:
            print("   Preferred content:")
            sorted_types = sorted(patterns['preferred_content_types'].items(), key=lambda x: x[1], reverse=True)[:5]
            for ct, score in sorted_types:
                print(f"      {ct}: {score:+d}")
        
        if patterns['preferred_themes']:
            print("\n   Preferred themes:")
            sorted_themes = sorted(patterns['preferred_themes'].items(), key=lambda x: x[1], reverse=True)[:5]
            for theme, score in sorted_themes:
                print(f"      {theme}: {score:+d}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python curator_feedback.py <like|dislike|save|show|bookmark> [rank|reference]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == 'show':
        show_recent_feedback()
        return
    
    # Bookmark command uses reference (hash ID, date-rank, or yesterday-N)
    if command == 'bookmark':
        if len(sys.argv) < 3:
            print("Error: bookmark requires article reference")
            print("Examples:")
            print("  python curator_feedback.py bookmark 90610")
            print("  python curator_feedback.py bookmark 2026-02-19-1")
            print("  python curator_feedback.py bookmark yesterday-1")
            sys.exit(1)
        
        ref = sys.argv[2]
        hash_id, article_data = resolve_article_reference(ref)
        
        if not hash_id:
            sys.exit(1)
        
        # Show article details
        print(f"\n📰 Article [{hash_id}]")
        print(f"   Title: {article_data['title']}")
        print(f"   Source: {article_data['source']}")
        print(f"   URL: {article_data['url']}")
        print()
        
        # Check for existing Like comments
        prefs = load_preferences()
        like_comment = None
        
        for feedback_id, feedback in prefs['feedback_history'].items():
            if feedback.get('article', {}).get('url') == article_data['url'] and feedback['feedback_type'] == 'liked':
                like_comment = feedback['user_words']
                print(f"📌 Found your Like comment: \"{like_comment}\"")
                print()
                break
        
        if not like_comment:
            print("📌 No Like found for this article.")
            like_comment = input("What interests you about it? ").strip()
            if not like_comment:
                print("❌ No context provided. Cancelled.")
                sys.exit(0)
            print()
        
        # Prompt for deep dive focus (optional)
        print("Add focus areas for deep dive? (Enter to skip)")
        dive_focus = input("> ").strip()
        print()
        
        # Generate deep dive
        markdown, cost, output_path = generate_deep_dive(hash_id, article_data, like_comment, dive_focus)
        
        if output_path:
            print("✅ Deep dive generated!")
            print(f"   📄 Saved to: {output_path}")
            print(f"   💰 Cost: ${cost:.4f}")
            
            # Update history with bookmark flag
            history_file = Path(__file__).parent / "curator_history.json"
            with open(history_file, 'r') as f:
                history = json.load(f)
            
            if hash_id in history:
                history[hash_id]['bookmarked'] = True
                history[hash_id]['deep_dive_path'] = output_path
                history[hash_id]['bookmark_date'] = datetime.now().strftime("%Y-%m-%d")
                
                with open(history_file, 'w') as f:
                    json.dump(history, f, indent=2)
        else:
            print("❌ Deep dive generation failed")
        
        return
    
    # Standard commands (like, dislike, save) use rank
    if len(sys.argv) < 3:
        print(f"Error: {command} requires article rank number")
        print("Example: python curator_feedback.py like 3")
        sys.exit(1)
    
    try:
        rank = int(sys.argv[2])
    except ValueError:
        print(f"Error: '{sys.argv[2]}' is not a valid rank number")
        sys.exit(1)
    
    # Parse articles
    articles = parse_curator_output()
    
    if rank not in articles:
        print(f"❌ Article #{rank} not found in curator output")
        print(f"Available ranks: {sorted(articles.keys())}")
        sys.exit(1)
    
    article = articles[rank]
    
    # Show article details
    print(f"\n📰 Article #{rank}")
    print(f"   Title: {article['title']}")
    print(f"   Source: {article['source']}")
    print(f"   Category: {article['category']}")
    print()
    
    # Get user feedback
    if command in ['like', 'dislike']:
        prompt = "What did you like about it? " if command == 'like' else "What didn't work for you? "
        user_words = input(prompt).strip()
        
        if not user_words:
            print("❌ No feedback provided. Cancelled.")
            sys.exit(0)
        
        record_feedback(rank, f"{command}d", user_words, article)
    
    elif command == 'save':
        reason = input("Why save this? (optional) ").strip() or "saved for later"
        record_feedback(rank, 'saved', reason, article)
    
    else:
        print(f"❌ Unknown command: {command}")
        print("Valid commands: like, dislike, save, show, bookmark")
        sys.exit(1)

if __name__ == '__main__':
    main()
