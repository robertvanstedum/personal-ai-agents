#!/usr/bin/env python3
"""
agent/research.py — Research Intelligence Session Script

Replaces the Sonnet subagent with a direct, instrumented Python script.
All model calls are explicit. Costs accumulate and are passed to run.py end.

Usage:
  python agent/research.py \\
    --session-name kotkin-002 \\
    --topic empire-landpower \\
    --estimated-cost 0.30

Resolves: BUG-002 (Ollama not wired), BUG-003 (budget ledger), BUG-004 (no research.py)
Spec: docs/specs/phase1-research-script-spec-2026-03-21.md
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from source_utils import load_seen_urls, save_seen_urls, apply_novelty_score

try:
    import anthropic
    import keyring
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}. Run: pip install anthropic keyring requests")
    sys.exit(1)

try:
    from agent.feedback import load_query_perf, query_id as make_qid, register_query
except ImportError:
    # Graceful degradation — feedback layer optional; all queries run at weight 1.0
    load_query_perf = lambda: {}
    make_qid        = lambda q: ""
    register_query  = lambda q, s: None

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).resolve().parent.parent   # research-intelligence/
MAIN_PROJECT = ROOT.parent.parent                        # personal-ai-agents/
SESSION_START = datetime.now()


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config():
    config_path = ROOT / "agent" / "config.json"
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}")
        sys.exit(1)
    return json.loads(config_path.read_text())


def load_env():
    """Load .env from main project root into a dict."""
    env_path = MAIN_PROJECT / ".env"
    if not env_path.exists():
        return {}
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


# ── Cost tracking ──────────────────────────────────────────────────────────────

def track_cost(usage, model="claude-3-5-haiku-20241022"):
    """Return USD cost for a single Haiku API call.

    Pricing as of March 2026:
      claude-3-5-haiku: $0.80/M input, $4.00/M output
    """
    input_cost  = (usage.input_tokens  / 1_000_000) * 0.80
    output_cost = (usage.output_tokens / 1_000_000) * 4.00
    return round(input_cost + output_cost, 6)


# ── Triage model routing ───────────────────────────────────────────────────────

def try_ollama(prompt, endpoint, timeout, model_name):
    """Call Ollama. Returns parsed JSON dict or None on failure (silently falls back)."""
    payload = {
        "model":  model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    try:
        r = requests.post(endpoint, json=payload, timeout=timeout)
        if r.ok:
            text = r.json().get("response", "")
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
    except Exception:
        pass
    return None


def check_ollama_available(triage_cfg=None):
    """Lightweight ping to check if Ollama is running. Runs once per session.

    Reads health endpoint and timeout from triage_cfg when provided.
    Falls back to sensible defaults if triage_cfg is absent.
    """
    if triage_cfg:
        health_url = triage_cfg.get(
            "ollama_health_endpoint",
            triage_cfg.get("ollama_endpoint", "http://localhost:11434/api/generate")
                .replace("/api/generate", "/api/tags"),
        )
        timeout = triage_cfg.get("ollama_timeout_seconds", 2)
    else:
        health_url = "http://localhost:11434/api/tags"
        timeout    = 2
    try:
        r = requests.get(health_url, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def call_haiku(prompt, client, model, temperature=0.0, max_tokens=512):
    """Call Haiku. Returns (result, usage) — result is the raw response text."""
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip(), msg.usage
    except Exception as e:
        print(f"  WARNING: Haiku call failed: {e}")
        return None, None


def parse_json_response(text):
    """Extract and parse first JSON object from a string."""
    if text is None:
        return None
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def build_triage_prompt(candidate, direction_context=None, targets=None):
    direction_block = (
        f"\nCurrent research focus (steer scoring toward this): {direction_context}\n"
        if direction_context else ""
    )
    if targets:
        targets_block = "\n".join(f"{i+1}. {t}" for i, t in enumerate(targets))
    else:
        targets_block = (
            "1. Non-Anglophone takes on China-Russia positional swap\n"
            "2. Authoritarian loyalty-vs-competence literature (comparative, beyond Stalin)\n"
            "3. Historical precedents for industrial hollowing\n"
            "4. Mackinder's actual argument — who has updated or challenged it seriously?\n"
            "5. Latin American dependency theory angle on current great power competition"
        )
    return f"""Score this source 1-5 for relevance to the following research targets.
Score on intellectual content and argument quality first.
Non-Anglophone origin is a tiebreaker between equal-quality sources — not a primary scoring criterion.
A strong English-language source from a non-Western scholar published in English scores high.
A weak source doesn't get promoted just for being non-Anglophone.
{direction_block}
Return JSON only: {{"score": N, "targets": [list of target numbers], "explanation": "one sentence", "language": "English/Portuguese/Chinese/etc"}}

Research targets:
{targets_block}

Source title: {candidate.get('title', 'Unknown')}
Source: {candidate.get('source', 'Unknown')}
Text: {candidate.get('snippet', '')[:500]}"""


# ── Step 1: Free source pass ───────────────────────────────────────────────────

def fetch_brave_results(query, api_key, count=5, rate_limit=1):
    """Fetch search results from Brave Search API."""
    url     = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept":               "application/json",
        "Accept-Encoding":      "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": count, "search_lang": "en"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        time.sleep(rate_limit)
        if r.ok:
            results = []
            for item in r.json().get("web", {}).get("results", []):
                domain = ""
                raw_url = item.get("url", "")
                if raw_url:
                    parts = raw_url.split("/")
                    domain = parts[2] if len(parts) > 2 else raw_url
                results.append({
                    "title":   item.get("title", ""),
                    "url":     raw_url,
                    "snippet": item.get("description", ""),
                    "source":  domain,
                })
            return results
        else:
            print(f"  WARNING: Brave API {r.status_code} for: {query!r}")
    except Exception as e:
        print(f"  WARNING: Brave search failed: {e}")
    return []


def fetch_wikipedia_citations(url="https://en.wikipedia.org/wiki/The_Geographical_Pivot_of_History"):
    """Extract cited works from Wikipedia Mackinder article."""
    candidates = []
    try:
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "research-intelligence-bot/1.0"})
        if r.ok:
            text = r.text
            cites = re.findall(r'<cite[^>]*>(.*?)</cite>', text, re.DOTALL)
            for cite in cites[:6]:
                title = re.sub(r'<[^>]+>', '', cite).strip()
                title = re.sub(r'\s+', ' ', title)
                if len(title) > 20:
                    candidates.append({
                        "title":   title,
                        "url":     url,
                        "snippet": "Citation from Wikipedia: The Geographical Pivot of History",
                        "source":  "Wikipedia / Mackinder citations",
                    })
    except Exception as e:
        print(f"  WARNING: Wikipedia fetch failed: {e}")
    return candidates


def dedup_candidates(candidates):
    """Remove duplicates by URL; preserve order."""
    seen = set()
    out  = []
    for c in candidates:
        key = c.get("url", "")
        if key and key not in seen:
            seen.add(key)
            out.append(c)
        elif not key:
            out.append(c)
    return out


# ── Step 2.5: Citation graph chase ────────────────────────────────────────────

OPENALEX_BASE = "https://api.openalex.org"


def chase_citations(candidate: dict) -> list:
    """Fetch works that cite this candidate via OpenAlex (free, no auth).

    Steps:
      1. Resolve title to an OpenAlex work ID (title.search filter, URL-encoded).
      2. Fetch up to 10 citing works sorted by citation count.

    Returns a list of candidate dicts with source_type='citation_chain'.
    Returns [] if the source cannot be resolved or has no citing works.
    """
    title = candidate.get("title", "").strip()
    if not title:
        return []
    try:
        # Step 1: resolve — quote() required; colons and non-ASCII break unencoded queries
        r = requests.get(
            f"{OPENALEX_BASE}/works",
            params={"filter": f"title.search:{quote(title)}", "per_page": 1},
            timeout=10,
        )
        works = r.json().get("results", [])
        if not works:
            return []
        oa_id = works[0]["id"].split("/")[-1]   # strip URL prefix e.g. "W2741809807"

        time.sleep(0.5)   # polite pause between OpenAlex calls

        # Step 2: citing works sorted by impact
        r2 = requests.get(
            f"{OPENALEX_BASE}/works",
            params={
                "filter": f"cites:{oa_id}",
                "per_page": 10,
                "sort": "cited_by_count:desc",
            },
            timeout=10,
        )
        citations = r2.json().get("results", [])
        return [
            {
                "title":       w.get("display_name", ""),
                "url":         w.get("doi", "") or w.get("id", ""),
                "snippet":     (w.get("abstract") or "")[:300],
                "source":      "OpenAlex / citation chain",
                "source_type": "citation_chain",
                "cited_by":    title,
            }
            for w in citations
            if w.get("display_name")
        ]
    except Exception as e:
        print(f"  WARNING: OpenAlex lookup failed for '{title[:40]}': {e}")
        return []


# ── Step 4: Translation ────────────────────────────────────────────────────────

def build_translation_prompt(excerpt):
    return (
        "Translate the following excerpt to English. Preserve the author's argumentative "
        "structure — don't smooth over technical terms, translate them and note the original "
        "in brackets. 300-500 words.\n\n"
        + excerpt
    )


# ── Step 8: Telegram ──────────────────────────────────────────────────────────

def send_telegram(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if not r.ok:
            print(f"  WARNING: Telegram returned {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"  WARNING: Telegram send failed: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def elapsed_minutes():
    delta = datetime.now() - SESSION_START
    return max(1, round(delta.total_seconds() / 60))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Research Intelligence Session Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--session-name",   required=True, help="e.g. kotkin-002")
    parser.add_argument("--topic",          required=True, help="e.g. empire-landpower")
    parser.add_argument("--estimated-cost", type=float, default=0.10,
                        help="Estimated cost for warn-ahead check (default: 0.10)")
    args = parser.parse_args()

    session_name   = args.session_name
    topic          = args.topic
    estimated_cost = args.estimated_cost

    session_cost = 0.0
    model_log    = []   # list of (title, "ollama"|"haiku")
    cost_log     = []   # list of (label, model, input_tokens, output_tokens, cost)

    # ── 0. Pre-flight ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Research Intelligence — {session_name} / {topic}")
    print(f"{'='*60}\n")
    print("[0] Pre-flight: checking budget...")

    run_py = ROOT / "agent" / "run.py"
    result = subprocess.run(
        [sys.executable, str(run_py), "start",
         "--session-name",   session_name,
         "--estimated-cost", str(estimated_cost)],
    )
    if result.returncode == 1:
        print("ABORT: hard budget limit reached.")
        sys.exit(1)
    if result.returncode == 2:
        print("ABORT: estimated cost would breach a limit.")
        sys.exit(2)

    cfg = load_config()
    env = load_env()

    # Resolve topic directory — no library/ prefix
    topic_dir = ROOT / "topics" / topic
    if not topic_dir.exists():
        print(f"ERROR: topic directory not found: {topic_dir}")
        subprocess.run([sys.executable, str(run_py), "end",
                        "--cost", "0.0000", "--duration", "1min",
                        "--notes", f"{session_name}: aborted — topic dir not found"])
        sys.exit(1)

    context_path = topic_dir / "CONTEXT.md"
    context_md   = context_path.read_text() if context_path.exists() else ""

    # ── Anthropic client ───────────────────────────────────────────────────────
    anthropic_key = keyring.get_password("anthropic", "api_key")
    if not anthropic_key:
        anthropic_key = env.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        print("ERROR: Anthropic API key not found in keyring or .env")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=anthropic_key)

    # ── Brave API key — read from Keychain (consistent with Anthropic/Telegram) ─
    brave_key = keyring.get_password("brave_search", "api_key") or \
                env.get("BRAVE_API_KEY", "") or \
                os.environ.get("BRAVE_API_KEY", "")

    # ── Triage model config ────────────────────────────────────────────────────
    triage_cfg      = cfg.get("triage_model", {})
    ollama_model    = triage_cfg.get("primary", "ollama/gemma").split("/")[-1]
    ollama_endpoint = triage_cfg.get("ollama_endpoint", "http://localhost:11434/api/generate")
    ollama_timeout  = triage_cfg.get("ollama_timeout_seconds", 3)
    haiku_model     = triage_cfg.get("fallback", "claude-3-5-haiku-20241022")
    triage_temp     = cfg.get("models", {}).get("triage", {}).get("temperature", 0.0)

    # Translation config
    translation_cfg   = cfg.get("models", {}).get("translation", {})
    translation_model = translation_cfg.get("model", "claude-3-5-haiku-20241022")
    translation_temp  = translation_cfg.get("temperature", 0.3)

    # Search config
    rate_limit        = cfg.get("search", {}).get("rate_limit_seconds", 1)
    results_per_query = cfg.get("search", {}).get("results_per_query", 5)

    # Triage targets — topic-specific, read from config
    triage_targets = cfg.get("triage_targets", {}).get(topic, [])
    if triage_targets:
        print(f"  Triage targets: {len(triage_targets)} loaded for topic '{topic}'")
    else:
        print(f"  Triage targets: none configured for '{topic}' — using defaults")

    ollama_available = check_ollama_available(triage_cfg)
    print(f"  Ollama: {'available' if ollama_available else 'unavailable — using Haiku fallback'}")

    # ── Direction shift — check for active annotation steering triage ──────────
    # Two import paths: package import (from curator_server) vs direct script run
    direction_context = None
    _active_shift_id  = None
    _mark_influenced  = None
    try:
        try:
            from agent.threads import check_direction_shifts, mark_influenced as _mark_influenced
        except ImportError:
            from threads import check_direction_shifts, mark_influenced as _mark_influenced
        shift = check_direction_shifts(topic)
        if shift:
            direction_context = shift["note"]
            _active_shift_id  = shift["id"]
            print(f"  Direction shift active ({_active_shift_id}): {direction_context[:100]}{'...' if len(direction_context) > 100 else ''}")
        else:
            print("  Direction shift: none")
    except ImportError:
        print("  Direction shift: threads.py not available (skipping)")

    # ── 1. Free source pass ────────────────────────────────────────────────────
    print("[1] Free source pass...")
    raw_candidates = []

    searches = cfg.get("session_searches", {}).get(topic, [
        "Mackinder heartland theory non-Western IR scholarship",
        "China Russia geopolitical position swap IR theory",
        "dependency theory great power competition 2020s",
        "CLACSO dependency theory hegemony United States China",
        "non-Western IR scholars citing Mackinder heartland",
    ])

    # Weight-sort: higher-weight queries run first. Pre-compute weights to
    # avoid calling make_qid(q) twice per comparison in the sort key.
    # Queries with no performance record default to weight 1.0.
    perf = load_query_perf()
    q_weights = {q: perf[make_qid(q)].weight if make_qid(q) in perf else 1.0
                 for q in searches}
    searches.sort(key=lambda q: q_weights[q], reverse=True)

    # Apply slot limit — lowest-weight queries dropped if over cap.
    max_searches = cfg.get("search", {}).get("max_searches", 8)
    if len(searches) > max_searches:
        dropped = searches[max_searches:]
        searches = searches[:max_searches]
        print(f"  Slot limit {max_searches}: dropped {len(dropped)} low-weight quer{'y' if len(dropped)==1 else 'ies'}")

    # Institution-targeted searches fill remaining slots after session_queries.
    # They are bonus precision — never truncate session_queries, never error if no slots.
    institution_queries = cfg.get("institution_searches", {}).get(topic, [])
    institution_slots   = max(0, max_searches - len(searches))
    institution_to_run  = institution_queries[:institution_slots]
    print(f"  Institution searches: {len(institution_queries)} configured, "
          f"{institution_slots} slots available, {len(institution_to_run)} run")

    if brave_key:
        for query in searches:
            print(f"  Brave [{q_weights[query]:.2f}w]: {query!r}")
            results = fetch_brave_results(query, brave_key,
                                          count=results_per_query,
                                          rate_limit=rate_limit)
            raw_candidates.extend(results)
            print(f"    → {len(results)} results")
        for query in institution_to_run:
            print(f"  Brave [institution]: {query!r}")
            results = fetch_brave_results(query, brave_key,
                                          count=results_per_query,
                                          rate_limit=rate_limit)
            for r in results:
                r["source_type"] = "institution"
            raw_candidates.extend(results)
            print(f"    → {len(results)} results")
        # Register session queries that ran — builds performance history
        for q in searches:
            register_query(q, session_name)
    else:
        print("  WARNING: No Brave API key found — skipping web searches.")

    print("  Fetching Wikipedia Mackinder citations...")
    wiki_cands = fetch_wikipedia_citations()
    raw_candidates.extend(wiki_cands)
    print(f"    → {len(wiki_cands)} Wikipedia citations")

    raw_candidates = dedup_candidates(raw_candidates)
    print(f"  Total raw candidates after dedup: {len(raw_candidates)}")

    if len(raw_candidates) > 12:
        raw_candidates = raw_candidates[:12]

    # ── 2. Triage ──────────────────────────────────────────────────────────────
    # Novelty scoring — load seen URLs for this topic
    seen_urls = load_seen_urls(topic)
    if seen_urls:
        print(f"  Novelty cache: {len(seen_urls)} previously-seen URLs for '{topic}'")

    print(f"\n[2] Triage ({len(raw_candidates)} candidates)...")
    scored = []

    for i, cand in enumerate(raw_candidates, 1):
        prompt      = build_triage_prompt(cand, direction_context, triage_targets)
        title_short = cand.get("title", "Unknown")[:60]
        print(f"  [{i}/{len(raw_candidates)}] {title_short}")

        # Try Ollama first (free) — skip entirely if unavailable (pre-checked at session start)
        result = try_ollama(prompt, ollama_endpoint, ollama_timeout, ollama_model) \
                 if ollama_available else None
        if result is not None:
            model_used = "ollama"
            usage_note = "ollama ($0.00)"
            model_log.append((cand.get("title", ""), "ollama"))
        else:
            # Fall back to Haiku
            raw_text, usage = call_haiku(prompt, client, haiku_model, triage_temp)
            result = parse_json_response(raw_text)
            model_used = "haiku"
            if usage is not None:
                call_cost     = track_cost(usage, haiku_model)
                session_cost += call_cost
                cost_log.append((f"triage-{i}", haiku_model,
                                 usage.input_tokens, usage.output_tokens, call_cost))
                usage_note    = f"haiku (${call_cost:.6f})"
            else:
                usage_note = "haiku (cost unknown)"
            model_log.append((cand.get("title", ""), "haiku"))

        if result is None:
            print(f"    ⚠ Could not parse triage response — skipping")
            continue

        score       = int(result.get("score", 0))
        targets_hit = result.get("targets", [])
        explanation = result.get("explanation", "")
        language    = result.get("language", "English")
        scored.append({
            **cand,
            "score":       score,
            "targets":     targets_hit,
            "explanation": explanation,
            "language":    language,
            "model_used":  model_used,
        })

        star = "⭐" if score >= 4 else "  "
        print(f"    {star} Score: {score}/5 | {language} | {usage_note} | {explanation[:80]}")

    # Apply novelty discount before ranking (modifies scores in-place)
    novelty_discount = config.get("search", {}).get("novelty_discount_factor", 0.3)
    apply_novelty_score(scored, seen_urls, discount=novelty_discount)
    flagged = sum(1 for c in scored if not c.get("novelty_flag", True))
    if flagged:
        print(f"  Novelty: {flagged} seen URL(s) discounted by {novelty_discount:.0%}")

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    ollama_count = sum(1 for _, m in model_log if m == "ollama")
    haiku_count  = sum(1 for _, m in model_log if m == "haiku")
    print(f"\n  Triage done: {len(scored)} scored | Ollama: {ollama_count} | Haiku: {haiku_count}")
    print(f"  Session cost so far: ${session_cost:.6f}")

    # ── 2.5. Citation graph chase via OpenAlex ─────────────────────────────────
    print(f"\n[2.5] Citation graph chase...")
    top_candidates = [c for c in scored if c.get("score", 0) >= 4]
    if not top_candidates:
        print("  Citation chase skipped — no candidates scored ≥4.")
    else:
        citation_extras: list = []
        for cand in top_candidates:
            print(f"  Chasing: {cand.get('title', '')[:60]}")
            extras = chase_citations(cand)
            print(f"    → {len(extras)} citing works found via OpenAlex")
            citation_extras.extend(extras)
            time.sleep(0.5)

        citation_extras = dedup_candidates(citation_extras)
        if citation_extras:
            print(f"  Triaging {len(citation_extras)} citation-chain candidates...")
            for i, cand in enumerate(citation_extras, 1):
                prompt      = build_triage_prompt(cand, direction_context, triage_targets)
                title_short = cand.get("title", "Unknown")[:60]
                print(f"  [cite-{i}/{len(citation_extras)}] {title_short}")

                result = try_ollama(prompt, ollama_endpoint, ollama_timeout, ollama_model) \
                         if ollama_available else None
                if result is not None:
                    model_used = "ollama"
                    usage_note = "ollama ($0.00)"
                    model_log.append((cand.get("title", ""), "ollama"))
                else:
                    raw_text, usage = call_haiku(prompt, client, haiku_model, triage_temp)
                    result     = parse_json_response(raw_text)
                    model_used = "haiku"
                    if usage is not None:
                        call_cost     = track_cost(usage, haiku_model)
                        session_cost += call_cost
                        cite_n = sum(1 for l in cost_log if l[0].startswith("cite-")) + 1
                        cost_log.append((f"cite-{cite_n}", haiku_model,
                                         usage.input_tokens, usage.output_tokens, call_cost))
                        usage_note = f"haiku (${call_cost:.6f})"
                    else:
                        usage_note = "haiku (cost unknown)"
                    model_log.append((cand.get("title", ""), "haiku"))

                if result is None:
                    continue

                score    = int(result.get("score", 0))
                cand.update({
                    "score":       score,
                    "targets":     result.get("targets", []),
                    "explanation": result.get("explanation", ""),
                    "language":    result.get("language", "English"),
                    "model_used":  model_used,
                })
                star = "⭐" if score >= 4 else "  "
                print(f"    {star} Score: {score}/5 | {usage_note} | {cand['explanation'][:80]}")

                if score >= 4:
                    scored.append(cand)

            scored.sort(key=lambda x: x.get("score", 0), reverse=True)
            ollama_count = sum(1 for _, m in model_log if m == "ollama")
            haiku_count  = sum(1 for _, m in model_log if m == "haiku")
            print(f"  Citation chase done. Total scored: {len(scored)} | "
                  f"Ollama: {ollama_count} | Haiku: {haiku_count}")
            print(f"  Session cost so far: ${session_cost:.6f}")

    # ── 3. Write sources-candidates-{session-name}.md ──────────────────────────
    print(f"\n[3] Writing sources-candidates-{session_name}.md...")

    candidates_path = topic_dir / f"sources-candidates-{session_name}.md"

    top3_keys = set()
    for c in scored[:3]:
        top3_keys.add(c.get("url", c.get("title", "")))

    rows = []
    for i, c in enumerate(scored, 1):
        key     = c.get("url", c.get("title", ""))
        star    = "⭐" if key in top3_keys else ""
        title   = c.get("title", "—")
        url     = c.get("url", "—")
        source  = c.get("source", "—")
        lang    = c.get("language", "English")
        score   = c.get("score", 0)
        targets = ", ".join(str(t) for t in c.get("targets", []))
        notes       = c.get("explanation", "")
        source_type = c.get("source_type", "")
        cited_by    = c.get("cited_by", "")
        if source_type == "citation_chain" and cited_by:
            notes = f"[citation_chain · cites: {cited_by[:50]}] {notes}"
        elif source_type == "institution":
            notes = f"[institution] {notes}"
        rows.append(
            f"| {i} | {star}{score} | {title} | — | {source} | {lang} | [{targets}] | {url} | {notes} |"
        )

    header = (
        f"# Source Candidates — {session_name}\n"
        f"_Last updated: {datetime.now().strftime('%Y-%m-%d')}_\n"
        f"_Thread: {topic}_\n\n"
        "| # | Score | Title | Author | Source | Language | Targets | URL | Notes |\n"
        "|---|-------|-------|--------|--------|----------|---------|-----|-------|\n"
    )
    candidates_path.write_text(header + "\n".join(rows) + "\n")
    print(f"  → Written: {candidates_path.name}")

    # ── 4. Translation ─────────────────────────────────────────────────────────
    print(f"\n[4] Translation pass...")
    translation_note = "none scored ≥ 4"
    translation_target = None

    for c in scored:
        if c.get("language", "").lower() == "english":
            continue  # language field is the sole translation gate (BUG-006)
        if c.get("score", 0) >= 4:
            translation_target = c
            break

    if translation_target is None:
        print("  No non-Anglophone candidate scored ≥ 4 — skipping translation")
    else:
        excerpt = translation_target.get("snippet", "")[:1500]
        t_title = translation_target.get("title", "Unknown")
        print(f"  Translating: {t_title[:60]}")

        prompt       = build_translation_prompt(excerpt)
        t_text, t_usage = call_haiku(prompt, client, translation_model,
                                     translation_temp, max_tokens=1024)
        if t_usage:
            call_cost     = track_cost(t_usage, translation_model)
            session_cost += call_cost
            cost_log.append(("translation", translation_model,
                             t_usage.input_tokens, t_usage.output_tokens, call_cost))
            print(f"  → Translation cost: ${call_cost:.6f}")

        translations_dir = topic_dir / "translations"
        translations_dir.mkdir(exist_ok=True)

        # Derive author last-name slug for filename
        source_raw = translation_target.get("source", "unknown")
        lastname   = re.sub(r'[^a-zA-Z]', '',
                            source_raw.split(".")[0].split()[-1]).lower()
        if not lastname:
            lastname = "source"

        t_path   = translations_dir / f"{session_name}-{lastname}.md"
        targets_str = ", ".join(f"Target {t}" for t in translation_target.get("targets", []))

        t_md = (
            f"# Translation: {t_title}\n"
            f"_Session: {session_name} | Language: {translation_target.get('language', 'Unknown')}_\n\n"
            f"## Original Excerpt\n\n{excerpt[:400]}\n\n"
            f"---\n\n"
            f"## Translation (Haiku)\n\n{t_text or '(translation failed)'}\n\n"
            f"---\n\n"
            f"## Why This Matters\n\n"
            f"Relevant to {targets_str}. {translation_target.get('explanation', '')}\n\n"
            f"Source: {translation_target.get('url', '—')}\n"
        )
        t_path.write_text(t_md)
        print(f"  → Written: translations/{t_path.name}")
        translation_note = f"{t_title[:60]} ({translation_target.get('language', '?')})"

    # ── 5. Write session findings ──────────────────────────────────────────────
    print(f"\n[5] Writing session findings...")
    findings_path = topic_dir / f"{session_name}.md"

    top_finds = scored[:3]
    top_find_str = top_finds[0].get("title", "none") if top_finds else "No candidates found"

    sources_section = ""
    for i, c in enumerate(top_finds, 1):
        sources_section += f"[{i}] {c.get('source', '—')}. \"{c.get('title', '')}\".\n    {c.get('url', '—')}\n"

    findings_bullets = ""
    for c in top_finds:
        expl    = c.get("explanation", "")
        tgt_str = ", ".join(f"Target {t}" for t in c.get("targets", []))
        findings_bullets += f"- {expl} ({tgt_str}) — {c.get('title', '')[:70]}\n"

    threads_list = [
        f"- Follow up on: {c.get('title', '')[:80]}"
        for c in scored[:5]
        if c.get("score", 0) >= 3
    ]
    threads_str = "\n".join(threads_list) if threads_list else "- No strong threads identified this session"

    fallback_detail = ""
    for title, m in model_log:
        if m == "haiku":
            fallback_detail += f"\n  - Haiku fallback: {title[:60]}"

    now      = datetime.now()
    duration = f"{elapsed_minutes()}min"

    _findings_body   = findings_bullets or "- No scoreable candidates found this session."
    _sources_body    = sources_section  or "[1] No qualified sources.\n"

    findings_md = (
        f"# Session Findings: {session_name}\n\n"
        f"<!-- MACHINE-READABLE HEADER — do not remove or reorder these lines -->\n"
        f"date: {now.strftime('%Y-%m-%d')}\n"
        f"session: {session_name}\n"
        f"topic: {topic}\n"
        f"cost: ${session_cost:.4f}\n"
        f"duration: {duration}\n"
        f"sources_reviewed: {len(scored)}\n"
        f"<!-- END HEADER -->\n\n"
        f"## Research Question\n\n"
        f"Structured triage session — identifying highest-value sources across the "
        f"{len(triage_targets)} research targets defined for topic: {topic}.\n\n"
        f"---\n\n"
        f"## Key Findings\n\n"
        f"{_findings_body}\n"
        f"---\n\n"
        f"## Sources\n\n"
        f"{_sources_body}\n"
        f"---\n\n"
        f"## Threads to Continue\n\n"
        f"{threads_str}\n\n"
        f"---\n\n"
        f"## Agent Notes\n\n"
        f"Model: Ollama-first ({ollama_model}) with Haiku fallback ({haiku_model})\n"
        f"Ollama at session start: {'available' if ollama_available else 'unavailable (fell back to Haiku)'}\n"
        f"Ollama calls: {ollama_count} | Haiku fallback calls: {haiku_count}\n"
        f"Searches run: {len(searches)} Brave queries + Wikipedia citations{fallback_detail}\n"
        f"Prompt approach: 5-target relevance scoring, score 1-5, JSON response\n"
        f"What worked: Brave API + Wikipedia dedup → {len(raw_candidates)} raw → {len(scored)} scored\n"
        f"What to adjust: Increase results_per_query if candidate pool too thin\n\n"
        f"---\n\n"
        f"## Cost Breakdown\n\n"
        f"| Call | Model | Tokens in | Tokens out | Cost |\n"
        f"|------|-------|-----------|------------|------|\n"
        + "".join(
            f"| {label} | {model} | {tok_in} | {tok_out} | ${cost:.6f} |\n"
            for label, model, tok_in, tok_out, cost in cost_log
        )
        + f"| **Total** | | | | **${session_cost:.6f}** |\n\n"
        + f"---\n\n"
        + f"_Session closed: {now.strftime('%Y-%m-%d %H:%M')}_\n"
    )
    findings_path.write_text(findings_md)
    print(f"  → Written: {findings_path.name}")

    # Persist seen URLs for next session (all triaged candidates)
    all_scored_urls = [c['url'] for c in scored if c.get('url')]
    if all_scored_urls:
        save_seen_urls(topic, all_scored_urls)
        print(f"  Novelty cache: {len(all_scored_urls)} URLs saved for '{topic}'")

    # ── 6. Update library/README.md ────────────────────────────────────────────
    print(f"\n[6] Updating library/README.md...")
    readme_path = ROOT / "library" / "README.md"
    qualified   = [c for c in scored if c.get("score", 0) >= 4]

    if qualified and readme_path.exists():
        readme_text = readme_path.read_text()
        today       = now.strftime("%Y-%m-%d")
        new_rows    = []

        for c in qualified:
            title   = c.get("title", "")
            url     = c.get("url", "—")
            lang    = c.get("language", "English")
            source  = c.get("source", "—")
            summary = c.get("explanation", "")[:120]
            row     = f"| {today} | {topic} | {source}. \"{title}\" | {lang} | Web | {summary} | {url} |"

            if url != "—" and url in readme_text:
                print(f"  Skip (already in library): {title[:50]}")
                continue
            new_rows.append(row)

        if new_rows:
            lines = readme_text.splitlines()
            insert_after = None
            for i, line in enumerate(lines):
                if line.startswith("|---") and i > 0:
                    insert_after = i
                    break
            if insert_after is not None:
                for row in reversed(new_rows):
                    lines.insert(insert_after + 1, row)
                readme_path.write_text("\n".join(lines) + "\n")
                print(f"  → Added {len(new_rows)} rows to library/README.md")
            else:
                print("  WARNING: Could not find table separator in library/README.md")
        else:
            print("  No new rows to add (all duplicates or no qualified candidates)")
    else:
        print(f"  Skipping README update: {len(qualified)} qualified, readme_exists={readme_path.exists()}")

    # ── 7. Close session ───────────────────────────────────────────────────────
    print(f"\n[7] Closing session...")
    duration    = f"{elapsed_minutes()}min"
    top_note    = top_finds[0].get("title", "no strong candidates")[:60] if top_finds else "no candidates"
    notes_str   = f"{session_name}: {len(scored)} triaged, top find: {top_note}"

    subprocess.run([
        sys.executable, str(run_py), "end",
        "--cost",     f"{session_cost:.4f}",
        "--duration", duration,
        "--notes",    notes_str,
    ])

    # ── 8. Telegram ────────────────────────────────────────────────────────────
    print(f"\n[8] Sending Telegram summary...")
    telegram_token   = keyring.get_password("telegram", cfg.get("research_telegram_bot", "bot_token"))
    research_chat_id = cfg.get("research_chat_id", "")

    max_score          = max((c.get("score", 0) for c in scored), default=0)
    send_notification  = session_cost > 0 or max_score >= 5

    if telegram_token and research_chat_id and research_chat_id != "REPLACE_WITH_CHAT_ID":
        if send_notification:
            threads_telegram = "\n".join(
                f"• {c.get('title', '')}"
                for c in scored[:3]
                if c.get("score", 0) >= 3
            ) or "• No strong threads this session"

            top_find_sentence = (
                top_finds[0].get("explanation") or top_finds[0].get("title", "no strong candidates")
            )[:300] if top_finds else "no candidates"
            top_find_url = top_finds[0].get("url", "—") if top_finds else "—"

            triage_model_label = ollama_model if ollama_available else haiku_model

            msg = (
                f"[Research Intel] 🌐 {session_name} complete\n\n"
                f"Thread: {topic} | Cost: ${session_cost:.4f} | ~{duration}\n"
                f"Triage: {triage_model_label} ({ollama_count} local / {haiku_count} Haiku)\n\n"
                f"⭐ Top find: {top_find_sentence}\n"
                f"   {top_find_url}\n\n"
                f"🌍 Non-English: {translation_note}\n\n"
                f"Open threads:\n{threads_telegram}\n\n"
                f"Full findings: topics/{topic}/{session_name}.md"
            )
        else:
            msg = (
                f"[Research Intel] ℹ️ {session_name}: no strong candidates found. "
                f"See findings log."
            )
        send_telegram(telegram_token, research_chat_id, msg)
        print("  → Telegram sent")
    else:
        print("  → Telegram skipped (no token or research_chat_id not configured)")

    # ── Mark direction shift as consumed ───────────────────────────────────────
    if _active_shift_id:
        try:
            _mark_influenced(topic, _active_shift_id, session_name)
            print(f"  Direction shift {_active_shift_id} marked as consumed by {session_name}")
        except Exception as e:
            print(f"  WARNING: could not mark direction shift: {e}")

    # ── Done ───────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Session complete: {session_name}")
    print(f"Cost: ${session_cost:.4f} | Duration: {duration} | Scored: {len(scored)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
