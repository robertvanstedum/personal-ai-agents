# 📊 Unified Briefing Platform

## Created: Feb 19, 2026

All pages now share a consistent design language and navigation system.

---

## Core Pages

### 1. **Morning Briefing** (`curator_briefing.html`)
- **Format:** Table layout (Bloomberg Terminal style)
- **Columns:** # | Category | Source | Title | Time | Score
- **Navigation:** Archive | Top 20 | Deep Dives
- **Features:**
  - 20 curated articles in scannable rows
  - Gray gridlines for clear separation
  - Alternating row backgrounds
  - Hover highlight

### 2. **Archive Index** (`curator_index.html`)
- **Format:** Table of past briefings
- **Columns:** Date | Day | Articles | Action
- **Navigation:** Today's Briefing | Top 20 | Deep Dives
- **Features:**
  - Chronological list of all briefings
  - Quick access to any day
  - Article counts visible

### 3. **Deep Dive Index** (`interests/2026/deep-dives/index.html`)
- **Format:** Table of analyses
- **Columns:** Date | Source | Title | Action
- **Navigation:** Today's Briefing | Archive | Top 20
- **Features:**
  - All deep dive analyses listed
  - Searchable by date/source

### 4. **Deep Dive Articles** (individual analysis pages)
- **Format:** Document layout with navigation bar
- **Navigation:** Today's Briefing | Archive | Deep Dives Index
- **Features:**
  - Compact yellow interest box
  - Concise analysis (sections 1-6)
  - Prominent bibliography with proper citations
  - Clean typography

---

## Unified Design Elements

### **Header Style** (all pages)
```css
- Purple gradient: #667eea → #764ba2
- Centered layout
- Title: 1.5em, bold
- Metadata: 0.88em, separated by bullets
- 12px padding
```

### **Navigation Buttons**
```css
- Purple background: #667eea
- Hover: #5568d3
- 6-8px padding
- 0.85-0.9em font
- Consistent icons: 📰 📚 🔍 🔝
```

### **Table Format** (briefing/archives/deep dives)
```css
- White background
- Gray header: #f8f9fa
- Border lines: #ddd (visible separation)
- Alternating rows: #fafafa
- Hover: #f0f4ff
- 12px cell padding
- 14px base font
```

### **Typography**
```css
- Font family: -apple-system, BlinkMacSystemFont, Segoe UI
- Base size: 14px
- Headings: 1.0-1.5em
- Metadata: 0.85-0.95em
- Line height: 1.35-1.6
```

### **Color Palette**
- **Primary:** #667eea (purple)
- **Hover:** #5568d3 (darker purple)
- **Background:** #f5f5f5 (light gray)
- **Cards:** #ffffff (white)
- **Borders:** #ddd (medium gray)
- **Text:** #333 (dark gray)
- **Meta:** #666, #888, #999 (gray scale)

---

## Navigation Flow

```
Archive Index (curator_index.html)
    ↓
Morning Briefing (curator_briefing.html)
    ↓
Deep Dive Index (interests/2026/deep-dives/index.html)
    ↓
Individual Analysis (90610-futures-slide-as-iran-war-risks-add-to-growing-ai.html)
```

All pages can navigate to any other page via buttons.

---

## File Locations

```
~/Projects/personal-ai-agents/
├── curator_index.html              # Archive index (UPDATED)
├── curator_briefing.html           # Today's briefing (UPDATED - table format)
├── curator_latest_with_buttons.html # Top 20 with feedback buttons
├── curator_archive/
│   └── curator_2026-02-*.html     # Daily archives (TODO: apply table format)
└── interests/2026/deep-dives/
    ├── index.html                  # Deep dive index (UPDATED)
    └── 90610-*.html               # Individual analyses (UPDATED)
```

---

## TODO (Optional Future Work)

1. **Apply table format to archived briefings** in `curator_archive/`
2. **Auto-generate deep dive index** from directory scan
3. **Add search functionality** across all pages
4. **RSS feed** for new briefings/deep dives
5. **Dark mode** toggle

---

## Code Templates

### **Header Template** (copy to new pages)
```html
<div class="header">
    <h1>🧠 Your Page Title</h1>
    <div>
        <span class="header-meta">Metadata 1</span>
        <span class="header-meta">•</span>
        <span class="header-meta">Metadata 2</span>
    </div>
</div>
```

### **Navigation Template**
```html
<div class="nav-buttons">
    <a href="curator_briefing.html" class="nav-btn">📰 Today's Briefing</a>
    <a href="curator_index.html" class="nav-btn">📚 Archive</a>
    <a href="interests/deep-dives/index.html" class="nav-btn">🔍 Deep Dives</a>
</div>
```

### **Table Template**
```html
<div class="briefing-table">
    <table>
        <thead>
            <tr>
                <th>Column 1</th>
                <th>Column 2</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>Data 1</td>
                <td>Data 2</td>
            </tr>
        </tbody>
    </table>
</div>
```

---

**Platform Status:** ✅ UNIFIED

All pages now share consistent styling, navigation, and information architecture.
