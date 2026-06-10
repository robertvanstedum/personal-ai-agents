"""
Loop A — Career Focus Scout
Runs twice daily (06:00 + 18:00).
Searches Tavily + RSS, scores candidates, writes to DB (or JSON fallback),
pings rvsopenbot for high-score results.

Date filter:
  fresh   — published ≤ 48 hours ago  → ping threshold: score ≥ 7.0
  recent  — published 3–14 days ago   → ping threshold: score ≥ 9.0
  unknown — no date available         → treated as recent
  old     — published > 14 days ago   → discarded before LLM scoring
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import keyring
import requests
from openai import OpenAI

logger = logging.getLogger("cos.loop_a")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[4]
COS_CONTEXT_PATH = REPO_ROOT / "domains/guild/config/cos_context.json"
NETWORK_COMPANIES_PATH = REPO_ROOT / "domains/guild/data/network_companies.json"
OPPORTUNITIES_FILE = REPO_ROOT / "data/guild/career_opportunities.json"

# ---------------------------------------------------------------------------
# RSS sources
# ---------------------------------------------------------------------------
RSS_SOURCES = {
    "indeed_tpm_chicago": "https://www.indeed.com/rss?q=technical+product+manager+AI&l=Chicago%2C+IL",
    "indeed_ai_chicago":  "https://www.indeed.com/rss?q=principal+engineer+agentic+AI&l=Chicago%2C+IL",
    "indeed_remote_ai":   "https://www.indeed.com/rss?q=principal+engineer+AI&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11",
    "indeed_director_ai": "https://www.indeed.com/rss?q=director+AI+technology&l=Chicago%2C+IL",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_context() -> dict:
    with open(COS_CONTEXT_PATH) as f:
        return json.load(f)


def _load_network_companies() -> list[dict]:
    """Returns list of {company, contact_count, contacts}. Empty list if file missing."""
    if not NETWORK_COMPANIES_PATH.exists():
        return []
    try:
        with open(NETWORK_COMPANIES_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _load_existing_urls() -> set[str]:
    """Try DB first, fall back to JSON file."""
    urls: set[str] = set()

    # Try DB
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="personal_agents",
            user="minimoi", password="simple123"
        )
        cur = conn.cursor()
        cur.execute("SELECT url FROM jobs.career_opportunities WHERE url IS NOT NULL")
        urls = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()
        return urls
    except Exception:
        pass

    # Fall back to JSON
    if OPPORTUNITIES_FILE.exists():
        try:
            with open(OPPORTUNITIES_FILE) as f:
                existing = json.load(f)
            urls = {r["url"] for r in existing if r.get("url")}
        except Exception:
            pass
    return urls


def _write_opportunity(record: dict) -> bool:
    """Write to DB if available, always write to JSON fallback. Returns True on success."""
    # Ensure fallback dir exists
    OPPORTUNITIES_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Try DB
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="personal_agents",
            user="minimoi", password="simple123"
        )
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO jobs.career_opportunities
              (title, company, geo, url, opportunity_type, fit_score, fit_narrative,
               warm_lead, warm_lead_contacts, cos_notes, source, model_used, status, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'suggested','cos_loop_a')
            ON CONFLICT (url) DO NOTHING
            """,
            (
                record.get("title"), record.get("company"), record.get("geo"),
                record.get("url"), record.get("opportunity_type"),
                record.get("fit_score"), record.get("fit_narrative"),
                record.get("warm_lead", False), record.get("warm_lead_contacts"),
                record.get("cos_notes"), record.get("source"), record.get("model_used"),
            )
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.debug("DB write skipped (%s), using file fallback", e)

    # Always write to JSON fallback
    try:
        existing: list = []
        if OPPORTUNITIES_FILE.exists():
            with open(OPPORTUNITIES_FILE) as f:
                existing = json.load(f)
        # dedup by url
        existing_urls = {r.get("url") for r in existing}
        if record.get("url") not in existing_urls:
            record["created_at"] = datetime.now(timezone.utc).isoformat()
            existing.append(record)
            with open(OPPORTUNITIES_FILE, "w") as f:
                json.dump(existing, f, indent=2)
    except Exception as e:
        logger.error("JSON fallback write failed: %s", e)
        return False
    return True


def _grok_client() -> OpenAI:
    api_key = keyring.get_password("xai", "api_key")
    return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")


def _send_telegram(msg: str) -> None:
    token = keyring.get_password("telegram", "bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "8379221702")
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

# Maximum age to accept at all
MAX_AGE_DAYS = 14
FRESH_AGE_HOURS = 48

_NOW = None  # set at start of each run so the whole run uses one consistent "now"


def _run_now() -> datetime:
    return _NOW or datetime.now(timezone.utc)


def _parse_published(raw: str | None) -> datetime | None:
    """Parse a date string from Tavily or feedparser into a UTC-aware datetime."""
    if not raw:
        return None
    # feedparser struct_time tuple
    if hasattr(raw, "tm_year"):
        try:
            import calendar
            ts = calendar.timegm(raw)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    raw = str(raw).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _age_tier(published_date: str | None) -> str:
    """
    Classify a result by age:
      fresh   — within FRESH_AGE_HOURS (48h)
      recent  — within MAX_AGE_DAYS (14d)
      old     — older than MAX_AGE_DAYS → discard
      unknown — no date, treated as recent
    """
    if not published_date:
        return "unknown"
    dt = _parse_published(published_date)
    if dt is None:
        return "unknown"
    age = _run_now() - dt
    if age <= timedelta(hours=FRESH_AGE_HOURS):
        return "fresh"
    if age <= timedelta(days=MAX_AGE_DAYS):
        return "recent"
    return "old"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search_tavily(ctx: dict) -> list[dict]:
    """
    Dual-pass Tavily search for date tiering:
      Pass 1 — days=2  → results tagged as 'fresh'
      Pass 2 — days=14 → results not seen in pass 1 tagged as 'recent'

    Tavily does not return per-result published_date on basic search,
    so the days param on the request is the only reliable date signal.
    """
    api_key = keyring.get_password("tavily", "api_key")
    if not api_key:
        logger.warning("Tavily API key not found — skipping Tavily search")
        return []

    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)

    cf = ctx["career_focus"]
    roles = cf["target_roles"][:5]
    intl_markets = cf["geo_priority"][3]["markets"][:2]

    queries = []
    for role in roles[:3]:
        queries.append(f'"{role}" Chicago')
    queries.append(f'"{roles[0]}" remote')
    queries.append('"agentic AI" site:careers.telekom.com')
    for market in intl_markets[:2]:
        queries.append(f'"telecom AI" {market}')
    queries.append('"agentic AI" jobs')
    queries = queries[:10]

    def _run_pass(day_limit: int, tier_label: str, skip_urls: set) -> tuple[list[dict], set]:
        pass_results: list[dict] = []
        seen: set[str] = set()
        for q in queries:
            try:
                response = client.search(
                    q,
                    max_results=8,
                    search_depth="basic",
                    days=day_limit,
                )
                for r in response.get("results", []):
                    url = r.get("url", "")
                    if not url or url in skip_urls or url in seen:
                        continue
                    seen.add(url)
                    pass_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("content", "")[:500],
                        "source": "tavily",
                        "query": q,
                        "published_date": None,   # Tavily basic doesn't return dates
                        "age_tier": tier_label,
                    })
                time.sleep(0.3)
            except Exception as e:
                logger.warning("Tavily query failed '%s' (days=%d): %s", q, day_limit, e)
        return pass_results, seen

    # Pass 1 — fresh (≤48h)
    fresh_results, fresh_urls = _run_pass(FRESH_AGE_HOURS // 24 or 1, "fresh", set())
    logger.info("Tavily fresh pass (days=2): %d results", len(fresh_results))

    # Pass 2 — recent (≤14d), skip anything already in fresh pass
    recent_results, _ = _run_pass(MAX_AGE_DAYS, "recent", fresh_urls)
    logger.info("Tavily recent pass (days=14): %d new results", len(recent_results))

    return fresh_results + recent_results


def _fetch_rss() -> list[dict]:
    """Fetch Indeed RSS feeds, fail gracefully per feed. Extracts published date."""
    results: list[dict] = []
    for feed_name, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                # feedparser provides published_parsed (struct_time) or published (string)
                pub_raw = entry.get("published_parsed") or entry.get("published") or None
                results.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "snippet": entry.get("summary", "")[:500],
                    "company": entry.get("source", {}).get("title", "") if hasattr(entry, "source") else "",
                    "source": feed_name,
                    "published_date": pub_raw,   # will be normalised by _age_tier()
                })
        except Exception as e:
            logger.warning("RSS feed %s failed: %s", feed_name, e)
    return results


# ---------------------------------------------------------------------------
# Deduplication + date filter
# ---------------------------------------------------------------------------

def _deduplicate(candidates: list[dict], existing_urls: set[str]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for c in candidates:
        url = c.get("url", "")
        if not url or url in existing_urls or url in seen:
            continue
        seen.add(url)
        out.append(c)
    return out


def _apply_date_filter(candidates: list[dict]) -> tuple[list[dict], int]:
    """
    Stamp age_tier on candidates and discard 'old' ones.
    - Tavily results arrive pre-tagged ('fresh' or 'recent') from dual-pass search
    - RSS results have published_date set; use _age_tier() to classify them
    - Anything without a tag or date defaults to 'unknown' (treated as recent threshold)
    Returns (kept_candidates, discarded_count).
    """
    kept: list[dict] = []
    discarded = 0
    tier_counts: dict[str, int] = {"fresh": 0, "recent": 0, "unknown": 0, "old": 0}

    for c in candidates:
        # Use pre-tagged tier if present (Tavily dual-pass), else classify from date
        if c.get("age_tier") in ("fresh", "recent"):
            tier = c["age_tier"]
        else:
            tier = _age_tier(c.get("published_date"))
            c["age_tier"] = tier

        tier_counts[tier] += 1
        if tier == "old":
            discarded += 1
        else:
            kept.append(c)

    logger.info(
        "Date filter — fresh=%d recent=%d unknown=%d old=%d (discarded)",
        tier_counts["fresh"], tier_counts["recent"],
        tier_counts["unknown"], tier_counts["old"],
    )
    return kept, discarded


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _filter_pass(candidates: list[dict], ctx: dict) -> list[dict]:
    """Cheap LLM pass — discard score < 5.
    Instructs the model to distinguish actual job listings from news articles."""
    if not candidates:
        return []

    client = _grok_client()
    cf = ctx["career_focus"]
    narrative = cf["narrative"]
    roles_str = ", ".join(cf["target_roles"][:6])

    shortlist: list[dict] = []
    for c in candidates:
        prompt = (
            f"Score this result 0-10 as a job opportunity fit for this candidate.\n"
            f"IMPORTANT scoring rules:\n"
            f"- A specific job listing or job board page: score normally based on fit\n"
            f"- A news article about a company expanding (no specific job): score ≤ 3\n"
            f"- An aggregator page listing many unrelated jobs: score ≤ 4\n"
            f"- A direct job listing URL (careers.*, jobs.*, linkedin job, indeed job): score normally\n"
            f"STALENESS rules — return score: 0 immediately if any of these are true:\n"
            f"- Snippet or title mentions 'no longer accepting', 'position filled', 'job closed', 'expired'\n"
            f"- Snippet shows 'reposted' with a date more than 14 days ago (e.g. 'reposted 1 month ago', 'reposted 3 weeks ago')\n"
            f"- Snippet contains an explicit posting date older than 14 days\n"
            f"- Any signal the role is closed or applications are not being accepted\n"
            f"Return JSON only: {{\"score\": N, \"reason\": \"one line\", \"is_job_listing\": true/false, \"stale\": true/false}}\n\n"
            f"Candidate: {narrative}\n"
            f"Target roles: {roles_str}\n\n"
            f"Title: {c.get('title', '')}\n"
            f"URL: {c.get('url', '')}\n"
            f"Company: {c.get('company', '')}\n"
            f"Snippet: {c.get('snippet', '')[:300]}"
        )
        try:
            resp = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            score = float(parsed.get("score", 0))
            stale = parsed.get("stale", False)
            if stale:
                logger.debug("Stale/closed listing dropped: %s", c.get("title", "")[:60])
            elif score >= 5:
                c["_filter_score"] = score
                c["_is_job_listing"] = parsed.get("is_job_listing", False)
                shortlist.append(c)
        except Exception as e:
            logger.debug("Filter pass error for '%s': %s", c.get("title"), e)
    return shortlist


def _evaluate_pass(shortlist: list[dict], ctx: dict) -> list[dict]:
    """Quality eval on shortlist — returns scored opportunity records."""
    if not shortlist:
        return []

    client = _grok_client()
    cf = ctx["career_focus"]

    evaluated: list[dict] = []
    for c in shortlist:
        prompt = (
            f"Evaluate this job opportunity for Robert van Stedum.\n"
            f"Return JSON only with these exact keys:\n"
            f'  {{"fit_score": 0-10, "fit_narrative": "2-3 sentences", '
            f'"opportunity_type": "employment|contract|advisory", "cos_notes": "any flags or empty string"}}\n\n'
            f"Career focus context:\n"
            f"Deadline: {cf['deadline']} — {cf.get('urgency_note','')}\n"
            f"Narrative: {cf['narrative']}\n"
            f"Target roles: {', '.join(cf['target_roles'])}\n"
            f"Mode preference: {cf['mode']}\n"
            f"Sector focus: {cf['sector_focus']['primary']}; {cf['sector_focus']['secondary']}\n\n"
            f"Opportunity:\n"
            f"Title: {c.get('title','')}\n"
            f"Company: {c.get('company','')}\n"
            f"URL: {c.get('url','')}\n"
            f"Description: {c.get('snippet','')[:600]}"
        )
        try:
            resp = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw = resp.choices[0].message.content.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            evaluated.append({
                "title": c.get("title", ""),
                "company": c.get("company", ""),
                "geo": _infer_geo(c),
                "url": c.get("url", ""),
                "opportunity_type": parsed.get("opportunity_type", "employment"),
                "fit_score": float(parsed.get("fit_score", 0)),
                "fit_narrative": parsed.get("fit_narrative", ""),
                "cos_notes": parsed.get("cos_notes", ""),
                "source": c.get("source", "unknown"),
                "model_used": "grok-4-1-fast-reasoning",
                "warm_lead": False,
                "warm_lead_contacts": None,
                "published_date": c.get("published_date"),
                "age_tier": c.get("age_tier", "unknown"),
            })
        except Exception as e:
            logger.warning("Eval pass error for '%s': %s", c.get("title"), e)
    return evaluated


def _infer_geo(c: dict) -> str:
    url = c.get("url", "").lower()
    snippet = (c.get("snippet", "") + " " + c.get("title", "")).lower()
    if "chicago" in snippet:
        return "Chicago, IL"
    if "remote" in snippet or "remote" in url:
        return "Remote"
    return "Unknown"


# ---------------------------------------------------------------------------
# Warm lead check
# ---------------------------------------------------------------------------

def _warm_lead_check(opportunities: list[dict], network: list[dict]) -> list[dict]:
    """Fuzzy match company against network. Boosts score on match."""
    if not network:
        return opportunities

    network_map: dict[str, list[str]] = {
        n["company"].lower(): [c if isinstance(c, str) else c.get("name", "") for c in n.get("contacts", [])]
        for n in network
    }
    company_names = list(network_map.keys())

    for opp in opportunities:
        company = (opp.get("company") or "").lower().strip()
        if not company:
            continue
        # direct match
        if company in network_map:
            opp["warm_lead"] = True
            opp["warm_lead_contacts"] = ", ".join(network_map[company][:3])
            opp["fit_score"] = min(10.0, opp["fit_score"] * 2.0)
            continue
        # partial match (company is substring of network entry or vice versa)
        for net_company in company_names:
            if (len(company) >= 4 and company in net_company) or \
               (len(net_company) >= 4 and net_company in company):
                opp["warm_lead"] = True
                opp["warm_lead_contacts"] = ", ".join(network_map[net_company][:3])
                opp["fit_score"] = min(10.0, opp["fit_score"] * 2.0)
                break
    return opportunities


# ---------------------------------------------------------------------------
# Notify
# ---------------------------------------------------------------------------

# Thresholds by age tier
# fresh (≤48h):   ping if score ≥ 7.0  (or warm_lead + score ≥ 6.0)
# recent (3-14d): ping if score ≥ 9.0  (or warm_lead + score ≥ 8.0)
# unknown:        same as recent
_PING_THRESHOLD = {
    "fresh":   (7.0, 6.0),   # (base, warm_lead)
    "recent":  (9.0, 8.0),
    "unknown": (9.0, 8.0),
}
MAX_PINGS_PER_RUN = 5


def _should_ping(opp: dict) -> bool:
    tier = opp.get("age_tier", "unknown")
    base_thresh, warm_thresh = _PING_THRESHOLD.get(tier, (9.0, 8.0))
    if opp.get("warm_lead") and opp["fit_score"] >= warm_thresh:
        return True
    return opp["fit_score"] >= base_thresh


def _notify_telegram(opportunities: list[dict]) -> int:
    """
    Send age-tiered Telegram alerts.
    Fresh results (≤48h) surface at score ≥ 7.0.
    Recent results (3-14d) only at score ≥ 9.0.
    Caps at MAX_PINGS_PER_RUN. Sorts fresh-first, then by score.
    Returns number of pings sent.
    """
    alerts = [o for o in opportunities if _should_ping(o)]
    if not alerts:
        return 0

    # Sort: fresh first, then by score descending
    tier_order = {"fresh": 0, "unknown": 1, "recent": 2}
    alerts.sort(key=lambda o: (tier_order.get(o.get("age_tier", "unknown"), 1), -o["fit_score"]))
    alerts = alerts[:MAX_PINGS_PER_RUN]

    sent = 0
    for opp in alerts:
        tier = opp.get("age_tier", "unknown")
        age_icon = "🆕" if tier == "fresh" else "📅"
        warm_line = f"\n⭐ Warm lead — {opp['warm_lead_contacts']}" if opp.get("warm_lead") else ""
        pub_note = f" · posted {_age_label(opp.get('published_date'), tier)}" if opp.get("published_date") else ""
        msg = (
            f"{age_icon} <b>Career opportunity:</b>\n"
            f"{opp['title']} — {opp.get('company','')}\n"
            f"{opp.get('geo','')} · {opp.get('opportunity_type','')}{pub_note}\n"
            f"Score: {opp['fit_score']:.1f}/10\n"
            f"{opp.get('fit_narrative','')}"
            f"{warm_line}\n"
            f"{opp.get('url','')}"
        )
        _send_telegram(msg)
        sent += 1
        time.sleep(0.5)
    return sent


def _age_label(published_date, tier: str) -> str:
    """Human-readable age label for the Telegram message."""
    if not published_date:
        return ""
    dt = _parse_published(published_date)
    if not dt:
        return ""
    age = _run_now() - dt
    hours = int(age.total_seconds() / 3600)
    if hours < 24:
        return f"{hours}h ago"
    days = age.days
    return f"{days}d ago"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_career_focus_scout() -> dict:
    """
    Run Loop A — Career Focus Scout.
    Returns summary dict with candidate counts, written, notified, and age tier breakdown.
    """
    global _NOW
    _NOW = datetime.now(timezone.utc)

    logger.info("Loop A — career focus scout starting (window: ≤%dd, fresh: ≤%dh)",
                MAX_AGE_DAYS, FRESH_AGE_HOURS)
    start = time.time()

    ctx = _load_context()
    network = _load_network_companies()
    existing_urls = _load_existing_urls()

    # Gather raw candidates
    raw: list[dict] = []
    raw.extend(_search_tavily(ctx))
    raw.extend(_fetch_rss())
    logger.info("Raw candidates: %d", len(raw))

    # Deduplicate against known URLs
    candidates = _deduplicate(raw, existing_urls)
    logger.info("After dedup: %d", len(candidates))

    if not candidates:
        logger.info("No new candidates — loop A done")
        return {"candidates_raw": len(raw), "candidates_shortlist": 0,
                "written": 0, "notified": 0, "discarded_old": 0}

    # Date filter — stamp age_tier, drop anything older than MAX_AGE_DAYS
    candidates, discarded_old = _apply_date_filter(candidates)
    logger.info("After date filter: %d kept, %d discarded as too old", len(candidates), discarded_old)

    if not candidates:
        logger.info("All candidates too old — loop A done")
        return {"candidates_raw": len(raw), "candidates_shortlist": 0,
                "written": 0, "notified": 0, "discarded_old": discarded_old}

    # Filter pass (cheap LLM — drops non-jobs and low-fit)
    shortlist = _filter_pass(candidates, ctx)
    logger.info("After filter pass (≥5, job listings preferred): %d", len(shortlist))

    if not shortlist:
        return {"candidates_raw": len(raw), "candidates_shortlist": 0,
                "written": 0, "notified": 0, "discarded_old": discarded_old}

    # Evaluate pass (quality eval)
    evaluated = _evaluate_pass(shortlist, ctx)
    logger.info("After eval pass: %d", len(evaluated))

    # Warm lead check
    evaluated = _warm_lead_check(evaluated, network)

    # Write results
    written = 0
    for opp in evaluated:
        if _write_opportunity(opp):
            written += 1

    # Age-tiered Telegram notifications
    notified = _notify_telegram(evaluated)

    elapsed = time.time() - start
    tier_summary = {
        t: sum(1 for o in evaluated if o.get("age_tier") == t)
        for t in ("fresh", "recent", "unknown")
    }
    logger.info(
        "Loop A done — raw=%d deduped=%d discarded_old=%d shortlist=%d "
        "written=%d notified=%d fresh=%d recent=%d unknown=%d elapsed=%.1fs",
        len(raw), len(candidates) + discarded_old, discarded_old, len(shortlist),
        written, notified, tier_summary["fresh"], tier_summary["recent"],
        tier_summary["unknown"], elapsed,
    )
    return {
        "candidates_raw": len(raw),
        "candidates_shortlist": len(shortlist),
        "written": written,
        "notified": notified,
        "discarded_old": discarded_old,
        "age_tiers": tier_summary,
        "elapsed_s": round(elapsed, 1),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run_career_focus_scout()
    print(json.dumps(result, indent=2))
