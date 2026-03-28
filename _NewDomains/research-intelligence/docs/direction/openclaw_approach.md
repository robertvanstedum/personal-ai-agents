# Research Intelligence Agent — Planned Approach
**Author:** OpenClaw (Mini-moi)  
**Date:** March 20, 2026  
**For:** Robert + Claude.ai design review  
**Status:** Draft — share before building  

---

## What I'm Reading This As

The direction document is a research collaboration charter, not a task spec. The agent isn't executing a list — it's building a working model of Robert's intellectual interests over time, and using that model to surface things Curator can't. That framing shapes everything below.

The Kotkin pilot thread is well-chosen. It's concrete enough to test the full workflow (find → triage → translate → validate → synthesize → deliver) but open enough to generate genuine surprises. If the workflow works on Kotkin, it works on anything in the two frames (geopolitics, monetary systems).

---

## Phase 0 — Infrastructure (Before Any Research)

Do this first, in one session, before spending a single API dollar.

### 1. Initialize the local library

```
~/research-intelligence/
├── README.md          # Master index table: date | topic | source | language | type | summary | path
├── session-log.md     # Running ledger: date | duration | cost | cumulative | open threads
├── reading-list.md    # Books + long-form: title | author | 2-sentence rationale | date added
├── sources/
│   ├── validated/     # High quality, ready to recommend for Curator
│   └── candidates/    # Found, not yet validated
├── topics/
│   └── empire-landpower/   # Kotkin pilot thread
├── translations/
└── essays/
```

Init as a local git repo (`git init`). Commit after every session. No GitHub yet — stays private until Robert decides to publish.

### 2. Build budget enforcement into session-log.md

Every session begins by reading the ledger. Format:

```
| Date       | Session | Cost   | Cumulative | Status    |
|------------|---------|--------|------------|-----------|
| 2026-03-21 | init    | $0.00  | $0.00      | ok        |
```

Hard rule: if cumulative ≥ $18 → stop everything, Telegram Robert immediately. Don't wait for $20.
Daily rule: if today's total ≥ $2.50 → stop for the day. Don't push to $3.

### 3. Scheduling mechanism

The direction document says "you decide when to run" — which is right in intent but needs a concrete trigger. Proposal:

- **Burst sessions (2–5 min):** OpenClaw cron jobs, 2x daily, off-peak hours. Task: follow one open thread, add 1–2 items to library, no Telegram unless something notable.
- **Research sessions (15–30 min):** Cron job 3x weekly. Task: source triage, translation draft, validate a candidate source.
- **Synthesis sessions (60–90 min total, spread):** Triggered by me when enough material accumulates on a thread to warrant an essay. Not on a schedule.

Robert activates/deactivates via simple flag in session-log.md: `agent_active: true/false`. He can pause without touching cron config.

---

## Phase 1 — Kotkin Pilot Thread (Weeks 1–2)

**The question:** Is Kotkin's positional swap argument (Russia/China swapped geopolitical positions over 70 years) right? What does non-Anglophone scholarship say? Who said it first or better?

### Step 1 — Free sources first (zero cost)

Before any API call:

- Google Scholar: search "Russia China geopolitical position reversal" + Russian/Chinese scholar names
- JSTOR abstracts (free tier): search IR journals for 1990s–2010s
- Archive.org: look for pre-2000 Russian and Chinese IR texts
- Wikipedia citation graph: Kotkin's Wikipedia page → follow footnotes → find primary sources cited
- SSRN: search for preprints on Eurasian order, Sino-Russian relations theory
- Author pages: find Kotkin's cited scholars, get their institutional affiliations, check university repositories

Target: 10–15 candidate sources identified before spending anything.

### Step 2 — Haiku triage

For each candidate: 
- Feed abstract or first page to Haiku
- Ask: "Does this engage with the question of whether China and Russia swapped geopolitical positions? Rate relevance 1–5 and explain in one sentence."
- Cost: ~$0.01–0.02 per item
- Keep score ≥ 3 as candidates, discard rest

### Step 3 — Language sourcing

**Chinese:** Search Fudan, Peking University, CASS institutional repositories. Search terms: 中俄关系 (Sino-Russian relations), 地缘政治 (geopolitics), 欧亚秩序 (Eurasian order). Flag Portuguese/German equivalents for Robert to read directly.

**Russian:** Carnegie Eurasia archives, Meduza long reads, pre-2022 Russian IR journals available via Google Scholar. Scholars in exile publishing in English/German are also valid.

**German:** DGAP, SWP Berlin — both have English summaries but full German texts often go further. Flag for Robert to read directly unless highly technical.

### Step 4 — Translate one example

Pick the strongest non-English source from triage. Haiku draft translation, I review and clean. Save to `translations/[language]/`. This is the first concrete deliverable.

### Step 5 — Validate

Cross-reference: does a Brazilian dependency theorist, a Japanese IR scholar, and a Russian historian point at the same dynamic without citing each other? Three independent lines = much stronger finding.

### Step 6 — Essay

When the above is done: write the synthesis essay. 3–5 pages. Structure:
1. Central claim (not background)
2. What Kotkin actually argues (precise, cited)
3. Who anticipated it, and from where
4. Where the non-Anglophone literature pushes back or adds nuance
5. Why it matters right now
6. What to watch (3–5 specifics)

Save to `essays/empire-landpower-2026-03.md`. Telegram Robert: title, 2-sentence summary, path.

---

## Budget Estimate — Kotkin Pilot

| Task | Model | Est. Cost |
|------|-------|-----------|
| Phase 0 infrastructure | none | $0.00 |
| Free source identification | none | $0.00 |
| Haiku triage (15 items) | Haiku | ~$0.20 |
| Translation draft (1 article, ~800 words) | Haiku | ~$0.15 |
| Cross-reference validation | Haiku | ~$0.20 |
| Essay synthesis (3–4 pages) | Sonnet | ~$0.80 |
| **Pilot thread total** | | **~$1.35** |

Well under the $10/week limit. Leaves budget for a second thread or unexpected Grok web search.

---

## What I'm Not Doing (and Why)

**No UI.** The direction document is right — grep is enough until the library has 50+ items. A UI before functional completeness is waste.

**No Curator integration yet.** Loose coupling is correct for PoC. If a validated source clearly belongs in Curator's RSS, I flag it and ask Robert before touching Curator config. That boundary matters.

**No parallel threads in Week 1.** Kotkin first, complete. Open a second thread only after the essay is written. Parallel threads in week one means neither finishes.

**No autonomous outreach.** I find sources. I don't contact scholars, request papers, or email anyone. That's a future question.

---

## Communication Plan

**Telegram — when to interrupt:**
- Found something genuinely surprising (unexpected source, strong non-Western counterargument)
- Translation done and worth reading
- Essay completed
- Budget checkpoint: $5, $8, $18 (hard stop warning)
- Thread going nowhere — should I drop it?

**Web chat — when Robert initiates:**
- Treat it as a research session input
- Take notes, update open threads accordingly
- Informal is fine — this is how the collaboration model works

**Format:** Per direction document. Short, specific, ends with `Reply YES to validate / NO to drop / or just talk`.

---

## Repo Plan

**Phase 0 (now):** `~/research-intelligence/` — local git only
**End of pilot:** If 3 of 5 success criteria pass → discuss publishing
**Phase 2:** New GitHub public repo or subfolder in personal-ai-agents — Robert decides

The origin conversation and direction document go into the repo as the case study record, as planned. Win or lose.

---

## My One Design Question for Robert + Claude.ai

The direction document gives me a lot of autonomy on *what* to research. The one thing I want to confirm before starting:

**When I find a source that's highly relevant but doesn't fit the stated frames (geopolitics + monetary systems) — do I flag it and ask, or skip it?**

The document says "flag and ask." I want to confirm that's the right default vs. a wider mandate to follow interesting threads wherever they go. Knowing this changes how aggressively I chase citation graphs.

---

## What Needs to Happen Before I Start

1. Robert confirms this approach (or redirects)
2. Phase 0 infrastructure initialized (one session, no cost)
3. Cron schedule agreed and set up in OpenClaw
4. Robert answers the design question above

After that: first burst session, free source identification, Kotkin thread begins.

---

*OpenClaw (Mini-moi) | March 20, 2026*  
*Draft for review — not a committed plan until Robert confirms*
