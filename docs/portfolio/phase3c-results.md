# Phase 3C: Bookmark Enrichment A/B Test
**Feature test:** Added content_domains, source_types, and content_topics from 20-tweet X bookmark enrichment pipeline.

---

## Profile Growth

| Metric | Baseline | Enriched | Change |
|--------|----------|----------|--------|
| Profile prompt size | 581 chars | 822 chars | +41% |
| content_domains | 0 | 3 | +3 |
| content_topics | 0 | 66 | +66 |
| source_types | 0 | 2 | +2 |

---

## Ranking Changes (Top 20)

*Sources replaced with category labels.*

| Rank A | Rank B | Δ | Category | Comment |
|--------|--------|---|----------|---------|
| #1 | #1 | — | [geopolitics] | Priority boost dominates |
| #2 | #2 | — | [market] | Different ZH article surfaced |
| #3 | #3 | — | [geopolitics] | Geopolitical focus shift |
| #4 | #4 | — | [monetary] | Labor market topic boost |
| #5 | #5 | — | [monetary] | Minor reshuffle |
| #6 | #6 | — | [market] | Market analysis surfaced (+3 from #9) |
| #7 | #7 | — | [technology] | AI/tech story enters top 10 |
| #8 | #8 | — | [geopolitics] | Stable position |
| #9 | #9 | — | [market] | Energy economics shift (+4) |
| #10 | #10 | — | [geopolitics] | Succession analysis surfaced (+4) |
| #11 | #11 | — | [fiscal] | Data source stable |
| #12 | #12 | — | [technology] | Tech/economic analysis surfaced |
| #13 | #13 | — | [commentary] | Duran down from #10 |
| #20 | #20 | -17 | [regional-media] | AJ dropped 17 positions from #3 |

### Notable movements

- 📈 market-analysis-A 'Oil Shock' (#9 → #6, +3) — market analysis got boost from commodity topics
- 📈 FAZ 'Iran Gas Price' (not ranked → #9, +4) — energy economics newly surfaced
- 📈 Die Welt 'Iran Succession' (#14 → #10, +4) — deeper geopolitical analysis valued
- 📉 Al Jazeera 'Oil on Fire' (#3 → #20, -17) — existing avoid patterns reinforced by content topic matching
- 📉 The Duran 'Trump Doomed' (#10 → #13, -3) — commentary source downranked
- 📉 Spiegel 'Palestinian Prisons' (#12 → #18, -6) — lower priority vs analysis-heavy pieces

---

## Cost Impact

Scoring cost unchanged: $0.16 both runs. Enrichment overhead: $0.001.

---

## Methodology

Run A: generated briefing with baseline profile (no enrichment). Merged Phase 3C branch. Ran enrich_signals.py on 20 most recent X bookmarks. Run B: generated briefing with enriched profile. Same 390-article pool, same Grok scoring model, same cost. Only variable: user profile injected into scorer prompt.

---

*Article titles, source names, and account identifiers have been replaced with category labels.*
*Methodology, scoring mechanics, and cost figures are accurate.*
