# mini-moi — German Language Domain
### *A personal language coach that learns from your sessions.*

**Status:** v0.9 beta — active daily use · Vienna trip May 2026 as real-world test  
**Sessions:** 60+ · **Anki cards:** 220+ · **Practice minutes:** 480+ · **Tests:** 31/31 passing

---

## What This Is

The German domain is the second mini-moi domain. It applies the same
local-first, model-agnostic architecture to a different problem: speaking
German in a real city, with real people, under real pressure.

The approach is top-down immersion — jump into scenes before building
foundational fluency. Not a grammar app. Not a vocabulary driller. A
pipeline that watches you practice, measures where you struggle, and
surfaces the right material at the right time.

The real-world test is a trip to Vienna in May 2026. Eight personas, each
representing a scene you actually encounter.

---

## How It Works

The same loop that drives the daily intelligence briefing now drives
language practice:

```
Scene (voice or writing)
  ↓
Review (LLM extracts errors, queues next lesson)
  ↓
Drill Pool (stages vocabulary waiting to be drilled)
  ↓
Drill (mechanical practice on specific gaps)
  ↓
Anki (spaced repetition — friction items only, syncs to phone)
```

Each layer has one job. Anki reinforces what you've struggled with. Drill
reveals what you've struggled with. Scene exposes you to what you need
to learn.

---

## The Eight Vienna Personas

Scene-first routing — say the scene, not the name.

| Persona | Scene | Trigger words |
|---|---|---|
| Herr Fischer | Hotel receptionist | hotel, room, checkout, checkin |
| Maria | Café waitress | café, coffee, cake, strudel |
| Dr. Huber | Museum guide | museum, painting, exhibit, art |
| Stefan | U-Bahn / directions | subway, metro, directions, train |
| Frau Novak | Pharmacist | pharmacy, medicine, sick, headache |
| Klaus | Restaurant | restaurant, dinner, reservation |
| Frau Berger | Bakery | bakery, bread, pastry, croissant |
| Anna | Airbnb host | airbnb, apartment, flat, keys |
| Georg | Friendly local | local, chat, heuriger, casual |

Georg fills the social gap between transactions. No script to fall back on.
Pre-loaded answers to common questions (*Woher kommen Sie? Was machen
Sie beruflich?*) so you're not assembling your own biography under pressure.

---

## Scaffold System

Each session delivers three phrases in the briefing — one transaction
sentence, one preference expression, one recovery phrase for when you
freeze. Phrases rotate so the full bank of six surfaces within a week of
practice with any persona. The AI never sees them — they test whether you
deploy them spontaneously.

```
🧱 Today's scaffold — try to use these:
   • Ich hätte gerne einen kleinen Brauner, bitte.
   • Ich möchte lieber ein ruhiges Zimmer.
   • 🆘 Einen Moment bitte — ich überlege kurz.
```

---

## Drill Mode

Three levels of focused mechanical practice:

**Level 0 — Conjugate** (`conjugate können`) — verb table fill-in-the-blank.
Thirty seconds. Just the table. Move on.

**Level 1 — Lock** (`drill tatsächlich`) — one word in one fixed template,
learner controls the rep count. Anchors the word before varying anything.

**Level 2 — Vary** (`drill hotel nouns`) — same frame, rotating nouns
across genders. Forces article variation. This is how *der/die/das* gets
into muscle memory — fifty conversational sessions won't fix it, twenty
minutes of Level 2 drills will.

```
Können Sie mir ___ bringen? — vary the noun.
Nouns: Schlüssel (m), Rechnung (f), Gepäck (n)

> Können Sie mir den Schlüssel bringen?  ✅ den ✅ verb
> Können Sie mir die Rechnung bringen?   ✅
> Können Sie mir das Gepäck bringen?     ✅
> enough
Saved.
```

---

## Anki Integration

Cards are earned, not dumped. Only friction items from drills go to the
Vienna deck — hints used, multiple wrong attempts. Correct on first
attempt means no card needed.

| Result | Tag | Card written? |
|---|---|---|
| Correct first attempt | — | No |
| Correct after 1 wrong | `drill-reinforced` | Yes |
| Correct after 2+ wrong / hint | `needs-practice` | Yes |

Drill on your laptop → AnkiWeb syncs to your phone. The cards that need
work are there when you need them.

---

## Telegram Interface

Natural language throughout — no command syntax to memorize.

```
café session      → Maria, café scenario
hotel             → Herr Fischer, hotel check-in
again             → repeat last session persona
drill können      → Level 1 drill, können
conjugate sein    → Level 0, sein conjugation table
drill hotel nouns → Level 2, hotel noun rotation
end drill         → close drill, write Anki cards
status            → session count, error patterns, next lesson
```

Voice messages transcribed automatically. Two-message delivery keeps
the briefing separate from the AI prompt — read one, paste the other.

---

## Architecture

Reuses the full mini-moi stack: `telegram_bot.py`, `reviewer.py`,
`get_german_session.py`, `drill_session.py`, `drill_pool.json`,
`keyword_map.json`. Everything configurable. Preloaded but not hardcoded.
Model-agnostic — swap any layer without touching the pipeline.

---

## Roadmap

**Post-Vienna (v1.0):** Scene → Drill Pool gate (vocabulary earns its
way to Anki through drill) + scaffold tracking (`scaffold_used` /
`scaffold_avoided`) + noun/article drill (L0).

**v1.3:** Cross-session pattern detection via Neo4j — *"when did the
dative confusion actually resolve?"*

The domain structure transfers to any target language. German is the
proof of concept.

---

## Case Study

A detailed case study documents how the domain evolved from a simple
transcript parser to a three-layer learning loop across four weeks of
daily use — including the design changes that only emerged from real use.

See: [GERMAN_CASE_STUDY_DRAFT_2026-05-02.md](GERMAN_CASE_STUDY_DRAFT_2026-05-02.md)  
Epilogue to be written after Vienna.

---

*mini-moi German Domain — v0.9 beta — May 2026 — Robert van Stedum*  
*[github.com/robertvanstedum/personal-ai-agents](https://github.com/robertvanstedum/personal-ai-agents)*
