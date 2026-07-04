# Portuguese Domain — Leitura Reading List
*Living document — versioned in GitHub*
*Created: 2026-06-25 — Claude.ai*
*Location: docs/design/portuguese_leitura_2026-06-25.md*

---

## Purpose

This document defines the reading list structure, sources, and content
philosophy for the Leitura tab of the Portuguese language domain.
Robert is the content curator. The list is shared across all users —
no personalization. Difficulty is signaled by level tags on each
article so users know what they're getting into.

---

## Structure — Carioca Hoje

Four sections, two tones. O Dia a Dia and Cultura Carioca are lighter
and more accessible. Jornal do Dia is harder news. Rio is mixed.

```
Carioca Hoje
├── 📅 O Dia a Dia        (accessible, lifestyle, local)
├── 🎭 Cultura Carioca    (culture, music, film, Carnaval)
├── 📰 Jornal do Dia      (harder news, economy, politics)
└── 🏙️ Rio                (city, neighborhoods, history)
```

---

## Sections in detail

### 📅 O Dia a Dia
*Praia · Família · Bairro · Esporte*

Everyday life in Rio — beach culture, family routines, neighborhood
news, weekend activities, surf, local markets, food. The content that
maps directly to the Conversas personas (padaria, barraca, Uber ride).

Anchor: Recreio dos Bandeirantes and western zone (Barra da Tijuca,
Recreio, Jacarepaguá), then Zona Sul, then wider Rio.

Level: mostly Iniciante and Intermediário.

**Sources:**
- O Dia — Rio tabloid, accessible language, local focus
- Extra — popular daily, everyday topics, simple vocabulary
- Veja Rio — local lifestyle, events, neighborhoods
- Recreio/Barra local blogs and community sites (TBD — Robert to identify)
- Jornal do Recreio (if active) or equivalent western zone source

---

### 🎭 Cultura Carioca
*Música · Cinema · Arte · Carnaval*

Rio culture — bossa nova, samba, funk carioca, MPB, film, street art,
Carnaval, theater, food culture. The content that makes Rio feel like
a specific place with a specific identity rather than generic Brazil.

Level: Intermediário. Some iniciante pieces on music and film.

**Sources:**
- O Globo Cultura — arts and culture section
- Rádio MEC — public radio, culture programming, accessible
- Veja Rio — events, film, food
- Piauí magazine — longer culture pieces (lighter selections for this level)
- Rolling Stone Brasil — music, accessible to intermediário readers

---

### 📰 Jornal do Dia
*Notícias · Economia · Brasil*

Harder news — national politics, economy, international affairs from
a Brazilian perspective. For advanced readers. This is where Avançado
users get real challenge — full newspaper language, longer sentences,
complex vocabulary.

Level: Avançado. Iniciante and Intermediário users are signaled this
is harder territory.

**Sources:**
- Folha de São Paulo — national paper, high quality, demanding
- O Globo — national/Rio perspective, politics, economy
- Agência Brasil — public news agency, slightly more accessible
- Valor Econômico — economy and business (most demanding)

---

### 🏙️ Rio
*Zona Oeste · Recreio · Cidade · História*

Rio as a city — neighborhoods, history, geography, urban life,
infrastructure, culture of specific areas. Mix of accessible local
stories and more demanding historical and analytical pieces.

Anchor: Recreio dos Bandeirantes as home base, then Barra da Tijuca,
then wider zones.

Level: Mixed. Local neighborhood stories are iniciante/intermediário.
History and analytical pieces are avançado.

**Sources:**
- O Globo Rio — local Rio section
- Veja Rio — neighborhoods, urban life
- Riotur (official tourism) — accessible, neighborhood descriptions
- Instituto Pereira Passos — Rio urban history and data
- Academic and cultural pieces on Rio history (avançado)

---

## Level tags

Each article gets one tag. Users see the tag before reading.

| Tag | Portuguese | Who it's for |
|-----|-----------|-------------|
| 🟢 Iniciante | Vocabulário simples | Daughter 1 (14) — starting point |
| 🟡 Intermediário | Vocabulário moderado | Daughter 2 (20) — comfortable range |
| 🔴 Avançado | Vocabulário complexo | Daughter 3 (28) — real challenge |

---

## Refresh cadence

Follows the German Lesen pattern — Robert curates when content
warrants it, not on an automated daily schedule. Weekly refresh
is the target rhythm. New articles added as Robert finds them;
older articles stay in the archive.

No automated RSS ingestion to start. Robert adds articles manually
via the admin interface. Automation can be added later if the
manual process becomes a burden.

---

## Content philosophy

**Specific over general.** An article about surf conditions at
Recreio is better than a generic piece about Brazilian beaches.
Recreio dos Bandeirantes is the anchor — content radiates outward
from there.

**Real language, not textbook language.** O Dia and Extra use
the same Portuguese Maria and Carlos speak. Folha de São Paulo
uses the language a daughter will encounter in professional life.
Both are real; both belong.

**Culture is primary.** Cultura Carioca is not supplementary —
it's central. Music, Carnaval, food, and film are how Rio explains
itself. A learner who knows bossa nova and Carnaval has a framework
for everything else.

**Portugal not included.** Brazilian Portuguese only. European
Portuguese can be added as a separate track later if there's
demand. For now, one country, one city, one world.

---

## Open decisions (resolve before Leitura spec is written)

1. **Admin interface for adding articles** — does Robert add articles
   via a form in the admin tab, or by editing a JSON file directly?
   Recommend: admin form (same pattern as German Lesen admin).

2. **Article storage** — full text stored in Postgres or just URL +
   metadata with content fetched on demand? Recommend: URL + metadata
   + excerpt in Postgres, full text fetched on demand (avoids storage
   bloat and copyright issues).

3. **Reading view** — simplified reader view (like German Lesen) or
   link out to the original article? Recommend: simplified reader for
   articles where possible, link out as fallback.

4. **Recreio local sources** — Robert to identify specific local
   blogs or community sites for the western zone. The big national
   papers don't cover Recreio specifically.

---

## Revision history

| Date | Change | By |
|------|--------|-----|
| 2026-06-25 | Initial structure and sources | Robert + Claude.ai |

---

*Leitura Design · docs/design/portuguese_leitura_2026-06-25.md*
*Content owner: Robert*
*Gate for Leitura spec: open decisions above resolved*
*Spec number: Spec 5 in Portuguese domain build plan*
EOF