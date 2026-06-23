---
type: decision-record
dr-type: design
domain: german
status: active
lora-candidate: yes
---

# Decision Record — Gespräche Async Analysis Architecture
*2026-06-18 · mini-moi*

---

## Decision

Use WebSocket server push for Gespräche analysis. The server owns the
job from submission to completion. The result is pushed to the client
when ready. Closing the app does not kill the job.

---

## Context

Gespräche analysis (tap [Analysieren →]) calls an LLM and takes 10-20
seconds. The current implementation blocks the entire UI during that
window: the button greys out, no progress is shown, the user cannot
start a new session. On mobile this is especially bad — the app appears
frozen for nearly 20 seconds.

Four approaches were evaluated. The question was not just "which feels
best" but "who should own the job."

---

## Alternatives considered

### Option 1 — Animated progress labels (frontend only)

**What it was:** Cycle through `Analysiere… Grammatik → Wortschatz →
Aussprache → Zusammenfassung` on a timer while the fetch awaits.

**Why it was attractive:** Zero backend change. Ships in 30 minutes.
Immediate perceived improvement.

**Rejected because:** The UI still blocks. The user cannot start a new
session or tap anything else while the label cycles. Worse, the labels
are fabricated — they imply real progress stages when the server is
just thinking. A dishonest UX pattern even if subtle.

> [FLAG: principle — animated labels that don't reflect real progress
> are a UX debt, not a fix. They manage perception, not the actual
> problem.]

---

### Option 3 — Client fire-and-forget

**What it was:** Remove the `await` from the click handler so
`analyseSession()` runs in the background. The card shows "Analysiere…"
while the fetch runs. The user can tap other things. Multiple cards can
be submitted in parallel.

*Note: Labelled Option 3 in the evaluation spec; included here because
it was the strongest contender before WebSocket was evaluated.*

**Why it was attractive:** No backend change. UI fully unblocked.
Multiple analyses can run in parallel. Ships in one session.

**Rejected because:** The browser owns the request, not the server.
If the user navigates away, the browser cancels the in-flight fetch.
The job is lost. On mobile, this is a real scenario — iOS will
background-kill the page if the user switches apps during a 15-second
analysis. The result is a silent failure with no retry.

> [FLAG: constraint — iOS Safari may suspend or kill the page during
> a long background fetch. Fire-and-forget only works when the client
> stays alive for the full duration. That is not a safe assumption on
> mobile.]

---

### Polling

**What it was:** Submit the job, get a job ID back, poll `/api/status/{id}`
every 2 seconds until done.

**Why it was attractive:** Common pattern. Works with any HTTP stack.
Server owns the job because the client just checks status.

**Rejected because:** Polling is architecturally wasteful — the client
repeatedly asks "are you done yet" when the server already knows the
answer. At 2s intervals over 15-20 seconds, that's 8-10 unnecessary
requests per analysis. It also introduces a delay: the result may be
ready at 14.5 seconds but the client won't see it until the 16s poll.
WebSocket eliminates both problems.

> [FLAG: principle — polling inverts the responsibility. The server
> knows when work is done. Push the result; don't make the client ask.]

---

### Streaming SSE (Option 2 in evaluation spec) — deferred, not rejected

**What it was:** Stream the LLM response as Server-Sent Events.
First feedback line appears in 2-3 seconds as tokens arrive.

**Why it was attractive:** Best user experience — incremental rendering,
real progress. The user sees something within seconds.

**Deferred because:** Different shaped problem than the others.
Requires backend streaming changes per model provider (xAI, OpenAI,
Claude each need different streaming handling), careful partial-response
parsing for the FEEDBACK/FEHLER/STÄRKEN/SCHWÄCHEN sections, and SSE
doesn't work cleanly with POST (needs query params or a two-step flow).
High effort, medium risk. The right long-term answer for the analysis
experience — not the right first step.

> [FLAG: deferral — streaming SSE is the best long-term experience.
> Gate: WebSocket server push is stable on AWS. Streaming can follow
> as a separate build — likely Phase 5 or later.]

---

### WebSocket server push (chosen)

**What it is:** Client submits a job and gets a job ID. The server
processes in a background thread. When done, the server pushes the
result over an established WebSocket connection. The client renders it.

**Why this is correct:**
- Server owns the job from submission to completion
- Client can navigate away — the job continues on the server
- Result arrives exactly when ready — no polling delay
- Multiple jobs can run in parallel; each gets its own push
- WebSocket connection survives page state changes on mobile

**The key insight:** The question is not "how do we show progress"
but "who should be responsible for this work." A 15-second LLM call
should not be the client's responsibility to hold alive. The server
should own it. WebSocket is the transport that matches this ownership
model.

> [FLAG: principle — for long-running jobs, ownership determines
> architecture. If the server owns the job, push. If the client owns
> it, poll or fire-and-forget. Server ownership is almost always right
> for LLM calls.]

---

## Constraints that shaped this

**iOS Safari:** The dominant mobile browser for Robert's use. Will
suspend or kill a page during long background fetches. Any
client-dependent approach (Options 1, 3, polling) has this risk.

**Daily-use system:** Silent failures are unacceptable. If the user
taps Analysieren, they expect a result. An architecture that loses
jobs on navigation is not suitable for a system in daily use.

**AWS migration coming:** The server will move to EC2. WebSocket
infrastructure on EC2 is standard. Building server-side job handling
now means the architecture survives the move unchanged.

---

## Assumptions made

**Flask-SocketIO is sufficient at mini-moi scale.** One user, one
active session at a time. The WebSocket implementation does not need
to handle concurrent users or horizontal scaling. If that assumption
ever changes, the job management layer needs rethinking.

**xAI/Grok does not impose timeouts shorter than 20 seconds.** If the
provider has aggressive timeouts, background threads may be killed at
the provider level regardless of how the client handles the wait.

---

## Known failure modes

**WebSocket connection drops mid-job.** If the WebSocket disconnects
after submission but before the push, the client has a job ID but
no result. Mitigation: store result on the server by job ID so the
client can fetch it by ID on reconnect.

**Server restart during a job.** Background threads die on server
restart. In-flight jobs are lost. Mitigation: acceptable at current
scale — jobs are short enough that this is rare. Persistent job queue
(e.g. Celery + Redis) is Phase 5+ if it becomes a real problem.

---

## Principles this decision reflects

- "For long-running jobs, ownership determines architecture. Server
  ownership means push; client ownership means poll or fire-and-forget.
  Server ownership is almost always right for LLM calls."

- "Animated labels that don't reflect real progress are a UX debt,
  not a fix. They manage perception without solving the problem."

- "Polling inverts responsibility. The server knows when work is done.
  Push the result; don't make the client ask."

- "Don't build for the happy path where the client stays alive. On
  mobile, assume the client will be suspended or killed during
  any long-running operation."

---

## What this is not

**Not a commitment to streaming SSE.** Streaming is deferred, not
rejected. It's the right answer for the analysis experience long-term.
The gate is WebSocket infrastructure stable on AWS.

**Not a general job queue.** This is a targeted WebSocket implementation
for Gespräche analysis. Celery, Redis, or a general task queue are not
in scope — they would be over-engineering at current scale.

---

## Flags from the session

- [FLAG: principle — server ownership determines push vs poll architecture]
- [FLAG: constraint — iOS Safari page suspension during long background fetches]
- [FLAG: deferral — streaming SSE, target Phase 5+, gate on WebSocket stable on AWS]
- [FLAG: assumption — single user, no horizontal scaling pressure on WebSocket]

---

## Open questions

**Should the WebSocket connection persist across page navigations or
reconnect on each page load?** Reconnect-on-load is simpler and
sufficient for single-user use. Persistent connection is an optimization
for later.

**What is the correct behavior if the user has no WebSocket connection
when a job completes?** Store the result by job ID, expose
`/api/analysis/{id}` for retrieval. Client fetches on reconnect.
This is the reconnect fallback.

---

## Impact / Follow-up

- Spec: `_working/spec_gesprache_async_analysis_2026-06-18.md` (evaluation)
  and `_working/spec_monitoring_stack_2026-06-18.md` (monitoring references this)
- Monitoring spec explicitly gates on "WebSocket spec stable on AWS"
- Build order: WebSocket analysis → AWS Phase 2 stable → monitoring stack
- Supersedes: none
- Related DR: `dr_monitoring_stack_2026-06-18.md` (monitoring chosen after
  this architecture was set)

---

*Decision Record · 2026-06-18 · Claude Code (from session transcript)*
*File: `docs/decision-records/dr_gesprache_async_analysis_2026-06-18.md`*
