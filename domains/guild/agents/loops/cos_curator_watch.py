"""
Loop C — Curator Domain Scout
Runs weekly (Sunday 10:00).
Searches for emerging topics matching Curator's scout_for list.
Writes suggestions to guild.cos_agenda. Never auto-adds Curator threads.
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

logger = logging.getLogger("cos.loop_c")

REPO_ROOT = Path(__file__).resolve().parents[4]
COS_CONTEXT_PATH = REPO_ROOT / "domains/guild/config/cos_context.json"
AGENDA_FILE = REPO_ROOT / "data/guild/cos_agenda.json"


def _load_context() -> dict:
    with open(COS_CONTEXT_PATH) as f:
        return json.load(f)


def _grok_client() -> OpenAI:
    api_key = keyring.get_password("xai", "api_key")
    return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")


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


def _search_tavily(term: str) -> list[dict]:
    api_key = keyring.get_password("tavily", "api_key")
    if not api_key:
        return []
    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)
    try:
        resp = client.search(term, max_results=6, search_depth="basic")
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")[:400],
                "term": term,
            }
            for r in resp.get("results", [])
        ]
    except Exception as e:
        logger.warning("Tavily query failed '%s': %s", term, e)
        return []


def run_curator_scout() -> dict:
    logger.info("Loop C — Curator scout starting")
    ctx = _load_context()
    scout_terms_raw = ctx.get("curator", {}).get("scout_for", "")
    scout_terms = [t.strip() for t in scout_terms_raw.split(",") if t.strip()]

    client = _grok_client()
    agenda_items_written = 0
    results_total = 0

    for term in scout_terms[:6]:  # cap at 6 terms per run
        results = _search_tavily(term)
        results_total += len(results)
        time.sleep(0.3)

        for r in results[:4]:  # evaluate top 4 per term
            prompt = (
                f"Is this an emerging topic worth tracking for someone who monitors: "
                f"AI governance, telecom AI, MENA geopolitics, USD hegemony, "
                f"agentic AI platforms, and global macro trends?\n"
                f"Return JSON only:\n"
                f'{{"worth_tracking": true/false, "reason": "one sentence", '
                f'"suggested_thread_name": "slug-style-name"}}\n\n'
                f"Topic: {r['title']}\n"
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

                if parsed.get("worth_tracking"):
                    thread_name = parsed.get("suggested_thread_name", term.replace(" ", "-").lower())
                    desc = (
                        f"Suggested thread: {thread_name} — {parsed.get('reason','')}"
                        f" | Source: {r['title']} ({r['url']})"
                    )
                    _write_agenda({
                        "domain": "curator",
                        "description": desc,
                        "confidence": 0.75,
                        "loop_name": "cos_curator_watch",
                        "status": "pending",
                        "url": r["url"],
                        "suggested_thread": thread_name,
                    })
                    agenda_items_written += 1
            except Exception as e:
                logger.debug("Eval error for '%s': %s", r.get("title"), e)

    logger.info(
        "Loop C done — terms=%d results=%d agenda_items=%d",
        len(scout_terms[:6]), results_total, agenda_items_written
    )
    return {
        "terms_searched": len(scout_terms[:6]),
        "results_total": results_total,
        "agenda_items_written": agenda_items_written,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run_curator_scout()
    print(json.dumps(result, indent=2))
