# Spec Amendment — Guild Roadmap Layout & Typography Fix
*Created: 2026-06-17 — Claude.ai*
*Amends: `spec_guild_roadmap_maturity_2026-06-17.md` and
`spec_guild_roadmap_visual_hierarchy_2026-06-17.md`*
*Status: Ready for `_working/` — build alongside or immediately after
visual hierarchy spec*
*Priority: Ship with the hierarchy spec — these are blocking visual issues*

---

## Problems identified from screenshot

### 1. Title rendering as raw HTML

`h1>mini-moi Roadmap` is showing as literal text — the markdown-to-HTML
renderer is not parsing the `#` heading correctly, or the template is
double-escaping it. The `<h1>` tag is visible in the rendered output.

**Fix:** Check the markdown renderer — the `# mini-moi Roadmap` heading
should render as a styled `<h1>`, not as escaped text. Compare against
how other pages render their `#` headings. This is likely a renderer
configuration issue, not a CSS issue.

### 2. Content too narrow and centered

The roadmap content is constrained to approximately 700px centered,
wasting the full available viewport width. Every other Guild page
(QUEUE, BUILD LOG) uses the full container width.

**Fix:** The roadmap template likely has an extra `max-width` or a
`container` class that the other pages don't use, or is missing the
standard Guild content wrapper class. Check the template against
`guild_queue.html` or `guild_build_log.html` — use the same outer
wrapper class. Remove any roadmap-specific `max-width` constraint.

### 3. Typography doesn't match design system

Body text, headers, and the "How this works" card use different font
weights and styles from the rest of Guild. The card border is too heavy
and the wrong color compared to Guild's parchment aesthetic.

**Fix — apply these from the existing design system:**

```css
/* Body text — match Guild standard */
.roadmap-content {
  font-family: Georgia, serif;   /* headings */
  color: var(--text-primary, #2A1F14);
}

/* Domain headers — match Guild section headers */
.roadmap-domain h2 {
  font-family: Georgia, serif;
  font-size: 1.5rem;
  font-weight: normal;          /* Guild uses normal weight Georgia */
  color: #2A1F14;
  border-bottom: 1px solid rgba(198,138,94,0.3);  /* amber rule, light */
  padding-bottom: 0.5rem;
  margin-bottom: 1rem;
}

/* Cards — match existing Guild card style */
.roadmap-domain {
  background: #F5F0E8;          /* parchment */
  border: 1px solid rgba(198,138,94,0.2);   /* amber, very light */
  border-radius: 4px;
  padding: 1.5rem 2rem;
  margin-bottom: 1.5rem;
}

/* Table — match Guild table style */
.roadmap-content table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.roadmap-content th {
  font-family: system-ui, sans-serif;   /* Guild uses sans for labels */
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgba(42,31,20,0.5);
  border-bottom: 1px solid rgba(198,138,94,0.3);
  padding: 0.5rem 0.75rem;
  text-align: left;
}

.roadmap-content td {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid rgba(198,138,94,0.1);
  color: #2A1F14;
  vertical-align: middle;
}
```

### 4. "How this works" section

Currently rendering as a card with a heavy border. Should render as a
plain parchment section — same background as the page, no heavy border,
just the amber rule below the heading. It's explanatory text, not a
domain block.

**Fix:** Don't apply `.roadmap-domain` card styling to the "How this
works" `##` section. Only apply card styling to the four domain sections
(German, Platform, Curator, Guild) and the Done section. The "How this
works" section renders as plain content with the standard heading style.

---

## What to check against

Open `guild_queue.html` or `guild_build_log.html` and compare:
- Outer wrapper class → use the same on the roadmap template
- Font stack → copy exactly
- Table `th` / `td` styles → copy exactly

The roadmap should look like it belongs on the same page as the build
log, not like a separate embedded document.

---

## Definition of Done

- `# mini-moi Roadmap` renders as a styled `<h1>`, not as literal text
- Roadmap content uses full container width — matches QUEUE and BUILD LOG
  page width
- Domain section headers use Georgia, normal weight, amber underrule
- Domain cards use parchment background (#F5F0E8), light amber border
- Table headers use sans-serif, uppercase, small, muted
- Table rows use correct padding and amber dividers
- "How this works" renders as plain content, not a heavy card
- Body text color matches #2A1F14 throughout
- Verified side-by-side: roadmap and build log use the same outer layout
  and font stack

## Commit

`Roadmap view: fix layout width, typography, and h1 rendering to match
Guild design system.`

---

*Spec Amendment · 2026-06-17 · Claude.ai*
