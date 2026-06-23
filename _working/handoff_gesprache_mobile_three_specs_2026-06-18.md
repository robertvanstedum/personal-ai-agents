# Handoff — Gespräche Mobile: Three Specs
*Created: 2026-06-18 — Claude.ai*
*Audience: Claude Code*
*Build in order — each is independent but 1 before 2 and 3*

---

## Context

Two parallel paths for German conversation practice on mobile:

**Path A — External model (Grok, Claude, Gemini, ChatGPT)**
Copy prompt from Gespräche → paste into model's app →
conversation happens there → copy transcript back →
paste into Gespräche → Analysieren when ready

**Path B — In-page KI-Sitzung**
Voice or text conversation directly in Gespräche →
transcript auto-saved → Analysieren when ready

Telegram stays as always-on channel for notifications and commands.
It is not required in either path above.

These three specs make both paths smooth on mobile.

---

## Spec 1 — Prompt kopieren button
**File:** `spec_gesprache_prompt_kopieren_2026-06-18.md`

Adds a "Prompt kopieren" button to the Gespräche button row that copies
the session prompt to clipboard independently of Telegram. Telegram button
stays but is no longer mandatory. Enables Path A without app switching.

**Button row after:**
`[▶ Sitzung starten] [📋 Prompt kopieren] [✈ Telegram] [💾 Als .txt]`

---

## Spec 2 — KI-Sitzung mobile tuning
**File:** `spec_gesprache_kisitzung_mobile_2026-06-18.md`

Makes Path B comfortable on mobile: full-width session area, suggestion
pills as full-width tappable rows, text input fallback when voice isn't
practical, larger touch targets for mic and Beenden.

---

## Spec 3 — Deferred analysis
**File:** `spec_gesprache_deferred_analysis_2026-06-18.md`

Auto-saves transcript on Beenden. Session card appears with optional
[Analysieren →]. Neue Sitzung available immediately. Enables doing
2-3 sessions before analysing — supports both paths.

---

## Build order

1. Spec 1 first — smallest change, unblocks Path A immediately
2. Spec 2 second — mobile layout, no backend changes
3. Spec 3 third — session state changes, most complex

Each can be a separate commit. Do not bundle all three.

---

## Verification — all three specs

Test on: iPhone Safari, iPhone Chrome, Samsung A36 Chrome

**Full Path A test:**
Tap "Prompt kopieren" → paste into Grok app → have conversation →
copy transcript → return to Gespräche → paste panel → Analysieren

**Full Path B test:**
Select persona → Sitzung starten → voice session → Beenden →
session card appears → Neue Sitzung (without analysing) →
second session → Beenden → both cards visible →
Analysieren first session → feedback correct →
Analysieren second session → feedback correct

---

## Also in this session — German batch (T1-B, T2-A, T2-B, T2-C)

Four additional small specs from `docs/GESPRACHE_FORWARD_SPEC.md`
are ready but secondary to the three above. Build these after the
three main specs ship:

| Item | What |
|------|------|
| T1-B | Safe area insets + all inputs ≥ 16px |
| T2-A | iOS Share Sheet (`navigator.share`) |
| T2-B | Schreiben save confirmation toast |
| T2-C | Post-session summary card |

---

*Handoff · 2026-06-18 · Claude.ai*
