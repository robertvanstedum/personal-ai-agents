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

from minimoi_portal.workspaces import workspace_navigation

# Headers that must not be forwarded between proxies
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length",
})

# JS content types
_JS_TYPES = ("application/javascript", "text/javascript", "application/x-javascript")



def _already_prefixed(path: str, prefix: str) -> bool:
    """True when an absolute path already carries the portal prefix.

    Backends that know their own base path (Connect HQ with
    CONNECTHQ_ROOT_PATH set) emit URLs that are already correct; rewriting
    them again would produce /app/x/app/x/... . Every rewrite site below is
    therefore idempotent.
    """
    if not prefix:
        return True
    return path == prefix or path.startswith(prefix + "/")


def _prefix_match(m: "re.Match", prefix: str) -> str:
    """Regex replacement: prefix group(2) unless the text at that point is
    already prefixed (idempotent form of `group(1) + prefix + group(2)`)."""
    s, i = m.string, m.start(2)
    if s.startswith(prefix, i):
        j = i + len(prefix)
        if j == len(s) or s[j] in "/'\"`?#) ;,":
            return m.group(0)
    return m.group(1) + prefix + m.group(2)

def _rewrite_js(text: str, portal_prefix: str) -> str:
    """Rewrite absolute URL paths inside a JavaScript string."""
    # fetch('/...') and fetch("/...")
    text = re.sub(
        r"""(fetch\s*\(\s*['"])(/[^'"?#`])""",
        lambda m: _prefix_match(m, portal_prefix),
        text,
    )
    # fetch(`/...`) — template literals e.g. fetch(`/api/foo?x=${bar}`)
    # Rewrites the leading path prefix before any ${ expression or ?
    text = re.sub(
        r"""(fetch\s*\(\s*`)(/[^`?#$])""",
        lambda m: _prefix_match(m, portal_prefix),
        text,
    )
    # postJSON('/...') — common helper wrapper around fetch used in German app
    text = re.sub(
        r"""(postJSON\s*\(\s*['"])(/[^'"?#`])""",
        lambda m: _prefix_match(m, portal_prefix),
        text,
    )
    # axios.get('/...'), axios.post('/...'), etc.
    text = re.sub(
        r"""(axios\.\w+\s*\(\s*['"])(/[^'"?#`])""",
        lambda m: _prefix_match(m, portal_prefix),
        text,
    )
    # url: '/...' patterns in JS objects/options
    text = re.sub(
        r"""(url\s*:\s*['"])(/[^'"?#`])""",
        lambda m: _prefix_match(m, portal_prefix),
        text,
    )
    # XMLHttpRequest .open("GET", '/...')
    text = re.sub(
        r"""(\.open\s*\(\s*['"][A-Z]+['"]\s*,\s*['"])(/[^'"?#`])""",
        lambda m: _prefix_match(m, portal_prefix),
        text,
    )
    # window.location assignments: window.location = '/...'
    text = re.sub(
        r"""(window\.location(?:\.href)?\s*=\s*['"])(/[^'"?#`])""",
        lambda m: _prefix_match(m, portal_prefix),
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

    curator_active    = "color:#ffffff;font-weight:600;" if portal_prefix == "/app/curator"    else ""
    german_active     = "color:#ffffff;font-weight:600;" if portal_prefix == "/app/german"     else ""
    portuguese_active = "color:#ffffff;font-weight:600;" if portal_prefix == "/app/portuguese" else ""
    guild_active      = "color:#ffffff;font-weight:600;" if portal_prefix == "/guild"          else ""
    cos_active        = "color:#ffffff;font-weight:600;" if portal_prefix == "/app/cos"        else ""

    active_styles = {
        "curator": curator_active,
        "german": german_active,
        "portuguese": portuguese_active,
        "guild": guild_active,
        "cos": cos_active,
    }
    nav_links = []
    for workspace in workspace_navigation(user):
        label = workspace.get("short_label", workspace["label"])
        nav_links.append(
            f'<a href="{workspace["path"]}" '
            f'class="portal-workspace-link" '
            f'style="color:#C68A5E;text-decoration:none;'
            f'{active_styles[workspace["key"]]}">{label}</a>'
        )
    workspace_links = "".join(nav_links)

    # Per-backend layout offset so backend sticky elements don't hide under our nav.
    # Curator body is display:flex (row) — padding-top pushes the flex row down.
    # German/Portuguese body is block — padding-top pushes block content down.
    if portal_prefix == "/app/curator":
        offset_css = (
            "body{padding-top:38px!important;}"
            "nav.curator-subnav{top:38px!important;max-width:100%;overflow-x:auto;"
            "overflow-y:hidden;scrollbar-width:none;}"
            "nav.curator-subnav::-webkit-scrollbar{display:none;}"
            "nav.curator-subnav .subnav-tab{flex:0 0 auto;}"
        )
    elif portal_prefix in ("/app/german", "/app/portuguese"):
        offset_css = (
            "body{padding-top:38px!important;}"
            "@media (min-width:769px){nav{top:38px!important;}}"
        )
    else:
        offset_css = "body{padding-top:38px!important;}"

    return f"""
<style id="portal-offset-css">
  {offset_css}
  #portal-nav-bar, #portal-nav-bar * {{ box-sizing:border-box; }}
  #portal-nav-workspaces {{
    min-width:0;display:flex;align-items:center;gap:14px;
    white-space:nowrap;
  }}
  @media (max-width:768px) {{
    #portal-nav-bar {{ padding:0 10px!important; }}
    #portal-nav-brand {{
      flex:0 0 auto;margin-right:10px!important;white-space:nowrap;
    }}
    #portal-nav-divider {{ display:none; }}
    #portal-nav-workspaces {{
      flex:1 1 auto;overflow-x:auto;overflow-y:hidden;
      gap:16px;padding:0 4px;
      scrollbar-width:none;-webkit-overflow-scrolling:touch;
      overscroll-behavior-x:contain;
    }}
    #portal-nav-workspaces::-webkit-scrollbar {{ display:none; }}
    .portal-workspace-link {{
      flex:0 0 auto;margin-right:0!important;white-space:nowrap;
    }}
    .portal-nav-account, .portal-nav-signout {{ display:none; }}
  }}
</style>
<div id="portal-nav-bar" style="
  position:fixed;top:0;left:0;right:0;z-index:999999;
  height:38px;background:#12122a;color:#e8e8e8;
  display:flex;align-items:center;padding:0 16px;gap:0;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;border-bottom:1px solid rgba(255,255,255,0.12);
  box-shadow:0 1px 8px rgba(0,0,0,0.4);
">
  <a id="portal-nav-brand" href="/dashboard" style="color:#C68A5E;font-weight:700;text-decoration:none;letter-spacing:-0.3px;margin-right:16px;">mini-moi</a>
  <span id="portal-nav-divider" style="color:rgba(255,255,255,0.2);margin-right:16px;">|</span>
  <span id="portal-nav-workspaces">{workspace_links}</span>
  <a class="portal-nav-account" href="/account/password" style="color:rgba(255,255,255,0.45);text-decoration:none;margin-left:auto;margin-right:12px;">{display_name}</a>
  <a class="portal-nav-signout" href="/logout" style="color:rgba(255,255,255,0.6);text-decoration:none;font-size:12px;">Sign out</a>
</div>
"""


def proxy_to(backend_url: str, path: str, portal_prefix: str,
             user: dict | None = None,
             strip_header_prefixes: tuple[str, ...] = (),
             forward_prefix: bool = False) -> Response:
    """
    Forward the current Flask request to backend_url/path.
    Rewrites URLs in HTML, CSS, and JS responses so they resolve
    correctly through the portal prefix.

    portal_prefix: e.g. '/app/curator' or '/app/german'
    backend_url:   e.g. 'http://localhost:8766'
    path:          the remaining path after stripping the portal prefix
    user:          current logged-in user dict (for nav bar injection)
    strip_header_prefixes: extra client header prefixes (lower-case) that must
                   never reach this backend — e.g. ("x-demo-",) for Connect HQ,
                   whose local persona headers are not a hosted identity.
    forward_prefix: send the backend the FULL path including portal_prefix.
                   Required for backends configured with a root path (Connect HQ
                   with CONNECTHQ_ROOT_PATH): Starlette matches routes on either
                   form but serves mounted static files only under the prefix.
    """
    if forward_prefix:
        target = f"{backend_url}{portal_prefix}/{path.lstrip('/')}"
    else:
        target = f"{backend_url}/{path.lstrip('/')}"
    if request.query_string:
        target += f"?{request.query_string.decode()}"

    # Forward request headers minus hop-by-hop.
    # SECURITY: strip any client-supplied X-Minimoi-* identity headers — only
    # portal-derived values (set below) may ever reach a backend. Without this,
    # a request lacking a session auth_id could smuggle a spoofed
    # X-Minimoi-Auth-Id straight through to the domain apps (identity/IDOR).
    fwd_headers = {
        k: v for k, v in request.headers
        if k.lower() not in _HOP_BY_HOP
        and k.lower() != "host"
        and not k.lower().startswith("x-minimoi-")
        and not any(k.lower().startswith(p) for p in strip_header_prefixes)
    }
    if user:
        fwd_headers["X-Minimoi-User-Tier"] = user.get("tier", "guest")
        if user.get("display_name"):
            fwd_headers["X-Minimoi-Display-Name"] = user["display_name"]
        if user.get("auth_id"):
            fwd_headers["X-Minimoi-Auth-Id"] = str(user["auth_id"])
        if user.get("username"):
            fwd_headers["X-Minimoi-Username"] = user["username"]

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
        if location.startswith("/") and not location.startswith("//") \
                and not _already_prefixed(location, portal_prefix):
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
                if (val and val.startswith("/") and not val.startswith("//")
                        and not _already_prefixed(val, portal_prefix)):
                    tag[attr] = f"{portal_prefix}{val}"

            # Rewrite inline style background-image: url('/...')
            style_val = tag.get("style", "")
            if style_val and "url(" in style_val:
                new_style = re.sub(
                    r"""(url\s*\(\s*['"]?)(/[^'")?#])""",
                    lambda m: _prefix_match(m, portal_prefix),
                    style_val,
                )
                if new_style != style_val:
                    tag["style"] = new_style

        # Rewrite absolute asset URLs declared inside inline <style> blocks.
        # Attribute-level rewriting above cannot see CSS rules such as
        # body::before { background: url('/static/...') }.
        for style in soup.find_all("style"):
            if not style.string or "url(" not in style.string:
                continue
            style.string = re.sub(
                r"""(url\s*\(\s*['"]?)(/[^/'")?#])""",
                lambda m: _prefix_match(m, portal_prefix),
                style.string,
            )

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

        # ── Inject per-app offset CSS so sticky elements clear the portal nav ─
        if portal_prefix == "/app/curator":
            offset_css = (
                "body{padding-top:38px!important;}"
                "nav.curator-subnav{top:38px!important;max-width:100%;overflow-x:auto;"
                "overflow-y:hidden;scrollbar-width:none;}"
                "nav.curator-subnav::-webkit-scrollbar{display:none;}"
                "nav.curator-subnav .subnav-tab{flex:0 0 auto;}"
            )
            style_tag = soup.new_tag("style")
            style_tag.string = offset_css
            head = soup.find("head")
            if head:
                head.append(style_tag)

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
            lambda m: _prefix_match(m, portal_prefix),
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
    elif "text/html" in content_type:
        resp_headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp_headers["Pragma"] = "no-cache"

    # ── Everything else: pass through as-is ──────────────────────────────
    return Response(
        resp.content,
        status=resp.status_code,
        headers=resp_headers,
        content_type=content_type,
    )
