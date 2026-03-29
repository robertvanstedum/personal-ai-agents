# SPEC_SOURCE_INTELLIGENCE_UPGRADE_v1.0_2026-03-29.md
*Date: March 29, 2026*
*Author: claude.ai design session*
*Status: Ready for OpenClaw review → Claude Code build*
*Applies to: Research Agent + Curator Daily Briefing (shared infrastructure)*

---

## Problem

Both the Research agent and the Curator daily briefing share a source quality problem: they surface the same small pool of sources repeatedly, miss non-Anglophone material, and reward pre-approved sources over discovery. The Deeper Dive POC made this concrete — four runs against the `strait-of-hormuz` thread consistently retrieved the same 2-3 EIA/Statista sources despite promoted queries targeting Iranian food dependency, SPR asymmetry, and 2026 negotiations.

The analysis layer (Deeper Dive) is now ahead of the research layer feeding it. This upgrade brings them into alignment — and improves the Curator daily briefing simultaneously, since they share the same search infrastructure.

---

## Design Principle: Structure Without Walls

The existing `curator_sources.json` trust tiers provide a quality floor. They should not become a ceiling. The goal is to be surprised — to surface material the user didn't know to look for — while maintaining signal quality.

Four mechanisms achieve this together:

1. **Discovery allocation** — reserved budget for sources outside the seeded list
2. **Citation chasing** — follow references from high-quality sources into unknown territory
3. **Lateral domain search** — search adjacent domains, not just the obvious ones
4. **Source novelty scoring** — discount any URL seen in a previous session

---

## Mechanism 1: Discovery Allocation

**What it does:** Reserves 20-30% of each session's source slots for sources outside the trust tier list entirely. The agent searches broadly, surfaces unexpected material, and scores it on content merit rather than source reputation.

**Implementation:**
```python
DISCOVERY_ALLOCATION = 0.25  # 25% of sources per session

# In session source selection:
trusted_sources = [s for s in candidates if s.domain in trust_tiers]
discovery_sources = [s for s in candidates if s.domain not in trust_tiers]

# Select top N * 0.75 from trusted, top N * 0.25 from discovery
# Score discovery sources on content quality, not source reputation
```

**Why:** Trust tiers reward known sources. Discovery allocation forces the agent to find unknown ones. Over time, strong discovery sources can be promoted to the trust tier list.

---

## Mechanism 2: Citation Chasing

**What it does:** When a high-trust source (tier 1 or 2) cites external sources, the agent follows those citations and adds them to the candidate pool for that session — even if the cited source is not in the trust tier list.

**Implementation:**
- After retrieving a tier-1/2 source, extract cited URLs or author references from the content
- Add cited sources to the session candidate pool with a `citation_source` tag
- Score on content merit; citation origin noted in session metadata

**Why:** Academic papers, RAND reports, and Chatham House analyses cite primary documents, regional journals, and datasets that never appear in web search results. This is how you find the FAO data on Iranian food imports, the IISS military balance figures, or the obscure regional studies the major outlets are summarizing.

**Scope for POC:** Citation chasing on tier-1 sources only. Expand to tier-2 once validated.

---

## Mechanism 3: Lateral Domain Search

**What it does:** For each research topic or daily briefing run, allocates 1-2 search queries to adjacent domains rather than the primary topic directly.

**Adjacent domain mapping (configurable per topic):**

For geopolitics/security topics:
- Maritime insurance pricing (Lloyd's of London data)
- Shipping route utilization (AIS data aggregators)
- Agricultural commodity flows (FAO, USDA)
- Energy trade flow data (IEA, EIA raw data)

For finance/monetary topics:
- Central bank balance sheet data
- BIS quarterly review
- IMF Article IV consultations

For technology topics:
- Patent filings (USPTO, EPO, JPO — Japanese Patent Office)
- Academic preprint servers (arXiv cs.AI, cs.RO for robotics)
- Standards body publications (IEEE, ISO)

**Implementation:**
```python
# In research_config.json per topic, add optional lateral_domains list:
{
  "topic": "strait-of-hormuz",
  "lateral_domains": ["maritime_insurance", "commodity_flows", "shipping_data"]
}

# Agent runs 1-2 lateral queries per session using domain-specific query templates
```

**Why:** The signal often shows up in data before it shows up in analysis. Shipping route data showing vessels avoiding Hormuz is more current than any think tank report about it.

---

## Mechanism 4: Source Novelty Scoring

**What it does:** Any URL that has appeared in a previous session for the same topic is discounted in scoring, regardless of trust tier. Forces the agent to find new material.

**Implementation:**
```python
# In session scoring, after initial relevance score:
seen_urls = load_seen_urls(topic)  # all URLs from previous sessions

for source in candidates:
    if source.url in seen_urls:
        source.score *= NOVELTY_DISCOUNT  # configurable in research_config.json, default 0.3
    else:
        source.score *= NOVELTY_BONUS    # e.g. 1.1 — slight boost for new sources

# After session: append all surfaced URLs to seen_urls for this topic
```

**For Curator daily:** Novelty window is 7 days (not per-topic, per-domain). A source seen in the last 7 daily runs is discounted.

**Why:** Without this, the agent re-ranks the same EIA page in every session because it scores well on relevance. Novelty scoring forces it to look elsewhere.

---

## Source Priority Additions

### Academic Preprints
- **arXiv** — cs.AI, cs.RO (robotics), econ.GN (general economics), q-fin (quantitative finance)
- **SSRN** — economics, finance, political science working papers
- **NBER** — National Bureau of Economic Research working papers
- Search via API where available; web search with site: operator otherwise

### Non-Anglophone Sources
Priority languages: Japanese, French, German, Arabic, Mandarin (translated)

**Japanese sources — specific priority:**
Japan sits at the intersection of China geopolitics and the cutting edge of robotics/tech. Japanese-language sources provide analytical perspectives unavailable in English:
- Nikkei Asia (partial English, full Japanese behind paywall — web search accessible content)
- RIETI (Research Institute of Economy, Trade and Industry) — Japanese economic research, English summaries available
- NIDS (National Institute for Defense Studies) — Japanese defense analysis
- JPO patent filings — robotics, semiconductor, AI patents filed in Japan often precede Western awareness

**Translation approach:**
- Retrieve Japanese-language source
- Translate headline + abstract via API before scoring
- Score translated content; if score > threshold, translate full content for session findings
- Tag source as `[JA→EN]` in session output

**Why Japanese specifically:** Japan's analytical community produces serious work on China that doesn't get translated or cited in Western outlets. The robotics/tech angle is genuinely underserved in English — Japanese industry publications and patent filings are often 12-18 months ahead of English-language coverage.

### Think Tanks — Curated, Not Comprehensive
Add to trust tiers with explicit bias notes:

| Source | Trust Tier | Bias Note |
|---|---|---|
| RAND Corporation | Tier 1 | US defense establishment — good on operational/military questions |
| IISS | Tier 1 | UK-based, hard security focus, relatively independent |
| Chatham House | Tier 2 | UK foreign policy, strong MENA and Russia coverage |
| Lowy Institute | Tier 2 | Australia — Indo-Pacific lens, genuinely non-Washington |
| ECFR | Tier 2 | European — explicitly non-US-aligned perspective |
| Carnegie Endowment | Tier 2 | More nuanced on Russia/China than CFR |
| Observer Research Foundation | Tier 2 | India — strategic autonomy perspective |
| Stimson Center | Tier 2 | Independent, good on arms control and Asia |

**Explicitly excluded from trust tiers:** CFR, Brookings, CSIS as primary sources — too aligned with Washington consensus that is currently under geopolitical stress. May appear via discovery allocation or citation chasing; not seeded as trusted.

---

## Implementation Scope

This upgrade applies to both:
- `agent/research.py` — Research session source retrieval and scoring
- `agent/curator_rss_v2.py` (or equivalent) — Curator daily briefing source retrieval

Shared utility functions go in a new `agent/source_utils.py` to avoid duplication:

```
agent/
├── source_utils.py          # NEW — shared source quality utilities
│   ├── novelty_scorer()
│   ├── citation_chaser()
│   ├── lateral_query_builder()
│   └── translate_source()   # Japanese → English via API
├── research.py              # Modified — import from source_utils
├── curator_rss_v2.py        # Modified — import from source_utils
```

---

## Files Modified / Created

| File | Change |
|---|---|
| `agent/source_utils.py` | New — shared source quality utilities |
| `agent/research.py` | Import novelty scoring, discovery allocation, citation chasing, lateral search |
| `agent/curator_rss_v2.py` | Import novelty scoring, discovery allocation |
| `curator_sources.json` | Add new trust tier entries (think tanks, preprint servers) with bias notes |
| `research_config.json` | Add optional `lateral_domains` field per topic |

---

## Build Order

Build and validate one mechanism at a time. Do not ship all four simultaneously.

1. **Source novelty scoring** — smallest change, immediate impact, easy to verify
2. **Discovery allocation** — adds new sources, verify quality doesn't drop
3. **Lateral domain search** — requires lateral_domains config per topic, test on strait-of-hormuz
4. **Citation chasing** — most complex, build last, tier-1 sources only for POC
5. **Japanese source translation** — parallel track, can be built alongside any of the above

**Verification per mechanism:**
```bash
# After each mechanism: run a research session and check:
# - Are new URLs appearing that weren't in previous sessions?
# - Is source quality maintained or improved?
# - What is the cost delta from additional API calls?
```

---

## Cost Considerations

Additional API calls per session:
- Translation: ~$0.01-0.02 per Japanese source translated
- Citation chasing: 1-3 additional fetches per tier-1 source
- Lateral search: 1-2 additional search queries per session

Estimated total cost increase per session: $0.02-0.05. Acceptable given current $0.000-0.003 per session baseline.

---

## Acceptance Criteria

- [ ] Novelty scoring: no URL repeats across sessions for same topic within 30 days; log discarded sources per-session for debug\n- [ ] No regression in session diversity (e.g., Shannon index on sources before/after)
- [ ] Discovery allocation: 20-25% of session sources from outside trust tier list
- [ ] Lateral search: at least 1 lateral domain query per session when configured
- [ ] Citation chasing: tier-1 citations added to candidate pool
- [ ] Japanese sources: at least 1 translated source per relevant session (geopolitics/tech topics)
- [ ] Curator daily: same novelty scoring applied, 7-day window
- [ ] No regression in existing session quality
- [ ] Cost increase per session < $0.10

---

## Out of Scope

- Full multilingual search beyond Japanese in this build
- Automatic lateral_domains inference (manual config per topic for now)
- Real-time AIS shipping data integration (future workstream)
- Paywall bypass of any kind
 per topic for now)
- Real-time AIS shipping data integration (future workstream)
- Paywall bypass of any kind
