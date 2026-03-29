# mini-moi v1.1 — Research Intelligence

**Released:** March 29, 2026
**Stack:** Python · Flask · Anthropic · xAI · Brave Search

---

## What is mini-moi?

Most people who care about geopolitics, finance, and technology spend their mornings drowning in feeds that reward volume over depth. mini-moi is the opposite: a personal AI intelligence system that reads broadly, thinks carefully, and gets smarter the longer it runs — all at a cost that stays well within reach of any serious individual user.

It has three layers that build on each other:

- **Curator** surfaces a daily briefing at 7 AM — scored, ranked, and filtered against your actual interests, not an algorithm's guess at them.
- **Research Intelligence** runs background threads on topics you care about, accumulating sources and synthesizing findings daily without you asking.
- **Deeper Dives** generate on-demand analysis documents from those threads — hypothesis testing, bibliography, and synthesis in one document you can return to and build on.

v1.0 proved the Curator worked. v1.1 makes Research Intelligence real.

---

## What's new in v1.1

### The reading-to-research loop is closed

The headline feature of v1.1 is not any single component — it's the connection between them. For the first time, the path from a morning article to a sustained research thread is a single connected flow:

```
Morning briefing → save article → trigger Deep Dive
→ Deep Dive generates analysis + bibliography
→ spawn research thread from Deep Dive
→ thread runs daily, accumulates sources
→ generate updated Deeper Dive as knowledge matures
```

Each step was previously manual and disconnected. In v1.1 they form a loop. You read something interesting, the system takes it from there.

### Research Intelligence graduates to production

Research Intelligence spent v1.0 in private staging. It now lives in the main repo as a first-class component, with five active threads accumulating real sessions:

| Thread | Sessions | Status |
|--------|----------|--------|
| empire-landpower | 14 | Active |
| china-rise | 2 | Active |
| gold-geopolitics | 5 | Active |
| strait-of-hormuz | 3 | Deeper dive generated |
| hellscape-taiwan-porcupine | 2 | First deeper dive in progress |

Each thread runs a daily session via launchd, scores sources against your research targets, and builds a candidate pool for future sessions.

### Novelty scoring — the source repetition problem is solved

Early research sessions had a frustrating pattern: run the same topic twice and get the same 2–3 sources both times. The agent wasn't learning what it had already found.

v1.1 fixes this. Every URL retrieved in a session is now tracked per topic. On subsequent runs, already-seen sources are automatically down-weighted (0.3× score), pushing fresh material to the top of the rankings. The system naturally broadens its source base the longer a thread runs — without any manual intervention.

This is the first phase of a broader source intelligence upgrade. Lateral domain search, citation chasing, and discovery allocation follow once novelty scoring has accumulated enough real session data to validate against.

### On-demand session trigger

Running a research session no longer requires terminal access. The ▶ button on the Research dashboard triggers an immediate session for any thread. Status updates live during execution. The cron schedule continues unchanged — the button is for when you want results now.

### Iterative Deeper Dives

v1.0 treated deeper dives as one-per-thread. v1.1 makes them iterative. After each new session, "Generate Deeper Dive →" reappears on the dashboard, letting you synthesize updated findings as the thread matures. The backend auto-numbers successive dives (`-001`, `-002`, etc.). A thread that started with a hypothesis can be re-examined weeks later against a much richer source base.

### Deep Dives archive redesigned

The archive page was rebuilt with a clear two-section hierarchy. Deeper Dives (research-backed threads with session metadata) sit above the simpler article-level Deep Dives. Both sections default to a compact view — 3 and 5 rows respectively — and expand on demand. Read links appear on hover only. The result fits comfortably on a laptop screen without losing access to the full archive.

### Flask routing completes the migration

All deep dive pages now render through Flask rather than static HTML files. This means the spawn-thread panel, clickable bibliography sources, and annotation tools are available on every deep dive — not just newly generated ones. Static `.html` files remain in place but are no longer linked and will be removed in v1.2.

---

## How it's built

mini-moi uses a three-agent workflow with strict separation of concerns:

| Agent | Role |
|-------|------|
| Claude.ai | Design, strategy, architecture decisions |
| Claude Code | Implementation, commits, file-level execution |
| OpenClaw | Planning, documentation, memory, issue tracking |

One agent is active on the repository at a time. Robert is the decision point between them.

The model stack adapts to task complexity: Sonnet for synthesis and deeper dives, Haiku for triage and scoring, xAI for daily briefing scoring. New domains and features stage in `_NewDomains/` before graduating to the main repo on release — Research Intelligence followed this path. Language and Jobs are next.

---

## Cost baseline

These numbers reflect active build and experimentation — multiple agents running, new threads spinning up, deeper dives generating frequently. Production costs for a stable daily-use system will be meaningfully lower as model selection is tuned and session frequency stabilizes.

| Item | Build-phase cost |
|------|-----------------|
| Monthly API spend | $35–45 |
| Per research session | ~$0.002–0.005 |
| Per deeper dive | ~$0.21–0.24 |
| Research pilot budget | $20 allocated · $0.08 used at v1.1 release |

Cost discipline is a design constraint, not an afterthought. The model stack, session architecture, and triage pipeline are all built to keep this sustainable as a personal system indefinitely.

---

## What's next

### v1.2 — Mac Mini + infrastructure

Migration to an always-on Mac Mini eliminates the sleep interruptions that occasionally disrupt scheduled sessions. PostgreSQL activates for structured storage. The Reading Room feature lands — save full article text, not just links. Curator novelty scoring follows Research Intelligence's lead with its own 7-day windowed implementation.

### v1.3 — Intelligence layer

Neo4j graph activation. Cross-thread pattern detection. Local LLM for mining latent connections across months of accumulated sessions. The backtrace vision: *"I thought X in March 2026 — was I right, and what led me there."*

---

## Known limitations

v1.1 ships with several known gaps being tracked for v1.2:

- Session count before first deeper dive generation not tracked correctly in thread metadata
- Deeper dive generation closes the thread automatically — a keep-open option is needed
- Nav labels ("Sessions", "Observations") not yet updated to final names ("Threads", "AI Feedback")
- Static `.html` deep dive files still present in directory pending cleanup

---

*Built by Robert van Stedum · [github.com/robertvanstedum/personal-ai-agents](https://github.com/robertvanstedum/personal-ai-agents)*
*Design: Claude.ai · Implementation: Claude Code · Planning: OpenClaw*
