# Build Plan — Research Intelligence Web UI
*Session: March 22, 2026*

## For Claude Code — Read Before Starting

1. Read `WAY_OF_WORKING.md` in `_NewDomains/research-intelligence/`
2. Read this spec fully
3. Scan these files before writing anything:
   - `curator_server.py` — existing routes, Flask app setup, how research routes are already structured
   - `agent/candidates.py` — `cmd_promote()` and `cmd_retire()` functions specifically
   - `agent/feedback.py` — `record_article_signal()` and `adjust_weights()` functions
   - `web/observe.html` — existing UI pattern, parchment palette, nav structure
4. Flag any conflicts with what you find before starting
5. Robert reviews this spec before handing to Claude Code — do not start until told to

---

# Build Plan — Research Intelligence

## Where things stand

All Phase 1 specs are implemented. The full loop exists in code:

```
save articles (CLI) → observe → Haiku extracts candidates → promote (CLI) → next session uses them
```

Two gaps remain before the loop is usable without the CLI:
1. No web UI for reviewing/promoting candidates
2. No web UI for saving articles with notes (the note field is the missing signal for Sonnet)

**Note:** Haiku fix is verified — `query_candidates_added: 5` confirmed live tonight. Start at Step 1.

---

## Step 1 — `web/candidates.html` (main build)

Closes the visual loop. Once it exists, the full cycle is doable in the browser:
observe → see candidates → promote → promoted queries feed next session via config.json.

**New server routes in `curator_server.py`:**
- `GET /research/candidates` — serve candidates.html
- `GET /api/research/candidates?topic=X&status=candidate` — read query_candidates.json, filter, return JSON
- `POST /api/research/candidates/promote` `{"id": "..."}` — promote candidate
- `POST /api/research/candidates/retire`  `{"id": "..."}` — retire candidate

**Implementation note:** Before writing the Flask routes, read `cmd_promote()` and `cmd_retire()`
in candidates.py. If they only print to stdout (CLI pattern), refactor them to also return a
status dict (e.g. `{"ok": True, "id": id, "status": "promoted"}`) so the route has something
serializable to return. Do this before writing any route code.

**UI:** Same parchment palette as observe.html. Table with ID | Query | Promote | Retire per row.
Filter bar: topic input + status dropdown (candidate / promoted / retired). On promote/retire,
**remove the row from the DOM** (not hide — hiding leaves stale counts). No page reload needed.

**Nav integration:** Four-way nav across all research pages:
`observe.html` ↔ `candidates.html` ↔ `save.html` ↔ `curator_intelligence.html` (library).
The library page is the most likely bookmark entry point — it must link out, not just receive links.

---

## Step 2 — `web/save.html` (priority over Step 1 polish)

The note field is unreachable without the CLI. Sonnet synthesis is weaker without researcher
annotations — this was proven tonight. **If time is tight, Step 2 wins over Step 1 polish.**
Functionality before interface.

**New route in `curator_server.py`:**
- `GET /research/save` — serve save.html
- `POST /api/research/save` `{"url", "title", "session_id", "note"}` — calls
  `record_article_signal()` + `adjust_weights()` from feedback.py directly (importable)

Simple form: URL (required), Title, Session ID, Note (textarea). Returns success/duplicate/error inline.

---

## Files touched

| File | Change |
|------|--------|
| `web/candidates.html` | New |
| `web/save.html` | New (Step 2 only) |
| `curator_server.py` | 4 new routes for candidates; 2 for save |
| `agent/candidates.py` | No changes — import existing functions |
| `agent/feedback.py` | No changes — import existing functions |
| `web/observe.html` | Add nav links to Candidates and Save |
| `curator_intelligence.html` | Add four-way nav links (observe, candidates, save, library) |

---

## Verification

1. Open `/research/candidates` — 3 pending candidates visible for empire-landpower
2. Promote one → config.json `session_searches["empire-landpower"]` gains the query
3. Retire one → moves to retired view
4. (Step 2) Save an article via `/research/save` with a note → check article_signals.json note field populated
