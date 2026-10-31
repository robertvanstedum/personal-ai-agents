"""Read-only briefing files selected by the caller; no application discovery."""
import json
from datetime import timezone
from domains.curator.briefing_views import InvalidBriefing, validate_articles

def load_briefing(json_path, *, clock, datetime_type, enrich):
    """Read curator_latest.json and enrich each article with time_ago and score_pct.

    Returns (articles, day_str, date_str, model_display, briefing_date) or None
    if the JSON file does not exist. Invalid/unreadable snapshots are refused.
    """
    if not json_path.exists():
        return None

    try:
        raw = json.loads(json_path.read_text())
    except (OSError, ValueError) as error:
        raise InvalidBriefing("Briefing snapshot is unavailable") from error

    validate_articles(raw)
    now = clock()

    # Prefer explicit generation metadata. Older files did not include it, so
    # their modification date is a truthful fallback; never label stale data
    # with the server's current date.
    candidate = (
        (raw[0].get('briefing_date') or raw[0].get('date'))
        if raw else None
    )
    try:
        briefing_dt = datetime_type.strptime(str(candidate)[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        try:
            briefing_dt = datetime_type.fromtimestamp(json_path.stat().st_mtime, timezone.utc)
        except (OSError, ValueError, OverflowError) as error:
            raise InvalidBriefing("Briefing date is unavailable") from error
    briefing_date = briefing_dt.strftime('%Y-%m-%d')
    day_str = briefing_dt.strftime('%A')
    date_str = briefing_dt.strftime('%B %d, %Y')

    model_display = raw[0].get('briefing_model', 'unknown') if raw else 'unknown'

    articles = enrich(raw, now)

    return articles, day_str, date_str, model_display, briefing_date


def load_radar(radar_path):
    """Load on-radar articles from curator_radar.json. Returns [] if missing or empty."""
    if not radar_path.exists():
        return []
    try:
        data = json.loads(radar_path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def latest_briefing_date(latest_path, *, datetime_type):
    """Return the persisted briefing date without claiming stale data is today."""
    try:
        raw = json.loads(latest_path.read_text())
        if raw:
            d = raw[0].get('briefing_date') or raw[0].get('date', '')
            if d:
                from datetime import datetime as _dt
                return _dt.strptime(d[:10], '%Y-%m-%d').strftime('%A, %B %-d, %Y')
            fallback = datetime_type.fromtimestamp(latest_path.stat().st_mtime, timezone.utc)
            return fallback.strftime('%A, %B %-d, %Y')
    except Exception:
        pass
    return '—'


def latest_article_count(latest_path):
    """Return count of articles in latest briefing. Falls back to 0."""
    try:
        raw = json.loads(latest_path.read_text())
        return len(raw)
    except Exception:
        return 0
