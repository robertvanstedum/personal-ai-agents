#!/usr/bin/env python3
"""
Portuguese Leitura RSS ingestion pipeline.
Fetches articles from configured sources, stores to portuguese.articles.
Run daily via launchd (Mac) and cron (EC2).

Pattern: mirrors curator_rss_v2.py — requests.get + feedparser.parse(content).
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import psycopg2
import requests

log = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).parent / "data" / "leitura_sources.json"
_USER_AGENT = "Mozilla/5.0 (compatible; RSS Reader Bot)"

# CONTENT QUALITY RULES (do not change without spec update)
# 1. O Dia: rio category only — never cotidiano
# 2. Minimum excerpt length: 30 characters
# 3. Articles older than 7 days: skip on ingest
# 4. Articles older than 30 days: deactivate on pipeline run
# 5. Max 3 articles per source per run
# 6. Duplicate URLs: skip (ON CONFLICT DO NOTHING)
# 7. Empty full_text: fetch on demand, cache result
# 8. Fetch failures: show "Abrir na fonte" link, never blank
MIN_EXCERPT_LENGTH = 30

# Sources that must never appear in wrong categories
_CATEGORY_RULES = {
    "odia.ig.com.br": "rio",
}


def load_sources() -> dict:
    with open(SOURCES_FILE) as f:
        return json.load(f)


def validate_sources(config: dict) -> bool:
    """Abort pipeline if source config has known bad assignments or duplicates."""
    seen_urls: dict[str, str] = {}
    ok = True
    for source in config.get("sources", []):
        url = source.get("url", "")
        cat = source.get("category", "")
        name = source.get("name", url)
        for domain, required_cat in _CATEGORY_RULES.items():
            if domain in url and cat != required_cat:
                log.error(
                    f"SOURCE CONFIG ERROR: {name} assigned to '{cat}' "
                    f"but must be '{required_cat}'. Fix leitura_sources.json."
                )
                ok = False
        if url in seen_urls:
            log.warning(
                f"Duplicate URL: {name} ({cat}) shares URL with {seen_urls[url]}. "
                f"Only first will store."
            )
        seen_urls[url] = f"{name} ({cat})"
    return ok


def _db_conn():
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:simple123@localhost:5432/personal_agents",
    )
    return psycopg2.connect(db_url)


def _strip_cdata(text: str) -> str:
    text = text.strip()
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        return text[9:-3].strip()
    return text


def _clean_summary(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text or "").strip()
    return (clean[:300] + "…") if len(clean) > 300 else clean


def _fetch_excerpt_snippet(url: str, max_chars: int = 300) -> str:
    """Fetch first meaningful paragraph from URL. Fallback when RSS has no excerpt."""
    try:
        from bs4 import BeautifulSoup
        res = requests.get(url, timeout=5, headers={"User-Agent": _USER_AGENT})
        soup = BeautifulSoup(res.content, "html.parser")
        for sel in ["article", "main", "[class*='content']", "[class*='materia']"]:
            section = soup.select_one(sel)
            if section:
                for p in section.find_all("p"):
                    text = p.get_text().strip()
                    if len(text) > 50:
                        return text[:max_chars]
        for p in soup.find_all("p"):
            text = p.get_text().strip()
            if len(text) > 50:
                return text[:max_chars]
    except Exception:
        pass
    return ""


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6])
            except Exception:
                pass
    return None


def fetch_feed(source: dict, max_articles: int) -> list[dict]:
    url = source["url"]
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        articles = []
        for entry in feed.entries[:max_articles]:
            link = entry.get("link", "")
            title = _strip_cdata((entry.get("title") or "").strip())
            if not link or not title:
                continue
            articles.append({
                "url": link,
                "title": title,
                "excerpt": _clean_summary(_strip_cdata(entry.get("summary", ""))),
                "published_at": _parse_date(entry),
            })
        log.info(f"  {source['name']}: {len(articles)} articles fetched")
        return articles
    except Exception as e:
        log.error(f"Feed fetch failed for {source['name']}: {e}")
        return []


def fetch_web(source: dict, max_articles: int) -> list[dict]:
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(
            source["url"],
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        articles = []
        for el in soup.select("article.teaser[data-matia]"):
            if len(articles) >= max_articles:
                break
            a = el.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if not href.startswith("http"):
                href = "https://odia.ig.com.br" + href
            title = a.get_text(strip=True) or el.get_text(strip=True)
            title = title[:200].strip()
            if len(title) < 10:
                continue
            # Try to grab teaser body text (description/subtitle in listing)
            teaser_el = el.find(class_=lambda c: c and any(
                x in c for x in ("teaser-text", "description", "subtitle", "summary", "lead")
            )) or el.find("p")
            excerpt = teaser_el.get_text(strip=True)[:300] if teaser_el else ""
            articles.append({
                "url": href,
                "title": title,
                "excerpt": excerpt,
                "published_at": None,
            })
        log.info(f"  {source['name']}: {len(articles)} articles scraped")
        return articles
    except Exception as e:
        log.error(f"Web scrape failed for {source['name']}: {e}")
        return []


def store_articles(
    articles: list[dict],
    source: dict,
    max_age_days: int,
    conn,
) -> int:
    cutoff = datetime.now() - timedelta(days=max_age_days)
    inserted = 0
    skipped_empty = 0
    with conn.cursor() as cur:
        for article in articles:
            if article["published_at"] and article["published_at"] < cutoff:
                continue
            excerpt = (article.get("excerpt") or "").strip()
            if len(excerpt) < MIN_EXCERPT_LENGTH:
                snippet = _fetch_excerpt_snippet(article["url"])
                if len(snippet) >= MIN_EXCERPT_LENGTH:
                    article["excerpt"] = snippet
                else:
                    log.warning(f"Skipping — empty excerpt: {article['url']}")
                    skipped_empty += 1
                    continue
            cur.execute(
                """
                INSERT INTO portuguese.articles
                  (url, title, excerpt, source, category, level, published_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (
                    article["url"],
                    article["title"],
                    article["excerpt"],
                    source["name"],
                    source["category"],
                    source["level"],
                    article["published_at"],
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
    conn.commit()
    if skipped_empty:
        log.info(f"Skipped {skipped_empty} articles with empty excerpts")
    return inserted


def expire_old_articles(retention_days: int, conn) -> int:
    cutoff = datetime.now() - timedelta(days=retention_days)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE portuguese.articles
               SET is_active = FALSE
             WHERE added_at < %s
               AND is_active = TRUE
            """,
            (cutoff,),
        )
        count = cur.rowcount
    conn.commit()
    return count


def run_pipeline() -> int:
    config = load_sources()
    if not validate_sources(config):
        log.error("Pipeline aborted — source config errors")
        return 0
    conn = _db_conn()
    try:
        total_new = 0
        for source in config["sources"]:
            if not source.get("active", True):
                continue
            log.info(f"Fetching {source['name']} ({source['category']})…")
            src_type = source.get("type", "rss")
            if src_type == "web":
                articles = fetch_web(source, config["max_articles_per_source"])
            else:
                articles = fetch_feed(source, config["max_articles_per_source"])
            if not articles:
                log.warning(f"{source['name']}: 0 articles — check selector or feed")
            new = store_articles(articles, source, config["max_age_days"], conn)
            log.info(f"  → {new} new stored")
            total_new += new

        expired = expire_old_articles(config["retention_days"], conn)
        log.info(f"Pipeline complete: {total_new} new, {expired} expired")
        return total_new
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    total = run_pipeline()
    print(f"Done. {total} new articles.")
