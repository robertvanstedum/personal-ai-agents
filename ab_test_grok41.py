#!/usr/bin/env python3
"""
A/B Test: grok-3-mini vs grok-4-1-fast-reasoning

Scores the same recent article batch with both models.
Generates comparison report showing:
- Side-by-side rankings
- Scoring rationale differences
- Significant rank flips (where model preference deviates)
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from openai import OpenAI

def get_xai_client():
    """Get OpenAI-compatible client for xAI API"""
    api_key = os.environ.get('XAI_API_KEY')
    if not api_key:
        # Try keychain
        import subprocess
        try:
            api_key = subprocess.check_output(
                ['security', 'find-generic-password', '-s', 'xai', '-w'],
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except:
            pass
    
    if not api_key:
        raise ValueError("XAI_API_KEY not found in env or keychain")
    
    return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

def load_recent_articles(limit=20):
    """Load articles from today's or most recent curator run"""
    history_file = Path(__file__).parent / 'curator_history.json'
    
    if not history_file.exists():
        print("❌ curator_history.json not found")
        return []
    
    with open(history_file) as f:
        history = json.load(f)
    
    # Get most recent run's articles
    articles = []
    for hash_id, entry in list(history.items())[:limit]:
        articles.append({
            'hash_id': hash_id,
            'title': entry.get('title', ''),
            'source': entry.get('source', ''),
            'url': entry.get('url', '')
        })
    
    return articles

def load_user_profile():
    """Load user profile from curator preferences"""
    prefs_file = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
    
    if not prefs_file.exists():
        return ""
    
    with open(prefs_file) as f:
        prefs = json.load(f)
    
    lp = prefs.get('learned_patterns', {})
    sections = []
    
    # Abbreviated profile for A/B test
    sources = lp.get('preferred_sources', {})
    if sources:
        pos = sorted([(k, v) for k, v in sources.items() if v > 0], key=lambda x: -x[1])[:3]
        if pos:
            sections.append(f"Preferred sources: {', '.join(k for k, _ in pos)}")
    
    domains = lp.get('content_domains', {})
    if domains:
        pos = sorted([(k, v.get('like', 0) * 2 + v.get('save', 0) - v.get('dislike', 0) * 2) 
                     for k, v in domains.items() if v.get('like', 0) * 2 + v.get('save', 0) - v.get('dislike', 0) * 2 > 0], 
                    key=lambda x: -x[1])[:2]
        if pos:
            sections.append(f"Content domains: {', '.join(k for k, _ in pos)}")
    
    topics = lp.get('content_topics', {})
    if topics:
        pos = sorted([(k, t.get('save', 0)) for k, t in topics.items() if t.get('save', 0) > 0], 
                    key=lambda x: -x[1])[:5]
        if pos:
            sections.append(f"Interests: {', '.join(k for k, _ in pos)}")
    
    if sections:
        return "PERSONALIZATION (adjust base score ±1 for strong matches):\n- " + "\n- ".join(sections)
    
    return ""

def score_articles_with_model(articles, model_name, user_profile=""):
    """Score articles with specified model (grok-3-mini or grok-4-1-fast-reasoning)"""
    client = get_xai_client()
    
    # Updated prompt with ground truth override rules + commodity/supply-chain domain clarification
    override_rules = """You are a sharp, personalized intelligence analyst scoring articles for relevance to the user's specific interests in finance, geopolitics, risk signals, uncertainty, contrarian views, and high-signal sources.

GROUND TRUTH RULES — FOLLOW THESE STRICTLY AND PRIORITIZE THEM OVER YOUR OWN GENERAL JUDGMENT:

- The USER PROFILE below is absolute ground truth. Source preferences, boosts, and avoids in the profile OVERRIDE any independent content evaluation you might make.
- If a source or type of content (e.g., daily link roundups, aggregates, specific authors/outlets) appears in the user's boost list or has positive feedback history, score it UP significantly — regardless of whether the article seems generic, broad, tangential, or off-topic on its own merits.
- The user has deliberately chosen and reinforced these sources over time; treat them as trusted and high-value signals, not as objective noise.
- Do NOT dismiss or downrank based on "generic form," "aggregate style," or surface-level categorization if the source matches profile boosts.
- Use your stronger reasoning to find nuanced alignments, hidden implications, contrarian angles, or overlooked risks/opportunities WITHIN the profile's priorities — not to second-guess the profile itself.
- For borderline cases, err toward higher relevance if profile signals support it.

DOMAIN CLARIFICATIONS (for this user):

- COMMODITY & SUPPLY-CHAIN DISRUPTIONS = Geopolitical risk signal. Agricultural, energy, or mineral supply disruptions carry geopolitical implications (trade leverage, sanctions, ESG-driven price volatility, emerging market dependencies). Score these as relevant geopol/finance risk even if framed as environmental or sectoral.
  Example: Brazil soy/deforestation → commodity supply disruption → price risk → macroeconomic impact. Score relevance, don't filter on surface category.

SCORE GUIDANCE:
9-10: Critical developments, major policy shifts, must-read
7-8: Important trends, significant analysis  
5-6: Relevant but not urgent
3-4: Tangential interest
0-2: Skip (off-topic, noise)

USER PROFILE (ground truth):
{user_profile}

OUTPUT FORMAT (one line per article):
<rank>|<score>|<reason>

ARTICLES:
"""
    
    prompt = override_rules
    
    for i, article in enumerate(articles, 1):
        prompt += f"\n{i}. [{article['source']}] {article['title']}"
    
    prompt += "\n\nOUTPUT (one line per article, format: rank|score|brief reason):\n"
    
    print(f"\n📡 Calling {model_name}...")
    
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0
    )
    
    content = response.choices[0].message.content
    usage = response.usage
    cost = usage.prompt_tokens * 5 / 1_000_000 + usage.completion_tokens * 15 / 1_000_000
    
    print(f"   Tokens: {usage.prompt_tokens} in, {usage.completion_tokens} out")
    print(f"   Cost: ${cost:.4f}")
    
    # Parse results
    results = []
    lines = content.strip().split('\n')
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or '|' not in line:
            continue
        
        try:
            parts = line.split('|')
            score = float(parts[1].strip())
            reason = parts[2].strip() if len(parts) > 2 else ""
            
            results.append({
                'rank': i + 1,
                'score': score,
                'reason': reason
            })
        except (ValueError, IndexError):
            # Skip malformed lines
            continue
    
    return results

def main():
    """Run A/B test"""
    print("=" * 70)
    print("🧪 A/B TEST: grok-3-mini vs grok-4-1-fast-reasoning")
    print("=" * 70)
    
    # Load articles
    print("\n📚 Loading recent articles...")
    articles = load_recent_articles(limit=15)
    
    if not articles:
        print("❌ No articles found. Run: python curator_rss_v2.py --model=xai")
        sys.exit(1)
    
    print(f"✅ Loaded {len(articles)} articles")
    
    # Load profile
    print("\n🧠 Loading user profile...")
    profile = load_user_profile()
    if profile:
        print("✅ Profile loaded")
        for line in profile.split('\n'):
            if line:
                print(f"   {line[:60]}...")
    else:
        print("⚠️  No profile yet (new user)")
        profile = ""
    
    # Score with both models
    print("\n" + "=" * 70)
    print("SCORING WITH BOTH MODELS")
    print("=" * 70)
    
    grok3_results = score_articles_with_model(articles, "grok-3-mini", profile)
    grok41_results = score_articles_with_model(articles, "grok-4-1-fast-reasoning", profile)
    
    # Compare
    print("\n" + "=" * 70)
    print("COMPARISON REPORT")
    print("=" * 70)
    
    comparison = []
    for i, article in enumerate(articles):
        if i < len(grok3_results) and i < len(grok41_results):
            g3 = grok3_results[i]
            g41 = grok41_results[i]
            
            delta = g41['score'] - g3['score']
            flip_flag = "⚠️  FLIP" if abs(delta) >= 3 else ""
            
            comparison.append({
                'article': article,
                'grok3': g3,
                'grok41': g41,
                'delta': delta,
                'flip': flip_flag
            })
    
    # Sort by delta to highlight biggest changes
    comparison.sort(key=lambda x: abs(x['delta']), reverse=True)
    
    print(f"\n{'#':<3} {'Title':<40} {'Grok-3':<8} {'Grok-4.1':<8} {'Δ':<5} {'Flag':<10}")
    print("-" * 90)
    
    for i, comp in enumerate(comparison, 1):
        title = comp['article']['title'][:37]
        g3_score = f"{comp['grok3']['score']:.1f}"
        g41_score = f"{comp['grok41']['score']:.1f}"
        delta = f"{comp['delta']:+.1f}"
        flip = comp['flip']
        
        print(f"{i:<3} {title:<40} {g3_score:<8} {g41_score:<8} {delta:<5} {flip:<10}")
    
    # Detailed comparison for top flips
    print("\n" + "=" * 70)
    print("SIGNIFICANT RANK FLIPS (δ ≥ 3.0)")
    print("=" * 70)
    
    flips = [c for c in comparison if abs(c['delta']) >= 3.0]
    
    if flips:
        for comp in flips[:5]:
            article = comp['article']
            g3 = comp['grok3']
            g41 = comp['grok41']
            delta = comp['delta']
            
            print(f"\n📰 {article['title']}")
            print(f"   [{article['source']}]")
            print(f"\n   Grok-3-mini ({g3['score']:.1f}): {g3['reason']}")
            print(f"   Grok-4-1 ({g41['score']:.1f}): {g41['reason']}")
            print(f"   Δ: {delta:+.1f} {'↑ Grok-4.1 ranks higher' if delta > 0 else '↓ Grok-3 ranks higher'}")
    else:
        print("✅ No significant flips detected — models align well!")
    
    # Summary stats
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    avg_g3 = sum(r['score'] for r in grok3_results) / len(grok3_results) if grok3_results else 0
    avg_g41 = sum(r['score'] for r in grok41_results) / len(grok41_results) if grok41_results else 0
    
    avg_delta = sum(c['delta'] for c in comparison) / len(comparison) if comparison else 0
    max_delta = max(abs(c['delta']) for c in comparison) if comparison else 0
    
    print(f"\nAverage scores:")
    print(f"  Grok-3-mini:           {avg_g3:.2f}")
    print(f"  Grok-4-1-fast:         {avg_g41:.2f}")
    print(f"  Difference:            {avg_g41 - avg_g3:+.2f}")
    
    print(f"\nRank stability:")
    print(f"  Average Δ per article: {avg_delta:+.2f}")
    print(f"  Max Δ:                 {max_delta:.1f}")
    print(f"  Significant flips:     {len([c for c in comparison if abs(c['delta']) >= 3])}/{len(comparison)}")
    
    # Verdict
    print(f"\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    
    if max_delta < 2 and len([c for c in comparison if abs(c['delta']) >= 3]) == 0:
        print("✅ READY TO SWITCH")
        print(f"\nGrok-4-1 maintains strong personalization alignment.")
        print(f"No significant rank flips detected. Safe to adopt as default.")
    elif max_delta < 3 and len([c for c in comparison if abs(c['delta']) >= 3]) <= 1:
        print("⚠️  CAUTION: Minor Flips")
        print(f"\n{len(flips)} articles show {max_delta:.1f}-point swings.")
        print(f"Review the flips above. If acceptable, safe to proceed.")
    else:
        print("❌ HOLD")
        print(f"\n{len(flips)} significant flips detected (δ ≥ 3.0).")
        print(f"Grok-4-1 may be overriding your preferences.")
        print(f"Review the rationales above before switching.")
    
    # Save report
    report_file = Path(__file__).parent / 'docs' / 'test-reports' / f'2026-03-06-grok41-ab-test.md'
    report_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(report_file, 'w') as f:
        f.write("# Grok-4-1-fast-reasoning A/B Test\n\n")
        f.write(f"Date: {datetime.now().isoformat()}\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"- Articles tested: {len(comparison)}\n")
        f.write(f"- Grok-3 avg score: {avg_g3:.2f}\n")
        f.write(f"- Grok-4-1 avg score: {avg_g41:.2f}\n")
        f.write(f"- Max delta: {max_delta:.1f}\n")
        f.write(f"- Significant flips: {len(flips)}\n\n")
        
        f.write(f"## Article Comparison\n\n")
        f.write(f"| Article | G3 | G4.1 | Δ | Status |\n")
        f.write(f"|---------|----|----- |----|--------|\n")
        for comp in comparison:
            title = comp['article']['title'][:50]
            g3 = f"{comp['grok3']['score']:.1f}"
            g41 = f"{comp['grok41']['score']:.1f}"
            delta = f"{comp['delta']:+.1f}"
            status = "⚠️ FLIP" if abs(comp['delta']) >= 3 else "✅"
            f.write(f"| {title} | {g3} | {g41} | {delta} | {status} |\n")
    
    print(f"\n📁 Report saved: {report_file}")
    print("\n✅ A/B test complete!")

if __name__ == "__main__":
    main()
