# Bug Fix: Source Variety Cap — Leitura (Portuguese) + Lesen (German)
*Created: 2026-06-29 · Claude.ai (design) → Claude Code (build)*

---

## Issue

**Type:** Bug fix (track as GitHub issue, fix immediately)
**Affects:** Both Portuguese (Leitura) and German (Lesen) reading domains
**Symptom:** Single source can dominate a category. Observed today: ORF Sport
supplied 5 of ~10 German sport articles. Metrópoles similarly heavy in
Portuguese cotidiano. This is a selection defect, not expected behavior.

**Root cause:** Article selection at projection/display time has no per-source
cap. All scored articles from a source pass through if they score well enough.

---

## Rule (identical for both domains)

```
MAX_ARTICLES_PER_CATEGORY = 10
MAX_ARTICLES_PER_SOURCE_PER_CATEGORY = 3
MIN_ARTICLES_PER_CATEGORY = 3  (show at least 3 if content exists)
```

**Selection logic within each category:**
1. Rank all candidate articles by existing score (descending).
2. Greedily select: take highest-scored article, increment source counter.
3. Skip any article whose source has already hit MAX_ARTICLES_PER_SOURCE_PER_CATEGORY.
4. Continue until MAX_ARTICLES_PER_CATEGORY reached or candidates exhausted.
5. If result count < MIN_ARTICLES_PER_CATEGORY, relax cap for the highest-scoring
   source only (fill to floor). This handles thin-source days without breaking
   the general rule.

**Applied at:** Selection / projection layer — NOT at ingestion. The DB retains
all scored articles. The cap is a display-time filter. This preserves
JSON-as-source-of-truth and allows the rule to change without re-running the
pipeline.

---

## Sibling parity requirement

German (Lesen) and Portuguese (Leitura) **must use identical selection logic
and identical config keys.** The only differences between them are:
- Source lists (leitura_sources.json vs. lesen_sources.json or equivalent)
- Language of content

No domain-specific selection logic. If the fix requires a code change, the
same change applies to both. If one drifts from the other in the future, that
is a bug.

---

## Config shape (both domains, same keys)

In each domain's config file:

```json
{
  "article_selection": {
    "max_per_category": 10,
    "max_per_source_per_category": 3,
    "min_per_category": 3
  }
}
```

These values are the defaults. They should be overridable per-category if
needed in the future (e.g., a thin category), but per-category override is
NOT required in this fix — defaults only for now.

---

## ORF source IDs (German)

ORF Wien and ORF Sport are treated as **distinct sources** — topics differ
(local news vs. sport) and that distinction is intentional. The cap applies
per source ID as stored in the config. No parent-publication grouping needed.
ORF Sport producing 5 sport articles is the bug this fix addresses — after
the cap, ORF Sport max is 3 in the sport category.

---

## Definition of Done

- [ ] Portuguese Leitura: no category shows more than 3 articles from one source
- [ ] German Lesen: no category shows more than 3 articles from one source
- [ ] Both domains: cap values are in config, not hardcoded
- [ ] Both domains: config keys are identical (same shape, same key names)
- [ ] Selection logic is the same code path for both (no domain-specific branching)
- [ ] Floor respected: if a category has content, at least 3 articles show
- [ ] Existing scoring/ranking is preserved — cap filters, does not re-rank
- [ ] No changes to ingestion pipeline or DB schema
- [ ] Tested on dev.minimoi.ai: Portuguese cotidiano, German sport — verify
      ORF Sport shows ≤3, Metrópoles shows ≤3 in cotidiano

---

## Commit

```
fix: source variety cap for Leitura and Lesen article selection

- Max 3 articles per source per category (was unlimited)
- Max 10 articles per category, floor of 3
- Config-driven (article_selection keys in domain config)
- Applied at selection layer, not ingestion
- German and Portuguese use identical logic and config shape
- Fixes ORF Sport dominating German sport category (5→≤3)
- Fixes Metrópoles dominating Portuguese cotidiano

Closes #[ISSUE_NUMBER]
```

---

## GitHub issue text (open before building)

**Title:** Bug: single source dominates category in article selection

**Body:**
Article selection in Leitura (Portuguese) and Lesen (German) has no
per-source cap, allowing one source to supply the majority of articles
in a category. Observed: ORF Sport 5/~10 German sport articles today;
Metrópoles similarly heavy in Portuguese cotidiano.

Fix: max 3 articles per source per category, applied at selection/display
layer. Config-driven. Identical logic for both domains.

**Label:** bug
**Priority:** fix immediately (next build)

---

*Spec · 2026-06-29 · Claude.ai → Claude Code · both domains affected*
