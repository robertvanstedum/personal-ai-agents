#!/usr/bin/env python3
"""
blur_screenshots.py — Apply privacy blur boxes to curator screenshots for GitHub README.

Usage: python blur_screenshots.py
Output: docs/screenshots/*.png
"""

from PIL import Image, ImageFilter
from pathlib import Path

INPUT  = Path("/Users/vanstedum/Projects/personal-ai-agents")
OUTPUT = Path("/Users/vanstedum/Projects/personal-ai-agents/docs/screenshots")
OUTPUT.mkdir(parents=True, exist_ok=True)

BLUR_RADIUS = 20

def blur_box(img, left_pct, top_pct, right_pct, bottom_pct, radius=BLUR_RADIUS):
    """Blur a region defined as percentage of image dimensions."""
    w, h = img.size
    box = (int(w * left_pct), int(h * top_pct), int(w * right_pct), int(h * bottom_pct))
    region = img.crop(box)
    blurred = region.filter(ImageFilter.GaussianBlur(radius))
    img.paste(blurred, box)
    return img


# ── 1. Morning Briefing ───────────────────────────────────────────────────────
# Columns: #, CATEGORY, SOURCE, TITLE, TIME, SCORE, ACTIONS
# Blur: SOURCE (~19–35%) and TITLE (~35–73%)
# Keep: #, CATEGORY, TIME, SCORE, ACTIONS, nav bar, column headers
img = Image.open(INPUT / "Screenshot - Morning Briefing.jpg")
w, h = img.size
print(f"Morning Briefing: {w}x{h}")

HEADER_BOTTOM = 0.115   # keep nav + column header row

blur_box(img, 0.135, HEADER_BOTTOM, 0.35,  1.0)   # SOURCE (starts at ~13.5%)
blur_box(img, 0.35,  HEADER_BOTTOM, 0.735, 1.0)   # TITLE

img.save(OUTPUT / "morning-briefing.png")
print("  → morning-briefing.png")


# ── 2. Reading Library ────────────────────────────────────────────────────────
# Columns: DATE, TITLE & NOTE, SOURCE, CATEGORY, SCORE, TYPE, ACTIONS
# Blur: TITLE & NOTE (~9–61%), SOURCE (~61–79%)
# Keep: DATE, CATEGORY, SCORE, TYPE, ACTIONS, nav, stats, filter buttons
img = Image.open(INPUT / "Screenshot - Reading Library.jpg")
w, h = img.size
print(f"Reading Library: {w}x{h}")

HEADER_BOTTOM = 0.365   # keep nav, "Your reading library", stats, filters, column headers

blur_box(img, 0.09,  HEADER_BOTTOM, 0.61,  1.0)   # TITLE & NOTE
blur_box(img, 0.61,  HEADER_BOTTOM, 0.785, 1.0)   # SOURCE

img.save(OUTPUT / "reading-library.png")
print("  → reading-library.png")


# ── 3. Deep Dives ─────────────────────────────────────────────────────────────
# Columns: DATE, SOURCE, TITLE, ACTION
# Blur: SOURCE column only (~15.5–30%)
# Keep: DATE, TITLE, "Read Analysis →" buttons, nav, heading
img = Image.open(INPUT / "Screenshot - Deepdives.jpg")
w, h = img.size
print(f"Deep Dives: {w}x{h}")

HEADER_BOTTOM = 0.24   # keep nav, "Deep Dive Archive", "15 deep dives", column headers

blur_box(img, 0.155, HEADER_BOTTOM, 0.275, 1.0)   # SOURCE

img.save(OUTPUT / "deep-dives.png")
print("  → deep-dives.png")


# ── 4. Priorities ─────────────────────────────────────────────────────────────
# Two priority cards, each with keyword tag rows to blur
# Blur: tags under "Tigray Conflict" and tags under "Iran Attack"
# Keep: topic names, +2.0x badges, ACTIVE badges, Matches/Created/Expires, action buttons
img = Image.open(INPUT / "Screenshot - Priorities.jpg")
w, h = img.size
print(f"Priorities: {w}x{h}")

# Tigray Conflict keyword tags (Tigray, Ethiopia, TPLF)
blur_box(img, 0.22, 0.555, 0.52, 0.625)

# Iran Attack keyword tags (missiles, israel, regime change, oil, people of iran)
blur_box(img, 0.22, 0.815, 0.79, 0.885)

img.save(OUTPUT / "priorities.png")
print("  → priorities.png")


print("\nAll done → docs/screenshots/")
