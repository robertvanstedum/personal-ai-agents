# Handoff — Gespräche Mobile: Button Visibility Investigation
*Created: 2026-06-18 — Claude.ai*
*Audience: Claude Code*
*Scope: Investigation only — no build, report findings*

---

## Context

On mobile (iPhone, 390px viewport) the action buttons in Gespräche
(▶ Im Browser, 📋 Prompt kopieren, ✈ Telegram, 💾 Als .txt) require
scrolling to reach after selecting a persona. Robert wants to know
if there's a clean low-risk option to get them above the fold without
a significant layout change. If not, we wait.

---

## What to investigate

Load Gespräche on mobile (or browser dev tools at 390px viewport)
with Maria selected. Measure:

**1. How much scroll is needed?**
How many pixels below the fold are the buttons? Is it one small
scroll or significantly below?

**2. What's consuming the space above the buttons?**
Document the height of each element in the detail panel:
- Persona header (icon + name + role)
- Persona description text
- Scene pills (3 pills)
- "Prompt anzeigen ▾" collapsed block
- MODELL selector
- Gap/padding between elements

**3. Would 2-line description truncation fix it?**
The persona description ("Young, slightly hurried, uses some Viennese
slang. Works at a classic Kaffeehaus. Tests natural pace and informal
register.") is 3 lines on mobile. If truncated to 2 lines with a
"mehr ▾" expand:
- What is the CSS selector for the description element?
- How many pixels would this save?
- Would it be enough to bring buttons above the fold at 390px?

**4. Any other quick wins?**
Is there unnecessary padding or margin between elements that could
be tightened on mobile without changing the layout? Look for gaps
> 16px between elements that could reasonably be reduced.

---

## What to report back

A short note covering:
- Pixels below fold for the buttons
- The main space consumer (probably description text)
- Whether 2-line truncation would fix it (yes/no/maybe)
- CSS selector for description element if truncation is viable
- Any other low-risk option spotted

## What NOT to build

Do not make any changes in this handoff. Investigation and report only.
Robert will decide whether to build based on the findings.

---

*Handoff · 2026-06-18 · Claude.ai*
