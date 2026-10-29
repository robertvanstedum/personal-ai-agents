"""Escape absolute web links for HTML attributes; not a backend fetch policy."""
from html import escape as _escape
from urllib.parse import urlsplit


def safe_web_href(value: str) -> str:
    """Render only absolute web navigation; keep other references non-clickable."""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return "#"
        # Accessing the port validates malformed and out-of-range port syntax.
        parts.port
    except (TypeError, ValueError):
        return "#"
    return _escape(value, quote=True)

