# Release Notes — v1.0 German Language Learning
*mini-moi · personal-ai-agents*
*Released: 2026-06-11*

## What this is

Mein Deutsch v1.0 is the first full release of the German language coaching domain.
All five content areas are live and in daily use. The pre-release label is retired.

## What's in v1.0

**Five fully operational tabs:**

- **Lesen** — Read authentic German articles from Vienna and German-speaking sources.
  Word-level annotation, vocabulary sidebar, article-level notes. Reading drill with
  bidirectional correction spec'd for v1.1.

- **Gespräche** — Spoken conversation practice. Eight Vienna-based personas with
  distinct voices, settings, and registers. Session transcripts reviewed and scored.
  Pre-session brief and post-session analysis.

- **Schreiben** — Writing practice in German. Prompts, correction, feedback. Full
  session history in Archiv.

- **Wörter** — Vocabulary management. Words captured from sessions, translation,
  example sentences. Anki card export. Cards are earned from actual friction —
  words that appeared in real sessions — not passive review lists.

- **Archiv** — Full session history across all tabs. Searchable, date-navigable.

**Pipeline:**

- Session memory across runs
- Anki card generation with session context
- Phrasebook with fuzzy-matched corrections
- DeepL translation primary, LLM fallback
- Multi-user support (owner / family tier)
- Admin tab for session management

**Multi-user in production:** second user (French/Portuguese learner) using the same
pipeline with domain-appropriate configuration.

**Vienna-tested:** all personas and scenes developed and validated against real Viennese
context. In daily use since May 2026.

## What comes in v1.1

- **Gespräche toggle** — KI-Personas / Konversation pill switch for cleaner mode selection
- **Lesen writing drill** — bidirectional correction and retype for muscle memory
  (GitHub #41, spec at `_working/feature_lesen_writing_drill_2026-06-08.md`)
- **Synthesizer + Challenger** — cross-provider review for writing correction and
  transcript analysis (correction nuance, translation nuance types)

## Upgrade from v0.9

No migration required. v1.0 is a continuation of the same running system.
The version bump reflects completion of the full interface and daily-use confirmation.

---

*Mein Deutsch v1.0 · mini-moi · 2026-06-11*
