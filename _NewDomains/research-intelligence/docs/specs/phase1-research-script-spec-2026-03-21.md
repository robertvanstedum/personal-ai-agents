# Spec: Phase 1 agent/research.py — Direct Research Script
**Author:** OpenClaw (Mini-moi) — Product Owner
**Date:** March 21, 2026
**For:** Claude Code (implementation)
**Status:** APPROVED BY ROBERT — ready to build
**Parent spec:** docs/specs/phase1-agent-runner-spec-2026-03-21.md
**Resolves:** BUG-002, BUG-003, BUG-004

---

## Context

Session 001 ran inside a Sonnet subagent orchestrator. That wrapper cost ~$2.09 in Sonnet overhead to execute ~$0.002 of Haiku triage work. The budget ledger only saw the Haiku spend — the orchestration cost was invisible. This is broken by design.

The fix: replace the subagent entirely with a standalone Python script. The order of operations is explicit in code, not inferred by a model. Every API call is instrumented. Costs accumulate explicitly and are passed to `run.py end`.

Read WAY_OF_WORKING.md before doing anything. It is binding.

---

## What this script is

`agent/research.py` — the standard Kotkin-thread research session. Replaces the Sonnet subagent for all structured research sessions.

**Run command:**
```bash
cd ~/Projects/personal-ai-agents/_NewDomains/research-intelligence
source ~/Projects/personal-ai-agents/ai-env/bin/activate
python agent/research.py --session-name kotkin-002 --topic empire-landpower --estimated-cost 0.30
```

The script handles everything: budget gate, searches, triage, translation, file writes, session close, Telegram.

---

## What it does — order of operations (hardcoded, not model-inferred)

### 0. Pre-flight
- Call `run.py start --session-name [name] --estimated-cost [est]`
- Check exit code: 0=proceed, 1=abort (hard stop), 2=abort (estimated cost too high)
- If abort: print reason, exit. Do not proceed.
- Read `agent/config.json` for all model IDs, budget limits, chat_id
- Read `topics/[topic]/CONTEXT.md` for research targets
- Initialize `session_cost = 0.0`

### 1. Free source pass (zero API cost)
Use the `requests` library (already in venv) to fetch search results. No model calls.

**Searches to run** (read from `agent/config.json` → `session_searches`, or use defaults):

Default searches:
- `"Mackinder heartland theory non-Western IR scholarship"`
- `"China Russia geopolitical position swap IR theory"`
- `"dependency theory great power competition 2020s"`
- `"CLACSO dependency theory hegemony United States China"`
- `"non-Western IR scholars citing Mackinder heartland"`

Use the Brave Search API. Key from environment: `BRAVE_API_KEY` in `~/Projects/personal-ai-agents/.env`.

For each result: extract title, URL, snippet, source domain. Write to a candidates list in memory.

Also fetch: `https://en.wikipedia.org/wiki/The_Geographical_Pivot_of_History` — extract linked citations (look for `<cite>` tags and footnote references to books/papers).

**Target:** 8-12 raw candidates before triage.

### 2. Haiku triage (with Ollama-first fallback logic)

For each candidate, call the triage model with this prompt:

```
Score this source 1-5 for relevance to the following research targets.
Score on intellectual content and argument quality first.
Non-Anglophone origin is a tiebreaker between equal-quality sources — not a primary scoring criterion.
A strong English-language source from a non-Western scholar publishes in English scores high.
A weak source doesn't get promoted just for being non-Anglophone.

Return JSON only: {"score": N, "targets": [list of target numbers], "explanation": "one sentence", "language": "English/Portuguese/Chinese/etc", "anglophone_origin": true/false}

Research targets:
1. Non-Anglophone takes on China-Russia positional swap
2. Authoritarian loyalty-vs-competence literature (comparative, beyond Stalin)
3. Historical precedents for industrial hollowing (dominant power transferring strategic capacity to rival)
4. Mackinder's actual argument — who has updated or challenged it seriously?
5. Latin American dependency theory angle on current great power competition

Source title: [title]
Source: [outlet/journal/institution]
Text: [snippet or first paragraph]
```

**Model routing — try Ollama first, fall back to Haiku:**

```python
def triage_source(candidate, config):
    primary = config["triage_model"]["primary"]   # "ollama/gemma" or similar
    fallback = config["triage_model"]["fallback"]  # "claude-3-5-haiku-20241022"

    result = try_ollama(candidate, primary)
    if result is None:
        result = call_haiku(candidate, fallback)
        log_model_used("haiku", cost=estimate_cost(candidate))
        session_cost += estimate_cost(candidate)
    else:
        log_model_used("ollama", cost=0.0)

    return result
```

Ollama endpoint: `http://localhost:11434/api/generate` — if connection refused or timeout (3s), fall back silently to Haiku. Log which model was used per candidate.

**Cost tracking:** Every Haiku call logs tokens in + tokens out. Use Anthropic's usage response fields. Accumulate into `session_cost`.

### 3. Write sources-candidates.md

After triage, write/update `topics/[topic]/sources-candidates.md`.

Format:
```markdown
# Source Candidates — [Session Name]
_Last updated: YYYY-MM-DD_

| # | Score | Title | Author | Source | Language | Anglophone | Targets | URL | Notes |
|---|-------|-------|--------|--------|----------|------------|---------|-----|-------|
```

Mark top 3 with `⭐`. Sort by score descending.

### 4. Translation (one candidate)

Pick the highest-scoring non-Anglophone-origin candidate with score ≥ 4.
If none qualify: log "no non-Anglophone candidate scored ≥ 4, skipping translation" and proceed.

Call Haiku with:
```
Translate the following excerpt to English. Preserve the author's argumentative structure — don't smooth over technical terms, translate them and note the original in brackets. 300-500 words.

[excerpt]
```

Temperature: read from `config["models"]["translation"]["temperature"]` (default: 0.3).

Track tokens. Add to `session_cost`.

Write to: `topics/[topic]/translations/[session-name]-[author-lastname].md`

Format (from template): include original language excerpt (first 2-3 sentences), translation, and "Why this matters" connecting to research targets.

### 5. Write session findings

Write `topics/[topic]/[session-name].md` using the template at `agent/templates/session-findings-template.md`.

The script writes the structured fields (date, session, cost, duration, sources_reviewed in the machine-readable header). The findings, sources, threads, and agent notes sections are written by the script based on what was found.

**For findings:** the script should write a plain summary — what the top candidates are, what the triage revealed, what the non-English find was. This is a templated summary, not a model-generated essay. No Sonnet call here.

**For agent notes:** log exactly which models ran, which fell back, costs per step, search queries used.

### 6. Update library/README.md

Add rows for candidates scoring ≥ 4. Format:
```
| Date | Topic | Source | Language | Type | Summary (one line) | Path/URL |
```

Read existing rows first — don't duplicate entries already in the table.

### 7. Close the session

Call:
```bash
python agent/run.py end \
  --cost [session_cost, rounded to 4 decimal places] \
  --duration "[elapsed_minutes]min" \
  --notes "[session-name]: [one line summary of what was found]"
```

`session_cost` is the accumulated total from all Haiku calls (Ollama calls = $0.00).

### 8. Send Telegram

Use `requests` to call the Telegram Bot API directly (not a model call). Token from keyring (`polling_bot_token`). Chat ID from `agent/config.json`.

Message format:
```
[Research Intel] 🌐 [session-name] complete

Thread: [topic]
Cost: $[session_cost] | Duration: ~[duration]min
Triage model: [ollama/gemma or haiku fallback]

Top find: [title + one sentence why it matters]
Non-English: [what was translated, or "none scored ≥ 4"]

Threads for next session:
• [thread 1 from session findings]
• [thread 2]
• [thread 3]

Full findings: topics/[topic]/[session-name].md
```

Send only if `session_cost > 0` or a candidate scored 5. If nothing interesting found (all scores ≤ 2), send a short: `[Research Intel] ℹ️ [session-name]: no strong candidates found. See findings log.`

---

## agent/config.json — updated structure

Add these blocks to the existing config:

```json
{
  "chat_id": "8379221702",
  "telegram_bot": "polling_bot_token",
  "budget": {
    "daily_limit": 3.00,
    "weekly_limit": 10.00,
    "total_limit": 20.00,
    "daily_warn": 2.50,
    "total_warn": 18.00
  },
  "session_log": "library/session-log.md",
  "triage_model": {
    "primary": "ollama/gemma",
    "fallback": "claude-3-5-haiku-20241022",
    "ollama_endpoint": "http://localhost:11434/api/generate",
    "ollama_timeout_seconds": 3
  },
  "models": {
    "triage": {
      "temperature": 0.0
    },
    "translation": {
      "model": "claude-3-5-haiku-20241022",
      "temperature": 0.3
    },
    "synthesis": {
      "model": "claude-sonnet-4-5",
      "temperature": 0.7,
      "note": "Session 3-4 only, after Haiku triage validates material"
    }
  },
  "search": {
    "brave_api_key_env": "BRAVE_API_KEY",
    "rate_limit_seconds": 1,
    "results_per_query": 5
  },
  "session_searches": {
    "empire-landpower": [
      "Mackinder heartland theory non-Western IR scholarship",
      "China Russia geopolitical position swap IR theory",
      "dependency theory great power competition 2020s",
      "CLACSO dependency theory hegemony United States China",
      "non-Western IR scholars citing Mackinder heartland"
    ]
  }
}
```

---

## Cost tracking — implementation detail

Every Haiku API call returns `usage.input_tokens` and `usage.output_tokens`. Pricing as of March 2026:
- claude-3-5-haiku: $0.80/M input, $4.00/M output

Track in script:
```python
def track_cost(usage, model="claude-3-5-haiku-20241022"):
    input_cost = (usage.input_tokens / 1_000_000) * 0.80
    output_cost = (usage.output_tokens / 1_000_000) * 4.00
    return round(input_cost + output_cost, 6)
```

Accumulate across all calls. Pass final total to `run.py end --cost`.

---

## What research.py does NOT do

- No Sonnet calls — ever in this script
- No model deciding what to do next — all decisions are in the script logic
- No interactive prompts — runs end to end, logs everything
- No writes outside `_NewDomains/research-intelligence/`
- No git commits — OpenClaw or Robert commits after review
- No GitHub issues

---

## Files touched

| File | Action |
|------|--------|
| `agent/research.py` | Create (new) |
| `agent/config.json` | Update (add triage_model, models, search blocks) |
| `topics/[topic]/sources-candidates.md` | Create or update at runtime |
| `topics/[topic]/[session-name].md` | Create at runtime |
| `topics/[topic]/translations/` | Create translation file at runtime (if qualified candidate) |
| `library/README.md` | Update rows at runtime |
| `library/session-log.md` | Updated by run.py end |

---

## Done when

- [ ] `agent/research.py` written and shown to Robert before saving
- [ ] `agent/config.json` updated with new blocks
- [ ] `python agent/research.py --session-name test-dry-run --topic empire-landpower --estimated-cost 0.10` runs end to end without error (use real searches, real triage)
- [ ] Session log shows correct cost (Haiku tokens only, Ollama = $0)
- [ ] Ollama fallback tested: kill Ollama, confirm script falls back to Haiku and logs the fallback
- [ ] Telegram message sent and received
- [ ] Commit on `poc/research-script` branch per WAY_OF_WORKING.md
- [ ] BUGS.md: BUG-002, BUG-003, BUG-004 marked FIXED with commit hash

---

## Review protocol (per WAY_OF_WORKING.md)

1. Claude Code: show `research.py` in full before writing any file
2. Robert or OpenClaw reviews
3. Claude Code writes only after approval
4. Claude Code runs the dry-run test and confirms output
5. Commit on branch, Robert merges

---

*OpenClaw (Mini-moi) | Product Owner | March 21, 2026*
*Robert approved architecture: rigid script, Ollama-first with Haiku fallback, explicit cost tracking.*
*Resolves BUG-002 (Ollama not wired), BUG-003 (budget ledger), BUG-004 (no research.py).*
*Claude Code: show research.py before writing any files.*
