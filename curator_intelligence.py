#!/usr/bin/env python3
"""
curator_intelligence.py — Daily intelligence observation layer (WS5 Phase A+B).

Runs at 7:30 AM daily, 30 minutes after the morning briefing. Reads the
briefing pipeline's own output, surfaces patterns and anomalies the reader
might miss, and delivers a concise Telegram summary.

Phase A observations:
  1. Topic Velocity  — what's gaining/missing in today's briefing vs 30-day baseline
  2. Discovery Candidates — probationary domains added today, Haiku quality-assessed

Phase B observations:
  3. Source Anomalies — trusted sources drifting in topic or quality (Haiku, quiet path)
  4. US Press Blind Spots — stories in non-US outlets absent from US sources (Haiku, quiet path)
  5. Lateral Connections — adjacent topics/sources to explore (Sonnet, weekly on Sundays)

Usage:
  python curator_intelligence.py --telegram          # production (send Telegram)
  python curator_intelligence.py --dry-run           # test (stdout only, no writes)
  python curator_intelligence.py --dry-run --date 2026-03-15  # test specific date

Output:
  ~/.openclaw/workspace/intelligence_YYYYMMDD.json
  ~/.openclaw/workspace/intelligence_weekly_YYYYMMDD.json  (Sundays only)
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from curator_utils import send_telegram_alert, extract_domain

# ── Paths & constants ─────────────────────────────────────────────────────────

HISTORY_PATH   = Path(__file__).parent / 'curator_history.json'
SOURCES_PATH   = Path(__file__).parent / 'curator_sources.json'
LATEST_PATH    = Path(__file__).parent / 'curator_latest.json'   # written by curator_rss_v2 (WS5 pre-condition)
PREFS_PATH     = Path(__file__).parent / 'curator_preferences.json'
OUTPUT_DIR     = Path.home() / '.openclaw' / 'workspace'

HAIKU_MODEL    = "claude-haiku-4-5"
SONNET_MODEL   = "claude-sonnet-4-5"   # confirmed from curator_rss_v2.py line 801
MAX_CANDIDATES = 5      # max probationary domains evaluated per day
MAX_TITLES     = 60     # interest profile cap (keeps prompt size predictable)
MAX_BASELINE   = 200    # baseline history cap

# Non-US and US RSS source domains (confirmed in feed — hardcoded per WS5 Phase B plan)
NON_US_DOMAINS = {
    "aljazeera.com", "dw.com", "spiegel.de",
    "faz.net", "welt.de", "oglobo.globo.com",
}
US_DOMAINS = {
    "zerohedge.com", "propublica.org", "stlouisfed.org",
    "ritholtz.com", "antiwar.com", "warontherocks.com", "justsecurity.org",
}


# ── LLM clients ──────────────────────────────────────────────────────────────

def _haiku_client():
    """Return an Anthropic client using keychain credentials."""
    import keyring
    import anthropic
    api_key = keyring.get_password("anthropic", "api_key")
    if not api_key:
        raise RuntimeError("Anthropic API key not found in keychain (service='anthropic', account='api_key')")
    return anthropic.Anthropic(api_key=api_key)


def _haiku(prompt: str, max_tokens: int = 400) -> str:
    """Call Haiku and return the text response."""
    client = _haiku_client()
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _sonnet(prompt: str, max_tokens: int = 800) -> str:
    """Call Sonnet and return the text response."""
    client = _haiku_client()   # same keychain/client pattern; model param differs
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_history() -> dict:
    """Load curator_history.json. Returns empty dict on error."""
    if not HISTORY_PATH.exists():
        print(f"⚠️  curator_history.json not found at {HISTORY_PATH}")
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text())
    except Exception as e:
        print(f"⚠️  Could not load curator_history.json: {e}")
        return {}


def _load_sources() -> list:
    """Load curator_sources.json. Returns empty list on error."""
    if not SOURCES_PATH.exists():
        return []
    try:
        return json.loads(SOURCES_PATH.read_text())
    except Exception as e:
        print(f"⚠️  Could not load curator_sources.json: {e}")
        return []


def _save_sources(sources: list) -> None:
    """Write updated curator_sources.json."""
    SOURCES_PATH.write_text(json.dumps(sources, indent=2))


def _load_interest_profile(days: int = 30) -> list:
    """
    Read curator_preferences.json and return deduplicated liked/saved titles
    from the last `days` days. Capped at MAX_TITLES to keep prompt size
    predictable.
    """
    if not PREFS_PATH.exists():
        return []
    try:
        prefs = json.loads(PREFS_PATH.read_text())
    except Exception as e:
        print(f"⚠️  Could not load curator_preferences.json: {e}")
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    seen: set = set()
    titles: list = []

    feedback = prefs.get("feedback_history", {})
    for day_key in sorted(feedback.keys(), reverse=True):
        if day_key < cutoff:
            break
        day_data = feedback[day_key]
        for bucket in ("liked", "saved"):
            for entry in day_data.get(bucket, []):
                title = entry.get("title", "").strip()
                if title and title not in seen:
                    seen.add(title)
                    titles.append(title)
                    if len(titles) >= MAX_TITLES:
                        return titles

    return titles


# ── Observation 1: Topic Velocity ─────────────────────────────────────────────

def observe_topic_velocity(today_str: str) -> dict:
    """
    Compare today's briefing titles vs 30-day reading history.
    Identifies momentum topics and interest-profile gaps.
    """
    print("\n📈 Running Topic Velocity observation...")
    history = _load_history()

    if not history:
        return {
            "type": "topic_velocity",
            "content": "📈 <b>Momentum:</b> curator_history.json unavailable — skipping velocity analysis.",
            "raw_data": {"error": "history not loaded"},
        }

    cutoff = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")

    today_titles: list = []
    baseline_titles: list = []

    for entry in history.values():
        appearances = entry.get("appearances", [])
        title = entry.get("title", "").strip()
        if not title:
            continue

        in_today    = any(a.get("date") == today_str for a in appearances)
        in_baseline = any(a.get("date", "") >= cutoff for a in appearances)

        if in_today:
            today_titles.append(title)
        if in_baseline and not in_today:
            # Baseline = articles that appeared in last 30 days but NOT today
            # (avoid double-counting)
            baseline_titles.append(title)

    print(f"   Today's articles: {len(today_titles)}")
    print(f"   30-day baseline:  {len(baseline_titles)}")

    # Quiet path — curator hasn't run yet (or ran with no results)
    if not today_titles:
        return {
            "type": "topic_velocity",
            "content": "📈 <b>Momentum:</b> Today's briefing not yet logged — curator may not have run.",
            "raw_data": {
                "today_count": 0,
                "baseline_count": len(baseline_titles),
            },
        }

    # Load interest profile
    interest_titles = _load_interest_profile()
    print(f"   Interest profile: {len(interest_titles)} titles")

    # Cap baseline for prompt economy
    baseline_sample = baseline_titles[:MAX_BASELINE]

    prompt = f"""Today's briefing titles ({len(today_titles)} articles):
{chr(10).join(f"- {t}" for t in today_titles)}

30-day reading history titles (sample of {len(baseline_sample)}):
{chr(10).join(f"- {t}" for t in baseline_sample)}

Reader's liked/saved interest profile (last 30 days, {len(interest_titles)} titles):
{chr(10).join(f"- {t}" for t in interest_titles)}

In 2-4 lines using HTML bold formatting:
Line 1: Start with exactly "📈 <b>Momentum:</b>" — name 2-3 specific topics/events gaining coverage today vs the baseline. Be concrete (name the topic, not the category).
Line 2: Start with exactly "⚠️ <b>Gap:</b>" — name 1-2 specific topics from the interest profile that are absent from today's briefing.
No preamble. No closing remarks. Output only those lines."""

    try:
        content = _haiku(prompt, max_tokens=300)
        print(f"   Haiku response: {len(content)} chars")
    except Exception as e:
        print(f"   ⚠️  Haiku call failed: {e}")
        content = f"📈 <b>Momentum:</b> Analysis unavailable ({e})"

    return {
        "type": "topic_velocity",
        "content": content,
        "raw_data": {
            "today_count": len(today_titles),
            "baseline_count": len(baseline_titles),
            "interest_profile_count": len(interest_titles),
        },
    }


# ── Observation 2: Discovery Candidates ───────────────────────────────────────

def observe_discovery_candidates(today_str: str) -> dict:
    """
    Haiku-evaluate probationary domains added today via Brave search.
    Writes verdicts back to curator_sources.json.
    """
    print("\n🔍 Running Discovery Candidates observation...")
    sources = _load_sources()

    # Find unevaluated probationary entries added today
    candidates = [
        s for s in sources
        if s.get("trust") == "probationary"
        and s.get("set_by") == "auto"
        and s.get("added_date") == today_str
        and "haiku_evaluated" not in s
    ]

    print(f"   Unevaluated candidates added today: {len(candidates)}")

    if not candidates:
        return {
            "type": "discovery_candidate",
            "content": "🔍 <b>Sources:</b> No new sources discovered today.",
            "raw_data": {"candidates_today": 0},
        }

    batch = candidates[:MAX_CANDIDATES]
    verdicts = {"credible": [], "noise": [], "unknown": []}

    for entry in batch:
        domain = entry["domain"]
        query  = entry.get("query", "")
        print(f"   Evaluating: {domain} (query: {query!r})")

        prompt = f"""Domain: {domain}
Found via Brave query: "{query}"
Is this a credible, original-reporting geopolitics or economics news/analysis source?
Answer with exactly: credible / noise / unknown — then one sentence explanation."""

        try:
            response = _haiku(prompt, max_tokens=80)
            first_word = response.split()[0].lower().rstrip('.,;:')
            verdict = first_word if first_word in ("credible", "noise", "unknown") else "unknown"
            note = response  # full response as the note
        except Exception as e:
            print(f"   ⚠️  Haiku call failed for {domain}: {e}")
            verdict = "unknown"
            note = f"Evaluation failed: {e}"

        verdicts[verdict].append(domain)

        # Write result back to curator_sources.json entry
        for s in sources:
            if s.get("domain") == domain:
                s["haiku_evaluated"]    = True
                s["haiku_verdict"]      = verdict
                s["haiku_note"]         = note
                s["haiku_evaluated_at"] = today_str
                break

    # Persist evaluations
    try:
        _save_sources(sources)
        print(f"   Wrote verdicts to curator_sources.json")
    except Exception as e:
        print(f"   ⚠️  Could not save curator_sources.json: {e}")

    # Compose summary line
    n = len(batch)
    parts = []
    if verdicts["credible"]:
        parts.append(f"{len(verdicts['credible'])} credible ({', '.join(verdicts['credible'])})")
    if verdicts["noise"]:
        parts.append(f"{len(verdicts['noise'])} noise")
    if verdicts["unknown"]:
        parts.append(f"{len(verdicts['unknown'])} unknown")

    summary = "; ".join(parts) if parts else "none evaluated"
    content = f"🔍 <b>Sources:</b> {n} evaluated — {summary}"

    return {
        "type": "discovery_candidate",
        "content": content,
        "raw_data": {
            "candidates_today": len(candidates),
            "evaluated": n,
            "verdicts": verdicts,
        },
    }


# ── Observation 3: Source Anomalies ──────────────────────────────────────────

def observe_source_anomalies(today_str: str):
    """
    Detect trusted sources drifting in topic focus, tone, or quality.
    Returns None (quiet path) if no anomalies are found.
    """
    print("\n⚠️  Running Source Anomalies observation...")
    sources  = _load_sources()
    history  = _load_history()

    trusted_domains = {s["domain"] for s in sources if s.get("trust") == "trusted"}
    if not trusted_domains:
        print("   No trusted domains in curator_sources.json — skipping")
        return None

    cutoff = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")

    anomaly_lines = []

    for domain in sorted(trusted_domains):
        # Collect articles from this domain in the history
        domain_entries = [
            e for e in history.values()
            if extract_domain(e.get("url", "")) == domain
        ]

        # Baseline: entries with any appearance in last 30 days
        baseline = [
            e for e in domain_entries
            if any(a.get("date", "") >= cutoff for a in e.get("appearances", []))
        ]

        # Minimum history threshold — skip sparse sources
        if len(baseline) < 5:
            print(f"   {domain}: {len(baseline)} articles in 30d — too sparse, skipping")
            continue

        # Recent: last 10 by most recent appearance date
        def _most_recent_date(entry):
            dates = [a.get("date", "") for a in entry.get("appearances", [])]
            return max(dates) if dates else ""

        recent = sorted(domain_entries, key=_most_recent_date, reverse=True)[:10]

        recent_titles   = [e.get("title", "").strip() for e in recent if e.get("title")]
        baseline_titles = [e.get("title", "").strip() for e in baseline if e.get("title")][:50]

        print(f"   {domain}: {len(baseline)} baseline, {len(recent)} recent — calling Haiku")

        prompt = f"""Source: {domain}
Recent articles (last {len(recent_titles)}):
{chr(10).join(f"- {t}" for t in recent_titles)}

30-day baseline articles (sample of {len(baseline_titles)}):
{chr(10).join(f"- {t}" for t in baseline_titles)}

Has this source changed in topic focus, tone, or quality recently?
Answer in one sentence. If no notable change, say exactly: "No anomaly detected." """

        try:
            response = _haiku(prompt, max_tokens=120)
            if "no anomaly detected" in response.lower():
                print(f"   {domain}: no anomaly")
            else:
                print(f"   {domain}: anomaly — {response[:80]}...")
                anomaly_lines.append(f"⚠️ <b>Source drift:</b> {domain} — {response}")
        except Exception as e:
            print(f"   ⚠️  Haiku call failed for {domain}: {e}")

    if not anomaly_lines:
        print("   Quiet path — no source anomalies detected")
        return None

    return {
        "type": "source_anomaly",
        "content": "\n".join(anomaly_lines),
        "raw_data": {"anomalies_found": len(anomaly_lines), "trusted_domains_checked": len(trusted_domains)},
    }


# ── Observation 4: US Press Blind Spots ───────────────────────────────────────

def observe_blind_spots(today_str: str):
    """
    Surface stories with strong non-US coverage that are absent from US sources.
    Returns None (quiet path) if curator_latest.json is missing or no gap found.
    Depends on: curator_latest.json written by curator_rss_v2.py (WS5 pre-condition).
    """
    print("\n🌍 Running US Press Blind Spots observation...")

    if not LATEST_PATH.exists():
        print(f"   curator_latest.json not found — skipping (run curator first)")
        return None

    try:
        pool = json.loads(LATEST_PATH.read_text())
    except Exception as e:
        print(f"   Could not load curator_latest.json: {e}")
        return None

    # Articles use 'link' (full URL) not 'source' (display name) — extract domain for classification
    non_us = [e for e in pool if extract_domain(e.get("link", "")) in NON_US_DOMAINS]
    us     = [e for e in pool if extract_domain(e.get("link", "")) in US_DOMAINS]

    print(f"   Pool: {len(pool)} articles — {len(non_us)} non-US, {len(us)} US")

    if not non_us:
        print("   No non-US articles in pool — quiet path")
        return None
    if not us:
        print("   No US articles in pool — quiet path")
        return None

    non_us_titles = [e.get("title", "").strip() for e in non_us if e.get("title")]
    us_titles     = [e.get("title", "").strip() for e in us     if e.get("title")]

    prompt = f"""Non-US source articles today ({len(non_us_titles)} articles):
{chr(10).join(f"- {t}" for t in non_us_titles)}

US source articles today ({len(us_titles)} articles):
{chr(10).join(f"- {t}" for t in us_titles)}

Identify 1-2 stories covered in non-US sources but absent or underrepresented
in US sources. Be specific — name the story, not the category.
If no significant gap exists, say exactly: "No blind spots today." """

    try:
        response = _haiku(prompt, max_tokens=200)
        print(f"   Haiku response: {response[:80]}...")
    except Exception as e:
        print(f"   ⚠️  Haiku call failed: {e}")
        return None

    if "no blind spots today" in response.lower():
        print("   Quiet path — no blind spots detected")
        return None

    return {
        "type": "blind_spot",
        "content": f"🌍 <b>Blind spot:</b> {response}",
        "raw_data": {
            "non_us_count": len(non_us),
            "us_count": len(us),
            "non_us_titles": non_us_titles,
        },
    }


# ── Observation 5: Lateral Connections (weekly, Sonnet) ───────────────────────

def _load_learned_patterns() -> dict:
    """Load the learned_patterns block from curator_preferences.json."""
    if not PREFS_PATH.exists():
        return {}
    try:
        prefs = json.loads(PREFS_PATH.read_text())
        return prefs.get("learned_patterns", {})
    except Exception as e:
        print(f"   ⚠️  Could not load learned_patterns: {e}")
        return {}


def observe_lateral_connections(today_str: str) -> dict:
    """
    Use Sonnet to surface adjacent topics, second-order implications, and
    suggested sources the reader isn't currently tracking.
    Weekly cadence — only fires on Sundays (enforced in main()).
    """
    print("\n🔗 Running Lateral Connections observation (Sonnet)...")

    interest_titles = _load_interest_profile()
    learned         = _load_learned_patterns()

    # Summarise learned_patterns for prompt
    # preferred_themes + preferred_sources are dicts {name: score} — extract keys
    themes         = list(learned.get("preferred_themes", {}).keys())
    sources        = list(learned.get("preferred_sources", {}).keys())[:10]
    content_types  = [k for k, v in learned.get("preferred_content_types", {}).items()
                      if isinstance(v, (int, float)) and v > 0]
    avoid          = list(learned.get("avoid_patterns", {}).keys())[:8]

    pattern_summary = (
        f"Preferred themes: {', '.join(themes)}\n"
        f"Preferred sources: {', '.join(sources)}\n"
        f"Preferred content types: {', '.join(content_types)}\n"
        f"Patterns to avoid: {', '.join(avoid)}"
    )

    print(f"   Interest profile: {len(interest_titles)} titles, {len(themes)} themes")

    prompt = f"""Reading history (last 30 days — liked and saved articles):
{chr(10).join(f"- {t}" for t in interest_titles)}

Current interest profile summary:
{pattern_summary}

You are a research advisor who knows this reader well.

Identify 2-3 topics, angles, or second-order implications the reader has NOT
been covering but would find genuinely valuable given their interests.

For each suggestion:
- Name the topic specifically
- Explain in one sentence why it connects to their existing interests
- Suggest 1-2 specific sources they could add (real, credible outlets)

Be specific and intellectually honest. Do not suggest topics already well-covered
in their history. Format using HTML bold tags for Telegram."""

    try:
        response = _sonnet(prompt, max_tokens=600)
        print(f"   Sonnet response: {len(response)} chars")
    except Exception as e:
        print(f"   ⚠️  Sonnet call failed: {e}")
        response = f"Analysis unavailable: {e}"

    dt = datetime.strptime(today_str, "%Y-%m-%d")
    date_label = dt.strftime("%b %-d")
    content = f"🧠 <b>Weekly Connections — {date_label}</b>\n\n{response}"

    return {
        "type": "lateral_connections",
        "content": content,
        "raw_data": {
            "interest_titles_count": len(interest_titles),
            "themes": themes,
        },
    }


# ── Telegram delivery ─────────────────────────────────────────────────────────

def format_telegram(observations: list, today_str: str) -> str:
    """Format daily observations as an HTML Telegram message."""
    dt = datetime.strptime(today_str, "%Y-%m-%d")
    date_label = dt.strftime("%b %-d")  # e.g. "Mar 15"
    header = f'🧠 <b>Intelligence — {date_label}</b>'

    body_lines = [obs["content"] for obs in observations]

    # Quiet day: obs1 failed/unavailable AND obs2 has nothing to report
    all_quiet = all(
        "unavailable" in obs["content"]
        or "not yet logged" in obs["content"]
        or "No new sources" in obs["content"]
        for obs in observations
    )
    if all_quiet:
        body_lines = ["Quiet day — no anomalies detected."]

    return header + "\n\n" + "\n".join(body_lines)


# ── Output storage ────────────────────────────────────────────────────────────

def save_output(observations: list, today_str: str, telegram_sent: bool,
                weekly: bool = False) -> Path:
    """Write intelligence JSON to ~/.openclaw/workspace/.
    Weekly lateral connections saved as intelligence_weekly_YYYYMMDD.json.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix   = "intelligence_weekly_" if weekly else "intelligence_"
    filename = OUTPUT_DIR / f"{prefix}{today_str.replace('-', '')}.json"
    payload  = {
        "date": today_str,
        "observations": observations,
        "telegram_sent": telegram_sent,
    }
    filename.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return filename


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="curator_intelligence.py — WS5 Phase A+B daily observation layer"
    )
    parser.add_argument("--telegram", action="store_true",
                        help="Send Telegram message after observations")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Stdout only — skip file write and Telegram")
    parser.add_argument("--date",     default=None,
                        help="Override today's date (YYYY-MM-DD)")
    args = parser.parse_args()

    today_str = args.date or date.today().isoformat()
    dry_run   = args.dry_run

    print(f"\n🧠 curator_intelligence — {today_str}" + (" [DRY RUN]" if dry_run else ""))
    print("=" * 60)

    observations = []

    # ── Phase A observations (always run) ──────────────────────────────────────

    # Observation 1: Topic Velocity
    try:
        obs1 = observe_topic_velocity(today_str)
        observations.append(obs1)
    except Exception as e:
        print(f"⚠️  Topic velocity observation failed: {e}")
        observations.append({
            "type": "topic_velocity",
            "content": f"📈 <b>Momentum:</b> Observation failed: {e}",
            "raw_data": {"error": str(e)},
        })

    # Observation 2: Discovery Candidates
    try:
        obs2 = observe_discovery_candidates(today_str)
        observations.append(obs2)
    except Exception as e:
        print(f"⚠️  Discovery candidates observation failed: {e}")
        observations.append({
            "type": "discovery_candidate",
            "content": f"🔍 <b>Sources:</b> Observation failed: {e}",
            "raw_data": {"error": str(e)},
        })

    # ── Phase B observations (quiet-path functions return None — only append if present) ──

    # Observation 3: Source Anomalies
    try:
        obs3 = observe_source_anomalies(today_str)
        if obs3:
            observations.append(obs3)
    except Exception as e:
        print(f"⚠️  Source anomaly observation failed: {e}")

    # Observation 4: US Press Blind Spots
    try:
        obs4 = observe_blind_spots(today_str)
        if obs4:
            observations.append(obs4)
    except Exception as e:
        print(f"⚠️  Blind spot observation failed: {e}")

    # ── Daily Telegram message ─────────────────────────────────────────────────

    telegram_msg  = format_telegram(observations, today_str)
    telegram_sent = False

    print("\n" + "=" * 60)
    print("📋 Daily Telegram preview:")
    print(telegram_msg)
    print("=" * 60)

    # ── Observation 5: Lateral Connections (weekly, Sundays only) ─────────────

    dt_check  = datetime.strptime(today_str, "%Y-%m-%d")
    is_sunday = dt_check.weekday() == 6   # 0=Monday, 6=Sunday
    weekly_obs     = None
    weekly_msg     = None

    if is_sunday:
        try:
            weekly_obs = observe_lateral_connections(today_str)
            weekly_msg = weekly_obs["content"]
            print("\n" + "=" * 60)
            print("🔗 Weekly Telegram preview:")
            print(weekly_msg)
            print("=" * 60)
        except Exception as e:
            print(f"⚠️  Lateral connections observation failed: {e}")

    # ── Dry-run exit ───────────────────────────────────────────────────────────

    if dry_run:
        print("\n🧪 DRY RUN — no files written, no Telegram sent")
        return

    # ── Save daily output ──────────────────────────────────────────────────────

    try:
        output_path = save_output(observations, today_str, telegram_sent=False)
        print(f"\n💾 Saved daily: {output_path}")
    except Exception as e:
        print(f"⚠️  Could not save daily output: {e}")
        output_path = None

    # ── Send daily Telegram ────────────────────────────────────────────────────

    if args.telegram:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        telegram_sent = send_telegram_alert(telegram_msg, chat_id=chat_id or None)

    if output_path and telegram_sent:
        try:
            payload = json.loads(output_path.read_text())
            payload["telegram_sent"] = True
            output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        except Exception:
            pass

    # ── Save + send weekly Lateral Connections (if Sunday) ────────────────────

    if is_sunday and weekly_obs:
        try:
            weekly_path = save_output([weekly_obs], today_str,
                                      telegram_sent=False, weekly=True)
            print(f"💾 Saved weekly: {weekly_path}")
        except Exception as e:
            print(f"⚠️  Could not save weekly output: {e}")
            weekly_path = None

        if args.telegram:
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            weekly_sent = send_telegram_alert(weekly_msg, chat_id=chat_id or None)
            if weekly_path and weekly_sent:
                try:
                    payload = json.loads(weekly_path.read_text())
                    payload["telegram_sent"] = True
                    weekly_path.write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False)
                    )
                except Exception:
                    pass

    print(f"\n✅ Intelligence run complete — {today_str}")


if __name__ == "__main__":
    main()
