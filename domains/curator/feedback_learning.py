"""Feedback learning policy; mutates supplied preferences without persistence."""

def deterministic_metadata(article):
    """Metadata available without an LLM call.

    Web buttons provide only a generic action, not an explanation to interpret.
    Source/domain learning remains useful, while content/style inference is
    intentionally left empty until the user supplies actual written feedback.
    """
    return {
        "content_type": [],
        "appeal": [],
        "style": [],
        "themes": [],
        "depth": "unknown",
        "signals": [],
        "source": article.get("source", ""),
        "url": article.get("url", ""),
    }


def update_patterns(prefs, metadata, feedback_type, *, now, classify_url):
    """Update aggregate patterns based on new feedback.

    Weights (confirmed 2026-02-26):
      liked   = +2  (strong quality signal: 'this was genuinely good')
      saved   = +1  (weak/uncertain signal: 'interesting, want to revisit')
      disliked = -1 (clear negative)
    Save is closer to a bookmark than an endorsement — like is the real curation signal.
    """
    patterns = prefs['learned_patterns']
    weight_map = {'liked': 2, 'saved': 1, 'disliked': -1}
    weight = weight_map.get(feedback_type, 1)

    # Update content types
    for ct in metadata.get('content_type', []):
        patterns['preferred_content_types'][ct] = patterns['preferred_content_types'].get(ct, 0) + weight

    # Update themes
    for theme in metadata.get('themes', []):
        patterns['preferred_themes'][theme] = patterns['preferred_themes'].get(theme, 0) + weight

    # Update sources
    source = metadata.get('source')
    if source:
        patterns['preferred_sources'][source] = patterns['preferred_sources'].get(source, 0) + weight

    # Update avoid patterns if disliked (format/quality signals)
    if feedback_type == 'disliked':
        for signal in metadata.get('signals', []):
            patterns['avoid_patterns'][signal] = patterns['avoid_patterns'].get(signal, 0) + 1

    # Update content domains and source types from article URL
    url = metadata.get('url', '')
    if url:
        try:
            domain, source_type = classify_url(url)
            action_key  = {'liked': 'like', 'saved': 'save', 'disliked': 'dislike'}.get(feedback_type, 'save')
            if domain:
                cd = patterns.setdefault('content_domains', {})
                cd.setdefault(domain, {'like': 0, 'save': 0, 'dislike': 0})
                cd[domain][action_key] = cd[domain].get(action_key, 0) + 1
            if source_type:
                st = patterns.setdefault('source_types', {})
                st.setdefault(source_type, {'like': 0, 'save': 0, 'dislike': 0})
                st[source_type][action_key] = st[source_type].get(action_key, 0) + 1
        except Exception:
            pass

    # Update metadata
    patterns['last_updated'] = now().isoformat()
    patterns['sample_size'] = patterns.get('sample_size', 0) + 1
