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
        print("Usage: python curator_feedback.py <like|dislike|save|show> [rank]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == 'show':
        show_recent_feedback()
        return
    
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
        print("Valid commands: like, dislike, save, show")
        sys.exit(1)

if __name__ == '__main__':
    main()
