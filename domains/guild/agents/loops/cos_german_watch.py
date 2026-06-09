"""
Loop B — German Domain Watch
Runs weekly (Sunday 09:00).
Checks practice cadence + searches for new tools/community.
Writes to guild.cos_agenda (or JSON fallback). Telegram reminder if overdue.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import keyring
import requests
from openai import OpenAI

logger = logging.getLogger("cos.loop_b")

REPO_ROOT = Path(__file__).resolve().parents[4]
COS_CONTEXT_PATH = REPO_ROOT / "domains/guild/config/cos_context.json"
GERMAN_DATA_PATH = REPO_ROOT / "domains/german/data"
AGENDA_FILE = REPO_ROOT / "data/guild/cos_agenda.json"


def _load_context() -> dict:
    with open(COS_CONTEXT_PATH) as f:
        return json.load(f)


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


def _write_agenda(item: dict) -> None:
    AGENDA_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Try DB
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost", port=5432, dbname="personal_agents",
            user="minimoi", password="simple123"
        )
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO guild.cos_agenda (domain, description, confidence, loop_name) VALUES (%s,%s,%s,%s)",
            (item["domain"], item["description"], item.get("confidence"), item["loop_name"])
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

    # JSON fallback
    try:
        existing: list = []
        if AGENDA_FILE.exists():
            with open(AGENDA_FILE) as f:
                existing = json.load(f)
        item["created_at"] = datetime.now(timezone.utc).isoformat()
        existing.append(item)
        with open(AGENDA_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        logger.error("Agenda JSON write failed: %s", e)


def _days_since_last_session() -> int | None:
    """Find most recent session file in domains/german/data/sessions/."""
    sessions_dir = GERMAN_DATA_PATH / "sessions"
    if not sessions_dir.exists():
        return None
    files = list(sessions_dir.glob("*.json"))
    if not files:
        return None
    # sort by name (sessions are date-stamped) or mtime
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    latest_mtime = files[0].stat().st_mtime
    now = datetime.now(timezone.utc).timestamp()
    return int((now - latest_mtime) / 86400)


def _search_tavily(queries: list[str]) -> list[dict]:
    api_key = keyring.get_password("tavily", "api_key")
    if not api_key:
        return []
    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)
    results: list[dict] = []
    for q in queries:
        try:
            resp = client.search(q, max_results=6, search_depth="basic")
            for r in resp.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:400],
                    "query": q,
                })
            time.sleep(0.3)
        except Exception as e:
            logger.warning("Tavily query failed '%s': %s", q, e)
    return results


def run_german_watch() -> dict:
    logger.info("Loop B — German watch starting")
    ctx = _load_context()
    german = ctx.get("german", {})

    # --- Cadence check ---
    days = _days_since_last_session()
    remind_after = german.get("remind_after_days", 4)
    target_sessions = german.get("practice_target_sessions_per_week", 3)
    notified_cadence = False

    if days is not None and days >= remind_after:
        msg = (
            f"🇩🇪 <b>German practice reminder</b>\n"
            f"{days} days since your last session.\n"
            f"Target: {target_sessions} sessions/week."
        )
        _send_telegram(msg)
        notified_cadence = True
        logger.info("Sent cadence reminder — %d days since last session", days)

    # --- Tool + community search ---
    queries = [
        "German language learning AI tools 2026",
        f"German conversation groups {german.get('local_search', 'Chicago')}",
        "online German language exchange 2026",
    ]
    results = _search_tavily(queries)
    logger.info("German watch — tool search returned %d results", len(results))

    # Evaluate tools found
    client = _grok_client()
    agenda_items_written = 0

    for r in results[:8]:  # cap to avoid runaway cost
        prompt = (
            f"Compare this language learning resource against Mein Deutsch — "
            f"a personal German coaching app with AI-driven drills, phrasebook, "
            f"Gespräche (conversation), and progress tracking. "
            f"Return JSON only:\n"
            f'{{"assessment": "threat|complement|incorporate|not_relevant", '
            f'"key_difference": "one sentence", "recommendation": "one sentence"}}\n\n'
            f"Resource: {r['title']}\n"
            f"URL: {r['url']}\n"
            f"Description: {r['snippet']}"
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
            assessment = parsed.get("assessment", "not_relevant")

            if assessment != "not_relevant":
                desc = (
                    f"{r['title']} ({assessment}) — {parsed.get('key_difference','')}. "
                    f"Rec: {parsed.get('recommendation','')}"
                )
                _write_agenda({
                    "domain": "german",
                    "description": desc,
                    "confidence": 0.7,
                    "loop_name": "cos_german_watch",
                    "status": "pending",
                    "url": r["url"],
                })
                agenda_items_written += 1

                if assessment == "incorporate":
                    _send_telegram(
                        f"🇩🇪 <b>German tool found:</b> {r['title']}\n"
                        f"Rec: incorporate\n{parsed.get('recommendation','')}\n{r['url']}"
                    )
        except Exception as e:
            logger.debug("Eval error for '%s': %s", r.get("title"), e)

    logger.info("Loop B done — cadence_notified=%s agenda_items=%d", notified_cadence, agenda_items_written)
    return {
        "days_since_session": days,
        "cadence_notified": notified_cadence,
        "tools_evaluated": min(len(results), 8),
        "agenda_items_written": agenda_items_written,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run_german_watch()
    print(json.dumps(result, indent=2))
