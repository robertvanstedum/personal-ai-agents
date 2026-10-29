"""Deterministic Curator category, boost and radar-selection rules.

The caller supplies entries, loaded interests/priorities and normalized topic
tags. These operations do not fetch feeds, read preferences or contact models.
Priority matching annotates the caller's entry; radar returns shallow copies.
Existing selection policy is intentionally preserved.
"""
from typing import Dict, List


# Keyword categories are shared by mechanical and AI selection. When more
# than one matches, the existing priority list below decides the result.
CATEGORIES = {
    'geo_major': ['china', 'beijing', 'xi jinping', 'russia', 'putin', 'moscow', 'europe', 'eu', 'european union',
                  'japan', 'tokyo', 'korea', 'seoul', 'united states', 'washington', 'us policy',
                  'ukraine', 'war', 'conflict', 'military operation', 'invasion', 'strike', 'attack'],
    'geo_other': ['middle east', 'iran', 'israel', 'saudi', 'africa', 'latin america', 'brazil', 'mexico',
                  'india', 'pakistan', 'southeast asia', 'vietnam', 'indonesia', 'turkey',
                  'terrorism', 'insurgency', 'civil war'],
    'monetary': ['gold', 'silver', 'bitcoin', 'crypto', 'currency', 'dollar', 'euro', 'yuan', 'yen',
                 'precious metal', 'commodity', 'bullion', 'forex', 'exchange rate', 'devaluation'],
    'fiscal': ['debt', 'deficit', 'treasury', 'bonds', 'fiscal', 'spending', 'budget', 'government spending',
               'national debt', 'sovereign debt', 'fiscal policy', 'austerity'],
    'technology': ['ai development', 'ai research', 'artificial intelligence', 'machine learning', 'robotics',
                   'autonomous systems', 'defense industry', 'weapons development', 'military tech',
                   'anduril', 'palantir', 'manufacturing capacity', 'dual-use technology',
                   'hypersonic', 'quantum computing', 'semiconductor', '5g', '6g'],
    'other': []  # Catch-all for articles that don't fit above
}


CATEGORY_PRIORITY = ['geo_major', 'geo_other', 'monetary', 'fiscal', 'technology', 'other']


def assign_category(entry: Dict) -> str:
    """
    Assign a category based on keyword matching (mechanical mode)

    Returns one of: geo_major, geo_other, monetary, fiscal, technology, other

    KEY DECISION: When multiple categories match (e.g., "China gold reserves"),
    we use CATEGORY_PRIORITY to pick the most specific/important one.
    The actual tie order is defined by the shared CATEGORY_PRIORITY list.
    This prevents over-representation and maintains diversity.
    """
    text = f"{entry['title']} {entry['summary']}".lower()

    # Check which categories match
    matches = []
    for category, keywords in CATEGORIES.items():
        if category == 'other':
            continue
        if any(kw in text for kw in keywords):
            matches.append(category)

    # Return highest-priority match, or 'other' if none
    if matches:
        for cat in CATEGORY_PRIORITY:
            if cat in matches:
                return cat

    return 'other'


def normalize_score(raw_score: float, max_score: float = 200.0) -> float:
    """
    Normalize raw mechanical score to 0-10 scale

    Args:
        raw_score: Mechanical score (typically 0-200+)
        max_score: Expected maximum for scaling (200 = recency + keywords + weights)

    Returns:
        Score between 0-10 (clamped)

    KEY DECISION: We normalize to 0-10 so mechanical and AI scores are comparable.
    This enables hybrid mode to blend both scoring methods meaningfully.
    """
    normalized = (raw_score / max_score) * 10
    return min(10.0, max(0.0, normalized))


def apply_interest_boost(entry: Dict, active_interests: Dict) -> int:
    """
    Apply score boost/penalty based on active interests.
    Returns total modifier to add to entry score.
    """
    total_modifier = 0

    # Match by category (strongest signal)
    category = entry.get('category', 'other')
    if category in active_interests:
        for interest in active_interests[category]:
            total_modifier += interest['modifier']

    return total_modifier


def apply_priorities_boost(entry: Dict, priorities: List[Dict]) -> float:
    """
    Apply score boost based on active priorities.
    Matches article title/summary against priority keywords (case-insensitive).
    Returns total boost (capped at +3.0).
    """
    if not priorities:
        return 0.0

    total_boost = 0.0
    matched_priorities = []

    # Get searchable text
    title = entry.get('title', '').lower()
    summary = entry.get('summary', '').lower()
    searchable = f"{title} {summary}"

    for priority in priorities:
        keywords = priority.get('keywords', [])
        boost = priority.get('boost', 0.0)
        priority_id = priority.get('id', 'unknown')

        # Check if any keyword matches (verbatim first, then token-level for multi-word phrases)
        matched = False
        for keyword in keywords:
            kw_lower = keyword.lower()
            if kw_lower in searchable:
                matched = True
                break
            # For multi-word keywords, split into tokens and match any significant token (4+ chars)
            tokens = [t for t in kw_lower.split() if len(t) >= 4]
            if tokens and any(token in searchable for token in tokens):
                matched = True
                break

        if matched:
            total_boost += boost
            matched_priorities.append({
                'priority_id': priority_id,
                'label': priority.get('label', 'Unknown'),
                'boost': boost
            })

    # Cap at +3.0
    total_boost = min(total_boost, 3.0)

    # Store matched priorities in entry for logging
    if matched_priorities:
        entry['matched_priorities'] = matched_priorities

    return total_boost


def find_radar_articles(pool: List[Dict], top_ids: set, topic_tags: dict,
                        cap: int = 10) -> List[Dict]:
    """Return up to `cap` articles from `pool` that match active Topic tags
    but are NOT already in the top selection.

    Matching: case-insensitive substring check of each tag against
    article title + source. Deterministic — no AI, no scoring.
    One item, one place: top_ids are excluded.
    """
    if not topic_tags:
        return []

    radar = []
    seen_ids = set(top_ids)

    for article in pool:
        aid = article.get("hash_id") or article.get("link", "")
        if aid in seen_ids:
            continue

        text = f"{article.get('title', '')} {article.get('source', '')}".lower()
        matched_topics = []
        for slug, tags in topic_tags.items():
            if any(tag and tag in text for tag in tags):
                matched_topics.append(slug)

        if matched_topics:
            article = dict(article)  # don't mutate original
            article["_radar_topics"] = matched_topics
            radar.append(article)
            seen_ids.add(aid)

        if len(radar) >= cap:
            break

    # Sort by score descending within the radar section
    radar.sort(key=lambda x: x.get("final_score", x.get("score", 0)), reverse=True)
    return radar[:cap]


def select_articles(all_entries, top_n, diversity_weight, serendipity_reserve, *,
                    active_interests, active_priorities, source_trust, x_post_cap,
                    domain_from_url, interest_boost_fn, priorities_boost_fn, report):
    """Select in two phases, mutating original records exactly as the pipeline does.

    Stable candidate order, cap state and annotations carry across both phases.
    No provider, preference or filesystem access; callbacks are caller-owned.
    """
    # Calculate split between personalized and serendipity articles
    serendipity_count = int(top_n * serendipity_reserve)
    personalized_count = top_n - serendipity_count

    if serendipity_count > 0:
        report(f"\n🎲 Serendipity reserve: {serendipity_count}/{top_n} articles ({int(serendipity_reserve*100)}%) from outside learned patterns")

    # Apply diversity-aware selection
    selected = []
    source_counts = {}
    category_counts = {}  # Track category distribution
    x_post_count = 0      # Hard cap on X bookmark posts across both selection phases
    candidates = all_entries.copy()

    # PHASE 1: Select personalized articles (with interest/priority boosts)
    while len(selected) < personalized_count and candidates:
        # Recalculate final scores based on current distribution
        for entry in candidates:
            source = entry["source"]
            category = entry.get("category", "other")

            source_count = source_counts.get(source, 0)
            category_count = category_counts.get(category, 0)

            # Source diversity penalty (existing logic; halved for trusted-tier sources)
            domain = domain_from_url(entry.get('link', ''))
            trust = source_trust.get(domain, 'neutral') if source_trust else 'neutral'
            discount = 0.5 if trust == 'trusted' else 1.0
            source_penalty = (source_count ** 2) * 30 * diversity_weight * discount

            # Category diversity penalty (NEW: avoid topic echo chambers)
            # Less aggressive than source penalty (we want some depth per topic)
            category_penalty = (category_count ** 2) * 15 * diversity_weight

            # Apply interest-based boosting
            interest_boost = interest_boost_fn(entry, active_interests)

            # Apply priorities-based boosting
            priorities_boost = priorities_boost_fn(entry, active_priorities)

            # Scale boosts by age multiplier so old articles don't get amplified
            # by historical interest. A 13d FAST article gets 65% of its boost;
            # a 1d article gets 100%. SLOW sources at <30d retain full boost.
            # This makes fresh content win when Grok scores are similar.
            age_mult = entry.get('age_multiplier', 1.0)
            scaled_interest = interest_boost * age_mult
            scaled_priorities = priorities_boost * age_mult

            entry["final_score"] = entry["score"] - source_penalty - category_penalty + scaled_interest + scaled_priorities
            if interest_boost != 0:
                entry["interest_boosted"] = True
                entry["interest_modifier"] = scaled_interest
            if priorities_boost > 0:
                entry["priorities_boosted"] = True
                entry["priorities_modifier"] = scaled_priorities

        # Pick the highest-scoring candidate (respecting X post hard cap)
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        best = None
        for _i, _cand in enumerate(candidates):
            if _cand.get('content_type') == 'x_bookmark' and x_post_count >= x_post_cap:
                continue  # Skip X posts once cap is reached
            best = candidates.pop(_i)
            break
        if best is None:
            break  # All remaining candidates are X posts at cap limit

        selected.append(best)

        source = best["source"]
        category = best.get("category", "other")
        if best.get('content_type') == 'x_bookmark':
            x_post_count += 1
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

    # PHASE 2: Select serendipity articles (NO interest/priority boosts, only base score + diversity)
    if serendipity_count > 0 and candidates:
        report(f"   Selecting {serendipity_count} serendipity articles from {len(candidates)} remaining candidates...")
        serendipity_selected = []

        while len(serendipity_selected) < serendipity_count and candidates:
            # Recalculate scores WITHOUT personalization boosts
            for entry in candidates:
                source = entry["source"]
                category = entry.get("category", "other")

                source_count = source_counts.get(source, 0)
                category_count = category_counts.get(category, 0)

                source_penalty = (source_count ** 2) * 30 * diversity_weight
                category_penalty = (category_count ** 2) * 15 * diversity_weight

                # Serendipity score = base score + diversity penalties ONLY (no boosts)
                entry["final_score"] = entry["score"] - source_penalty - category_penalty
                entry["serendipity_pick"] = True

            # Pick highest base-scoring candidate (respecting X post hard cap)
            candidates.sort(key=lambda x: x["final_score"], reverse=True)
            best = None
            for _i, _cand in enumerate(candidates):
                if _cand.get('content_type') == 'x_bookmark' and x_post_count >= x_post_cap:
                    continue  # Skip X posts once cap is reached
                best = candidates.pop(_i)
                break
            if best is None:
                break  # All remaining candidates are X posts at cap limit

            serendipity_selected.append(best)

            source = best["source"]
            category = best.get("category", "other")
            if best.get('content_type') == 'x_bookmark':
                x_post_count += 1
            source_counts[source] = source_counts.get(source, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1

        # Merge serendipity articles into main selection
        selected.extend(serendipity_selected)
        report(f"   ✅ Added {len(serendipity_selected)} serendipity articles")

    # Report distributions
    report("\n📊 Source distribution in top 20:")
    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        report(f"   {source}: {count} articles")

    report("\n🏷️  Category distribution in top 20:")
    for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
        report(f"   {category}: {count} articles")

    return selected
