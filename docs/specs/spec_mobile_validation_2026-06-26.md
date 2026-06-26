# Spec — Mobile Validation: German + Portuguese Domains
*Created: 2026-06-26 — Claude.ai*
*Status: spec_ready*
*Type: standing spec — run before every production push*
*Location: docs/specs/spec_mobile_validation_2026-06-26.md*

---

## Purpose

Mobile is the primary device for daughters and wife.
Every production push to either language domain must pass
this validation on an actual phone before the push goes out.

This spec defines:
1. The four targeted fixes needed right now
2. The standing mobile checklist that runs before every push

---

## Immediate fixes — Portuguese

### Fix 1 — Landing + Leitura (highest priority)

Full detail in `docs/specs/spec_portuguese_mobile_css_2026-06-26.md`

- Landing: hero gap above photo — fix `body.landing-mode::before`
- Landing: tab links in 2x2 grid — force single row
- Leitura: category cards in single row — force 2x2 grid

### Fix 2 — Conversas: pill toggle second button missing

"Com pessoa real" button not visible on mobile.
German always shows both pill buttons as a pair.
Check if hidden by CSS or missing from template.
Fix whichever is the cause — both buttons must be visible.

### Fix 3 — Arquivo: raw session type label

"KI_SESSAO" showing as raw DB value in session row.
Replace with human-readable label: "KI-Sessão".

```python
SESSION_TYPE_LABELS = {
    'KI_SESSAO': 'KI-Sessão',
    'REAL_SESSAO': 'Sessão real',
}
```

Style as small muted badge — same pattern as German Archiv.

### Fix 4 — Page title color

H1 titles on Conversas, Escrita, Palavras, Arquivo rendering
in dark green (#1B2E1F). German uses near-black for stronger
typographic contrast.

```css
/* Add to :root */
--pt-text-heading: #1A1A1A;

/* Apply to h1 */
h1, .tab-title {
  color: var(--pt-text-heading);
}
```

---

## Standing mobile checklist

Run on actual phone before every production push.
Not desktop browser resize — real iPhone, iOS Safari.

---

### German (Mein Deutsch) ✅ currently passing

Run after any German change to confirm no regression.

**Landing:**
- [ ] Hero photo fills viewport from nav — no gap
- [ ] Tab links in single row
- [ ] Title and subtitle readable over photo

**Lesen:**
- [ ] Hero photo at correct height
- [ ] Four category cards in 2x2 grid
- [ ] Cards tap-friendly (≥ 140px height)
- [ ] Article rows readable below cards

**Gespräche:**
- [ ] Both pill buttons visible (Mit KI-Persona / Mit echtem Mensch)
- [ ] Persona cards: circular avatar, name bold, role subtitle
- [ ] Round indicator visible on active persona
- [ ] Right panel hidden on mobile
- [ ] Sitzung starten button tappable

**Schreiben:**
- [ ] All three pill buttons in single row
- [ ] Daily prompt in italic
- [ ] Textarea full width, ≥ 180px
- [ ] No iOS zoom on tap (font-size ≥ 16px)
- [ ] Korrigieren button visible
- [ ] Right panel hidden on mobile

**Wörter:**
- [ ] Pill toggle visible
- [ ] Filter dropdowns full width
- [ ] Anki Export button visible
- [ ] Vocab table fits screen

**Archiv:**
- [ ] Pill toggle full width
- [ ] Session rows: date / persona / scene in one line
- [ ] Session type human-readable (not raw DB value)

**Admin:**
- [ ] Persona list readable
- [ ] Action buttons tappable

---

### Portuguese (meu português)

Fix items 1-4 above, then validate full checklist.
✅ = currently passing from screenshots

**Landing:**
- [ ] Hero fills viewport — no dark gap ← Fix 1
- [ ] Tab links in single row ← Fix 1
- [ ] "Português" title readable over photo

**Leitura:**
- [ ] Hero photo visible
- [ ] Category cards in 2x2 grid ← Fix 1
- [ ] Cards tap-friendly
- [ ] Articles appear when RSS pipeline runs

**Conversas:**
- [ ] Both pill buttons visible ← Fix 2
- [ ] Persona cards: avatar + name + role ✅
- [ ] Round indicator after sessions
- [ ] Right panel hidden ✅
- [ ] Session start button tappable

**Escrita:**
- [ ] All three pill buttons in single row ✅
- [ ] Daily prompt in italic ✅
- [ ] Textarea full width ✅
- [ ] No iOS zoom ✅
- [ ] Corrigir button visible
- [ ] Right panel hidden ✅

**Palavras:**
- [ ] Pill toggle visible ✅
- [ ] Filter dropdowns readable ✅
- [ ] Anki Export visible ✅
- [ ] Empty state readable ✅

**Arquivo:**
- [ ] Pill toggle full width ✅
- [ ] Session rows readable ✅
- [ ] "KI-Sessão" not "KI_SESSAO" ← Fix 3
- [ ] Date / persona / scene in one line ✅

**Admin:**
- [ ] Cards readable ✅
- [ ] Palette toggle not obscuring content ✅

---

## When to run

| Trigger | German | Portuguese |
|---------|--------|-----------|
| German template or CSS change | ✅ | — |
| Portuguese template or CSS change | — | ✅ |
| Portal / auth / nav change | ✅ | ✅ |
| CI/CD pipeline change | ✅ | ✅ |
| Major version push | ✅ | ✅ |

---

## Future domains

When a third language domain is added, add a section to this
spec following the Portuguese pattern. German is the reference.

---

## Definition of Done

- [ ] Fix 1: Landing hero and Leitura grid confirmed on phone
- [ ] Fix 2: Both Conversas pill buttons visible on phone
- [ ] Fix 3: Arquivo shows "KI-Sessão" not "KI_SESSAO"
- [ ] Fix 4: Page titles near-black, strong contrast
- [ ] Full Portuguese checklist passes on phone
- [ ] German checklist passes — no regression
- [ ] This spec committed to docs/specs/

## Commit message

`Mobile: Portuguese fixes (landing hero, Conversas toggle,
Arquivo label, title color) + standing mobile validation spec`

---

*Standing spec · Mobile Validation · 2026-06-26 · Claude.ai*
*Run before every production push to either language domain*
*iOS Safari on iPhone is the target browser*
*German is the reference. Portuguese matches German.*
EOF