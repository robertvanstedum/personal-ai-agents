# Spec — Gespräche: Async Analysis
*Created: 2026-06-18 — Claude Code*
*Status: Ready for Claude.ai review*
*Context: Analysis call blocks UI for 10-20s with no feedback — button greys out, no progress visible*

---

## Problem

When the user taps [Analysieren →] on a session card, the LLM call
takes 10-20 seconds. During that time:

- The button is disabled and grey
- No progress is shown
- The user cannot start a new session or do anything else without
  the page appearing frozen
- On mobile the wait feels especially long with no feedback

Three options are proposed below, from lowest to highest effort.

---

## Option 1 — Animated progress text (frontend only)

**Effort:** ~30 min · Frontend only · No backend change

### What changes

The Analysieren button animates through progress labels while waiting:

```
Analysiere…          (0s)
Analysiere… Grammatik     (3s)
Analysiere… Wortschatz    (6s)
Analysiere… Aussprache    (9s)
Analysiere… Zusammenfassung (12s)
```

Labels cycle on a timer. The actual fetch still awaits — the UI
appears active even though it's blocking.

### Implementation

In `analyseSession()`:
```js
const labels = ['Analysiere…', 'Analysiere… Grammatik',
  'Analysiere… Wortschatz', 'Analysiere… Aussprache',
  'Analysiere… Zusammenfassung'];
let i = 0;
const ticker = setInterval(() => {
  analyseBtn.textContent = labels[++i % labels.length];
}, 3000);
const res = await fetch('/api/review', { ... });
clearInterval(ticker);
```

### Tradeoffs

- ✅ Zero backend change, lowest risk
- ✅ Immediate improvement in perceived responsiveness
- ✅ Ships in one session
- ❌ Page still blocks — user cannot start a new session during analysis
- ❌ Labels are fabricated progress, not real — slightly dishonest UX
- ❌ Does not actually reduce wait time

---

## Option 2 — Streaming LLM response (backend + frontend)

**Effort:** ~2-3h · Backend + Frontend · Moderate risk

### What changes

The `/api/review` endpoint streams the LLM response as Server-Sent
Events (SSE). The frontend renders feedback incrementally as tokens
arrive. First feedback line appears in 2-3s instead of 15-20s.

### Backend

`/api/review` returns `text/event-stream`:

```python
@app.route("/api/review", methods=["POST"])
def api_review():
    # ... existing validation ...
    def generate():
        for chunk in llm_stream(transcript, persona, scene, model):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    return Response(generate(), mimetype="text/event-stream")
```

LLM call switches from `client.chat.completions.create(...)` to
`client.chat.completions.create(..., stream=True)` for xAI/Grok.

### Frontend

```js
const evtSource = new EventSource('/api/review?' + params);
let buffer = '';
evtSource.onmessage = e => {
  if (e.data === '[DONE]') { evtSource.close(); renderFeedback(buffer); return; }
  buffer += JSON.parse(e.data).chunk;
  renderPartialFeedback(buffer);  // render as it arrives
};
```

### Tradeoffs

- ✅ Real-time feedback — first text appears in 2-3s
- ✅ User sees analysis building — genuinely responsive feel
- ✅ Does not block the UI (EventSource is non-blocking)
- ❌ Requires backend streaming change for each model (xAI, OpenAI, Claude)
- ❌ SSE doesn't work easily with POST — needs query params or a two-step
  submit → stream pattern
- ❌ Partial feedback rendering needs careful parsing (FEEDBACK/FEHLER
  sections appear mid-stream)
- ❌ Highest effort and risk of the three options

---

## Option 3 — Fire and forget, callback when done (frontend only)

**Effort:** ~45 min · Frontend only · Low risk

### What changes

The fetch call is fired without blocking the UI. The card immediately
shows "Analysiere…" as a persistent state. When the server responds
(whenever that is), the card updates with results. The user can start
a new session, view other cards, or do anything else while analysis
runs.

### Key insight

JavaScript `fetch` is already async. The current code does:
```js
card.querySelector('.btn-session-card-analyse').addEventListener('click', async () => {
  await analyseSession(session, card);  // ← this blocks the click handler
});
```

The fix: don't `await` `analyseSession` — fire it and let it resolve
in the background:
```js
card.querySelector('.btn-session-card-analyse').addEventListener('click', () => {
  analyseSession(session, card);  // ← no await, returns immediately
});
```

`analyseSession` still uses `await fetch(...)` internally, but since
nothing awaits `analyseSession` itself, the call runs in the
background.

### Card states

```
[Analysieren →]           — default
[Analysiere… ⏳]          — in flight (button disabled, non-blocking)
[✓ Analysiert]            — done, feedback rendered below card
[Fehler — Nochmal →]      — error, retry available
```

### Multiple cards in flight

If the user hits Analysieren on two cards in sequence, both fetches
run in parallel. Each resolves independently and updates its own card.
No coordination needed — each card manages its own state.

### Implementation notes

- `analyseSession(session, card)` becomes fire-and-forget from the
  click handler
- Inside `analyseSession`: button disabled + "Analysiere…" immediately
- `await fetch(...)` runs to completion in the background
- On resolve: render feedback, update button to "✓ Analysiert"
- On error: show "Fehler — Nochmal →" with retry listener
- No backend change needed
- No polling, no job ID, no SSE

### Tradeoffs

- ✅ No backend change
- ✅ UI fully unblocked — start new session, tap other cards freely
- ✅ Multiple analyses run in parallel
- ✅ Low risk — JS-only, isolated to click handler
- ✅ Ships in one session
- ❌ User still waits 10-20s for result — no reduction in actual time
- ❌ If user navigates away, in-flight request is lost (browser cancel)
  — same as today

---

## Comparison

| | Option 1 · Animated text | Option 2 · Streaming | Option 3 · Fire & forget |
|---|---|---|---|
| Effort | 30 min | 2-3h | 45 min |
| Backend change | No | Yes | No |
| Reduces wait time | No | Yes | No |
| UI unblocked | No | Yes | Yes |
| Can start new session during analysis | No | Yes | Yes |
| Risk | Low | Medium | Low |

---

## Recommendation

**Option 3 first, Option 1 as a detail within it.**

Option 3 unblocks the UI immediately with minimal risk. Option 1's
animated text can be added inside `analyseSession` (while the fetch
runs) at no extra cost — the labels give the user feedback during the
10-20s wait even when the UI is otherwise free.

Option 2 (streaming) is the best long-term answer but is a separate
build. It can follow Option 3 if streaming becomes a priority.

**Combined build:** Option 3 + Option 1 labels inside it. Ships in
one session, no backend change, UI fully unblocked, progress visible.

---

## Definition of Done (Option 3 + Option 1 labels)

- Tapping [Analysieren →] returns immediately — page is fully
  interactive while analysis runs
- Card shows animated progress text during analysis
- Result renders on the card when server responds
- Two cards can be analysed in parallel without conflict
- Error state shows retry button
- No regression on existing analysis output format

## Commit

`Gespräche: non-blocking analysis — fire-and-forget fetch, animated progress`

---

*Spec · 2026-06-18 · Claude Code*
*Review: Claude.ai — evaluate all three options, confirm recommendation*
