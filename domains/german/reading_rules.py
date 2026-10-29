"""Pure Lesen URL identity and display-selection rules.

No feed fetches, store access or historical-ID rewriting. Callers own article
persistence, refresh locking and category selection before display capping.
"""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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



def normalize_url(url: str) -> str:
    """Return a stable canonical URL for deduplication and forward-only IDs."""
    try:
        parts = urlsplit((url or "").strip())
        scheme = parts.scheme.lower()
        hostname = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if not scheme or not hostname:
        return ""
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "")
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path[:-1]
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid"}
        ),
        doseq=True,
    )
    return urlunsplit((scheme, netloc, path, query, ""))



def article_id(normalized_url: str, date_fetched: str | None = None) -> str:
    """Return a stable ID derived only from the canonical URL.

    ``date_fetched`` remains an accepted argument for compatibility with the
    old call shape, but it deliberately does not affect identity.
    """
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:24]
    return f"art_url_{digest}"
