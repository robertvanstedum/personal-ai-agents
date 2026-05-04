# Release v0.9 — German Language Domain (beta)
**Date:** May 4, 2026  
**Status:** Beta — active daily use · Vienna trip May 10, 2026 as real-world test

---

## What's New

### German Language Domain — v0.9 beta

The second mini-moi domain. Same local-first, model-agnostic pipeline applied to
spoken language practice in real Viennese scenes.

**Scene layer**
- 9 Vienna personas (hotel, café, museum, U-Bahn, pharmacy, restaurant, bakery, Airbnb, local)
- Scene-first routing via natural language trigger words
- Voice transcription — speak your practice, get it reviewed
- Scaffold system: 3 phrases delivered per session, rotated across a bank of 6, never shown to the AI

**Review layer**
- `reviewer.py` extracts errors, tracks scaffold usage (`scaffold_used` / `scaffold_avoided`)
- Per-session lesson files with structured feedback
- 60+ sessions, 480+ practice minutes at v0.9

**Drill layer**
- Level 0 — `conjugate <verb>`: full conjugation table, fill-in-the-blank
- Level 1 — `drill <verb>`: translation drill, all phrases for a verb
- Natural language control: `drill können`, `drill list`, `again`, `end drill`, `hint`, `skip`
- Pronoun-inclusive answers accepted (`ich nehme` = `nehme`)
- Drill state persisted to disk — survives bot restarts mid-drill
- Lazy state reload on each message — handles multi-instance edge cases

**Anki layer (Phase A)**
- Friction-only cards: correct first attempt → no card; 1 wrong → `drill-reinforced`; hint/2+ wrong → `needs-practice`
- Cards written to `vienna_deck.csv` on drill completion
- Pre-loaded Vienna deck: 11 verb conjugation tables, 6 core phrases, 10 gendered nouns
- 220+ Anki cards at v0.9

**Infrastructure**
- 31/31 tests passing
- LLM provider chain: grok-mini → claude-haiku → ollama gemma3:1b (configurable)
- On-demand verb fetch and cache in `drill_pool.json`

---

## Known Gaps (v1.0 targets)

- Scene → Drill Pool gate not yet wired (post-Vienna, Phase B)
- Noun/article drill (L0) not yet built
- Duplicate friction cards blocked by pre-loaded deck entries — by design for Vienna
- `drill pending` command (Phase C, post-Vienna)

---

## Files

| File | Purpose |
|---|---|
| `telegram_bot.py` | Bot, drill engine, routing |
| `reviewer.py` | Session review, scaffold tracking |
| `get_german_session.py` | Session generation |
| `_NewDomains/language-german/language/german/config/drill_pool.json` | Verb/phrase pool, on-demand cache |
| `_NewDomains/language-german/language/german/anki/vienna_deck.csv` | Anki export deck |
| `docs/GERMAN.md` | Domain overview |

---

*Previous release: [v1.1](RELEASE_v1.1_2026-03-29.md)*
