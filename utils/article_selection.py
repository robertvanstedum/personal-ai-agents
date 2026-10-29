"""Pure article display selection shared by language domains.

Preserves input order and object identity. Minimum display counts may relax
source limits; callers own category filtering, configuration and persistence.
"""

def apply_source_cap(articles: list, config: dict) -> list:
    """Cap articles per source per category. Applied at display time, not ingestion."""
    max_per_cat = config.get("max_per_category", 10)
    max_per_src = config.get("max_per_source_per_category", 3)
    min_per_cat = config.get("min_per_category", 3)
    source_counts: dict[str, int] = {}
    selected = []
    for article in articles:
        src = article.get("source", "")
        if source_counts.get(src, 0) < max_per_src:
            selected.append(article)
            source_counts[src] = source_counts.get(src, 0) + 1
        if len(selected) >= max_per_cat:
            break
    if len(selected) < min_per_cat:
        already = set(id(a) for a in selected)
        for article in articles:
            if id(article) not in already:
                selected.append(article)
                if len(selected) >= min_per_cat:
                    break
    return selected

