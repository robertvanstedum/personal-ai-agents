# Spec — Gespräche: Prompt Kopieren Button
*Created: 2026-06-18 — Claude.ai*
*Status: Ready for `_working/`*
*Priority: High — unblocks Path A (external model workflow)*

---

## Context

The current button row in Gespräche has "Telegram öffnen" which copies
the session prompt AND opens Telegram. This couples two actions that
should be independent. Robert now has a mobile web interface, so the
mandatory Telegram step is no longer needed for the copy-and-paste
workflow with external models (Grok, Claude, Gemini, ChatGPT).

Telegram stays as a first-class channel — notifications, commands,
voicemail-style sends. It's infrastructure-independent and always-on.
But it should not be mandatory in the prompt-copy flow.

---

## Change

### Current button row
```
[▶ Sitzung starten]  [✈ Telegram öffnen]  [□ Kopieren]  [💾 Als .txt]
```

### New button row
```
[▶ Sitzung starten]  [📋 Prompt kopieren]  [✈ Telegram]  [💾 Als .txt]
```

### "Prompt kopieren" button (new / replaces existing Kopieren)
- Copies the formatted session prompt to clipboard — one tap
- No app opens, no redirect — just copies
- Shows brief inline confirmation: "Kopiert ✓" for 1.5s
- Works on iPhone Safari, iPhone Chrome, Samsung A36 Chrome
- Requires HTTPS (app.minimoi.ai) — works on portal, not localhost
- If clipboard unavailable: show "Manuell kopieren" note, focus
  the prompt textarea so user can select-all and copy manually

### "Telegram" button (renamed, behaviour unchanged)
- Same as current "Telegram öffnen" — opens Telegram with prompt
- Label shortened to "Telegram" — cleaner on mobile
- Keeps its place in the row

### Prompt editable before copying

The prompt textarea (currently read-only or hidden) becomes directly
editable before the user taps "Prompt kopieren." The user sees the
full prompt, can tweak it — fix a word, adjust an instruction, correct
a detail — then copies. No modal, no settings screen. Just editable
text in place.

- Prompt textarea is visible and editable in the resting state
- Shows the full formatted prompt: persona brief, scene, focus areas,
  framing
- User edits directly if needed, then taps "Prompt kopieren"
- If user makes no edits, default prompt copies as-is — no extra step
- Font size ≥ 16px (no Safari auto-zoom), parchment bg, amber border
  on focus

**What the prompt contains (default, editable):**
- Persona name, role, and scene brief
- Today's preparation / focus areas (Heutige Vorbereitung if filled)
- Any suggestion pills selected
- Standard session framing

**Note — gender/persona settings:**
The prompt currently reflects the persona file as written. Allowing
gender overrides or persona-level tweaks requires changes to the persona
file structure — this is a separate design session, not part of this
spec. For now, the editable textarea gives Robert manual control:
he can edit the prompt directly before copying if he wants to adjust
any detail including gender references.

---

## Mobile layout

On 390px viewport the four buttons must not overflow. Options:
- Two rows of two buttons (preferred — larger touch targets)
- Single row with smaller text (acceptable if all fit at 44px height)

Check current button row width at 390px before deciding. Match whatever
pattern the existing layout uses for overflow.

---

## Definition of Done

- "Prompt kopieren" copies prompt to clipboard on tap
- "Kopiert ✓" confirmation shows for 1.5s then clears
- "Telegram" button retains all existing behaviour
- Four buttons fit correctly at 390px — no overflow
- All buttons meet 44px minimum touch target height
- Clipboard unavailable: graceful fallback shown
- Verified on: iPhone Safari, iPhone Chrome, Samsung A36 Chrome

## Commit

`Gespräche: add Prompt kopieren button independent of Telegram.
Decouples prompt copy from Telegram for multi-model workflow.`

---

*Spec 1 of 3 · 2026-06-18 · Claude.ai*
