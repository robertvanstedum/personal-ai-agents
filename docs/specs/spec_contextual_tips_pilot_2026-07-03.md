# Spec: Contextual Tips System — Curator Briefing Pilot
*Created: 2026-07-03 · Claude.ai (design) → Claude Code (build)*

---

## Intent

mini-moi is a personal system built for Robert and a small circle of
trusted users — family members and occasional guests. Most of these users
are not technical and have no prior context for what they're looking at.
Features like the Curator briefing, the Scan function, and voice practice
in Gespräche are not self-explanatory. Without guidance, a new user lands
on the briefing page and doesn't know what to do, or runs a Scan and
doesn't understand what they're paying for or why.

A formal help section won't be read. What's needed is lightweight,
in-context guidance that appears exactly where the user is, explains what
the feature does in plain language, and disappears when it's no longer
needed — without any user action required.

The tips system also serves Robert directly: transitory guidance (like
"voice works better on Grok mobile right now due to API latency") can be
added and retired as the technology changes, without a code deployment.
The system evolves with the product rather than becoming stale documentation.

This is not a help system. It is a living editorial layer on top of the UI.

---

## Overview

A backend-driven contextual tips system. Tips appear when active, vanish
completely when not. No user-dismissed state. No empty containers. Same
content for all users (owner, family, guest). Text is updated by editing
a JSON file — no code deploy needed.

**Pilot scope: Curator briefing page only.** One location, one tip slot.
Validate the pattern before rolling to other pages/domains.

---

## Architecture

### Tips file
`domains/curator/data/tips.json`

```json
{
  "briefing.main": {
    "active": true,
    "text": "placeholder — update before launch"
  }
}
```

One key per location. `active: false` = tip element not rendered at all
(not hidden with CSS — simply not in the DOM). Updating this file takes
effect on next page load, no restart needed if the template reads it fresh
each request.

### Adding tips later
New locations added as new keys. Same file. Same pattern. Roll to other
pages only after pilot is validated.

---

## UI placement — Curator briefing

**Location:** above the article list, below the subnav bar, left-aligned.

**Style:** subtle — small text, muted color, 💡 prefix. Does not compete
with article content. Inline with the content column, not a sidebar.

```
[subnav: Daily | ...]
─────────────────────────────────────────
💡 [tip text here]
─────────────────────────────────────────
[article 1]
[article 2]
...
```

**When active = false:** the tip element is not rendered. No empty space,
no placeholder, no border. The article list starts immediately below the
subnav. The layout does not shift.

**CSS target:** minimal — one small `<div class="page-tip">` with:
- `font-size: 0.8rem`
- `color: var(--text-muted)` (uses existing CSS variable)
- `padding: 8px 0 12px 0`
- `border-bottom: 1px solid var(--border)` (matches existing border style)
- No background, no box, no visual weight beyond the text itself

---

## Template change

`templates/curator_briefing.html` — add one block above the article list:

```html
{% if tip %}
<div class="page-tip">💡 {{ tip }}</div>
{% endif %}
```

The route passes `tip=tips.get("briefing.main", {}).get("text") if
tips.get("briefing.main", {}).get("active") else None` to the template.
If `active` is false or the key doesn't exist, `tip` is `None` and the
block is not rendered.

---

## Backend change

`curator_server.py` → briefing route:
- Read `domains/curator/data/tips.json` on each request (cheap, file is
  tiny, no caching needed)
- Pass `tip` to template context as described above
- If file doesn't exist or key is missing, `tip=None` (graceful fallback)

---

## Placeholder content (to be updated before or after launch)

```json
{
  "briefing.main": {
    "active": true,
    "text": "Your daily briefing — ~700 articles scored against your learned profile. Like or Save to teach the system your interests."
  }
}
```

This is the starting placeholder. Robert updates the text directly in the
JSON file when he wants to change it. No spec needed for content updates.

---

## What this spec does NOT include

- Tips on any other page or domain (pilot only)
- Guest-specific vs owner-specific tip content (same for everyone)
- User-dismissed state (tips show until deactivated on the backend)
- Tip categories or types (one format, one location for now)
- Admin UI for editing tips (edit the JSON file directly for now)
- Roll-out to German, Portuguese, Guild (after pilot validated)

---

## Definition of Done

- [ ] `domains/curator/data/tips.json` created with placeholder content
- [ ] Briefing route reads tips.json and passes `tip` to template
- [ ] Tip renders above article list when `active: true`
- [ ] Tip element absent from DOM when `active: false` (not just hidden)
- [ ] Layout does not shift when tip is absent
- [ ] Tested: set `active: true` → tip shows; set `active: false` → tip
      gone, no empty space
- [ ] Tested on mobile viewport (tip wraps cleanly, doesn't break layout)
- [ ] Tested for all tiers (owner, guest) — same tip shown to both

---

## Commit

```
feat: contextual tips system — Curator briefing pilot

- tips.json: backend-driven tip content, active flag controls visibility
- Briefing template: tip rendered above article list when active
- DOM-absent when inactive (no layout shift, no empty container)
- Same tip for all users/tiers
- Placeholder content — update text via tips.json, no deploy needed

Closes #[ISSUE_NUMBER]
```

---

## Next step after validation

Once Robert confirms the placement and behavior work on the Curator briefing,
open a follow-on spec to roll the same pattern to:
- Curator scan button area (scan explanation tip)
- German Gespräche (voice latency tip)
- Portuguese Conversas (same voice tip)
- Lesen/Leitura (reading list tip)

Each follow-on is a template change + one new key in that domain's
tips.json. No architecture changes.

---

*Spec · 2026-07-03 · pilot only · validate before rolling out*
