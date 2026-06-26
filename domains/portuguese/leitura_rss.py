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


def load_sources() -> dict:
    with open(SOURCES_FILE) as f:
        return json.load(f)


def _db_conn():
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:simple123@localhost:5432/personal_agents",
    )
    return psycopg2.connect(db_url)


def _clean_summary(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text or "").strip()
    return (clean[:300] + "…") if len(clean) > 300 else clean


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
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            articles.append({
                "url": link,
                "title": title,
                "excerpt": _clean_summary(entry.get("summary", "")),
                "published_at": _parse_date(entry),
            })
        log.info(f"  {source['name']}: {len(articles)} articles fetched")
        return articles
    except Exception as e:
        log.error(f"Feed fetch failed for {source['name']}: {e}")
        return []


def store_articles(
    articles: list[dict],
    source: dict,
    max_age_days: int,
    conn,
) -> int:
    cutoff = datetime.now() - timedelta(days=max_age_days)
    inserted = 0
    with conn.cursor() as cur:
        for article in articles:
            if article["published_at"] and article["published_at"] < cutoff:
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
    conn = _db_conn()
    try:
        total_new = 0
        for source in config["sources"]:
            if not source.get("active", True):
                continue
            log.info(f"Fetching {source['name']} ({source['category']})…")
            articles = fetch_feed(source, config["max_articles_per_source"])
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
