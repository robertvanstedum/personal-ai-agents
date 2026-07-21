# Guild Build Log

> SUPERSEDED 2026-07-21. Last entry 2026-06-11 — over a month stale despite
> being described as a living document; build tracking has since moved
> elsewhere. Preserved for history.

*mini-moi · personal-ai-agents*

Living document. One entry per significant build session.
Maintained manually until the Design/Dev agent (Phase 5) takes over.
Add new sessions at the top.

---

## 2026-06-11 — Challenger Phase 2 + Scan → Deeper Dive (CLOSED)

### Challenger Phase 2 — ChallengerService wired into Curator deep dives

8 dives run in production. 5/8 outputs_differ=True (gate: 3/5 — passed).
Acceptance rates: china-rise 2/3 · empire-landpower 2/4 · gold-geo-002 3/4 ·
hellscape-taiwan 1/4 · strait-of-hormuz-007 2/3.
Three 0/4 dives = strong original synthesis, not failure. Pattern validated.
Robert visual sign-off confirmed 2026-06-11. CLOSED.

### Scan → Deeper Dive — one-click promotion

Route live. Thread auto-created from scan, dive generated immediately.
Desk shows auto-created threads. CLOSED.

---

## 2026-06-09 — Phase 4: CoS Intelligence Loops
*Commits: d9f3938, 2584b37, 8aaf87c · Author: Claude Code + Robert*

### What shipped

Four autonomous intelligence loops wired into the Chief of Staff agent via APScheduler.
CoS transitions from reactive (chat only) to proactive (loops fire on schedule, push findings).

| Loop | Cadence | Function |
|---|---|---|
| A — Career Focus Scout | Daily 06:00 + 18:00 | Tavily + RSS → 2-pass LLM scoring → Telegram alerts |
| B — German Domain Watch | Sunday 09:00 | Practice cadence check + tool search |
| C — Curator Topic Scout | Sunday 10:00 | Emerging topic search → Desk suggestions queue |
| D — mini-moi Novelty Watch | 1st + 15th 08:00 | Competitive scan → Telegram for threat/incorporate |

New DB tables: `jobs.career_opportunities`, `guild.cos_agenda`, `guild.agent_feedback`

### Calibration learnings (Loop A)

Four problems found and fixed during the first run — documented here as patterns to apply
to all future loop development.

**1. Article discrimination in the filter pass**
*Problem:* First run returned 32 results including Deutsche Telekom news articles about
their AI factory expansion — relevant topic but not job listings.
*Fix:* Filter pass prompt now explicitly instructs the LLM:
- News articles about company expansion → force score ≤ 3
- Aggregator pages listing many unrelated jobs → force score ≤ 4
- Actual job listings scored normally on fit
*Result:* Shortlist dropped from 32 to 10–12. Noise eliminated.
*Apply to:* Any loop that searches for actionable items vs. informational content.

**2. Staleness detection — the Tavily date limitation**
*Problem:* Pinterest "Principal Engineer, Agentic Engineering" appeared in the `days=2`
fresh pass (Tavily crawled it recently) but was actually a month-old repost with
"No longer accepting applications."
*Root cause:* LinkedIn reposts jobs and resets Tavily's crawl date. `days=2` window
picks these up as fresh even when the actual posting is old.
*Fix:* Dual-pass search (`days=2` tagged `fresh`, `days=14` new URLs tagged `recent`)
plus staleness rules in the filter prompt — force score to 0 if snippet contains:
"no longer accepting", "position filled", "job closed", "expired", "reposted X months ago"
(>2 weeks), any explicit date older than 14 days, or any closed-role signal.
*Result:* Stale and closed listings dropped before the expensive evaluation pass.
*Pattern:* Use dual-pass date windowing for any Tavily search where result freshness
matters. Staleness rules belong in the filter pass, not evaluation.

**3. Warm lead contact format**
*Problem:* `network_companies.json` stores contacts as plain strings. Code assumed dicts
with a `name` key — crashed on first run.
*Fix:* One-line isinstance check:
`c if isinstance(c, str) else c.get("name", "")`
*Apply to:* Any code that reads from a JSON file produced by an external parser.
Defensive isinstance checks are standard practice here.

**4. sys.path under launchd**
*Problem:* Loop imports (`from domains.guild.agents.loops...`) failed under launchd
because the working directory is `domains/guild/agents/`, not the repo root.
*Fix:* `sys.path.insert(0, str(BASE_DIR))` added before loop imports in `main()`.
*Apply to:* All new agent files run under launchd. Always insert repo root into sys.path
at the top of any script that imports from the `domains/` package structure.

### Age-tier notification model

Fresh-first sorting with tiered Telegram thresholds:

| Tier | Window | Ping threshold |
|---|---|---|
| 🆕 fresh | ≤ 48h crawl date | score ≥ 7.0 |
| 📅 recent | 3–14d | score ≥ 9.0 |
| Cap | — | 5 pings per run |

### Proof of value — real applications generated

**Application 1: Principal Engineer, Agentic AI — Comcast (Xfinity)**
- Loop A fit score: **9.0/10** · Age tier: fresh · Status at discovery: active
- URL: https://jobs.comcast.com/job/philadelphia/principal-engineer-agentic-ai/45483/94521326256
- Why 9.0: exact title match, major US telecom (30-year background transfers directly),
  role is explicitly about reusable agents + multi-agent orchestration + LLM-as-a-Judge,
  Chicago metro presence, T-Mobile end date aligns with hiring window
- Genuine gap: no Google ADK or Gemini Enterprise background (Comcast is Google-ecosystem)
- Applied: 2026-06-09

**Application 2: Principal Engineer, Enterprise Agentic AI Platform — NVIDIA IT**
- Loop A fit score: **7.0/10** · Status: closed March 2026 (no longer accepting)
- Why 7.0: production agentic AI matches core mandate, 30+ years distributed systems
  exceeds 15-year requirement, builder profile (writes code daily) is a direct match
- Genuine gaps: no LangChain/LangGraph or Kubernetes in narrative; domain shift to
  enterprise IT vs. telecom; closed before Aug 1 availability
- **Retained as benchmark:** NVIDIA $272K–$431K base + equity establishes a quality floor.
  When NVIDIA posts similar roles, Loop A catches them fresh.
- Applied: not applicable (closed) — filed as benchmark reference

**Assessment:** System worked as designed on first scheduled use. An exact title match at
a major telecom was surfaced and applied to on the day the loop was built. The benchmark
collection pattern (NVIDIA) emerged organically and adds value beyond the original spec.

### Open items carried forward

- Apply `schema_phase4.sql` when Docker is back up
- Calibrate Loop A thresholds after 2–3 days of scheduled runs (target: 0–3 pings/run)
- Loops B, C, D first scheduled fire: Sunday 2026-06-15. Review `/loops` endpoint output.
- Guild portal pages: chat interface to wire to `/chat` endpoint on port 8769 (Phase 5 scope)

---

## Template for future entries

```markdown
## YYYY-MM-DD — Phase N: [Name]
*Commits: [hash list] · Author: [who built]*

### What shipped
[brief description]

### Calibration learnings
[problems found + fixes + "apply to" pattern for future use]

### Proof of value
[real-world results, if any]

### Open items
[carried forward]
```

---

*Guild Build Log · docs/GUILD_BUILD_LOG.md · started 2026-06-09*
*Maintained by Design/Dev agent once Phase 5 is running.*
