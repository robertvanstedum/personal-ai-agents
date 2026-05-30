"""
minimoi_portal/proxy.py — Reverse proxy for Curator and Mein Deutsch backends.

Forwards authenticated requests to the backend Flask apps and rewrites
HTML content so all internal links resolve correctly through the portal prefix.

Rewriting strategy:
  - HTML: tag attributes (href/src/action/data-src/data-url) + inline style url()
          + inline <script> blocks + injected portal nav bar
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
    # fetch(`/...`) — template literals e.g. fetch(`/api/foo?x=${bar}`)
    # Rewrites the leading path prefix before any ${ expression or ?
    text = re.sub(
        r"""(fetch\s*\(\s*`)(/[^`?#$])""",
        lambda m: m.group(1) + portal_prefix + m.group(2),
        text,
    )
    # postJSON('/...') — common helper wrapper around fetch used in German app
    text = re.sub(
        r"""(postJSON\s*\(\s*['"])(/[^'"?#`])""",
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


def _portal_nav_html(user: dict, portal_prefix: str) -> str:
    """
    Render a slim fixed portal nav bar to inject at the top of every proxied page.
    Self-contained inline CSS + per-backend offset rules so it works regardless of
    the backend's own styles.

    Strategy: position:fixed (not sticky) so the nav bar is removed from normal flow
    and doesn't disrupt the Curator's display:flex body layout.
    Companion <style> block offsets each backend's own sticky elements by 38px.
    """
    display_name = user.get("display_name", user.get("username", "")) if user else ""

    curator_active = "color:#ffffff;font-weight:600;" if portal_prefix == "/app/curator" else ""
    german_active  = "color:#ffffff;font-weight:600;" if portal_prefix == "/app/german"  else ""

    # Per-backend layout offset so backend sticky elements don't hide under our nav.
    # Curator body is display:flex (row) — padding-top pushes the flex row down.
    # German body is block — padding-top pushes block content down.
    if portal_prefix == "/app/curator":
        offset_css = (
            "body{padding-top:38px!important;}"
            "nav.curator-subnav{top:38px!important;}"
        )
    elif portal_prefix == "/app/german":
        offset_css = (
            "body{padding-top:38px!important;}"
            "nav{top:38px!important;}"
        )
    else:
        offset_css = "body{padding-top:38px!important;}"

    return f"""
<style id="portal-offset-css">{offset_css}</style>
<div id="portal-nav-bar" style="
  position:fixed;top:0;left:0;right:0;z-index:999999;
  height:38px;background:#12122a;color:#e8e8e8;
  display:flex;align-items:center;padding:0 16px;gap:0;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;border-bottom:1px solid rgba(255,255,255,0.12);
  box-shadow:0 1px 8px rgba(0,0,0,0.4);
">
  <a href="/dashboard" style="color:#C68A5E;font-weight:700;text-decoration:none;letter-spacing:-0.3px;margin-right:16px;">mini-moi</a>
  <span style="color:rgba(255,255,255,0.2);margin-right:16px;">|</span>
  <a href="/app/curator" style="color:#C68A5E;text-decoration:none;margin-right:14px;{curator_active}">Curator</a>
  <a href="/app/german"  style="color:#C68A5E;text-decoration:none;{german_active}">German</a>
  <span style="color:rgba(255,255,255,0.45);margin-left:auto;margin-right:12px;">{display_name}</span>
  <a href="/logout" style="color:rgba(255,255,255,0.6);text-decoration:none;font-size:12px;">Sign out</a>
</div>
"""


def proxy_to(backend_url: str, path: str, portal_prefix: str,
             user: dict | None = None) -> Response:
    """
    Forward the current Flask request to backend_url/path.
    Rewrites URLs in HTML, CSS, and JS responses so they resolve
    correctly through the portal prefix.

    portal_prefix: e.g. '/app/curator' or '/app/german'
    backend_url:   e.g. 'http://localhost:8766'
    path:          the remaining path after stripping the portal prefix
    user:          current logged-in user dict (for nav bar injection)
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

        # Rewrite tag attributes (href, src, action, data-* URL attrs)
        for tag in soup.find_all(True):
            for attr in ("href", "src", "action", "data-src", "data-url"):
                val = tag.get(attr, "")
                if val and val.startswith("/") and not val.startswith("//"):
                    tag[attr] = f"{portal_prefix}{val}"

            # Rewrite inline style background-image: url('/...')
            style_val = tag.get("style", "")
            if style_val and "url(" in style_val:
                new_style = re.sub(
                    r"""(url\s*\(\s*['"]?)(/[^'")?#])""",
                    lambda m: m.group(1) + portal_prefix + m.group(2),
                    style_val,
                )
                if new_style != style_val:
                    tag["style"] = new_style

        # Rewrite inline <script> blocks
        for script in soup.find_all("script"):
            if script.string:
                script.string = _rewrite_js(script.string, portal_prefix)

        # Inject portal nav bar + any guest-specific overrides right after <body>
        body = soup.find("body")
        if body:
            nav_html = _portal_nav_html(user, portal_prefix)
            # For guest users on German: hide owner-only nav links
            if user and user.get("tier") == "guest" and portal_prefix == "/app/german":
                nav_html += """
<style>
  a[href="/app/german/admin"] { display: none !important; }
</style>"""
            nav_soup = BeautifulSoup(nav_html, "html.parser")
            body.insert(0, nav_soup)

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

    # ── Static assets (images, fonts, etc.): pass through with cache headers ─
    # Add a 1-hour browser cache for images/fonts so they only proxy once.
    if any(t in content_type for t in ("image/", "font/", "application/font")):
        resp_headers.setdefault("Cache-Control", "public, max-age=3600")
        resp_headers.setdefault("Vary", "Accept-Encoding")
    elif "text/html" in content_type and portal_prefix == "/app/curator":
        # Curator briefing changes daily — never allow browser to cache it
        resp_headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp_headers["Pragma"] = "no-cache"

    # ── Everything else: pass through as-is ──────────────────────────────
    return Response(
        resp.content,
        status=resp.status_code,
        headers=resp_headers,
        content_type=content_type,
    )
