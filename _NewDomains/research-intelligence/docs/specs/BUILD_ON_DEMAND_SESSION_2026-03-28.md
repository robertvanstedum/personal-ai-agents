# BUILD: On-Demand Research Session Trigger
*Date: March 28, 2026*
*Author: Claude Code planning session + OpenClaw review*
*Status: Approved — ready for Claude Code*

---

## Context

The Research landing page (`/research/dashboard`) now shows a compact thread list. The next natural action from there — "run a session on this topic right now" — requires navigating into the topic view first. This build adds a `▶` hover button on each thread row that fires the research agent for that topic directly from the landing page, without navigating away.

**Key constraints discovered:**
- Sessions run 1–35 minutes (usually 1–2 min). Cannot block a Flask worker — must use `subprocess.Popen()` (non-blocking).
- `research.py` outputs human-readable status lines to stdout (74 print calls), not JSON. Results go to markdown files on disk.
- `agent_active` is a boolean flag in `library/session-log.md` header — currently never set to `true` at runtime. This build replaces that with a live PID check.
- No async/threading pattern exists in the codebase yet — this build introduces it minimally.
- Session names follow `{topic}-{NNN}` convention (e.g., `strait-of-hormuz-001`). Must be auto-generated.
- CLI args confirmed: `python agent/research.py --session-name {name} --topic {topic}` — both flags exist and are required (lines 368-369 of research.py).

---

## What This Build Does

1. **Backend: `POST /api/research/run-session`** — spawns `research.py` as a non-blocking subprocess, writes a state file, returns immediately.
2. **Backend: `GET /api/research/run-session/status`** — checks PID liveness, returns current state. Clears state when process exits.
3. **Dashboard API update** — derive `agent_active` from `run_state.json` PID check instead of session-log.md text.
4. **Frontend: hover `▶` button** on each thread row in `dashboard.html` — fires POST, shows in-row "Running…" state, tightens polling to 10s while active (max 5 min).

---

## Backend — Part A: `POST /api/research/run-session`

**File:** `research_routes.py`

**Request body:**
```json
{ "topic": "strait-of-hormuz" }
```

**Validation:**
- `topic` required, must exist in `RESEARCH_ROOT / 'agent' / 'config.json'` session_searches
- No session already running (check state file PID liveness)

**Session name auto-generation:**
```python
# Scan topics/<topic>/ for existing .md session files, find max N, use N+1
existing = list((RESEARCH_ROOT / 'topics' / topic).glob('*.md'))
# Filter to files matching {topic}-NNN pattern, extract max N, return {topic}-{N+1:03d}
# Default to {topic}-001 if none exist
```

**Subprocess launch with error handling:**
```python
try:
    proc = subprocess.Popen(
        [sys.executable, 'agent/research.py',
         '--topic', topic,
         '--session-name', session_name],
        cwd=str(RESEARCH_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except (OSError, subprocess.SubprocessError) as e:
    return jsonify({"ok": False, "error": f"Failed to launch agent: {e}"}), 500
```

**State file:** `RESEARCH_ROOT / 'data' / 'run_state.json'`
```json
{
  "topic": "strait-of-hormuz",
  "session_name": "strait-of-hormuz-001",
  "pid": 12345,
  "started_at": "2026-03-28T17:30:00Z"
}
```

**Do NOT touch `session-log.md`.** `agent_active` is derived live from `run_state.json` in the dashboard API (see below).

**Response:**
```json
{ "ok": true, "topic": "strait-of-hormuz", "session_name": "strait-of-hormuz-001", "pid": 12345 }
```

**Error cases:**
- Topic not in config → 400
- Session already running → 409 `{"ok": false, "error": "session already running for <topic>", "running_topic": "..."}`
- Popen failure → 500

---

## Backend — Part B: `GET /api/research/run-session/status`

**File:** `research_routes.py`

**Logic:**
```python
# No state file → not running
if not state_file.exists():
    return jsonify({"running": False})

state = json.loads(state_file.read_text())
pid = state['pid']

# Stale check — if started_at > 60 min ago, treat as stale
# NOTE: PID recycling is an accepted risk for a personal tool.
# A future improvement could verify the process name matches research.py.
started_at = datetime.fromisoformat(state['started_at'])
if datetime.now(UTC) - started_at > timedelta(minutes=60):
    state_file.unlink(missing_ok=True)
    return jsonify({"running": False})

try:
    os.kill(pid, 0)   # signal 0 = "are you alive?"
    running = True
except ProcessLookupError:
    running = False
    state_file.unlink(missing_ok=True)

return jsonify({"running": running, **state})
```

**Response:**
```json
{ "running": true, "topic": "strait-of-hormuz", "session_name": "strait-of-hormuz-001", "started_at": "..." }
```
or `{"running": false}` when idle.

---

## Backend — Part C: Dashboard API update (`api_research_dashboard`)

**File:** `research_routes.py`

Currently reads `agent_active` from `session-log.md` header text. Change to derive it live:

```python
# Derive agent_active from run_state.json instead of session-log.md
state_file = RESEARCH_ROOT / 'data' / 'run_state.json'
agent_active = False
if state_file.exists():
    try:
        state = json.loads(state_file.read_text())
        os.kill(state['pid'], 0)
        agent_active = True
    except (ProcessLookupError, KeyError, json.JSONDecodeError):
        agent_active = False
```

This removes the stale markdown flag problem and survives server restarts cleanly.

---

## Frontend — `dashboard.html`

**Modified `buildRow()`** — add run button and running badge to each row:
```javascript
row.innerHTML = `
  <span class="thread-name">${t.topic}</span>
  <span class="thread-stats">${parts.join(' · ')}</span>
  <span class="thread-last-run">${lastRun}</span>
  <span class="thread-running-badge">running…</span>
  <button class="thread-run-btn" onclick="runSession(event, '${t.topic}')">▶</button>
`;
```

**CSS additions:**
```css
.thread-run-btn {
  display: none;
  margin-left: 0.75rem;
  flex-shrink: 0;
  background: none;
  border: none;
  font-family: 'DM Mono', monospace;
  font-size: 0.75rem;
  color: var(--accent);
  cursor: pointer;
  padding: 0 4px;
}
.thread-row:hover .thread-run-btn { display: inline-block; }
.thread-row.running .thread-run-btn { display: none; }

.thread-running-badge {
  font-family: 'DM Mono', monospace;
  font-size: 0.72rem;
  color: #7a9e6e;
  margin-left: 0.75rem;
  display: none;
}
.thread-row.running .thread-running-badge { display: inline; }
```

**`runSession()` and polling JS:**
```javascript
let pollTimer = null;
let pollCount = 0;
const MAX_POLLS = 30; // 5 minutes at 10s intervals

async function runSession(e, topic) {
  e.preventDefault(); e.stopPropagation();
  const row = e.target.closest('.thread-row');
  row.classList.add('running');
  try {
    const res  = await fetch('/api/research/run-session', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({topic}),
    });
    const data = await res.json();
    if (!data.ok) {
      row.classList.remove('running');
      showToast(data.error || 'Failed to start session', true);
      return;
    }
    showToast(`Session ${data.session_name} started`);
    startTightPoll();
  } catch (err) {
    row.classList.remove('running');
    showToast('Network error', true);
  }
}

function startTightPoll() {
  clearTimeout(pollTimer);
  pollCount = 0;
  pollStatus();
}

async function pollStatus() {
  pollCount++;
  if (pollCount > MAX_POLLS) {
    // Stalled — give up tight polling, show persistent badge
    showToast('Session still running — check back later');
    load(); // reload thread list
    return;
  }
  const res  = await fetch('/api/research/run-session/status');
  const data = await res.json();
  if (!data.running) {
    document.querySelectorAll('.thread-row.running').forEach(r => r.classList.remove('running'));
    load(); // reload thread list with updated stats
    return;
  }
  pollTimer = setTimeout(pollStatus, 10000);
}
```

---

## Files Modified

| File | What changes |
|------|-------------|
| `research_routes.py` | `POST /api/research/run-session`, `GET /api/research/run-session/status`, dashboard API `agent_active` update |
| `_NewDomains/research-intelligence/web/dashboard.html` | Hover `▶` button, running badge CSS, `runSession()` + `pollStatus()` JS |

**Runtime writes (no static edits needed):**
- `_NewDomains/research-intelligence/data/run_state.json` — single source of truth for running state, created/deleted at runtime

---

## Scope Boundary

**NOT in this build:**
- stdout streaming / live log viewer
- Session name override (always auto-generated)
- Multiple concurrent sessions
- Telegram notification on completion
- Scheduling / cron

---

## Build Order

1. `POST /api/research/run-session` + `GET /api/research/run-session/status` routes
2. Dashboard API `agent_active` update
3. `dashboard.html` — CSS + `runSession()` + `pollStatus()`

Commit all as one unit.

---

## Verification

```bash
# 1. Start a session via API
curl -X POST http://localhost:8765/api/research/run-session \
  -H 'Content-Type: application/json' \
  -d '{"topic": "strait-of-hormuz"}'
# → {ok: true, session_name: "strait-of-hormuz-001", pid: NNNN}

# 2. Check status while running
curl http://localhost:8765/api/research/run-session/status
# → {running: true, topic: "strait-of-hormuz", session_name: "...", started_at: "..."}

# 3. State file present
cat _NewDomains/research-intelligence/data/run_state.json
# → {topic, session_name, pid, started_at}

# 4. Dashboard API reflects running state
curl http://localhost:8765/api/research/dashboard | python3 -m json.tool | grep agent_active
# → "agent_active": true

# 5. After process exits
curl http://localhost:8765/api/research/run-session/status
# → {running: false}

# 6. Browser: open /research/dashboard
#    Hover a thread row → ▶ button appears
#    Click → toast "Session strait-of-hormuz-001 started"
#    Row shows "running…" badge, ▶ hidden
#    Status bar shows green dot, polling every 10s
#    When done → row reverts, stats update on reload
```
