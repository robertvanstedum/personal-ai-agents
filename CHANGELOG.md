# Changelog

## 2026-02-19 - Platform Unification & UI Consistency

### Major Changes

**Unified Briefing Platform Architecture**
- Replaced card-based layout with table-based layout (Bloomberg Terminal aesthetic)
- Unified header across all pages (1.5em, purple gradient, centered)
- Consistent navigation on every page: 📰 Today | 📚 Archive | 🔍 Deep Dives
- Fixed navigation flow between all three core pages

**Files Modified:**
- `curator_rss_v2.py`:
  - Rewrote `format_html()` to generate table format (was card format)
  - Fixed rank numbering: `for i, entry in enumerate(entries, 1)` → `rank = i`
  - Fixed field mappings: `category_tag` → `category`, `url` → `link`
  - Rewrote `generate_index_page()` with unified header (1.5em, not 2.5em)
  - Fixed deep dives path: `interests/deep-dives/` → `interests/2026/deep-dives/`

- `curator_feedback.py`:
  - Updated deep dive prompt for concise "point-of-departure" format
  - Sections 1-6: Brief (2-3 sentences max)
  - Section 7 (Bibliography): Most detailed with proper citations
  - Fixed `\1` bug in HTML generation (regex backreference: `r'\1'` → `'\\1'`)
  - Added unified header and navigation bar to deep dive articles
  - Reduced yellow interest box padding/font size
  - Reduced back button size for consistency

**Deep Dive Cost Optimization:**
- Output tokens: 2995 → 977 (67% reduction)
- Cost per analysis: $0.047 → $0.017 (64% cheaper)
- Quality: Improved (concise research launchpad vs verbose explanations)

**New Files:**
- `PLATFORM_POC.md` - Complete platform overview and usage guide
- `PLATFORM_UNIFIED.md` - Design system specifications
- `curator_cache/` - Article storage for deep dive system (hash-based)
- `curator_history.json` - Article index with appearance tracking
- `interests/2026/deep-dives/` - New deep dive storage location
- `interests/2026/deep-dives/index.html` - Deep dive archive index

### Bug Fixes
- Fixed archive header size (was 2.5em, now 1.5em for consistency)
- Fixed rank numbers showing "?" instead of 1-20
- Fixed deep dive path inconsistencies across navigation
- Fixed browser caching issue with `curator_latest_with_buttons.html`
- Fixed navigation back button on all pages

### Design System
- Base font: 14px (System fonts)
- Header: 1.5em title, 0.88em metadata, 12px padding
- Navigation buttons: 6-14px padding, 0.85em font, purple (#667eea)
- Tables: 12px cell padding, #ddd borders, alternating row backgrounds
- Max width: 1400px (consistent across all pages)
- Colors: Purple gradient (#667eea → #764ba2)

### Status
✅ POC Complete - Consistent navigation flow across all pages
✅ All pages use identical header/navigation structure
✅ Table format generates correctly from curator_rss_v2.py
✅ Deep dive format optimized (cost -64%, quality improved)

### Next Steps (Future)
- UI polish (fine-tune colors, spacing)
- Deep dive bookmark action implementation
- CLI history viewer
- Telegram button integration
