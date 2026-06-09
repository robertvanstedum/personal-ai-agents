"""
Loop D — mini-moi Novelty Watch
Runs bi-weekly (1st and 15th of month, 08:00).
Competitive scan across watch_terms. LLM evaluates combined results.
Writes to guild.cos_agenda. Sends Telegram for threat or incorporate.
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

logger = logging.getLogger("cos.loop_d")

REPO_ROOT = Path(__file__).resolve().parents[4]
COS_CONTEXT_PATH = REPO_ROOT / "domains/guild/config/cos_context.json"
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


def _search_tavily(queries: list[str]) -> list[dict]:
    api_key = keyring.get_password("tavily", "api_key")
    if not api_key:
        return []
    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)
    all_results: list[dict] = []
    for q in queries:
        try:
            resp = client.search(q, max_results=5, search_depth="basic")
            for r in resp.get("results", []):
                all_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:400],
                    "query": q,
                })
            time.sleep(0.3)
        except Exception as e:
            logger.warning("Tavily failed '%s': %s", q, e)
    return all_results


def run_novelty_watch() -> dict:
    logger.info("Loop D — novelty watch starting")
    ctx = _load_context()
    watch_terms = ctx.get("mini_moi", {}).get("watch_terms", [])

    if not watch_terms:
        logger.warning("No watch_terms in cos_context.json — skipping")
        return {"watch_terms": 0, "results_total": 0, "agenda_items": 0, "notified": 0}

    # Gather results across all watch terms
    results = _search_tavily(watch_terms[:7])  # cap at 7 terms
    logger.info("Novelty watch raw results: %d", len(results))

    if not results:
        return {"watch_terms": len(watch_terms), "results_total": 0, "agenda_items": 0, "notified": 0}

    # Build combined context for single LLM eval
    results_text = "\n\n".join([
        f"[{i+1}] {r['title']}\nURL: {r['url']}\n{r['snippet']}"
        for i, r in enumerate(results[:20])  # cap at 20 for prompt size
    ])

    client = _grok_client()
    prompt = (
        "Robert van Stedum has built mini-moi: a personal, local-first, model-agnostic "
        "AI agent platform with Curator (daily intelligence briefing), Mein Deutsch "
        "(German coaching), Research Intelligence (threaded deep research), and Guild "
        "(autonomous agents: Chief of Staff + Operations monitor).\n\n"
        "Here are recent search results for competitive and related platforms:\n\n"
        f"{results_text}\n\n"
        "For each relevant result, return a JSON array (omit not_relevant items):\n"
        '[{"name": "...", "url": "...", '
        '"assessment": "threat|complement|incorporate|not_relevant", '
        '"key_difference": "one sentence", "recommendation": "one sentence"}]\n'
        "Return an empty array [] if nothing is relevant. Return JSON only."
    )

    agenda_items = 0
    notified = 0

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
        findings: list[dict] = json.loads(raw)

        for f in findings:
            assessment = f.get("assessment", "not_relevant")
            if assessment == "not_relevant":
                continue

            desc = (
                f"{f.get('name','')} ({assessment}): {f.get('key_difference','')} | "
                f"Rec: {f.get('recommendation','')}"
            )
            _write_agenda({
                "domain": "mini_moi",
                "description": desc,
                "confidence": 0.8 if assessment in ("threat", "incorporate") else 0.5,
                "loop_name": "cos_novelty_watch",
                "status": "pending",
                "url": f.get("url", ""),
                "assessment": assessment,
            })
            agenda_items += 1

            if assessment in ("threat", "incorporate"):
                msg = (
                    f"🔍 <b>mini-moi novelty watch:</b>\n"
                    f"Found: {f.get('name','')}\n"
                    f"Assessment: {assessment}\n"
                    f"{f.get('key_difference','')}\n"
                    f"Recommendation: {f.get('recommendation','')}\n"
                    f"{f.get('url','')}"
                )
                _send_telegram(msg)
                notified += 1
                time.sleep(0.5)

    except Exception as e:
        logger.error("LLM eval failed: %s", e)

    logger.info(
        "Loop D done — results=%d agenda_items=%d notified=%d",
        len(results), agenda_items, notified
    )
    return {
        "watch_terms": len(watch_terms),
        "results_total": len(results),
        "agenda_items": agenda_items,
        "notified": notified,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run_novelty_watch()
    print(json.dumps(result, indent=2))
