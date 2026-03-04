# Test Report Schema

Reports are generated from a structured JSON file using `scripts/generate_test_report.py`.

## Usage

```bash
# Standard run — MD only (renders in GitHub, no extras needed)
python3 scripts/generate_test_report.py --results path/to/results.json

# With explicit phase label
python3 scripts/generate_test_report.py --results path/to/results.json --phase phase3d

# With HTML (opt-in — only needed for styled local viewing)
python3 scripts/generate_test_report.py --results path/to/results.json --html
```

Produces (default):
- `docs/test-reports/YYYY-MM-DD-{phase}-ab-test.md`   ← private, full detail
- `docs/portfolio/{phase}-results.md`                 ← public, sanitized

With `--html` also produces:
- `docs/test-reports/YYYY-MM-DD-{phase}-ab-test.html` ← styled dark-theme, local viewing

---

## JSON Schema

```json
{
  "phase": "phase3c",
  "date": "2026-03-03",
  "title": "Phase 3C: Bookmark Enrichment A/B Test",
  "description": "One-sentence description of what changed between baseline and enriched.",
  "profile": {
    "baseline_size": 581,
    "enriched_size": 822,
    "baseline_metrics": {
      "content_domains": 0,
      "source_types": 0,
      "content_topics": 0
    },
    "enriched_metrics": {
      "content_domains": 3,
      "source_types": 2,
      "content_topics": 66
    }
  },
  "rankings": [
    {
      "rank_baseline": 1,
      "rank_enriched": 1,
      "delta": 0,
      "title_baseline": "Article title in Run A",
      "title_enriched": "Article title in Run B",
      "source_baseline": "sourceA.com",
      "source_enriched": "sourceA.com",
      "score_baseline": 21.0,
      "score_enriched": 21.0,
      "category": "geopolitics",
      "comment": "Priority boost dominates"
    }
  ],
  "observations": {
    "winners": ["sourceX.com article moved up N positions due to Y"],
    "losers":  ["sourceY.com article dropped N positions"],
    "new_top10": ["Category description of article that newly entered top 10"]
  },
  "cost": {
    "baseline_usd": 0.16,
    "enriched_usd": 0.16,
    "enrichment_overhead_usd": 0.001
  },
  "methodology": "Free text: how the test was run, what was held constant, what changed."
}
```

---

## Sanitization Rules

The portfolio version replaces identifiable signals with category labels.
Known patterns are in `scripts/generate_test_report.py` under `SANITIZE_PATTERNS`.

To add a new source mapping:
```python
SANITIZE_PATTERNS = [
    (r'newdomain\.com', 'category-label-X'),
    ...
]
```

Convention for portfolio labels:
- `[geopolitics-A]`, `[geopolitics-B]` — geopolitical sources
- `[market-analysis-A]` — market commentary
- `[intl-press-A]` — international newspapers
- `[regional-media-A]` — regional/national broadcast
- `[macro-substack-A]` — macro-focused newsletters
- `[commentary-A]` — opinion/commentary sources
- `[govt-data-A]` — government data sources
- `[newswire-A]` — wire services

---

## File Naming Convention

```
docs/test-reports/
  YYYY-MM-DD-{phase}-ab-test.html    ← private
  YYYY-MM-DD-{phase}-ab-test.md      ← private

docs/portfolio/
  {phase}-results.md                 ← public (sanitized)
```

Examples:
```
2026-03-03-phase3c-ab-test.html
2026-03-03-phase3c-ab-test.md
2026-04-15-phase3d-ab-test.html     ← image analysis test
2026-05-01-phase4a-ab-test.html     ← neo4j integration test
```
