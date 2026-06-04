#!/usr/bin/env python3
"""
Screenshot capture tool for mini-moi UI documentation.

Usage:
    python3 tools/capture_screenshots.py curator
    python3 tools/capture_screenshots.py german
    python3 tools/capture_screenshots.py all

Run before/after UI commits to document the state of the UI.
Archives previous current/ screenshots to archive/YYYY-MM-DD/ first.
"""

import sys
import shutil
from pathlib import Path
from datetime import date
from playwright.sync_api import sync_playwright
import time

# ── paths ──────────────────────────────────────────────────────────────────

REPO_ROOT        = Path(__file__).parent.parent
DOCS_SCREENSHOTS = REPO_ROOT / "docs" / "screenshots"
WORKING          = REPO_ROOT / "_working"

# ── page definitions ───────────────────────────────────────────────────────

CURATOR_PAGES = {
    "briefing":           "http://localhost:8766/",
    "library":            "http://localhost:8766/library",
    "observations":       "http://localhost:8766/intelligence",
    "focus":              "http://localhost:8766/priorities",
    "research_dashboard": "http://localhost:8766/research/dashboard",
    "scans_index":        "http://localhost:8766/interests/2026/scans/index.html",
}

GERMAN_PAGES = {
    "landing":   "http://localhost:8767/",
    "lesen":     "http://localhost:8767/lesen",
    "schreiben": "http://localhost:8767/schreiben",
    "gesprache": "http://localhost:8767/gesprache",
    "woerter":   "http://localhost:8767/woerter",
    "archiv":    "http://localhost:8767/archiv",
    "admin":     "http://localhost:8767/admin",
}

# ── helpers ────────────────────────────────────────────────────────────────

def archive_current(domain: str):
    """Move current/ screenshots to archive/YYYY-MM-DD/ before new capture."""
    current_dir = DOCS_SCREENSHOTS / domain / "current"
    archive_dir = DOCS_SCREENSHOTS / domain / "archive" / str(date.today())

    if current_dir.exists() and any(current_dir.iterdir()):
        archive_dir.mkdir(parents=True, exist_ok=True)
        for f in current_dir.iterdir():
            shutil.move(str(f), str(archive_dir / f.name))
        print(f"  Archived existing {domain} screenshots → {archive_dir.name}/")
    else:
        current_dir.mkdir(parents=True, exist_ok=True)


def capture_domain(domain: str, pages: dict):
    """Capture all pages for a domain and generate a PDF."""
    print(f"\nCapturing {domain}...")
    archive_current(domain)

    current_dir = DOCS_SCREENSHOTS / domain / "current"
    current_dir.mkdir(parents=True, exist_ok=True)

    captured = []
    failed  = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page(viewport={"width": 1440, "height": 900})

        for name, url in pages.items():
            print(f"  {name}: {url}")
            try:
                page.goto(url, timeout=15000, wait_until="networkidle")
                time.sleep(1)  # let JS settle
                out_path = current_dir / f"{name}.png"
                page.screenshot(path=str(out_path), full_page=True)
                captured.append(str(out_path))
                print(f"    ✓ {name}.png")
            except Exception as e:
                print(f"    ✗ {name} FAILED: {e}")
                failed.append(name)

        browser.close()

    if failed:
        print(f"\n  ⚠️  {len(failed)} page(s) failed to capture: {', '.join(failed)}")

    # ── PDF ────────────────────────────────────────────────────────────────
    if captured:
        try:
            import img2pdf
            working_dir = WORKING / f"{domain}-redesign"
            working_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = working_dir / f"baseline_{date.today()}.pdf"
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(captured))
            print(f"  PDF: {pdf_path.relative_to(REPO_ROOT)}")
        except Exception as e:
            print(f"  PDF skipped: {e}")

    print(f"  Done. {len(captured)}/{len(pages)} pages captured.")
    return captured, failed


# ── main ───────────────────────────────────────────────────────────────────

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    all_failed = []

    if target in ("curator", "all"):
        _, failed = capture_domain("curator", CURATOR_PAGES)
        all_failed.extend(f"curator/{f}" for f in failed)

    if target in ("german", "all"):
        _, failed = capture_domain("german", GERMAN_PAGES)
        all_failed.extend(f"german/{f}" for f in failed)

    print("\nDone.")
    if all_failed:
        print(f"⚠️  Failed pages (server not responding or route changed):")
        for f in all_failed:
            print(f"   {f}")
        print("   Fix these before committing — do not commit partial captures.")
        sys.exit(1)
    else:
        print("All pages captured. Commit docs/screenshots/ with your UI changes.")


if __name__ == "__main__":
    main()
