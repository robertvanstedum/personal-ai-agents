# Spec — Portuguese Leitura: RSS + Web Article Pipeline
*Created: 2026-06-26 — Claude.ai*
*Status: built and deployed*
*Commit history: 6c909aa, 942c0f3, 26b65f3*
*File: domains/portuguese/leitura_rss.py*

---

## What this is

Daily article ingestion pipeline for the Portuguese Leitura tab.
Fetches from 16 sources across 4 categories. Sources use either
RSS (feedparser) or HTML scraping (BeautifulSoup) depending on
what the source supports. All sources produce the same article
format and store to `portuguese.articles`.

---

## Architecture

```
leitura_sources.json
    ↓
run_pipeline()
    ↓ routes by source["type"]
    ├── type: "rss"  → fetch_feed()   (feedparser)
    └── type: "web"  → fetch_web()    (requests + BeautifulSoup)
    ↓
store_articles()     (INSERT, deduplicate by URL)
    ↓
expire_old_articles() (deactivate articles > 30 days old)
```

---

## Source config

File: `domains/portuguese/data/leitura_sources.json`

Each source has:
- `name` — display name
- `url` — RSS feed URL or article listing page URL
- `category` — cotidiano / rio / cultura / noticias
- `level` — iniciante / intermediario / avancado
- `active` — true/false toggle
- `type` — "rss" (default) or "web" (BeautifulSoup scraping)

**16 sources, 4 per category:**

| Category | Source | Type | Level |
|----------|--------|------|-------|
| cotidiano | G1 | rss | iniciante |
| cotidiano | Metrópoles | rss | iniciante |
| cotidiano | O Dia | web | iniciante |
| cotidiano | Extra | rss | iniciante |
| rio | Veja Rio | rss | intermediario |
| rio | G1 Rio de Janeiro | rss | intermediario |
| rio | O Globo Rio | rss | intermediario |
| rio | O Dia Rio | web | iniciante |
| cultura | Rolling Stone Brasil | rss | intermediario |
| cultura | Folha Ilustrada | rss | intermediario |
| cultura | O Globo Cultura | rss | intermediario |
| cultura | Gshow | rss | iniciante |
| noticias | Agência Brasil | rss | intermediario |
| noticias | Folha de São Paulo | rss | avancado |
| noticias | CNN Brasil | rss | intermediario |
| noticias | UOL Notícias | rss | intermediario |

**Note on O Dia / O Dia Rio:** Same URL, different category.
Deduplication by URL means an article appears once in the DB.
Whichever category processes it first wins. Acceptable for v1.

---

## Curation rules

- Max 3 articles per source per run
- Skip articles older than 7 days
- Deactivate articles older than 30 days on each run
- Deduplicate by URL
- No scoring — all articles from active sources eligible
- User browses by category and self-selects

---

## Key functions

### fetch_feed(url, max_articles)
RSS ingestion via feedparser. Returns list of article dicts:
```python
{"url": str, "title": str, "excerpt": str, "published_at": datetime|None}
```
Logs article count. Falls back gracefully on parse errors.

### fetch_web(url, max_articles)
HTML scraping via requests + BeautifulSoup.
Current selector for O Dia: `article.teaser[data-matia]`
Returns same dict format as fetch_feed().
Logs article count.

**Selector fragility note:** Brazilian news sites redesign
frequently. If a web source returns 0 articles, check whether
the CSS selector still matches the current HTML structure.

### store_articles(articles, source, conn)
INSERT into `portuguese.articles`.
`ON CONFLICT (url) DO NOTHING` for deduplication.
Skips articles older than max_age_days.
Returns count of new articles inserted.

### expire_old_articles(retention_days, conn)
Deactivates articles older than retention_days.
Returns count of deactivated articles.

### run_pipeline()
Main entry point. Routes by source type, stores, expires.

**Zero-article warning (commit 26b65f3):**
After each fetch, if count is 0:
```
WARNING: {source}: 0 articles — check selector or feed
```
Silent failures are visible without breaking the pipeline.

---

## Database

Table: `portuguese.articles` (migration 004)

Key columns:
- `url VARCHAR(500) UNIQUE` — deduplication key
- `category VARCHAR(50)` — cotidiano/rio/cultura/noticias
- `level VARCHAR(20)` — iniciante/intermediario/avancado
- `full_text TEXT` — NULL on ingest, populated on demand
- `is_active BOOLEAN` — FALSE after 30 days

---

## Schedule

### Mac — launchd
`~/Library/LaunchAgents/com.user.portuguese-leitura.plist`
Runs daily at 06:30. Logs to `logs/portuguese_leitura_stdout.log`.

### EC2 — cron
`/etc/cron.d/minimoi`
```
30 6 * * * ec2-user docker exec minimoi-portuguese \
  python3 domains/portuguese/leitura_rss.py
```

**Important:** `beautifulsoup4` in `docker/requirements.portuguese.txt`
as of commit 942c0f3. Web scraping requires a full Docker image
rebuild — not just a container restart. CI/CD handles this on
every push to main.

---

## Dependencies

```
feedparser>=6.0.0
beautifulsoup4>=4.12.0
requests>=2.31.0
```

All in `docker/requirements.portuguese.txt`.

---

## Adding a new web source

1. Find article listing page URL
2. Identify CSS selector for article elements
3. Add to `fetch_web()` if custom selector needed
4. Add to `leitura_sources.json` with `"type": "web"`
5. Test locally, confirm count > 0 in logs

Extra and Gshow are candidates if RSS proves unreliable.

---

## Monitoring

After each cron run, check for:
- `{source}: N articles fetched` — all sources
- `WARNING: {source}: 0 articles` — broken selector or feed
- `ModuleNotFoundError: beautifulsoup4` — image needs rebuild

```bash
aws ssm send-command \
  --instance-ids i-0d13db821169627e2 \
  --document-name "AWS-RunShellScript" \
  --parameters "commands=['docker logs minimoi-portuguese --tail 50']"
```

---

## Flask route

`GET /api/pt/leitura-category?category=cotidiano`

Returns `portuguese.articles` for category, ordered by
`published_at DESC`, limit 20, `is_active = TRUE` only.

---

*Spec · Portuguese Leitura Pipeline · 2026-06-26 · Claude.ai*
*Reflects actual implementation — commits 6c909aa, 942c0f3, 26b65f3*
*Commit to docs/specs/spec_portuguese_leitura_rss_2026-06-26.md*
