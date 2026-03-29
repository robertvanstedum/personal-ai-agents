# CASE STUDY: Research Intelligence — Taiwan Thread

**File:** `docs/case-studies/CASE_STUDY_TAIWAN_THREAD_2026-03-29.md`

---

## Overview

This documents the full arc of the `hellscape-taiwan-porcupine` research thread — from initial spawn through topic drift, diagnosis, fix, and first meaningful output. It's a useful record of how the research pipeline actually behaves under real conditions, including failure modes.

**Thread hypothesis (verbatim from user input):**

> "I suspect that Taiwan wants to fight China more than other countries want it to. I think it wants a credible defense so it can stay an independent trading country with both China and the West. But it is culturally Chinese and China is probably its biggest partner. I suspect it wants a real Hong Kong type deal, and that would be a win-win. China used the Philippines as a trading post going back to the Spaniards. Taiwan could be the trading post if they reach a deal, which they would both want rather than war."

| | |
|---|---|
| **Domain** | Geopolitics / Research Intelligence |
| **Sessions** | 4 (2 pre-fix, 2 post-fix) |
| **Deeper dives generated** | 4 (3 from drifted sessions, 1 clean) |
| **Total cost** | ~$0.19 |

---

## What Went Wrong — Topic Drift

Sessions 001 and 002 retrieved almost entirely irrelevant sources. Despite the thread being titled `hellscape-taiwan-porcupine`, the agent surfaced:

- *The New American Way of War: Military Culture and the Political Utility of Force* (appeared in both sessions, multiple times)
- RAND report on U.S. force planning
- References to Mackinder's geopolitics, China-Russia positional swap, Latin American dependency theory

Zero sources on Taiwan's actual defense posture, the ODC, cross-strait economics, or Taiwanese public opinion.

The deeper dive synthesizer (Opus) caught this immediately and was honest about it:

> "The research sessions did not directly investigate the stated hypothesis. The sources retrieved focus almost exclusively on a single text: 'The New American Way of War' — a work analyzing U.S. military doctrine, the Iraq War, and American strategic culture."

This is the system working correctly at the synthesis layer even when the research layer failed.

### Root causes identified

1. **No session-level system prompt** — `research.py` had no mechanism to anchor the agent to the thread's core question. The triage model (gemma3:1b via Ollama) was scoring on generic keyword relevance rather than thread-specific focus.

2. **Missing triage_targets** — the Taiwan thread had no triage targets defined in `config.json`, so the agent had no structured scoring criteria to work against.

3. **Generic promoted queries** — the initial query set included terms like "military culture" and "deterrence" that matched U.S. doctrine literature as readily as Taiwan-specific material.

4. **Silent novelty scoring failure** — a `NameError: config vs cfg` in the novelty scoring pass meant `source_utils.py` was never actually running, despite being "shipped" in the prior commit. The triage loop crashed before reaching that line and fell through silently.

---

## Diagnosis

The deeper dive output made the failure unambiguous. Three deeper dives generated from the same two sessions all returned identical sources — the clearest possible signal that the research layer wasn't functioning.

Grok's review of the third deeper dive confirmed: the system prompt and query fixes from commit `effe2ff` had not actually been tested because the thread auto-closed before a new session could run. Every deeper dive was synthesizing stale pre-fix data.

**Timeline that clarified this:**

| Time | Event |
|------|-------|
| 11:40 AM | session-001 runs (pre-fix) |
| 11:44 AM | session-002 runs (pre-fix) |
| 11:55 AM | deeper-dive-001 generated (from drifted sessions) |
| 1:13 PM | deeper-dive-002 generated (same sessions, thread reopened manually) |
| 2:02 PM | Note added: "prompts updated, hopefully better" |
| 2:04 PM | deeper-dive-003 generated (still same sessions — thread had auto-closed again before any new session ran) |

The auto-close behavior was actively preventing the fix from being tested.

---

## What Was Fixed

### Commit `effe2ff` — system prompt + query fix

**`agent/prompts/research_session_v1.1.md` created** with five sections:

- **CORE ANCHOR** — thread title, domain, hypothesis, research question, session number injected from `thread.json`
- **STRICT RULES** — explicit rejection criteria, relevance threshold
- **Source Selection & Scoring** — novelty discount integrated
- **Output Format** — structured session summary enforced
- **Taiwan Porcupine guardrails** — explicit blocklist: U.S. military culture books, Iraq War analysis, Mackinder, Latin American dependency theory

**`research.py` modified** to load and inject prompt at session start via `load_session_system_prompt()`. Both `try_ollama()` and `call_haiku()` gained optional `system=` parameter.

**Taiwan `thread.json` queries replaced** with eight Taiwan-specific candidates:

1. Taiwan Overall Defense Concept ODC asymmetric strategy
2. Taiwan porcupine defense procurement anti-ship missiles drones
3. Taiwan independence polling NCCU Election Study Center 2024 2025
4. TSMC semiconductor leverage cross-strait relations
5. Taiwan one country two systems public opinion post-Hong Kong
6. Cross-strait trade volumes economic interdependence 2024
7. Taiwan defense budget spending trajectory
8. PRC military pressure Taiwan strait 2025 2026

### Auto-close removal

`research_routes.py` line 1024 deleted. Deeper dive generation no longer sets `status: closed` on the thread. Threads stay active after synthesis.

### `config` vs `cfg` NameError fix

The novelty scoring pass had a variable name mismatch that caused a silent crash before `source_utils.py` could run. Fixed in the same session. This means novelty scoring had never actually executed in production despite being committed.

### ▶ button rendering fix

Nested `<a>` inside `<a>` in the dashboard template caused browsers to silently terminate the outer anchor, orphaning the run button outside `.thread-row`. The hover CSS never fired. Fixed by converting inner `<a>` to `<span>` with onclick navigation, active rows changed from `<a>` to `<div>`, and a flex spacer added so ▶ and "Generate Deeper Dive →" coexist on the same row.

---

## First Clean Session Output

**Session 003 (post-fix) confirmation from logs:**

```
System prompt: loaded and populated (4204 chars)
WARNING: thread.json missing field 'title' — using fallback
WARNING: thread.json missing field 'domain' — using fallback
WARNING: thread.json missing field 'current_focus' — using fallback
```

**Sources retrieved — 10 of 12 scored ≥ 4/5, all Taiwan-specific:**

- "Porcupine or Honey Badger?: The Overall Defense Concept" — Global Taiwan Institute
- "Winning the Fight Taiwan Cannot Afford to Lose" — CredibleDefense
- "Taiwan's Overall Defense Concept, Explained"
- "The Porcupine Strategy: Taiwan's Road to Self-Defense"
- "Taiwan strengthened porcupine defense strategy with Kuai Chi"
- RAND: "U.S. Military Capabilities and Forces for a Dangerous World" (retained — relevant to deterrence framing)

Zero Iraq War sources. Zero Mackinder. Zero QDR.

---

## Deeper Dive Output — What the System Found

The fourth deeper dive (generated from 4 sessions, 8 sources) produced substantively different synthesis. Key findings from Opus:

**On the hypothesis:** The ODC sources establish *how* Taiwan plans to defend itself — denial-based asymmetric strategy, mission kills, survivability — but say nothing about *why* or toward what political end. The hypothesis requires understanding Taiwan's political objectives; the research delivered military doctrine analysis. These are different questions.

**Devil's advocate — where the hypothesis doesn't hold:**

- Taiwan's population increasingly identifies as "Taiwanese only" (~67% as of 2023, NCCU data), not Chinese. The cultural affinity assumption is contested by data.
- Hong Kong's post-2019 trajectory — NSL, democratic dismantling, mass emigration — has been observed in real-time by Taiwan and has moved public opinion *against* "one country, two systems," not toward it.
- The ODC's existence is itself evidence against accommodation preference. A porcupine strategy is a commitment device, not a negotiating posture.
- The Philippines analogy is structurally weak — Taiwan is a functioning democracy with its own military, currency, and governance, not a colonial trading post.

**Unexpected lateral finding:** The research drift toward American strategic doctrine may itself be diagnostic. The "hellscape" concept (Admiral Paparo, 2024) is an American military framing, not a Taiwanese one. This raises the question of whether Taiwan has full agency to negotiate any deal, or whether U.S. strategic interests constrain its options regardless of Taipei's preferences.

**Revised research questions the system surfaced:**

1. What does Taiwan's population actually want? (Polling, identity surveys, electoral patterns — not yet examined)
2. What does Taiwan's leadership believe is achievable vs. what it publicly states?
3. Does China offer terms Taiwan could accept post-2019?
4. Who constrains Taiwan's choices — domestic politics, U.S. strategic interests, Beijing's red lines?

---

## What This Demonstrates

**The synthesis layer is robust even when the research layer fails.** Opus correctly diagnosed three consecutive deeper dives as having no usable evidence for the hypothesis. It didn't hallucinate confirmation — it said flatly "the hypothesis was not tested."

**The system prompt approach works.** One session with the injected prompt produced categorically different sources than two sessions without it. The fix was immediate and measurable.

**Silent failures are the hardest bugs.** The `config/cfg` NameError meant novelty scoring appeared to be working (no error thrown, session completed) but was silently not running. The only way to catch it was to notice that session 2 returned identical sources to session 1 — which is exactly what the novelty scoring was supposed to prevent.

**Auto-close as default was wrong.** The behavior that closed threads after deeper dive generation was reasonable for the Hormuz POC (one thread, one synthesis, done) but became actively harmful for iterative research. Removing it as a default and making close an explicit user choice was the right call.

---

## Open Items from This Thread

| Item | Status |
|------|--------|
| `thread.json` missing `title`, `domain`, `current_focus` fields | Warnings firing cleanly, fallbacks work — field population is a future improvement |
| Sessions 001–002 invalid baseline data | Acknowledged, not deleted — preserved for audit trail |
| Novelty scoring not yet validated across sessions | First real test will be session 005 — `seen_urls` cache now populated from sessions 003–004 |
| Polling data, identity surveys, NCCU data | Not yet retrieved — next query set should target this gap explicitly |

---

Thread continues. Cron runs daily.

---

*Documented: 2026-03-29 · mini-moi v1.1*
