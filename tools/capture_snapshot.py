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
import sys
import json
import getpass
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import sync_playwright

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
PREVIEW_DIR = REPO_ROOT / "minimoi_portal" / "static" / "preview"
PORTAL_URL  = os.environ.get("PORTAL_URL", "http://localhost:5001")
USERNAME    = "robert"

# ── Page manifest ──────────────────────────────────────────────────────────────

PAGES = [
    {"domain": None, "name": "index", "path": "/", "label": "mini-moi — Home", "out_path": "index.html", "is_index": True},
    {"domain": "curator", "name": "briefing",   "path": "/app/curator",              "label": "Curator — Daily Briefing"},
    {"domain": "curator", "name": "scans_dives", "path": "/app/curator/scans-dives", "label": "Curator — Scans & Dives"},
    {"domain": "curator", "name": "archive",    "path": "/app/curator/archive",       "label": "Curator — Archive"},
    {"domain": "german",  "name": "lesen",      "path": "/app/german",                "label": "Mein Deutsch — Lesen"},
    {"domain": "german",  "name": "gesprache",  "path": "/app/german/gesprache",      "label": "Mein Deutsch — Gespräche"},
    {"domain": "german",  "name": "worter",     "path": "/app/german/worter",         "label": "Mein Deutsch — Wörter"},
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
    "/app/curator":             "/preview/curator/briefing.html",
    "/app/curator/scans-dives": "/preview/curator/scans_dives.html",
    "/app/curator/archive":     "/preview/curator/archive.html",
    "/app/german":              "/preview/german/lesen.html",
    "/app/german/gesprache":    "/preview/german/gesprache.html",
    "/app/german/worter":       "/preview/german/worter.html",
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
  color: #C68A5E; text-transform: uppercase; letter-spacing: 0.06em;
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
    <h3>Admin — not available in preview</h3>
    <p>This section is for authenticated users. Request guest access to explore the live platform.</p>
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
    month_label = dt.strftime("%B %Y")

    head = soup.find("head")
    if head:
        head.append(BeautifulSoup(BANNER_CSS, "html.parser"))

    banner_html = f"""<div class="preview-banner">
      <span>Preview snapshot — </span>
      <span class="preview-date">Captured {month_label}</span>
      <span>. Data is real but frozen.</span>
      <a href="/contact" class="preview-request-link">Request live access →</a>
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
    """Disable all form submissions and write-action buttons."""
    for form in soup.find_all("form", method=lambda m: m and m.upper() == "POST"):
        form["data-preview-disabled"] = "true"
        form["onsubmit"] = "return false;"

    for btn in soup.find_all(["button", "input"], type=lambda t: t and t.lower() in ("submit", "button")):
        btn["disabled"] = "disabled"
        btn["data-preview-disabled"] = "true"

    # Disable select dropdowns that trigger state changes
    for sel in soup.find_all("select"):
        sel["disabled"] = "disabled"
        sel["data-preview-disabled"] = "true"


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


def process_page(html: str, page: dict, captured_at: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove any existing <base> tags that could mess up relative paths
    for base in soup.find_all("base"):
        base.decompose()

    _inject_banner(soup, captured_at)
    _inject_modal(soup)
    _disable_writes(soup)
    _block_admin_links(soup)
    _rewrite_links(soup)
    _replace_dashboard_btn(soup)

    if page.get("is_index"):
        _inject_whats_running_links(soup)

    if page.get("career_aggregate"):
        _apply_career_aggregate(soup)

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

                html = page.content()
                processed = process_page(html, pg, captured_at)
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
    print(f"\nNext: git add minimoi_portal/static/preview/ && git push")
