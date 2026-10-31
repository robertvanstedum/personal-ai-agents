"""Deterministic keyword scoring and source-age policy with a supplied clock."""
from datetime import datetime
from typing import Dict
from domains.curator.selection_rules import normalize_score, assign_category

KEYWORDS = [
    "gold", "sanctions", "debt", "fiscal", "geopolitical", "trade war",
    "central bank", "inflation", "russia", "ukraine", "china", "treasury",
    "fed", "powell", "rates", "recession", "currency", "dollar", "euro",
    "oil", "energy", "conflict", "policy", "tariff", "deficit", "bonds"
]

SLOW_SOURCE_DOMAINS = {
    "crisisgroup.org",
    "chathamhouse.org",
    "warontherocks.com",
    "foreignaffairs.com",
    "cepr.org",
    "arxiv.org",
    "stlouisfed.org",        # Fed On The Economy
    "federalreserve.gov",
    "treasurydirect.gov",    # Treasury MSPD
    "newyorkfed.org",        # NY Fed Liberty Street
    "bis.org",
    "adamtooze.substack.com",
    "geopoliticalfutures.com",
    "justsecurity.org",
    "propublica.org",        # Investigative — long relevance window
}

def compute_age_multiplier(entry: Dict, *, now: datetime, slow_domains=SLOW_SOURCE_DOMAINS) -> float:
    """
    Two-tier age decay multiplier based on source type.

    SLOW sources (think tanks, academic, institutional) decay gently —
    a 90-day Crisis Group report is still valuable if the topic resurfaces.

    FAST sources (news, X posts, blogs) decay sharply — content older
    than 30 days is near-dead in the briefing candidate pool.

    Returns a multiplier in (0, 1.0]. Returns 1.0 when published date
    is unknown (X bookmarks with no date get no age penalty).
    """
    pub = entry.get('published')
    if pub is None:
        return 1.0  # No date — no penalty (X bookmarks, some institutional feeds)

    days_old = (now - pub).total_seconds() / 86400
    link = entry.get('link', '').lower()
    is_slow = any(domain in link for domain in slow_domains)

    if is_slow:
        if days_old <= 30:  return 1.0
        if days_old <= 60:  return 0.85
        if days_old <= 90:  return 0.65
        return 0.40          # 90+ days — significant penalty, still eligible
    else:
        # Fast content: news, X posts, blogs, finance commentary
        if days_old <= 3:   return 1.0
        if days_old <= 7:   return 0.85
        if days_old <= 14:  return 0.65
        if days_old <= 30:  return 0.40
        return 0.05          # 30+ days — essentially dead for fast content

def score_entry_mechanical(entry: Dict, *, now: datetime, keywords=KEYWORDS,
                           normalizer=normalize_score, categorizer=assign_category) -> Dict:
    """
    Score an entry using mechanical keyword matching (V2: category-aware)

    Returns:
        {
            'score': float (0-10),       # Normalized score
            'category': str,              # Assigned category
            'raw_score': float,           # Original score (for debugging)
            'method': 'mechanical'        # Scoring method used
        }

    KEY DECISION: Return a dict instead of just a float.
    This consistent structure works across mechanical/ai/hybrid modes.
    """
    raw_score = 0.0

    # Recency score (decay over 7 days)
    if entry["published"]:
        age_hours = (now - entry["published"]).total_seconds() / 3600
        recency_score = max(0, 100 - (age_hours / 24) * 10)  # 10 points per day decay
    else:
        recency_score = 50  # treat unknown date as ~5 days old, neutral
    raw_score += recency_score

    # Keyword matching score
    text = f"{entry['title']} {entry['summary']}".lower()
    keyword_matches = sum(1 for kw in keywords if kw in text)
    raw_score += keyword_matches * 5

    # Source priority weights
    source_weights = {
        "Geopolitical Futures": 1.4,
        "The Big Picture": 1.2,
        "ZeroHedge": 1.1,
        "Fed On The Economy": 1.2,
        "Treasury MSPD": 1.3,
    }
    weight = source_weights.get(entry["source"], 1.0)
    raw_score *= weight

    # Normalize to 0-10
    normalized = normalizer(raw_score)

    # Assign category
    category = categorizer(entry)

    return {
        'score': normalized,
        'category': category,
        'raw_score': raw_score,
        'method': 'mechanical'
    }
