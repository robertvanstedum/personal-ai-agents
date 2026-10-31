"""Briefing display values from supplied records and time; no storage lookup."""
import re
import json
import math
import html as _html
from datetime import timezone

class InvalidBriefing(ValueError):
    """The stored snapshot cannot be safely displayed or returned as JSON."""


def validate_articles(raw):
    """Refuse a malformed snapshot as a unit; never silently drop articles."""
    if not isinstance(raw, list) or any(not isinstance(entry, dict) for entry in raw):
        raise InvalidBriefing("Briefing must contain a list of article objects")
    for entry in raw:
        for score_field in ('final_score', 'raw_score'):
            score = entry.get(score_field)
            if score is not None:
                try:
                    valid_score = (not isinstance(score, bool)
                                   and isinstance(score, (int, float)) and math.isfinite(score))
                except OverflowError:
                    valid_score = False
                if not valid_score:
                    raise InvalidBriefing("Article score must be a finite number")
        for field in ('summary', 'published', 'title', 'source', 'link', 'url',
                      'briefing_date', 'date', 'briefing_model'):
            if entry.get(field) is not None and not isinstance(entry[field], str):
                raise InvalidBriefing("Article text fields must be strings")
    try:
        json.dumps(raw, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as error:
        raise InvalidBriefing("Briefing contains invalid JSON values") from error


def time_ago(published_str, now):
    """Return a human-readable 'Xh ago' / 'Xd ago' string for a published timestamp."""
    if not published_str:
        return 'N/A'
    try:
        import dateutil.parser
        pub_dt = dateutil.parser.parse(published_str)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        diff = now - pub_dt
        hours = diff.total_seconds() / 3600
        if hours < 1:
            return f"{int(diff.total_seconds() / 60)}m ago"
        elif hours < 24:
            return f"{int(hours)}h ago"
        else:
            return f"{int(hours / 24)}d ago"
    except Exception:
        return 'N/A'


def strip_summary(text: str, max_len: int = 200) -> str:
    """Strip HTML tags, decode entities, trim RSS footer boilerplate, cap length."""
    if not text:
        return ''
    clean = _html.unescape(re.sub(r'<[^>]+>', ' ', text))
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Drop common RSS footer lines ("The post X appeared first on Y.")
    clean = re.sub(r'\s*The post .+ appeared first on .+\.$', '', clean, flags=re.DOTALL).strip()
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(' ', 1)[0] + '…'
    return clean


def enrich_articles(raw, now, *, time_ago, strip_summary, ranked=False):
    """Shallow-copy articles for display without changing persisted records."""
    validate_articles(raw)
    articles = []
    for rank, entry in enumerate(raw, 1):
        score = entry.get('final_score', 0) or 0
        score_pct = min(100, max(0, (score / 20.0) * 100)) if score > 0 else 0
        enriched = dict(entry)
        enriched['time_ago'] = time_ago(entry.get('published', ''), now)
        enriched['score_pct'] = score_pct
        enriched['summary'] = strip_summary(entry.get('summary', ''))
        if ranked:
            enriched['rank'] = rank
        articles.append(enriched)
    return articles
