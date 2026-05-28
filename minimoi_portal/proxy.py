"""
minimoi_portal/proxy.py — Reverse proxy for Curator and Mein Deutsch backends.

Forwards authenticated requests to the backend Flask apps and rewrites
HTML content so all internal links resolve correctly through the portal prefix.

Rewriting strategy:
  - HTML: tag attributes (href/src/action/data-src) + inline <script> blocks
  - CSS:  url('/...') references
  - JS:   external .js files — fetch/axios/url patterns rewritten
  - Template literals (fetch(`/...`)) are NOT rewritten — known gap for
    dynamically-constructed paths. Good enough for portfolio use.
"""

import re

import requests
from bs4 import BeautifulSoup
from flask import Response, request

# Headers that must not be forwarded between proxies
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length",
})

# JS content types
_JS_TYPES = ("application/javascript", "text/javascript", "application/x-javascript")


def _rewrite_js(text: str, portal_prefix: str) -> str:
    """Rewrite absolute URL paths inside a JavaScript string."""
    # fetch('/...') and fetch("/...")
    text = re.sub(
        r"""(fetch\s*\(\s*['"])(/[^'"?#`])""",
        lambda m: m.group(1) + portal_prefix + m.group(2),
        text,
    )
    # axios.get('/...'), axios.post('/...'), etc.
    text = re.sub(
        r"""(axios\.\w+\s*\(\s*['"])(/[^'"?#`])""",
        lambda m: m.group(1) + portal_prefix + m.group(2),
        text,
    )
    # url: '/...' patterns in JS objects/options
    text = re.sub(
        r"""(url\s*:\s*['"])(/[^'"?#`])""",
        lambda m: m.group(1) + portal_prefix + m.group(2),
        text,
    )
    # XMLHttpRequest .open("GET", '/...')
    text = re.sub(
        r"""(\.open\s*\(\s*['"][A-Z]+['"]\s*,\s*['"])(/[^'"?#`])""",
        lambda m: m.group(1) + portal_prefix + m.group(2),
        text,
    )
    # window.location assignments: window.location = '/...'
    text = re.sub(
        r"""(window\.location(?:\.href)?\s*=\s*['"])(/[^'"?#`])""",
        lambda m: m.group(1) + portal_prefix + m.group(2),
        text,
    )
    return text


def proxy_to(backend_url: str, path: str, portal_prefix: str) -> Response:
    """
    Forward the current Flask request to backend_url/path.
    Rewrites URLs in HTML, CSS, and JS responses so they resolve
    correctly through the portal prefix.

    portal_prefix: e.g. '/app/curator' or '/app/german'
    backend_url:   e.g. 'http://localhost:8766'
    path:          the remaining path after stripping the portal prefix
    """
    target = f"{backend_url}/{path.lstrip('/')}"
    if request.query_string:
        target += f"?{request.query_string.decode()}"

    # Forward request headers minus hop-by-hop
    fwd_headers = {
        k: v for k, v in request.headers
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }

    try:
        resp = requests.request(
            method=request.method,
            url=target,
            headers=fwd_headers,
            data=request.get_data(),
            allow_redirects=False,
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        return Response(
            "<h2>Backend unavailable</h2><p>The app is not running. Try again shortly.</p>",
            status=503,
            content_type="text/html",
        )

    # Rewrite Location header on redirects
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location", "")
        if location.startswith("/"):
            location = f"{portal_prefix}{location}"
        return Response(status=resp.status_code, headers={"Location": location})

    # Filter hop-by-hop response headers
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    content_type = resp.headers.get("content-type", "")

    # ── HTML: rewrite internal URLs ───────────────────────────────────────
    if "text/html" in content_type:
        soup = BeautifulSoup(resp.content, "html.parser")

        # Rewrite tag attributes
        for tag in soup.find_all(True):
            for attr in ("href", "src", "action", "data-src", "data-url"):
                val = tag.get(attr, "")
                if val and val.startswith("/") and not val.startswith("//"):
                    tag[attr] = f"{portal_prefix}{val}"

        # Rewrite inline <script> blocks
        for script in soup.find_all("script"):
            # Use .string for simple text nodes; fall back to iterating NavigableStrings
            if script.string:
                script.string = _rewrite_js(script.string, portal_prefix)
            else:
                # Script has mixed content (rare) — skip safely
                pass

        resp_headers.pop("Content-Type", None)
        return Response(
            str(soup).encode("utf-8"),
            status=resp.status_code,
            headers=resp_headers,
            content_type="text/html; charset=utf-8",
        )

    # ── CSS: rewrite url('/...') references ──────────────────────────────
    if "text/css" in content_type:
        text = resp.text
        text = re.sub(
            r"""(url\s*\(\s*['"]?)(/[^'")?#])""",
            lambda m: m.group(1) + portal_prefix + m.group(2),
            text,
        )
        resp_headers.pop("Content-Type", None)
        return Response(
            text.encode("utf-8"),
            status=resp.status_code,
            headers=resp_headers,
            content_type="text/css; charset=utf-8",
        )

    # ── JavaScript: rewrite absolute paths in external .js files ─────────
    if any(jt in content_type for jt in _JS_TYPES):
        text = _rewrite_js(resp.text, portal_prefix)
        resp_headers.pop("Content-Type", None)
        return Response(
            text.encode("utf-8"),
            status=resp.status_code,
            headers=resp_headers,
            content_type="application/javascript; charset=utf-8",
        )

    # ── Everything else: pass through as-is ──────────────────────────────
    return Response(
        resp.content,
        status=resp.status_code,
        headers=resp_headers,
        content_type=content_type,
    )
