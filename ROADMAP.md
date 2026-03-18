# Roadmap — Mini-moi

A personal AI agent platform. The first domain — geopolitical and
financial intelligence — is live and generating daily data.
Additional domains follow the same pipeline pattern.

---

## v1.0 — Complete (March 2026)

**Geopolitics & Finance domain, first production release.**

- Daily briefing pipeline — RSS + X bookmarks, ~700 candidates,
  grok-4-1 scoring
- Learning loop — like/dislike/save signals feed a persistent
  user profile injected at scoring time
- Serendipity algorithm — 80/20 personalized/surprise split,
  deliberate anti-filter-bubble design
- AI Observations — daily intelligence layer, source anomalies
  and topic velocity
- Priority feed — inject focus areas directly into scoring
- Deep dives — on-demand structured research briefs
- Web portal — 5 views, feedback inline
- Telegram delivery — morning briefing + feedback callbacks +
  voice commands
- Reliable scheduling — hourly launchd, idempotent,
  sleep-safe on laptop

---

## Near-term (Geopolitics domain)

**Broader sources** — Substacks, academic and institutional feeds
(BIS, Fed, IMF, arXiv), Reddit, YouTube transcripts. Bibliographies
and cited sources from deep dives become a growing reference
library — not just links, but structured entries that accumulate
over time.

**Richer feedback** — like/dislike/save was the foundation.
The next layer is freeform: ad hoc notes, inline commentary,
deeper writing on a topic. Feedback becomes a first-class signal,
not just a binary.

**Longitudinal tracking** — user-directed threads with a defined
question and time horizon. Example: track the impact of the Tigray
conflict on the Ethiopian economy through end of 2026 — an obscure
topic, rarely surfaced by news volume, but worth sustained attention.
The agent holds the question, polls continuously, surfaces relevant
signals, and builds a running synthesis. User sets the question;
agent does the legwork over weeks or months.

**Infrastructure** — database and graph migration (flat files →
PostgreSQL + graph layer capturing relationships between sources,
topics, and entities), migration to always-on hardware (Mac Mini
or equivalent), and evaluation of additional mobile channels —
particularly voice interaction — as the platform shifts from
build to daily use.

---

## Platform

The geopolitics domain is the first instance of a reusable
pipeline pattern:

**input → analysis → learned profile → personalized output → feedback loop**

Additional domains follow the same pattern with different inputs
and outputs. The core architecture — model-agnostic, local-first,
feedback-driven — does not change.

**Next domain: language learning.** Foundation begins after
infrastructure upgrades are complete, running in parallel with
ongoing geopolitics operations.

A shared library will be extracted when two or more domains
are active and the reuse pattern is proven.
