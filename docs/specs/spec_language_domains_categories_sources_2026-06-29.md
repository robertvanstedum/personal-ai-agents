# Handoff: Language Domain Sources & Categories — German + Portuguese
*Created: 2026-06-29 · Claude.ai (design) → Claude Code (build)*

---

## Summary

Two changes across both language domains:
1. **Category rename/reassignment** — Politik (German) dissolved; content
   redistributed. Local/neighborhood/weather moves to Wien/Rio. Both domains
   now mirror each other exactly.
2. **Source updates** — Portuguese sources added to both domains. Final source
   lists below.

Both domains must stay siblings: same category structure, same selection logic,
same config shape, different content. Any drift between them is a bug.

---

## Category Structure (both domains — final)

| German | Portuguese | Content |
|---|---|---|
| Alltag | Cotidiano | Everyday life: sports, food, household, fashion, lifestyle. Magazine rack feel. NO politics, NO local/neighborhood (that goes to Wien/Rio). |
| Kultur | Cultura | Art, music, film, theater, literature. |
| Nachrichten | Notícias | Hard news, politics, economics. Picks up all political content previously in Politik (German). |
| Wien | Rio | City-specific: local news, weather, neighborhood, events. NO national politics. |

### Key reassignments vs. current config
- **Politik (German)** → dissolve this category. Political/hard news content
  moves to Nachrichten. If "Politik" exists as a category key in the German
  config, rename it to "Nachrichten".
- **Local/neighborhood/weather content** — remove from Alltag/Cotidiano if
  currently there; reassign to Wien/Rio respectively.
- **Cotidiano (Portuguese)** — confirm category key in config is `cotidiano`
  (not `dia_a_dia` or any other key from earlier sessions). Cotidiano is the
  locked name.

---

## German Sources — Final (by category)

| Category | Source | RSS URL | Notes |
|---|---|---|---|
| Alltag | ORF Wien | (existing) | Keep — local/lifestyle |
| Alltag | ORF Sport | (existing) | Keep — sports |
| Alltag | Wiener Bezirksblätter | (existing) | Keep — neighborhood feel fits Alltag |
| Alltag | [verify existing Alltag sources] | — | Audit: remove any politics/hard news sources from this category |
| Kultur | ORF Kultur | (existing) | Keep |
| Kultur | [verify existing Kultur sources] | — | Keep as-is |
| Nachrichten | [existing Politik sources] | — | Migrate here from Politik |
| Nachrichten | [verify existing Nachrichten if any] | — | Merge with migrated Politik content |
| Wien | ORF Wien (local subset) | (existing) | Keep — local Vienna |
| Wien | vienna.at | (existing) | Keep |
| Wien | [verify existing Wien sources] | — | Keep as-is |

**Note:** German source URLs are already in the config. This handoff is
primarily a **category reassignment**, not new source additions. Claude Code
should audit current German sources against the new category definitions and
reassign any that are miscategorized.

---

## Portuguese Sources — Final (18 Brazilian + 4 Portuguese = 22 sources)

| Category | Source | Country | RSS URL |
|---|---|---|---|
| Cotidiano | G1 | Brazil | https://g1.globo.com/rss/g1/ |
| Cotidiano | Metrópoles | Brazil | https://www.metropoles.com/feed/ |
| Cotidiano | O Dia | Brazil | https://odia.ig.com.br/rss |
| Cotidiano | Extra | Brazil | https://extra.globo.com/rss |
| Cotidiano | Folha Cotidiano | Brazil | https://feeds.folha.uol.com.br/cotidiano/rss091.xml |
| Cotidiano | Jornal de Notícias | Portugal | https://www.jn.pt/rss |
| Cotidiano | Correio da Manhã | Portugal | https://www.cmjornal.pt/rss |
| Rio | Veja Rio | Brazil | https://vejario.abril.com.br/feed/ |
| Rio | G1 Rio de Janeiro | Brazil | https://g1.globo.com/rss/g1/rj/rio-de-janeiro/ |
| Rio | O Globo Rio | Brazil | https://oglobo.globo.com/rio/rss.xml |
| Rio | O Dia Rio | Brazil | https://odia.ig.com.br/rss |
| Cultura | Rolling Stone Brasil | Brazil | https://rollingstone.uol.com.br/feed/ |
| Cultura | Folha Ilustrada | Brazil | https://feeds.folha.uol.com.br/ilustrada/rss091.xml |
| Cultura | O Globo Cultura | Brazil | https://oglobo.globo.com/cultura/rss.xml |
| Cultura | Gshow | Brazil | https://gshow.globo.com/rss/ |
| Cultura | Expresso Cultura | Portugal | Verify current feed URL before committing |
| Notícias | Agência Brasil | Brazil | https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml |
| Notícias | Folha de São Paulo | Brazil | https://feeds.folha.uol.com.br/emcimadahora/rss091.xml |
| Notícias | CNN Brasil | Brazil | https://www.cnnbrasil.com.br/feed/ |
| Notícias | UOL Notícias | Brazil | https://rss.uol.com.br/index.xml |
| Notícias | BBC Brasil | Brazil | Verify current feed URL before committing |
| Notícias | Público | Portugal | https://rss.publico.pt/ultimas |

### Feed verification required before committing
These three need a live check — feed URLs may have changed:
- **BBC Brasil** — find current RSS endpoint
- **Expresso Cultura** — confirm feed exists and is active
- **O Globo Rio** — confirm feed is active (was missing from config previously)

---

## Selection Logic (both domains — unchanged)

Per the committed fix (270fa76):
- Max 10 articles per category per day
- Max 3 articles per source per category
- Floor of 3 articles if content exists
- Applied at selection/projection layer, not ingestion
- Config-driven — same keys, same shape for both domains

No changes to selection logic in this handoff. Sources only.

---

## Content Quality Notes (not in this spec — queue separately)

These are known issues flagged for a future spec:
- G1 regional TV clutter in Cotidiano (keyword/exclusion filter needed)
- Metrópoles fluff threshold (quality guardrail)
- European Portuguese register differs from Brazilian — consider labeling
  Portugal sources with a flag in the UI (e.g. 🇵🇹) so learners know what
  register they're reading. Low priority, nice-to-have.

---

## Definition of Done

- [ ] German: Politik category dissolved; content migrated to Nachrichten
- [ ] German: Local/weather/neighborhood content confirmed in Wien, not Alltag
- [ ] German: Alltag sources audited — no politics/hard news sources remain
- [ ] German: Category keys in config match: `alltag`, `kultur`, `nachrichten`, `wien`
- [ ] Portuguese: Category key confirmed as `cotidiano` (not `dia_a_dia`)
- [ ] Portuguese: All 22 sources added to config with correct category assignments
- [ ] Portuguese: 3 feed URLs verified live before commit (BBC Brasil, Expresso, O Globo Rio)
- [ ] Both domains: selection logic unchanged (max 10 / max 3 / floor 3)
- [ ] Both domains: category structure is identical mirror (Alltag↔Cotidiano, Kultur↔Cultura, Nachrichten↔Notícias, Wien↔Rio)
- [ ] Tested on dev: each Portuguese category returns articles from at least 2 sources
- [ ] Tested on dev: no German category contains political/hard news in Alltag

---

## Commit

```
feat: language domain category restructure + source expansion

German:
- Dissolve Politik category → migrate content to Nachrichten
- Audit Alltag sources: remove politics, confirm local content in Wien
- Category keys: alltag / kultur / nachrichten / wien

Portuguese:
- Confirm category key: cotidiano (not dia_a_dia)
- Add 8 new sources (Folha Cotidiano, JN, CM, Expresso, BBC Brasil,
  Público, O Globo Rio wired in)
- Verify 3 feed URLs before commit
- Category keys: cotidiano / cultura / noticias / rio

Both domains: siblings — same structure, same selection logic, different content
```

---

*Handoff · 2026-06-29 · Claude.ai → Claude Code · language domains closed for now*
