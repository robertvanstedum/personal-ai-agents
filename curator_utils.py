#!/usr/bin/env python3
"""
curator_utils.py - Utility functions for curator system

Includes validation, diagnostics, and helper functions.
"""

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict


def validate_signal_store_correlation(n: int = 10, signal_store_path: str = None) -> bool:
    """
    Validate that article_id values correlate correctly between scored and feedback events.
    
    This is a sanity check to catch ID format mismatches that would break Stage 3 learning.
    
    Args:
        n: Number of recent events to check (default 10)
        signal_store_path: Path to signal_store.jsonl (defaults to ./signal_store.jsonl)
    
    Returns:
        True if validation passes, False otherwise
    
    Prints loud, clear diagnostics to stdout.
    """
    if signal_store_path is None:
        signal_store_path = Path(__file__).parent / "signal_store.jsonl"
    else:
        signal_store_path = Path(signal_store_path)
    
    if not signal_store_path.exists():
        print(f"⚠️  Signal Store not found: {signal_store_path}")
        print(f"   This is OK if curator hasn't run yet.")
        return True
    
    # Read events
    scored_ids: Set[str] = set()
    feedback_ids: Set[str] = set()
    scored_by_id: Dict[str, dict] = {}
    feedback_by_id: Dict[str, List[dict]] = defaultdict(list)
    
    print(f"\n📊 Signal Store Correlation Validator")
    print(f"=" * 60)
    print(f"Reading from: {signal_store_path}")
    
    with open(signal_store_path, 'r') as f:
        for line in f:
            try:
                event = json.loads(line.strip())
                event_type = event.get('event')
                article_id = event.get('article_id')
                
                if not article_id:
                    continue
                
                if event_type == 'article_scored':
                    scored_ids.add(article_id)
                    scored_by_id[article_id] = event
                elif event_type == 'feedback':
                    feedback_ids.add(article_id)
                    feedback_by_id[article_id].append(event)
                    
            except json.JSONDecodeError:
                continue
    
    print(f"\n📈 Event Summary:")
    print(f"   Scored events: {len(scored_ids)} unique article IDs")
    print(f"   Feedback events: {len(feedback_ids)} unique article IDs")
    
    # Check for correlation
    matched = scored_ids & feedback_ids
    unmatched_feedback = feedback_ids - scored_ids
    unmatched_scored = scored_ids - feedback_ids
    
    print(f"\n🔗 Correlation Check:")
    print(f"   Matched IDs (scored + feedback): {len(matched)}")
    print(f"   Feedback with no scored event: {len(unmatched_feedback)}")
    print(f"   Scored with no feedback: {len(unmatched_scored)}")
    
    # Detailed diagnostics if problems found
    validation_passed = True
    
    if unmatched_feedback:
        print(f"\n❌ CORRELATION FAILURE: {len(unmatched_feedback)} feedback events have no matching scored event")
        print(f"\n   This means Stage 3 learning will fail to correlate feedback with articles.")
        print(f"\n   Unmatched feedback article_ids (first {min(5, len(unmatched_feedback))}):")
        
        for i, article_id in enumerate(list(unmatched_feedback)[:5], 1):
            feedback_event = feedback_by_id[article_id][0]
            print(f"      {i}. ID: {article_id}")
            print(f"         Title: {feedback_event.get('title', 'N/A')}")
            print(f"         Source: {feedback_event.get('source', 'N/A')}")
            print(f"         Channel: {feedback_event.get('channel', 'N/A')}")
        
        print(f"\n   Format analysis:")
        print(f"      Scored IDs sample: {list(scored_ids)[:3]}")
        print(f"      Feedback IDs sample: {list(unmatched_feedback)[:3]}")
        
        validation_passed = False
    
    if matched:
        print(f"\n✅ {len(matched)} article(s) have both scored and feedback events")
        print(f"\n   Sample matched articles (first {min(3, len(matched))}):")
        
        for i, article_id in enumerate(list(matched)[:3], 1):
            scored = scored_by_id[article_id]
            feedback_list = feedback_by_id[article_id]
            print(f"      {i}. ID: {article_id}")
            print(f"         Title: {scored.get('title', 'N/A')[:60]}...")
            print(f"         Scored: {scored.get('score')} ({scored.get('model')})")
            print(f"         Feedback: {len(feedback_list)} event(s) - {[f.get('action') for f in feedback_list]}")
    
    # Format consistency check
    print(f"\n🔍 Format Consistency Check:")
    
    scored_formats = analyze_id_formats(scored_ids)
    feedback_formats = analyze_id_formats(feedback_ids)
    
    print(f"   Scored ID formats:")
    for fmt, count in scored_formats.items():
        print(f"      {fmt}: {count} IDs")
    
    print(f"   Feedback ID formats:")
    for fmt, count in feedback_formats.items():
        print(f"      {fmt}: {count} IDs")
    
    # Check if formats match
    if scored_formats.keys() != feedback_formats.keys() and feedback_ids:
        print(f"\n   ⚠️  WARNING: Scored and feedback events use different ID formats!")
        print(f"      This suggests a schema mismatch that will break correlation.")
        validation_passed = False
    
    # Final verdict
    print(f"\n" + "=" * 60)
    if validation_passed:
        print(f"✅ VALIDATION PASSED: Signal Store correlation is healthy")
    else:
        print(f"❌ VALIDATION FAILED: Fix ID correlation before Stage 3")
    print(f"=" * 60 + "\n")
    
    return validation_passed


def analyze_id_formats(ids: Set[str]) -> Dict[str, int]:
    """
    Analyze article_id formats to detect patterns.
    
    Returns dict mapping format description to count.
    """
    formats = defaultdict(int)
    
    for article_id in ids:
        if not article_id:
            formats['empty'] += 1
        elif article_id.startswith('fallback-'):
            formats['fallback-{source}-{date}-{rank}'] += 1
        elif article_id.startswith('test_'):
            formats['test_*'] += 1
        elif article_id.startswith('article-'):
            formats['article-{rank}'] += 1
        elif len(article_id) == 5 and all(c in '0123456789abcdef' for c in article_id):
            formats['hash5 (MD5 URL)'] += 1
        elif '-' in article_id and len(article_id.split('-')) >= 4:
            formats['{source}-{date}-{rank}'] += 1
        else:
            formats['other/unknown'] += 1
    
    return dict(formats)


if __name__ == '__main__':
    # Self-test: run validation on current Signal Store
    print("🧪 Running Signal Store correlation validator...")
    validate_signal_store_correlation(n=10)
