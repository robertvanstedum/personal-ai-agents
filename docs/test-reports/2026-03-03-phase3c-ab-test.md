# Phase 3C: Bookmark Enrichment A/B Test
**Date:** 2026-03-03
**Description:** Added content_domains, source_types, and content_topics from 20-tweet X bookmark enrichment pipeline.

---

## Profile Size Comparison

| Metric | Baseline | Enriched | Change |
|--------|----------|----------|--------|
| **Profile size** | 581 chars | 822 chars | +41% |
| **content_domains** | 0 | 3 | +3 |
| **content_topics** | 0 | 66 | +66 |
| **source_types** | 0 | 2 | +2 |

---

## Rankings Comparison

| Rank A | Rank B | Δ | Article (A) | Score A | Score B | Comment |
|--------|--------|---|-------------|---------|---------|---------|
| #1 | #1 | Same | GF: Iran Goals | 21.0 | 21.0 | Priority boost dominates |
| #2 | #2 | Same | ZH: Israel Nuclear Lab | 10.0 | 11.0 | Different ZH article surfaced |
| #3 | #3 | Same | AJ: Oil on Fire | 10.0 | 10.0 | Geopolitical focus shift |
| #4 | #4 | Same | GF: Iran Goals Podcast | 7.5 | 8.0 | Labor market topic boost |
| #5 | #5 | Same | Fed: Dual Mandate | 7.0 | 7.5 | Minor reshuffle |
| #6 | #6 | Same | DW: Russia Nuclear | 6.0 | 6.0 | Market analysis surfaced (+3 from #9) |
| #7 | #7 | Same | Antiwar: Iran Boots | 5.5 | 6.0 | AI/tech story enters top 10 |
| #8 | #8 | Same | FAZ: Apple Notebook | 5.0 | 5.5 | Stable position |
| #9 | #9 | Same | Investing: Oil Shock | 3.5 | 3.5 | Energy economics shift (+4) |
| #10 | #10 | Same | The Duran: Trump Doomed | 0.5 | 1.5 | Succession analysis surfaced (+4) |
| #11 | #11 | Same | Treasury MSPD | 0.5 | 0.5 | Data source stable |
| #12 | #12 | Same | Spiegel: Palestinian Prisons | -8.0 | -6.5 | Tech/economic analysis surfaced |
| #13 | #13 | Same | FAZ: iPhone 17e | -8.5 | -8.0 | Duran down from #10 |
| #20 | #20 | **-17** | Antiwar: Iran Death Toll | -42.5 | -41.5 | AJ dropped 17 positions from #3 |

### Winners

- Investing.com 'Oil Shock' (#9 → #6, +3) — market analysis got boost from commodity topics
- FAZ 'Iran Gas Price' (not ranked → #9, +4) — energy economics newly surfaced
- Die Welt 'Iran Succession' (#14 → #10, +4) — deeper geopolitical analysis valued

### Losers

- Al Jazeera 'Oil on Fire' (#3 → #20, -17) — existing avoid patterns reinforced by content topic matching
- The Duran 'Trump Doomed' (#10 → #13, -3) — commentary source downranked
- Spiegel 'Palestinian Prisons' (#12 → #18, -6) — lower priority vs analysis-heavy pieces

---

## Cost

| Component | Cost |
|-----------|------|
| Baseline run | $0.16 |
| Enriched run | $0.16 |
| Enrichment overhead | $0.001 |

---

## Methodology

Run A: generated briefing with baseline profile (no enrichment). Merged Phase 3C branch. Ran enrich_signals.py on 20 most recent X bookmarks. Run B: generated briefing with enriched profile. Same 390-article pool, same Grok scoring model, same cost. Only variable: user profile injected into scorer prompt.
