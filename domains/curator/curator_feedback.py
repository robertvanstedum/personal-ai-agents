#!/usr/bin/env python3
"""
curator_feedback.py - Interactive feedback system for curator articles

Usage:
    python curator_feedback.py like 3
    python curator_feedback.py dislike 8
    python curator_feedback.py save 5
    python curator_feedback.py show         # Show recent feedback
    
Non-interactive (for automation):
    python curator_feedback.py like 3 --channel telegram --text "Good article"
    python curator_feedback.py save 5 --text "Read later"
"""

import json
import sys
import re
import os
import threading
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))  # sibling import below (signal_store)
from signal_store import get_session_id, log_feedback

# Load environment variables
load_dotenv()

# File paths
REPO_ROOT = Path(__file__).parent.parent.parent  # domains/curator -> domains -> repo root
sys.path.insert(0,str(REPO_ROOT))
from domains.curator import preferences_store as preference_store
from domains.curator.research.paths import configured as research_paths
_RESEARCH_DATA = research_paths().data
# Production sets CURATOR_OUTPUT_PATH into the mounted data/curator folder so
# the day's briefing text survives container replacement (#305).
CURATOR_OUTPUT = Path(os.environ.get("CURATOR_OUTPUT_PATH") or REPO_ROOT / "curator_output.txt")
# Canonical preferences location — shared with curator_server.py and curator_rss_v2.py.
# Defaults to data/curator/ in the repo; override with CURATOR_DATA_DIR env var on EC2.
from domains.curator.data_paths import data_directory
_DATA_DIR = data_directory(REPO_ROOT)
_DATA_DIR.mkdir(parents=True, exist_ok=True)
PREFERENCES_FILE = _DATA_DIR / "curator_preferences.json"
_FEEDBACK_LOCK = threading.RLock()

def get_anthropic_api_key():
    """Get Anthropic API key from keychain, env, or SSM"""
    try:
        import keyring
        api_key = keyring.get_password("anthropic", "api_key")
        if api_key:
            return api_key
    except Exception:
        pass

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if api_key:
        return api_key

    try:
        from core.get_secret import get_secret
        return get_secret("ANTHROPIC_API_KEY")
    except Exception:
        pass

    return None

def load_preferences():
    """Load existing preferences or create new structure"""
    prefs = preference_store.load(PREFERENCES_FILE)
    prefs.setdefault('learned_patterns', {'last_updated':None,'sample_size':0,'preferred_content_types':{},'preferred_themes':{},'preferred_sources':{},'avoid_patterns':{}})
    return prefs

def save_preferences(prefs):
    """CAS-protected atomic save; caller holds complete RMW transaction."""
    preference_store.write(PREFERENCES_FILE,prefs)
    print(f"💾 Preferences saved to {PREFERENCES_FILE}")


def parse_curator_output():
    """Read the current briefing, preserving the CLI missing-file contract."""
    if not CURATOR_OUTPUT.exists():
        print(f"❌ Error: {CURATOR_OUTPUT} not found. Run curator first.")
        sys.exit(1)
    from domains.curator.feedback_articles import parse_briefing
    with open(CURATOR_OUTPUT, 'r') as stream:
        return parse_briefing(stream.read())

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
    
    history_file = REPO_ROOT / "curator_history.json"
    cache_dir = REPO_ROOT / "curator_cache"
    
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

def generate_scan(hash_id, article_data, initial_interest, dive_focus=None):
    """
    Generate a Scan (article-level analysis) using Sonnet.
    Formerly called generate_deep_dive — renamed to Scan per v2 naming.

    Returns: (markdown_content, cost, output_path) or (None, None, None) on error
    """
    from datetime import datetime, timezone

    api_key = get_anthropic_api_key()
    if not api_key:
        print("❌ No Anthropic API key found")
        return None, None, None

    client = Anthropic(api_key=api_key)

    today = datetime.now().strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build prompt
    context_parts = [f"Your initial interest: \"{initial_interest}\""]
    if dive_focus:
        context_parts.append(f"Deep dive focus: \"{dive_focus}\"")

    context = "\n".join(context_parts)

    prompt = f"""Current date: {today_utc} (UTC). The article below and its metadata are authoritative and already live in a real, current news feed. Do not reject, doubt, or decline to analyze it merely because its publication date falls after your training cutoff — treat it as real and analyze it as given.

Provide a concise deep dive analysis of this article. This is a point of departure for further research, not a complete explanation.

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

    print("🧠 Calling Sonnet for scan analysis...")
    
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
        output_dir = REPO_ROOT / "interests" / "2026" / "scans"
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

## Scan Analysis

{content}

---

*Generated by Claude Sonnet • {input_tokens} input + {output_tokens} output tokens • ${cost:.4f}*
"""
        
        # Save markdown
        with open(output_path, 'w') as f:
            f.write(markdown)
        
        # Also save HTML version
        html = generate_scan_html(
            hash_id, article_data, initial_interest, dive_focus,
            content, cost, input_tokens, output_tokens
        )
        html_path = str(output_path).replace('.md', '.html')
        with open(html_path, 'w') as f:
            f.write(html)
        
        return markdown, cost, str(output_path)
        
    except Exception as e:
        print(f"❌ Error generating scan: {e}")
        return None, None, None

def regenerate_scans_index():
    """Publish the legacy Scan index using explicit catalog/render boundaries."""
    from datetime import datetime
    from domains.curator.research.scan_catalog import collect_catalog
    from domains.curator.research.scan_index_render import render_catalog
    scans_dir = REPO_ROOT / "interests" / "2026" / "scans"
    if not scans_dir.exists():
        return
    scans, dives = collect_catalog(scans_dir, _RESEARCH_DATA / "dives", now=datetime.now)
    html = render_catalog(scans, dives)
    with open(scans_dir / "index.html", 'w') as stream:
        stream.write(html)
    total = len(dives) + len(scans)
    print(f"📑 Scans & Dives index updated ({len(dives)} dives + {len(scans)} scans = {total} total)")

def generate_scan_html(hash_id, article_data, initial_interest, dive_focus, analysis_content, cost, input_tokens, output_tokens):
    from datetime import datetime
    from domains.curator.research.legacy_scan_render import render_scan
    return render_scan(hash_id, article_data, initial_interest, dive_focus,
                       analysis_content, cost, input_tokens, output_tokens,
                       today=datetime.now().strftime("%Y-%m-%d"))

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
    
    from domains.curator.feedback_prompts import metadata_prompt
    prompt = metadata_prompt(article, user_words, feedback_type)

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
        print(f"⚠️  Metadata extraction failed ({type(e).__name__}); using fallback")
        return {
            "content_type": ["unknown"],
            "appeal": [],
            "style": [],
            "themes": [],
            "depth": "unknown",
            "signals": []
        }

def _deterministic_metadata(article):
    from domains.curator.feedback_learning import deterministic_metadata
    return deterministic_metadata(article)


def record_feedback(
    rank,
    feedback_type,
    user_words,
    article,
    channel='cli',
    *,
    analyze_metadata=True,
):
    """Record feedback and update learned patterns
    
    Args:
        rank: Article rank in briefing
        feedback_type: liked, disliked, or saved
        user_words: User's explanation
        article: Article data dict
        channel: Source of feedback (cli, web_ui, telegram)
    """
    if feedback_type not in {"liked", "disliked", "saved"}:
        raise ValueError(f"Unsupported feedback type: {feedback_type}")

    required = ("title", "source", "category", "url")
    missing = [key for key in required if not article.get(key)]
    if missing:
        raise ValueError(f"Article is missing: {', '.join(missing)}")

    if analyze_metadata:
        print("🧠 Analyzing your feedback...")
        metadata = extract_metadata(article, user_words, feedback_type)
    else:
        metadata = _deterministic_metadata(article)

    from domains.curator.feedback_learning import metadata_for_feedback
    metadata = metadata_for_feedback(metadata, article)

    # Inject source and URL into metadata so update_learned_patterns can track them
    if article.get('source'):
        metadata['source'] = article['source']
    if article.get('url'):
        metadata['url'] = article['url']

    today = datetime.now().strftime("%Y-%m-%d")
    article_id = article.get('hash_id') or (
        f"fallback-{article['source'].lower().replace(' ', '-')}-{today}-{rank}"
    )
    feedback_entry = {
        'rank': rank,
        'article_id': article_id,
        'url': article['url'],
        'title': article['title'],
        'source': article['source'],
        'category': article['category'],
        'timestamp': datetime.now().isoformat(),
        'your_words': user_words,
        'extracted_signals': metadata
    }

    with _FEEDBACK_LOCK, preference_store.locked(PREFERENCES_FILE):
        prefs = load_preferences()
        day_feedback = prefs['feedback_history'].setdefault(today, {
            'liked': [],
            'disliked': [],
            'saved': []
        })

        # Browser retries should not double-count the same action.
        existing = next(
            (
                item for item in day_feedback[feedback_type]
                if item.get('article_id') == article_id
                or (item.get('url') and item.get('url') == article['url'])
            ),
            None,
        )
        if existing:
            return {"entry": existing, "duplicate": True}

        day_feedback[feedback_type].append(feedback_entry)
        update_learned_patterns(prefs, metadata, feedback_type)
        save_preferences(prefs)

        # Append the secondary event only after the canonical durable write.
        # A telemetry failure must not turn a successful user action into an
        # error response.
        try:
            log_feedback(
                article_id=feedback_entry['article_id'],
                action=feedback_type,
                channel=channel,
                title=article['title'],
                source=article['source'],
                category=article['category'],
                rank=rank,
                reason=user_words,
                metadata=metadata
            )
        except Exception as exc:
            print(f"⚠️  Signal Store append skipped: {exc}")
    
    # Show what was detected
    print(f"\n✅ Feedback recorded for article #{rank}")
    print(f"📊 Detected patterns:")
    print(f"   Content: {', '.join(metadata.get('content_type', []))}")
    print(f"   Appeal: {', '.join(metadata.get('appeal', []))}")
    print(f"   Style: {', '.join(metadata.get('style', []))}")
    if metadata.get('themes'):
        print(f"   Themes: {', '.join(metadata['themes'])}")
    # Curator's activity record for CoS (CoS 1.2 spec section 4.3): off unless configured, never fails this save.
    from domains.curator import activity
    activity.after_save()
    return {"entry": feedback_entry, "duplicate": False}

def update_learned_patterns(prefs, metadata, feedback_type):
    """Apply learning inside the caller's existing preference transaction."""
    from domains.curator.feedback_learning import update_patterns
    def classify_url(url):
        from curator_utils import extract_domain, classify_source_type
        return extract_domain(url), classify_source_type(url)
    return update_patterns(prefs, metadata, feedback_type,
                           now=datetime.now, classify_url=classify_url)

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
        print("Usage: python curator_feedback.py <like|dislike|save|show|bookmark> [rank|reference] [--channel <cli|web_ui|telegram>] [--text \"feedback text\"]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    # Parse optional --channel flag
    channel = 'cli'  # Default
    if '--channel' in sys.argv:
        channel_idx = sys.argv.index('--channel')
        if channel_idx + 1 < len(sys.argv):
            channel = sys.argv[channel_idx + 1]
    
    # Parse optional --text flag (for non-interactive feedback)
    feedback_text = None
    if '--text' in sys.argv:
        text_idx = sys.argv.index('--text')
        if text_idx + 1 < len(sys.argv):
            feedback_text = sys.argv[text_idx + 1]
    
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
            # Check if stdin is piped (non-interactive mode)
            if not sys.stdin.isatty():
                # Read from stdin (web UI provides interest + focus)
                lines = sys.stdin.read().strip().split('\n')
                like_comment = lines[0] if len(lines) > 0 else ""
                dive_focus = lines[1] if len(lines) > 1 else ""
                
                if not like_comment:
                    print("❌ No interest provided. Cancelled.")
                    sys.exit(0)
                    
                print(f"📌 Interest: {like_comment}")
                if dive_focus:
                    print(f"📌 Focus: {dive_focus}")
                print()
            else:
                # Interactive mode
                print("📌 No Like found for this article.")
                like_comment = input("What interests you about it? ").strip()
                if not like_comment:
                    print("❌ No context provided. Cancelled.")
                    sys.exit(0)
                print()
                
                # Prompt for deep dive focus (optional)
                print("Add focus areas for scan? (Enter to skip)")
                dive_focus = input("> ").strip()
                print()
        else:
            # Had existing like, check for focus from stdin or prompt
            if not sys.stdin.isatty():
                lines = sys.stdin.read().strip().split('\n')
                dive_focus = lines[1] if len(lines) > 1 else ""
            else:
                print("Add focus areas for scan? (Enter to skip)")
                dive_focus = input("> ").strip()
                print()
        
        # Generate deep dive
        markdown, cost, output_path = generate_scan(hash_id, article_data, like_comment, dive_focus)
        
        if output_path:
            print("✅ Scan generated!")
            print(f"   📄 Saved to: {output_path}")
            print(f"   💰 Cost: ${cost:.4f}")
            
            # Update history with bookmark flag
            history_file = REPO_ROOT / "curator_history.json"
            with open(history_file, 'r') as f:
                history = json.load(f)
            
            if hash_id in history:
                history[hash_id]['bookmarked'] = True
                history[hash_id]['deep_dive_path'] = output_path
                history[hash_id]['bookmark_date'] = datetime.now().strftime("%Y-%m-%d")
                
                with open(history_file, 'w') as f:
                    json.dump(history, f, indent=2)
            
            # Regenerate scans index
            regenerate_scans_index()
        else:
            print("❌ Scan generation failed")
        
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
    # A Telegram rank is meaningful only for the article shown in that message.
    # Compare exact URLs without redirects/normalization and use this same
    # parsed object for persistence, so a new briefing cannot retarget feedback.
    if '--expected-url' in sys.argv:
        expected_idx = sys.argv.index('--expected-url') + 1
        expected = sys.argv[expected_idx] if expected_idx < len(sys.argv) else None
        if not expected or article.get('url') != expected:
            print("Article changed or identity is unavailable; request a fresh briefing")
            sys.exit(2)

    # Show article details
    print(f"\n📰 Article #{rank}")
    print(f"   Title: {article['title']}")
    print(f"   Source: {article['source']}")
    print(f"   Category: {article['category']}")
    print()
    
    # Get user feedback
    if command in ['like', 'dislike']:
        if feedback_text:
            user_words = feedback_text
        elif not sys.stdin.isatty():
            # Non-interactive mode without --text: use default message
            user_words = f"{command.capitalize()}d via {channel}"
        else:
            # Interactive mode: prompt user
            prompt = "What did you like about it? " if command == 'like' else "What didn't work for you? "
            user_words = input(prompt).strip()
            
            if not user_words:
                print("❌ No feedback provided. Cancelled.")
                sys.exit(0)
        
        record_feedback(rank, f"{command}d", user_words, article, channel=channel)
    
    elif command == 'save':
        if feedback_text:
            reason = feedback_text
        elif not sys.stdin.isatty():
            # Non-interactive mode without --text: use default message
            reason = f"Saved via {channel}"
        else:
            # Interactive mode: prompt user
            reason = input("Why save this? (optional) ").strip() or "saved for later"
        record_feedback(rank, 'saved', reason, article, channel=channel)
    
    else:
        print(f"❌ Unknown command: {command}")
        print("Valid commands: like, dislike, save, show, bookmark")
        sys.exit(1)

if __name__ == '__main__':
    main()
