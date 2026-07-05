# Spec #121: Tips UX Redesign — Discrete Contextual Pattern
**File:** `spec_121_tips_ux_redesign_2026-07-05.md`
**Status:** Spec Ready
**Date:** 2026-07-05
**Build queue:** #121
**Author:** Claude.ai design session

---

## Intent

The current tips implementation renders as a full-width unstyled text strip between the nav and page content. It looks like a debug message, disrupts the page layout, and competes with the content it's meant to support. On Gespräche it runs two lines and breaks the visual flow entirely.

The fix is not to remove tips — they are genuinely useful, especially for new users, and the owner likes to check content. The fix is to make them discrete, attractive, and placed in context. The design policy: **small, styled, in context — not a help banner.**

Tips are always visible. No dismiss logic, no persistence, no localStorage. Just well-placed, well-styled, unobtrusive guidance that sits near the relevant UI element rather than above the entire page.

---

## Design Policy

- **Discrete** — small, never the loudest thing on the page
- **Attractive** — styled to match the domain nav bar, not a raw text strip
- **In context** — tab-specific content, changes based on current tab
- **Always visible** — no dismiss, no hide, no persistence logic needed
- **Never blocking** — does not push content down or interrupt page flow

---

## Current Problem

Tips render as:
```
💡 [full tip text spanning entire page width, left-aligned, unstyled]
```
Positioned between the domain nav bar and the page content. Wrong on every dimension — width, position, styling, and placement relative to relevant UI.

---

## Target Treatment

### Primary placement — domain nav bar, top right

The tip icon lives in the domain nav bar (the brown bar for German, dark green bar for Portuguese), right-aligned before "Robert / Sign out". This space is currently clear on all tabs.

- **Icon only** at rest — small 💡 or minimal ℹ, ~14px, styled to nav bar palette
- **On hover/tap:** small popover appears with tip text for the current tab, dismisses on click away
- **Content is tab-specific** — the same icon location shows different tip text depending on which tab is active
- **Color:** muted amber/parchment on German dark brown nav; muted terracotta/parchment on Portuguese dark green nav; muted tone on Curator nav
- **Position:** right side of nav bar, before "Robert / Sign out"
- **Never competes** with the domain title (Mein Deutsch / meu português) or tab links

### Secondary placement — in-page context (non-landing tabs)

For tabs where additional inline guidance helps (Gespräche prompt area, Schreiben mode pills, etc.), a small inline tip may also appear near the relevant UI element. This is secondary and optional — nav bar tip is always the primary.

---

## Tab-Specific Tip Content

Content shown in the nav bar popover changes per active tab:

### German

| Tab | Tip text |
|---|---|
| Landing | Conversation first — eight Vienna personas, real scenarios. Lesen, Schreiben, and Wörter reinforce what you practice in Gespräche. |
| Lesen | Best on desktop — read, hover to translate, and save words to Wörter in one flow. On mobile, tap and hold to translate. |
| Lesen (article) | Hover a word to translate. Save to Wörter with one click. Use Notas to write a reaction — then Corrigir for feedback. |
| Gespräche | Use in-app on any device, or copy the prompt into Grok, ChatGPT, Gemini, or Claude on your phone for faster, more natural voice. |
| Schreiben | Three modes — free write, prompted, and vocabulary-driven. Best on desktop for the full correction experience. |
| Wörter | Filter by origin or status, add words manually, or export to Anki. Switch to Treino mode to drill what you've saved. |
| Archiv | Your full session history — conversations, writing, and reading. Use it to track consistency and revisit good sessions. |

### Portuguese

| Tab | Tip text |
|---|---|
| Landing | Conversation first — Brazilian personas from padaria to praia. Leitura, Escrita, and Palavras keep it grounded in real Rio life. |
| Leitura | Best on desktop — read, hover to translate, and save words to Palavras in one flow. On mobile, tap and hold to translate. |
| Leitura (article) | Hover a word to translate. Save to Palavras with one click. Use Notas to write a reaction — then Corrigir for feedback. |
| Conversas | Use in-app on any device, or copy the prompt into Grok, ChatGPT, Gemini, or Claude on your phone for faster, more natural voice. |
| Escrita | Three modes — free write, prompted, and vocabulary-driven. Best on desktop for the full correction experience. |
| Palavras | Filter by origin or status, add words manually, or export to Anki. Switch to Treino mode to drill what you've saved. |
| Arquivo | Your full session history — conversations, writing, and reading. Use it to track consistency and revisit good sessions. |

### Curator

**Claude Code: verify actual tab/view names before wiring. Names below are approximate.**

| Tab/View | Tip text |
|---|---|
| Briefing (main) | Your daily briefing — curated signals across geopolitics, finance, and technology. Use the category filters to focus, or read straight through. |
| Scan | Quick pass through today's top stories — scored and ranked. Click into any article to go deeper. |
| Dive | Full article with AI observations and counterpoints. Use this when a story deserves more attention. |
| Archive | Your reading history — articles you've opened or saved. Use it to revisit stories or track what you've been following. |

---

## Secondary In-Page Tip — Gespräche / Conversas Only

The voice/native app guidance is important enough to also appear inline near the prompt action buttons (Copiar prompt / Telegram / Como .txt row) — not just in the nav bar popover. Small icon + one line of text, left-aligned with the button row. This is the one case where both nav bar and inline tip coexist.

All other tabs: nav bar tip only. Remove existing full-width banner entirely.

---

## tips.json additions needed

Add Curator slots to `config/curator/tips.json`:
```json
"curator.scan": {
  "active": true,
  "text": "Quick pass through today's top stories — scored and ranked. Click into any article to go deeper."
},
"curator.dive": {
  "active": true,
  "text": "Full article with AI observations and counterpoints. Use this when a story deserves more attention."
},
"curator.archive": {
  "active": true,
  "text": "Your reading history — articles you've opened or saved. Use it to revisit stories or track what you've been following."
}
```

`briefing.main` already exists — keep content, restyle only.

Update `tips_slot_registry.md` changelog and slot table with new entries.

---

## Implementation Notes

- Remove the current full-width banner render from ALL domains — German, Portuguese, Curator
- Nav bar icon: right-aligned in the domain nav bar, before "Robert / Sign out"
- Tab-specific content: server passes current tab context alongside tip text, or JS reads active tab
- Landing hover/tap popover: only interactive element — minimal JS
- All other tabs: popover on hover/tap of nav bar icon
- Gespräche/Conversas: additional inline tip near prompt buttons (secondary, always visible)
- Tips still read from `config/curator/tips.json` via `_load_tip()` — no change to data layer
- Styling via CSS, domain palette CSS variables
- Claude Code verifies Curator tab names before wiring
- Robert reviews on dev before any EC2 push

---

## Definition of Done

- [ ] Full-width banner strip removed from all German tabs
- [ ] Full-width banner strip removed from all Portuguese tabs  
- [ ] Full-width banner strip removed from all Curator tabs
- [ ] Nav bar tip icon renders top-right in domain nav bar, all three domains
- [ ] Tip content is tab-specific — correct text per active tab
- [ ] Popover appears on hover/tap, dismisses on click away
- [ ] Gespräche/Conversas: additional inline tip near prompt action buttons
- [ ] Curator Scan, Dive, Archive tips wired with verified tab names
- [ ] tips.json updated with three new Curator slots
- [ ] tips_slot_registry.md updated with new slots and changelog entry
- [ ] Styled to domain nav bar palette — not raw text
- [ ] Tested on desktop and mobile, all tabs, all three domains
- [ ] Robert reviews on dev before any EC2 push

---

## Commit

```
feat(tips): redesign tip display — nav bar icon, tab-specific popovers (#121)

- Remove full-width banner from German, Portuguese, and Curator
- Nav bar tip icon top-right, tab-specific content via popover
- Gespräche/Conversas: additional inline tip near prompt buttons
- Curator Scan, Dive, Archive tips added and wired
- tips.json: three new curator slots
- tips_slot_registry.md updated
```

---

## Notes for Grok Review

- Always-visible icon is intentional — no dismiss/persistence logic, keeps implementation simple
- Tab-specific content requires either server-side tab context or JS tab detection — Claude Code decides implementation approach
- Curator tab names must be verified before wiring — names in spec are approximate
- Gespräche/Conversas is the only tab with both nav bar tip and inline tip — intentional, voice guidance is important enough to surface twice
- Portuguese and German nav bar placement must be visually identical, palette-adjusted only

---

*Spec · 2026-07-05 · Claude.ai design session · Status: Spec Ready*
