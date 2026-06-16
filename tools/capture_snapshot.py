#!/usr/bin/env python3
"""
tools/capture_snapshot.py — Playwright snapshot capture for minimoi.ai /preview.

Authenticates as owner, captures all pages in the manifest (fully-rendered DOM
via headless Chromium), post-processes each page, and writes static HTML to
minimoi_portal/static/preview/.

Usage:
    python tools/capture_snapshot.py
    PORTAL_PASSWORD=mypass python tools/capture_snapshot.py

Environment:
    PORTAL_URL       — defaults to http://localhost:5001
    PORTAL_PASSWORD  — owner password (prompts interactively if not set)

Output:
    minimoi_portal/static/preview/<domain>/<page>.html
    minimoi_portal/static/preview/manifest.json
    minimoi_portal/static/preview/index.html   (navigation index)
"""

import os
import re
import sys
import json
import getpass
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import sync_playwright

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
PREVIEW_DIR = REPO_ROOT / "static" / "public" / "preview"
ASSETS_DIR  = PREVIEW_DIR / "assets"
PORTAL_URL  = os.environ.get("PORTAL_URL", "http://localhost:5001")
USERNAME    = "robert"

# ── Page manifest ──────────────────────────────────────────────────────────────

PAGES = [
    {"domain": None, "name": "index", "path": "/", "label": "mini-moi — Home", "out_path": "index.html", "is_index": True},
    {"domain": "curator", "name": "briefing",   "path": "/app/curator",              "label": "Curator — Hub"},
    {"domain": "curator", "name": "daily",      "path": "/app/curator/briefing",     "label": "Curator — Daily Briefing"},
    {"domain": "curator", "name": "scans_dives", "path": "/app/curator/scans-dives", "label": "Curator — Scans & Dives"},
    {"domain": "curator", "name": "archive",    "path": "/app/curator/archive",       "label": "Curator — Archive"},
    {"domain": "curator", "name": "leanings",      "path": "/app/curator/research/leanings",      "label": "Curator — Leanings"},
    {"domain": "curator", "name": "reading_room",  "path": "/app/curator/curator_library.html",   "label": "Curator — Reading Room"},
    {"domain": "german",  "name": "lesen",      "path": "/app/german/lesen",          "label": "Mein Deutsch — Lesen"},
    {"domain": "german",  "name": "gesprache",  "path": "/app/german/gesprache",      "label": "Mein Deutsch — Gespräche"},
    {"domain": "german",  "name": "worter",     "path": "/app/german/woerter",        "label": "Mein Deutsch — Wörter"},
    {"domain": "german",  "name": "schreiben",  "path": "/app/german/schreiben",      "label": "Mein Deutsch — Schreiben"},
    {"domain": "german",  "name": "archiv",     "path": "/app/german/archiv",         "label": "Mein Deutsch — Archiv"},
    {"domain": "guild",   "name": "briefing",   "path": "/guild",                     "label": "Guild — Daily Briefing"},
    {"domain": "guild",   "name": "build_log",  "path": "/guild/build",               "label": "Guild — Build Log"},
    {"domain": "guild",   "name": "queue",      "path": "/guild/build/queue",         "label": "Guild — Build Queue"},
    {"domain": "guild",   "name": "roadmap",    "path": "/guild/build/roadmap",       "label": "Guild — Roadmap"},
    {"domain": "guild",   "name": "career",     "path": "/guild/career",              "label": "Guild — Career Focus", "career_aggregate": True},
]

# Live path → preview URL mapping (for link rewriting)
LINK_MAP = {
    "/":                        "/preview/",
    "/contact":                 "/contact",
    # Curator nav paths — proxy prepends /app/curator so /briefing → /app/curator/briefing
    "/app/curator/briefing":                    "/preview/curator/daily.html",
    "/app/curator/research/leanings":           "/preview/curator/leanings.html",
    "/app/curator/curator_library.html":        "/preview/curator/reading_room.html",
    # /research/dashboard (Desk) → stay blocked
    "/app/curator":             "/preview/curator/briefing.html",
    "/app/curator/scans-dives": "/preview/curator/scans_dives.html",
    "/app/curator/archive":     "/preview/curator/archive.html",
    "/app/german":              "/preview/german/lesen.html",
    "/app/german/lesen":        "/preview/german/lesen.html",
    "/app/german/gesprache":    "/preview/german/gesprache.html",
    "/app/german/worter":       "/preview/german/worter.html",
    "/app/german/woerter":      "/preview/german/worter.html",
    "/app/german/schreiben":    "/preview/german/schreiben.html",
    "/app/german/archiv":       "/preview/german/archiv.html",
    "/guild":                   "/preview/guild/briefing.html",
    "/guild/build":             "/preview/guild/build_log.html",
    "/guild/build/queue":       "/preview/guild/queue.html",
    "/guild/build/roadmap":     "/preview/guild/roadmap.html",
    "/guild/career":            "/preview/guild/career.html",
    "/dashboard":               "/preview/",
    "/guild/":                  "/preview/guild/briefing.html",
}

# ── Inline assets ──────────────────────────────────────────────────────────────

FETCH_INTERCEPT_JS = """
<script id="preview-fetch-intercept">
(function() {
  var _orig = window.fetch;
  // Endpoints allowed to pass through to the real server
  var PASSTHROUGH = ['/app/german/api/translate'];
  window.fetch = function(url, opts) {
    var u = typeof url === 'string' ? url : (url && url.url) || '';
    // Lesen category API — serve pre-captured data if available
    if (u.indexOf('/api/lesen-category') !== -1) {
      var cat = '';
      try { cat = new URL(u, location.origin).searchParams.get('category') || ''; } catch(e) {}
      var data = window._previewLesenData || {};
      var articles = data[cat] || [];
      return Promise.resolve(new Response(JSON.stringify({articles: articles}), {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    // Reading Room library API — serve pre-captured data if available
    if (u === '/api/library' || u.indexOf('/api/library') !== -1) {
      var libData = window._previewLibraryData || {articles: []};
      return Promise.resolve(new Response(JSON.stringify(libData), {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    // Leanings API — serve pre-captured data (GET) or silently succeed (writes)
    if (u.indexOf('/api/research/leanings') !== -1) {
      if (opts && opts.method && opts.method !== 'GET') {
        return Promise.resolve(new Response(JSON.stringify({ok: true}), {status: 200, headers: {'Content-Type': 'application/json'}}));
      }
      var lData = window._previewLeaningsData || [];
      return Promise.resolve(new Response(JSON.stringify({ok: true, leanings: lData}), {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    // Intelligence/observations API — return empty state for preview
    if (u.indexOf('/api/intelligence/') !== -1) {
      return Promise.resolve(new Response(JSON.stringify({daily: null, weekly: null, today: '', has_prev: false, is_today: true}), {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    // Translation — return placeholder (live translation requires auth)
    if (u.indexOf('/api/translate') !== -1) {
      return Promise.resolve(new Response(JSON.stringify({translation: '(live access only)'}), {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    // Allow translate through — works for authenticated users, fails silently otherwise
    for (var i = 0; i < PASSTHROUGH.length; i++) {
      if (u.indexOf(PASSTHROUGH[i]) !== -1) return _orig.apply(this, arguments);
    }
    if (u.startsWith('/app/') || u.startsWith('/api/') || u.startsWith('/research/')) {
      return Promise.resolve(new Response('[]', {status: 200, headers: {'Content-Type': 'application/json'}}));
    }
    return _orig.apply(this, arguments);
  };
})();
</script>
"""

BANNER_CSS = """
<style id="preview-banner-style">
.preview-banner {
  position: sticky; top: 0; z-index: 9999;
  background: #2A1F14; color: #F5F0E8;
  font-family: 'DM Mono', 'Courier New', monospace;
  font-size: 11px; letter-spacing: 0.04em;
  padding: 7px 20px;
  border-bottom: 1px solid rgba(198,138,94,0.3);
  display: flex; align-items: center; gap: 8px;
}
.preview-banner .preview-date { color: rgba(245,240,232,0.7); }
.preview-request-link {
  color: #C68A5E; text-decoration: none; margin-left: auto;
  border: 1px solid rgba(198,138,94,0.4); padding: 2px 10px; border-radius: 3px;
}
.preview-request-link:hover { background: rgba(198,138,94,0.1); }
</style>
"""

MODAL_CSS_JS = """
<style id="preview-modal-style">
.preview-modal-overlay {
  display: none; position: fixed; inset: 0; z-index: 99999;
  background: rgba(0,0,0,0.6); align-items: center; justify-content: center;
}
.preview-modal-overlay.active { display: flex; }
.preview-modal {
  background: #2a1f14; border: 1px solid rgba(198,138,94,0.4);
  border-radius: 8px; padding: 28px 32px; max-width: 380px; width: 90%;
  color: #F5F0E8; font-family: 'Source Sans 3', system-ui, sans-serif;
}
.preview-modal h3 {
  font-size: 14px; font-weight: 600; margin: 0 0 12px;
  color: #C68A5E !important; text-transform: uppercase; letter-spacing: 0.06em;
}
.preview-modal p { font-size: 13px; color: rgba(245,240,232,0.75); line-height: 1.6; margin: 0 0 20px; }
.preview-modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
.preview-modal-btn {
  font-size: 12px; padding: 6px 16px; border-radius: 4px; cursor: pointer;
  border: 1px solid rgba(198,138,94,0.5); background: transparent; color: #C68A5E;
  text-decoration: none; display: inline-block;
}
.preview-modal-btn:hover { background: rgba(198,138,94,0.15); }
.preview-modal-close {
  font-size: 12px; padding: 6px 16px; border-radius: 4px; cursor: pointer;
  border: 1px solid rgba(245,240,232,0.2); background: transparent;
  color: rgba(245,240,232,0.6);
}
.preview-modal-close:hover { border-color: rgba(245,240,232,0.4); color: #F5F0E8; }
</style>
<div class="preview-modal-overlay" id="previewModal">
  <div class="preview-modal">
    <h3>Not available in preview</h3>
    <div class="preview-modal-actions">
      <a href="/contact" class="preview-modal-btn">Request access →</a>
      <button class="preview-modal-close" onclick="document.getElementById('previewModal').classList.remove('active')">Close</button>
    </div>
  </div>
</div>
<script id="preview-modal-script">
(function() {
  document.addEventListener('click', function(e) {
    var el = e.target.closest('[data-admin-blocked]');
    if (el) { e.preventDefault(); e.stopPropagation(); document.getElementById('previewModal').classList.add('active'); }
  });
  document.getElementById('previewModal').addEventListener('click', function(e) {
    if (e.target === this) this.classList.remove('active');
  });
})();
</script>
"""

# ── HTML processing ────────────────────────────────────────────────────────────

def _inject_banner(soup: BeautifulSoup, captured_at: str) -> None:
    dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    date_label = dt.strftime("%B %-d, %Y")

    head = soup.find("head")
    if head:
        head.append(BeautifulSoup(FETCH_INTERCEPT_JS + BANNER_CSS, "html.parser"))

    banner_html = f"""<div class="preview-banner">
      <span>Preview snapshot — </span>
      <span class="preview-date">Captured {date_label}</span>
      <span>. Data is real but frozen.</span>
      <a href="https://app.minimoi.ai/register" class="preview-request-link">Request live access →</a>
    </div>"""
    banner = BeautifulSoup(banner_html, "html.parser")

    body = soup.find("body")
    if body:
        body.insert(0, banner)


def _inject_modal(soup: BeautifulSoup) -> None:
    body = soup.find("body")
    if body:
        body.append(BeautifulSoup(MODAL_CSS_JS, "html.parser"))


def _disable_writes(soup: BeautifulSoup) -> None:
    """Disable all form submissions and write-action buttons/inputs."""
    modal = soup.find(id="previewModal")

    for form in soup.find_all("form"):
        form["data-preview-disabled"] = "true"
        form["onsubmit"] = "return false;"

    for el in soup.find_all(["button", "select", "textarea"]):
        if modal and el in modal.descendants:
            continue
        t = (el.get("type") or "").lower()
        if t in ("radio", "checkbox", "reset"):
            continue
        if el.get("data-section"):  # view-switcher tabs (archiv, etc.) — leave functional
            continue
        el["disabled"] = "disabled"
        el["data-preview-disabled"] = "true"

    for el in soup.find_all("input"):
        if modal and el in modal.descendants:
            continue
        t = (el.get("type") or "text").lower()
        if t in ("hidden", "radio", "checkbox"):
            continue
        el["disabled"] = "disabled"
        el["data-preview-disabled"] = "true"


def _block_admin_links(soup: BeautifulSoup) -> None:
    """Replace links to admin/write routes with modal triggers."""
    admin_prefixes = ("/admin", "/logout", "/approve", "/guild/build/items")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(href.startswith(p) for p in admin_prefixes):
            del a["href"]
            a["data-admin-blocked"] = "true"
            a["style"] = (a.get("style", "") + "; cursor: pointer;").lstrip("; ")


def _rewrite_links(soup: BeautifulSoup) -> None:
    """Rewrite internal links to /preview/... equivalents; block unknown internal links."""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("http://", "https://", "mailto:", "#", "javascript:")):
            continue  # External or fragment — leave untouched
        # Strip query strings for matching
        path = href.split("?")[0].rstrip("/") or "/"
        if path in LINK_MAP:
            a["href"] = LINK_MAP[path]
        elif href.startswith("/"):
            # Internal link with no preview equivalent → block
            del a["href"]
            a["data-admin-blocked"] = "true"
            a["style"] = (a.get("style", "") + "; cursor: pointer;").lstrip("; ")


def _replace_dashboard_btn(soup: BeautifulSoup) -> None:
    """Replace 'Dashboard →' nav button with 'Browse Preview →'."""
    for a in soup.find_all("a", href=True):
        if "/dashboard" in a.get("href", "") or (a.get_text(strip=True) in ("Dashboard →", "Dashboard")):
            a["href"] = "/preview/curator/briefing.html"
            a.string = "Browse Preview →"


EXPAND_ONCLICK_PATTERNS = ("toggleSection", "showMore", "ShowMore", "showAll", "expandAll", "dailyShowMore",
                           "toggleEditRow", "toggleHistRow")

def _block_expand_controls(soup: BeautifulSoup) -> None:
    """Block JS-driven expand/show-all controls — replace with admin-blocked modal trigger."""
    for el in soup.find_all(onclick=True):
        onclick = el.get("onclick", "")
        if any(p in onclick for p in EXPAND_ONCLICK_PATTERNS):
            del el["onclick"]
            el["data-admin-blocked"] = "true"
            el["style"] = (el.get("style", "") + "; cursor: pointer;").lstrip("; ")
            if el.get("href") in ("#", ""):
                del el["href"]


# Buttons that navigation/reading needs to stay enabled in Lesen preview
LESEN_NAV_BUTTON_IDS = {
    "btn-back-liste", "btn-art-prev", "btn-art-next",
    "btn-mehr", "btn-weniger", "btn-laden", "btn-vorlesen",
    "btn-close-popover",   # must be enabled so user can dismiss translation popover
    "btn-artikel-laden",   # refresh articles — works via pre-cached data
}


def _process_lesen_page(soup: BeautifulSoup, lesen_data: dict) -> None:
    """Enable full Lesen reading experience in preview using pre-captured article data."""
    # Un-block category cards and article rows — they work via injected data
    for card in soup.find_all("div", class_=lambda c: c and "lesen-cat-card" in c):
        card.attrs.pop("data-admin-blocked", None)
    for row in soup.find_all("div", class_=lambda c: c and "lesen-article-row" in c):
        row.attrs.pop("data-admin-blocked", None)

    # Re-enable navigation/reading buttons that _disable_writes disabled
    for btn_id in LESEN_NAV_BUTTON_IDS:
        el = soup.find(id=btn_id)
        if el and el.get("disabled"):
            del el["disabled"]
    # Day-toggle buttons
    for btn in soup.find_all(class_=lambda c: c and "day-btn" in c):
        btn.attrs.pop("disabled", None)

    # Inject pre-captured article data and auto-select first category
    if lesen_data:
        data_json = json.dumps(lesen_data, ensure_ascii=False)
        first_cat = next(iter(lesen_data), "alltag")
        script = f"""<script id="preview-lesen-data">
window._previewLesenData = {data_json};
document.addEventListener('DOMContentLoaded', function() {{
  if (typeof selectCategory === 'function') selectCategory('{first_cat}');
}});
</script>"""
        body = soup.find("body")
        if body:
            body.append(BeautifulSoup(script, "html.parser"))


def _process_leanings_page(soup: BeautifulSoup, leanings_data: list) -> None:
    """Inject pre-captured leanings into <head> so fetch interceptor can serve them."""
    if not leanings_data:
        return
    data_json = json.dumps(leanings_data, ensure_ascii=False)
    script = f"""<script id="preview-leanings-data">
window._previewLeaningsData = {data_json};
</script>"""
    head = soup.find("head")
    if head:
        head.append(BeautifulSoup(script, "html.parser"))


def _process_reading_room_page(soup: BeautifulSoup, library_data: dict) -> None:
    """Inject pre-captured library data into <head> so fetch interceptor can serve it.

    Must be in <head> because loadData() runs inline (not in DOMContentLoaded)
    and the interceptor checks window._previewLibraryData synchronously.
    """
    if not library_data:
        return
    data_json = json.dumps(library_data, ensure_ascii=False)
    script = f"""<script id="preview-library-data">
window._previewLibraryData = {data_json};
</script>"""
    head = soup.find("head")
    if head:
        head.append(BeautifulSoup(script, "html.parser"))


def _process_scans_dives_page(soup: BeautifulSoup) -> None:
    """Remove row-hidden from all thread/article rows so all content is visible."""
    for el in soup.find_all(class_=lambda c: c and "row-hidden" in c):
        classes = el.get("class", [])
        el["class"] = [c for c in classes if c != "row-hidden"]
    # Un-block the show-all toggle buttons so JS can run
    for btn_id in ("dives-toggle", "scans-toggle"):
        el = soup.find(id=btn_id)
        if el:
            el.attrs.pop("data-admin-blocked", None)
            el.attrs.pop("data-preview-disabled", None)
            el.attrs.pop("disabled", None)


def _inject_whats_running_links(soup: BeautifulSoup) -> None:
    """On the landing page, wrap domain names in What's running with preview links."""
    domain_links = {
        "Curator":     "/preview/curator/briefing.html",
        "Mein Deutsch": "/preview/german/lesen.html",
        "Guild":       "/preview/guild/briefing.html",
    }
    for strong in soup.find_all("strong"):
        text = strong.get_text()
        for keyword, href in domain_links.items():
            if text.startswith(keyword):
                a = soup.new_tag("a", href=href)
                a["style"] = "color: inherit; text-decoration: underline; text-decoration-color: rgba(198,138,94,0.5);"
                strong.wrap(a)
                break


def _apply_career_aggregate(soup: BeautifulSoup) -> None:
    """Option A: replace opportunity pipeline table with aggregate counts only."""
    # Find any table or section with opportunity rows
    tables = soup.find_all("table")
    for table in tables:
        # Count rows (minus header)
        rows = table.find_all("tr")
        data_rows = len(rows) - 1 if rows else 0
        if data_rows > 0:
            placeholder = soup.new_tag("div")
            placeholder["style"] = (
                "padding: 16px; background: rgba(245,240,232,0.04); "
                "border: 1px solid rgba(221,214,200,0.3); border-radius: 6px; "
                "font-size: 13px; color: rgba(245,240,232,0.6); "
                "font-family: 'Source Sans 3', system-ui, sans-serif;"
            )
            placeholder.string = f"{data_rows} opportunities tracked. Detail available with guest access."
            table.replace_with(placeholder)
            break  # Replace first substantial table only


def _asset_local_name(url_path: str) -> str:
    """Derive a collision-safe local filename from an asset URL path."""
    clean = url_path.split("?")[0]
    static_idx = clean.find("/static/")
    relative = clean[static_idx + len("/static/"):] if static_idx >= 0 else clean.lstrip("/")
    return relative.replace("/", "_")


def _localize_assets(soup: BeautifulSoup, context, assets_dir: Path) -> None:
    """Download /app/... CSS and images; rewrite paths to /preview/assets/..."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, str] = {}

    targets = []
    for tag in soup.find_all("link", rel=lambda r: r and "stylesheet" in r, href=True):
        if tag["href"].startswith(("/app/", "/static/")):
            targets.append((tag, "href", tag["href"]))
    for tag in soup.find_all("img", src=True):
        if tag["src"].startswith(("/app/", "/static/")):
            targets.append((tag, "src", tag["src"]))
    # Inline style background-image: url('/app/...') or url('/static/...')
    for tag in soup.find_all(style=True):
        for match in re.finditer(r"url\(['\"]?((?:/app/|/static/)[^)'\"]+)['\"]?\)", tag.get("style", "")):
            targets.append((tag, "_style", match.group(1)))

    for tag, attr, url_path in targets:
        if url_path in downloaded:
            if attr == "_style":
                tag["style"] = tag["style"].replace(url_path, downloaded[url_path])
            else:
                tag[attr] = downloaded[url_path]
            continue
        filename = _asset_local_name(url_path)
        local_file = assets_dir / filename
        try:
            resp = context.request.get(f"{PORTAL_URL}{url_path}")
            if resp.ok:
                local_file.write_bytes(resp.body())
                preview_path = f"/preview/assets/{filename}"
                downloaded[url_path] = preview_path
                if attr == "_style":
                    tag["style"] = tag["style"].replace(url_path, preview_path)
                else:
                    tag[attr] = preview_path
        except Exception as exc:
            print(f"\n  [asset error] {url_path}: {exc}", flush=True)


def process_page(html: str, page: dict, captured_at: str, extra: dict | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove any existing <base> tags that could mess up relative paths
    for base in soup.find_all("base"):
        base.decompose()

    _inject_banner(soup, captured_at)
    _inject_modal(soup)
    _disable_writes(soup)
    _block_expand_controls(soup)
    _block_admin_links(soup)
    _rewrite_links(soup)
    _replace_dashboard_btn(soup)

    if page.get("is_index"):
        _inject_whats_running_links(soup)

    if page.get("career_aggregate"):
        _apply_career_aggregate(soup)

    if page.get("name") == "lesen":
        _process_lesen_page(soup, (extra or {}).get("lesen_data", {}))

    if page.get("name") == "leanings":
        _process_leanings_page(soup, (extra or {}).get("leanings_data", []))

    if page.get("name") == "reading_room":
        _process_reading_room_page(soup, (extra or {}).get("library_data", {}))

    if page.get("name") == "scans_dives":
        _process_scans_dives_page(soup)

    return str(soup)


# ── Capture ────────────────────────────────────────────────────────────────────

def capture_all(password: str = "") -> dict:
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {"captured_at": captured_at, "pages": [], "failures": []}

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()

        # ── Auth via localhost-only capture route ──────────────────────────
        print(f"  Authenticating via /capture-auth...", end=" ", flush=True)
        page.goto(f"{PORTAL_URL}/capture-auth")
        if page.inner_text("body").strip() != "ok":
            # Fallback: password login
            if password:
                page.goto(f"{PORTAL_URL}/login")
                page.fill('input[name="username"]', USERNAME)
                page.fill('input[name="password"]', password)
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle")
                if "/login" in page.url:
                    print("FAILED — check password")
                    browser.close()
                    return manifest
            else:
                print("FAILED — /capture-auth returned non-ok (not localhost?)")
                browser.close()
                return manifest
        print("ok")

        # ── Capture each page ──────────────────────────────────────────────
        for pg in PAGES:
            url = f"{PORTAL_URL}{pg['path']}"
            if pg.get("out_path"):
                out_file = PREVIEW_DIR / pg["out_path"]
            else:
                out_file = PREVIEW_DIR / pg["domain"] / f"{pg['name']}.html"
            out_file.parent.mkdir(parents=True, exist_ok=True)

            print(f"  {pg['label']:<40}", end=" ", flush=True)
            try:
                page.goto(url, timeout=30_000)
                page.wait_for_load_state("networkidle", timeout=15_000)

                # Reading Room: wait for JS-rendered table rows
                if pg["name"] == "reading_room":
                    try:
                        page.wait_for_selector("#table-body tr", timeout=8_000)
                    except Exception:
                        pass  # capture whatever loaded

                # Scans & Dives: wait for JS-rendered rows
                if pg["name"] == "scans_dives":
                    try:
                        page.wait_for_selector(".row.row-thread", timeout=8_000)
                    except Exception:
                        pass

                extra: dict = {}
                # Lesen: fetch article data for all categories via authenticated page context
                if pg["name"] == "lesen":
                    try:
                        lesen_data = {}
                        for cat in ["alltag", "kultur", "politik", "wien"]:
                            result = page.evaluate(f"""async () => {{
                                const res = await fetch('/app/german/api/lesen-category?category={cat}');
                                return res.ok ? await res.json() : null;
                            }}""")
                            if result and "articles" in result:
                                lesen_data[cat] = result["articles"]
                        extra["lesen_data"] = lesen_data
                    except Exception as exc:
                        print(f"\n  [lesen-data error] {exc}", end="", flush=True)

                # Leanings: capture leanings list so fetch interceptor can serve it
                if pg["name"] == "leanings":
                    try:
                        result = page.evaluate("""async () => {
                            const res = await fetch('/api/research/leanings');
                            return res.ok ? await res.json() : null;
                        }""")
                        if result and result.get("ok"):
                            extra["leanings_data"] = result.get("leanings", [])
                            print(f"\n  [leanings-data] {len(extra['leanings_data'])} leanings captured", end="", flush=True)
                    except Exception as exc:
                        print(f"\n  [leanings-data error] {exc}", end="", flush=True)

                # Reading Room: capture library data so fetch interceptor can serve it
                if pg["name"] == "reading_room":
                    try:
                        result = page.evaluate("""async () => {
                            const res = await fetch('/api/library');
                            return res.ok ? await res.json() : null;
                        }""")
                        if result:
                            extra["library_data"] = result
                            print(f"\n  [library-data] {len(result.get('articles', []))} articles captured", end="", flush=True)
                    except Exception as exc:
                        print(f"\n  [library-data error] {exc}", end="", flush=True)

                html = page.content()
                # Download /app/... assets and rewrite to local /preview/assets/
                _soup = BeautifulSoup(html, "html.parser")
                _localize_assets(_soup, context, ASSETS_DIR)
                processed = process_page(str(_soup), pg, captured_at, extra=extra)
                out_file.write_text(processed, encoding="utf-8")

                preview_path = f"/preview/{pg['out_path']}" if pg.get("out_path") else f"/preview/{pg['domain']}/{pg['name']}.html"
                manifest["pages"].append({
                    "domain": pg["domain"],
                    "name": pg["name"],
                    "label": pg["label"],
                    "live_path": pg["path"],
                    "preview_path": preview_path,
                })
                print("✓")
            except Exception as exc:
                print(f"✗  {exc}")
                manifest["failures"].append({"page": pg["name"], "error": str(exc)})

        browser.close()

    # ── Write manifest ─────────────────────────────────────────────────────
    manifest_path = PREVIEW_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return manifest


def _write_index(manifest: dict) -> None:
    """Write a simple navigation index at /preview/index.html."""
    dt = datetime.fromisoformat(manifest["captured_at"].replace("Z", "+00:00"))
    month_label = dt.strftime("%B %Y")

    rows = ""
    for pg in manifest["pages"]:
        rows += f'<li><a href="{pg["preview_path"]}">{pg["label"]}</a></li>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>mini-moi — Preview</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600&family=Source+Sans+3:wght@400;500&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Source Sans 3', system-ui, sans-serif; background: #1a1208; color: #F5F0E8; padding: 48px 32px; max-width: 600px; margin: 0 auto; }}
    h1 {{ font-family: 'Playfair Display', serif; font-weight: 400; font-size: 2rem; margin-bottom: 6px; }}
    .sub {{ color: rgba(245,240,232,0.5); font-size: 13px; margin-bottom: 32px; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ margin-bottom: 10px; }}
    a {{ color: #C68A5E; text-decoration: none; font-size: 14px; }}
    a:hover {{ text-decoration: underline; }}
    .request {{ margin-top: 40px; padding: 16px 20px; border: 1px solid rgba(198,138,94,0.3); border-radius: 6px; }}
    .request p {{ color: rgba(245,240,232,0.65); font-size: 13px; margin: 0 0 10px; }}
  </style>
</head>
<body>
  <h1>mini-moi</h1>
  <p class="sub">Preview snapshot — {month_label} &nbsp;·&nbsp; Data is real but frozen</p>
  <ul>
{rows}  </ul>
  <div class="request">
    <p>Want to explore the live platform?</p>
    <a href="/contact">Request guest access →</a>
  </div>
</body>
</html>"""

    index_path = PREVIEW_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    print(f"  index.html written → {index_path}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    password = os.environ.get("PORTAL_PASSWORD", "")  # optional fallback

    print(f"\ncapture_snapshot.py — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Portal: {PORTAL_URL}")
    print(f"Output: {PREVIEW_DIR}\n")

    manifest = capture_all(password)

    total = len(manifest["pages"])
    failures = len(manifest["failures"])
    print(f"\n{'─'*50}")
    print(f"Captured {total} pages, {failures} failures")
    if failures:
        print("Failures:")
        for f in manifest["failures"]:
            print(f"  {f['page']}: {f['error']}")
    print(f"manifest.json → {PREVIEW_DIR / 'manifest.json'}")
    print(f"\nNext: git add static/public/preview/ && git push")
