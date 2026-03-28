#!/usr/bin/env python3
"""
observe.py — AI observation synthesis for Research Intelligence Agent

Reads saved article signals for a topic, synthesises with Sonnet,
writes markdown to data/observations/, prints JSON result to stdout.

Called as a subprocess by curator_server.py /api/research/observe.
Never imported directly into the Flask process — keeps server worker clean.

Usage:
  python agent/observe.py --topic empire-landpower --command observe
  python agent/observe.py --topic empire-landpower --command status

Output (stdout, JSON):
  {
    "ok": true,
    "text": "...",
    "tokens_in": 1240,
    "tokens_out": 380,
    "cost_usd": 0.0141,
    "output_file": "data/observations/empire-landpower-2026-03-22T07-00-00.md",
    "saved_articles_used": 3
  }

On error:
  {"ok": false, "error": "..."}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import anthropic
    import keyring
except ImportError as e:
    print(json.dumps({"ok": False, "error": f"Missing dependency: {e}"}))
    sys.exit(1)

# ── Paths (same pattern as research.py) ──────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent   # research-intelligence/

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    path = ROOT / "agent" / "config.json"
    if not path.exists():
        _fail(f"config not found: {path}")
    return json.loads(path.read_text())

# ── Error helper ──────────────────────────────────────────────────────────────

def _fail(msg: str):
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)

# ── Cost tables ───────────────────────────────────────────────────────────────
# Sonnet: $3.00 / 1M input, $15.00 / 1M output
SONNET_COST_IN  = 3.00  / 1_000_000
SONNET_COST_OUT = 15.00 / 1_000_000
# Haiku:  $0.80 / 1M input,  $4.00 / 1M output
HAIKU_COST_IN   = 0.80  / 1_000_000
HAIKU_COST_OUT  = 4.00  / 1_000_000

def compute_cost(tokens_in: int, tokens_out: int,
                 rate_in: float = SONNET_COST_IN,
                 rate_out: float = SONNET_COST_OUT) -> float:
    return round(tokens_in * rate_in + tokens_out * rate_out, 6)

# ── Load saved articles ───────────────────────────────────────────────────────

def load_saved_articles(topic: str, cfg: dict) -> list[dict]:
    """
    Return article_signals where signal == 'save'.
    Topic filter: match session_id prefixes against the topic name.
    If no sessions are tagged to topic, returns all saved articles
    (reasonable for early POC with a single topic).
    """
    signals_path = ROOT / "data" / "feedback" / "article_signals.json"
    if not signals_path.exists():
        return []

    raw = json.loads(signals_path.read_text())
    saved = [r for r in raw if r.get("signal") == "save"]

    # Topic filter: session IDs that contain the topic slug
    topic_slug = topic.replace("-", "").lower()
    topic_saved = [r for r in saved
                   if topic_slug in r.get("session_id", "").replace("-", "").lower()]

    # Fallback: return all saved if no topic match (single-topic POC)
    return topic_saved if topic_saved else saved

# ── Prompts ───────────────────────────────────────────────────────────────────

OBSERVE_SYSTEM = """\
You are a research intelligence analyst. You have access to a curated set of
articles that a researcher has marked as high-value during recent research
sessions on a specific topic. Your job is to synthesise what these sources
collectively say, surface tensions or gaps, and identify the most promising
threads for further research.

Be direct and analytical. Avoid generic academic hedging. If the source set is
thin, say so — do not pad the observation to seem comprehensive.
Write in clean markdown. Use headers sparingly — only if the synthesis is long
enough to warrant navigation."""

def build_observe_prompt(topic: str, articles: list[dict]) -> str:
    lines = [f"# Research topic: {topic}\n",
             f"## Saved articles ({len(articles)} total)\n"]
    for i, a in enumerate(articles, 1):
        title = a.get("title") or a.get("url", "Untitled")
        url   = a.get("url", "")
        note  = a.get("note", "")
        session = a.get("session_id", "")
        lines.append(f"{i}. **{title}**")
        if url:
            lines.append(f"   URL: {url}")
        if session:
            lines.append(f"   Session: {session}")
        if note:
            lines.append(f"   Note: {note}")
        lines.append("")

    lines.append("---\n")
    lines.append(
        "Synthesise what these sources collectively reveal about the research topic. "
        "Surface the strongest argument or finding across the set, note any tensions "
        "or contradictions between sources, and identify the single most promising "
        "thread to pull next. Keep the synthesis tight — under 400 words unless the "
        "material genuinely demands more."
    )
    return "\n".join(lines)

STATUS_PROMPT = """\
Summarise the current state of the research signal store:
- How many saved articles exist?
- What sessions have produced saves?
- What query threads appear most productive based on saved article notes?
- What looks thin or missing?

Be brief. This is a status check, not a synthesis."""

# ── Model calls ───────────────────────────────────────────────────────────────

def call_sonnet(system: str, prompt: str, client: anthropic.Anthropic,
                model: str, temperature: float) -> tuple[str, int, int]:
    """Returns (text, tokens_in, tokens_out)."""
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text if msg.content else ""
    return text, msg.usage.input_tokens, msg.usage.output_tokens


def extract_query_candidates(text: str, client: anthropic.Anthropic,
                              haiku_model: str) -> tuple[list[str], int, int]:
    """
    Haiku call — lightweight parse of Sonnet synthesis output.
    Extracts 3-5 search queries from gaps / promising-thread sections.
    Returns (queries, tokens_in, tokens_out). On any failure returns ([], 0, 0).
    """
    prompt = (
        "Extract 3-5 search query strings from this research synthesis. "
        "Look especially at any gaps, missing sources, or next research threads. "
        "Return ONLY a JSON array. Start your response with [ and end with ]. "
        "No explanation. No markdown fences. Example: [\"query one\", \"query two\"]\n\n"
        + text
    )
    try:
        msg = client.messages.create(
            model=haiku_model,
            max_tokens=256,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip() if msg.content else "[]"
        print(f"[haiku-debug] raw={raw!r}", file=sys.stderr)
        # Strip markdown fences — Haiku sometimes wraps JSON despite instructions
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines()
                if not line.strip().startswith("```")
            ).strip()
        queries = json.loads(raw)
        if not isinstance(queries, list):
            queries = []
        return queries, msg.usage.input_tokens, msg.usage.output_tokens
    except Exception as e:
        print(f"[haiku-debug] exception={e!r}", file=sys.stderr)
        return [], 0, 0


QUERY_CANDIDATES = ROOT / "data" / "feedback" / "query_candidates.json"

def write_query_candidates(queries: list[str], topic: str,
                            source_obs: str) -> int:
    """
    Append extracted queries to query_candidates.json.
    Dedup on SHA-256 hash of query text. Returns count added.
    """
    data = json.loads(QUERY_CANDIDATES.read_text()) if QUERY_CANDIDATES.exists() else []
    existing_ids = {r["id"] for r in data}
    added = 0
    for q in queries:
        if not q or not isinstance(q, str):
            continue
        qid = hashlib.sha256(q.encode()).hexdigest()[:8]
        if qid in existing_ids:
            continue
        data.append({
            "id": qid,
            "topic": topic,
            "query": q,
            "source_observation": source_obs,
            "status": "candidate",
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        })
        existing_ids.add(qid)
        added += 1
    QUERY_CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    QUERY_CANDIDATES.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return added

# ── Write observation ─────────────────────────────────────────────────────────

def write_observation(topic: str, command: str, text: str,
                      tokens_in: int, tokens_out: int,
                      cost: float, obs_dir: Path) -> Path:
    obs_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    name = f"{topic}-{command}-{ts}.md"
    path = obs_dir / name

    header = (
        f"# Observation: {topic} ({command})\n"
        f"**Generated:** {datetime.utcnow().isoformat(timespec='seconds')}Z  \n"
        f"**Model:** Sonnet  \n"
        f"**Tokens:** {tokens_in:,} in / {tokens_out:,} out  \n"
        f"**Cost:** ${cost:.4f}  \n\n"
        f"---\n\n"
    )
    path.write_text(header + text, encoding="utf-8")
    return path

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic",   required=True)
    parser.add_argument("--command", default="observe",
                        choices=["observe", "status"])
    args = parser.parse_args()

    cfg         = load_config()
    model       = cfg.get("models", {}).get("synthesis", {}).get("model", "claude-sonnet-4-5")
    temp        = cfg.get("models", {}).get("synthesis", {}).get("temperature", 0.7)
    haiku_model = cfg.get("triage_model", {}).get("fallback", "claude-3-5-haiku-20241022")
    obs_dir     = ROOT / cfg.get("observations_dir", "data/observations")

    # ── Auth ──────────────────────────────────────────────────────────────────
    api_key = keyring.get_password("anthropic", "api_key")
    if not api_key:
        _fail("Anthropic API key not found in keyring (service='anthropic', account='api_key')")
    client = anthropic.Anthropic(api_key=api_key)

    # ── Load context ──────────────────────────────────────────────────────────
    saved = load_saved_articles(args.topic, cfg)

    if args.command == "observe":
        if not saved:
            _fail(f"No saved articles found for topic '{args.topic}'. "
                  "Save some articles first: python agent/feedback.py save --url ...")
        system = OBSERVE_SYSTEM

        # Direction shift — prepend to synthesis prompt if active annotation exists
        # Two import paths: package import (from curator_server) vs direct script run
        direction_prefix = ""
        try:
            try:
                from agent.threads import get_active_direction
            except ImportError:
                from threads import get_active_direction
            shift = get_active_direction(args.topic)
            if shift:
                direction_prefix = (
                    f"Researcher's current focus: {shift['note']}\n"
                    f"Synthesise the saved articles with this focus in mind, "
                    f"not just general patterns.\n\n"
                )
                print(f"  Direction shift active: {shift['note'][:80]}{'...' if len(shift['note']) > 80 else ''}")
        except ImportError:
            pass

        prompt = direction_prefix + build_observe_prompt(args.topic, saved)

    elif args.command == "status":
        # Status uses all saved articles, not topic-filtered
        all_signals_path = ROOT / "data" / "feedback" / "article_signals.json"
        all_saved = json.loads(all_signals_path.read_text()) if all_signals_path.exists() else []
        system = OBSERVE_SYSTEM
        prompt = STATUS_PROMPT + f"\n\nSignal store contents:\n```json\n{json.dumps(all_saved, indent=2)}\n```"

    # ── Call Sonnet ───────────────────────────────────────────────────────────
    try:
        text, tokens_in, tokens_out = call_sonnet(system, prompt, client, model, temp)
    except Exception as e:
        _fail(f"Sonnet call failed: {e}")

    cost = compute_cost(tokens_in, tokens_out)

    # ── Write observation markdown ─────────────────────────────────────────────
    out_path = write_observation(args.topic, args.command, text,
                                 tokens_in, tokens_out, cost, obs_dir)

    # ── Query extraction (Haiku — best-effort, never blocks output) ───────────
    candidates_added = 0
    htokens_in = htokens_out = 0
    hcost = 0.0
    if args.command == "observe":
        queries, htokens_in, htokens_out = extract_query_candidates(
            text, client, haiku_model
        )
        hcost = compute_cost(htokens_in, htokens_out,
                             rate_in=HAIKU_COST_IN, rate_out=HAIKU_COST_OUT)
        if queries:
            candidates_added = write_query_candidates(
                queries, args.topic, str(out_path.relative_to(ROOT))
            )

    # ── JSON result to stdout (Flask reads this) ──────────────────────────────
    print(json.dumps({
        "ok": True,
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "haiku_tokens_in": htokens_in,
        "haiku_tokens_out": htokens_out,
        "haiku_cost_usd": hcost,
        "total_cost_usd": round(cost + hcost, 6),
        "query_candidates_added": candidates_added,
        "output_file": str(out_path.relative_to(ROOT)),
        "saved_articles_used": len(saved),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
