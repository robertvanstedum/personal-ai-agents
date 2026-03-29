# BUILD_DEEPER_DIVE_MECHANICS_v1.0_2026-03-29.md
*Date: 2026-03-29*
*Author: Claude Code (build plan from plan session)*
*Status: Approved — ready to build*
*Companion specs:*
- *SPEC_DEEPER_DIVE_MECHANICS_v1.0_2026-03-28.md (original spec)*
- *SPEC_DEEPER_DIVE_POC_v1.0_2026-03-28.md (POC spec, already built)*
- *BUILD_SOURCE_INTELLIGENCE_v1.0_2026-03-29.md (Phase 2, deferred)*

---

## Intent

Wire the Deeper Dive POC (`scripts/generate_deeper_dive.py`) into the research thread lifecycle. Threads get a duration, expire automatically, trigger a one-click Deeper Dive generation, and close. This is the mechanics layer — no Curator archive integration, no UI beyond what's needed to complete the flow.

---

## Codebase Corrections vs. Original Spec

Five issues found during codebase review that the original spec did not account for:

**1. ThreadRecord `valid_status` validator rejects `expired` and `archived`**
`agent/threads.py` — `valid_status` currently only allows `active | closed | retired`. Adding `expired` and `archived` is one line but must be done first — all status writes depend on it.

**2. `duration_days` received in `spawn-thread` but never persisted**
`research_routes.py` — the form sends `duration_days` but `cmd_create()` doesn't accept it and `thread.json` has no `duration_days` or `expires` field. Schema and `cmd_create()` both need updating.

**3. No daily cron exists for the research domain**
The spec says "add to existing daily cron" — there is no research domain cron. The only launchd job is `com.user.curator-server` (the Flask server). A separate expiry-check script + plist is new infrastructure, not a modification.

**4. Curator archive integration deferred**
`regenerate_deep_dives_index()` in `curator_feedback.py` scans for `**Source:** ...` and `**Date:** ...` metadata fields. Deeper Dive output files use `*Domain: Research*` and a different header — incompatible. Writing to `interests/2026/deep-dives/` would produce broken index rows. **Deeper Dives stay in `data/deeper_dives/`**, served via Flask route `/research/deep-dive/<hash_id>`. Curator integration is a follow-up commit.

**5. Async generation requires Popen + poll pattern**
Opus calls take 1–3 minutes. The confirmation page cannot block a Flask worker. Must use `subprocess.Popen` + status polling — same proven architecture as `run-session`. Module-level `_DD_PROC` + `_DD_STATE_PATH`.

---

## Files to Create / Modify

| File | Action | What Changes |
|------|--------|--------------|
| `agent/threads.py` | Modify | Add `expired`, `archived` to validator; add `duration_days`, `expires`, `deeper_dive_generated`, `deeper_dive_path` fields |
| `research_routes.py` | Modify | Fix `spawn-thread` to persist `duration_days` + `expires`; add expire/archive routes; add DD generate + status routes; expose new fields in dashboard API |
| `scripts/generate_deeper_dive.py` | Modify | Add `--output-path` argument override |
| `web/dashboard.html` | Modify | Add `.thread-expired` CSS; expired state in `buildRow()`; `✦ Deeper Dive` button; `archiveThread()` function |
| `web/deeper_dive_confirm.html` | Create | Confirmation page with thread metadata, loading state, poll loop |
| `agent/thread_expiry.py` | Create | Lightweight script: check all threads, mark expired where `expires <= today` and `status == active` |
| `launchd/com.user.research-expiry.plist` | Create | Daily cron at 06:00 running `thread_expiry.py` |

---

## Schema Changes — `agent/threads.py`

```python
# validator update
valid_status = {'active', 'expired', 'closed', 'archived', 'retired'}

# new fields on ThreadRecord
duration_days:         Optional[int]  = None
expires:               Optional[str]  = None   # ISO date string YYYY-MM-DD
deeper_dive_generated: bool           = False
deeper_dive_path:      Optional[str]  = None
```

---

## Route Changes — `research_routes.py`

### Fix: `spawn-thread` — persist `duration_days` + `expires`

After `cmd_create()`, immediately reload the thread and set the two new fields:
```python
duration_days = int(form.get('duration_days', 14))
thread = load_thread(topic)
thread.duration_days = duration_days
thread.expires = (date.today() + timedelta(days=duration_days)).isoformat()
save_thread(thread)
```

### New: `POST /api/research/thread/<topic>/expire`

Sets `status = "expired"`. Used by the Stop Thread button on the dashboard.

```python
thread = load_thread(topic)
thread.status = "expired"
save_thread(thread)
return jsonify({'ok': True, 'status': 'expired'})
```

### New: `POST /api/research/thread/<topic>/archive`

Sets `status = "archived"`. Thread disappears from dashboard.

```python
thread = load_thread(topic)
thread.status = "archived"
save_thread(thread)
return jsonify({'ok': True, 'status': 'archived'})
```

### New: `GET /research/deeper-dive-confirm`

Renders `web/deeper_dive_confirm.html` with thread metadata:
```python
# Query param: ?topic=<slug>
# Loads thread.json, session count, source count
# Returns page with metadata injected
```

### New: `POST /api/research/generate-deeper-dive`

Spawns `generate_deeper_dive.py` asynchronously. Same Popen pattern as `run-session`:
```python
_DD_PROC: subprocess.Popen | None = None
_DD_STATE_PATH = RESEARCH_ROOT / 'data' / 'dd_run_state.json'

# On POST:
# - Validate no existing run active
# - Determine output path: data/deeper_dives/{topic}-deeper-dive-NNN.md
# - Spawn: python scripts/generate_deeper_dive.py --topic {topic} --output-path {path}
# - Write dd_run_state.json: {topic, pid, started, output_path, status: "running"}
# - Return {ok: True, pid: NNN}
```

### New: `GET /api/research/generate-deeper-dive/status`

Polls `_DD_PROC.poll()`. On completion:
- Reads `output_path` from `dd_run_state.json`
- Updates `thread.json`: `status = "closed"`, `deeper_dive_generated = True`, `deeper_dive_path = output_path`
- Clears state file
- Returns `{running: false, path: "...", topic: "..."}`

### Update: dashboard API response

Include new thread fields: `expires`, `status` (now includes `expired`), `deeper_dive_generated`, `deeper_dive_path`.

---

## Dashboard UI — `web/dashboard.html`

### CSS additions

```css
.thread-expired {
  border-left: 3px solid var(--amber, #f59e0b);
}
.thread-expired .thread-status-badge {
  background: #fef3c7;
  color: #92400e;
}
.btn-deeper-dive {
  background: var(--accent);
  color: white;
  font-size: 0.75rem;
  padding: 0.25rem 0.6rem;
  border-radius: 4px;
  cursor: pointer;
  text-decoration: none;
}
```

### `buildRow()` update

```javascript
if (t.status === 'expired') {
  // Show: amber badge "Ready to close"
  // Show: ✦ Deeper Dive button → /research/deeper-dive-confirm?topic={t.topic}
  // Show: Archive button → archiveThread(t.topic)
  row.classList.add('thread-expired');
}
```

### `archiveThread(topic)` function

```javascript
async function archiveThread(topic) {
  await fetch(`/api/research/thread/${topic}/archive`, {method:'POST'});
  loadDashboard(); // refresh
}
```

---

## Confirmation Page — `web/deeper_dive_confirm.html`

Single-purpose page. No form, one button.

```
DEEPER DIVE: {topic}
Sessions: N  ·  Sources: N  ·  Est. cost: ~$0.50–1.50
Original motivation: "..."

[✦ Generate Deeper Dive]   [Archive without generating]
```

On click "Generate":
1. POST `/api/research/generate-deeper-dive` with `{topic}`
2. Show loading state: spinner + "Running two-agent analysis… (1–3 min)"
3. Poll `/api/research/generate-deeper-dive/status` every 5s
4. On completion: redirect to `/research/deep-dive/<hash_id>` or dashboard

On "Archive without generating":
1. POST `/api/research/thread/<topic>/archive`
2. Redirect to dashboard

---

## Expiry Script — `agent/thread_expiry.py`

```python
#!/usr/bin/env python3
"""
thread_expiry.py — Daily expiry check for research threads.
Marks threads as 'expired' when their expires date has passed.

Run by launchd daily at 06:00.
"""
from datetime import date
from pathlib import Path
from threads import load_thread, save_thread

THREADS_DIR = Path(__file__).resolve().parent.parent / 'data' / 'threads'

def check_expiry():
    today = date.today().isoformat()
    changed = 0
    for thread_json in THREADS_DIR.glob('*/thread.json'):
        try:
            thread = load_thread(thread_json.parent.name)
            if thread.status == 'active' and thread.expires and thread.expires <= today:
                thread.status = 'expired'
                save_thread(thread)
                print(f"Expired: {thread.topic}")
                changed += 1
        except Exception as e:
            print(f"Error processing {thread_json}: {e}")
    print(f"Expiry check complete. {changed} thread(s) expired.")

if __name__ == '__main__':
    check_expiry()
```

---

## Launchd Plist — `launchd/com.user.research-expiry.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.research-expiry</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/vanstedum/Projects/personal-ai-agents/_NewDomains/research-intelligence/agent/thread_expiry.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>6</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/research-expiry.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/research-expiry-err.log</string>
</dict>
</plist>
```

Load with:
```bash
cp launchd/com.user.research-expiry.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.research-expiry.plist
```

---

## `generate_deeper_dive.py` Change

Add `--output-path` argument so the Flask route can control where the file is written:

```python
parser.add_argument('--output-path', type=Path, default=None,
                    help='Override output path (default: auto-numbered in data/deeper_dives/)')

# In main():
out_path = args.output_path or next_output_path(topic)
```

---

## Build Order

1. **`agent/threads.py`** — schema changes (foundation; all other steps depend on this)
2. **`research_routes.py`** — fix spawn-thread + 4 new routes + dashboard API update
3. **`scripts/generate_deeper_dive.py`** — add `--output-path`
4. **`web/dashboard.html`** — expired state UI
5. **`web/deeper_dive_confirm.html`** — new confirmation + loading page
6. **`agent/thread_expiry.py`** + **`launchd/com.user.research-expiry.plist`** — daily cron

Commit after each step or as a batch once verified.

---

## Deferred (explicit out-of-scope)

- Curator archive integration (`interests/2026/deep-dives/`, index badge)
- Reading Room population
- Multi-domain deployment (architecture supports it, not tested)
- UI polish beyond functional states

---

## Verification Checklist

```bash
# 1. Expire a thread manually
curl -X POST http://localhost:8765/api/research/thread/strait-of-hormuz/expire
# → {"ok": true, "status": "expired"}

# 2. Dashboard shows amber "Ready to close" + ✦ button
# Open /research/dashboard, verify row state

# 3. Confirm page loads
# Navigate to /research/deeper-dive-confirm?topic=strait-of-hormuz
# Verify thread metadata: sessions, sources, motivation

# 4. Generate (async)
curl -X POST http://localhost:8765/api/research/generate-deeper-dive \
  -H 'Content-Type: application/json' -d '{"topic":"strait-of-hormuz"}'
# → {"ok": true, "pid": NNN}

# 5. Poll status
curl http://localhost:8765/api/research/generate-deeper-dive/status
# → {"running": true} → {"running": false, "path": "data/deeper_dives/..."}

# 6. thread.json updated
cat _NewDomains/research-intelligence/data/threads/strait-of-hormuz/thread.json
# → status: "closed", deeper_dive_generated: true, deeper_dive_path: "..."

# 7. Archive without generating
# POST expire, then Archive → status: "archived", row disappears from dashboard

# 8. Expiry cron runs
python agent/thread_expiry.py
# → logs any expired threads, exits cleanly
```
