# Build Spec — CoS Build Discipline
*mini-moi · Guild*
*Created: 2026-06-12 06:48 CDT — Claude.ai*
*Last revised: 2026-06-12 06:56 CDT — Claude.ai*
*For: Claude Code*
*Design reference: `docs/design/build_discipline_2026-06-12.md` (the "why" —
state model, mechanics, Grok review). This doc is the "what to build."*

**Revision history:**
- v0.1 (06:48) — initial build spec, five parts
- v0.2 (06:56) — Grok final review, three polish items incorporated:
  `spec_title` column + extraction, explicit numbered completeness-check
  prompt, documented config comment

---

## Part 1 — Schema migrations

```sql
-- guild.design_log: new columns
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'spec_ready';
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS spec_file TEXT;
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS spec_title TEXT;
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS last_transition_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS blocked_reason TEXT;
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS github_issue TEXT;

-- New transitions audit table
CREATE TABLE IF NOT EXISTS guild.design_log_transitions (
    id SERIAL PRIMARY KEY,
    design_log_id INTEGER REFERENCES guild.design_log(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    triggered_by TEXT,   -- 'design_dev' | 'cos_staleness' | 'robert' | 'claude_code'
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

`status` values: `spec_ready | in_build | blocked | done | deferred | incomplete`.

Update `test_schema.py` for the new columns and table, including `robert_ro`
grants on `guild.design_log_transitions`.

---

## Part 2 — Design/Dev agent extensions (port 8770)

### 2a — Completeness check + title extraction (extend existing Haiku classification)

When Design/Dev classifies a new/modified `_working/` doc as `handoff` or
`spec`, the same Haiku call also extracts four things. Explicit and numbered
in the prompt — reduces ambiguity in what Haiku is checking:

```
Additionally, extract the following for build tracking. Respond as JSON:

1. spec_title: the document's title, taken from its top-level (#) heading.
   Strip any leading "Handoff —", "Build Spec —", "Design —" prefix.
   Example: "# Build Spec — CoS Build Discipline" → "CoS Build Discipline"

2. has_dod: true if the document contains a section whose heading is or
   clearly means "Definition of Done" (a checklist of completion criteria),
   else false.

3. has_commit: true if the document contains a section whose heading is or
   clearly means "Commit" (containing git commands or file paths for
   committing), else false.

4. referenced_files: a list of every file path under `_working/` mentioned
   anywhere in this document (e.g. "_working/spec_foo.md"). List paths only —
   do not check whether they exist; that is verified separately by code.

{
  "spec_title": "...",
  "has_dod": true/false,
  "has_commit": true/false,
  "referenced_files": ["_working/...", ...]
}
```

**Completeness logic (code, not Haiku):**

```python
referenced_exist = all(os.path.exists(f) for f in referenced_files)
complete = has_dod and has_commit and referenced_exist
status = 'spec_ready' if complete else 'incomplete'
```

This is the same three checks as before (DoD section, Commit section,
referenced files exist) — now explicit and numbered for Haiku, with checks
1-2 and 4's *extraction* done by Haiku, but existence-checking (part of
check 4) and the final boolean logic done in code. Check 3's existence
verification reuses `scripts/check_handoff_gaps.py` logic.

`spec_title` is stored regardless of completeness — even an `incomplete`
item gets a readable title in the Build Log/Queue.

All four pass (or fail) → `guild.design_log` row: `spec_title`, `spec_file`,
`status = 'spec_ready'` or `'incomplete'`. If incomplete, Design/Dev notifies
Robert + Claude.ai with which check(s) failed.

Write a transition row: `from_status = NULL`, `to_status = 'spec_ready'` or
`'incomplete'`, `triggered_by = 'design_dev'`.

### 2b — `/start-build` endpoint (new)

```python
@app.route('/start-build', methods=['POST'])
def start_build():
    data = request.get_json()
    spec_file = data.get('spec_file')
    if not spec_file:
        return jsonify({"error": "spec_file required"}), 400

    row = db_query(
        "SELECT id, status FROM guild.design_log WHERE spec_file = %s",
        (spec_file,)
    )
    if not row:
        return jsonify({"error": "spec_file not found in design_log"}), 404

    log_id, current_status = row[0]['id'], row[0]['status']
    db_execute(
        "UPDATE guild.design_log SET status='in_build', "
        "last_transition_at=NOW() WHERE id=%s",
        (log_id,)
    )
    db_execute(
        "INSERT INTO guild.design_log_transitions "
        "(design_log_id, from_status, to_status, triggered_by) "
        "VALUES (%s, %s, 'in_build', 'claude_code')",
        (log_id, current_status)
    )
    return jsonify({"status": "in_build", "spec_file": spec_file})
```

### 2c — `scripts/start_build.sh` (thin wrapper)

```bash
#!/bin/bash
# Usage: scripts/start_build.sh <spec-filename>
curl -s -X POST localhost:8770/start-build \
     -d "{\"spec_file\": \"$1\"}" \
     -H "Content-Type: application/json"
```

Claude Code runs this once at the start of work on a spec.

### 2d — Build log entry drafting (extend existing logic)

When an `in_build` item's Definition of Done is confirmed complete (Robert
sign-off, same pattern as all session), Design/Dev drafts a build log entry:
Haiku summarizes the original DoD checklist + commits since `last_transition_at`.
Drafted entry presented to Robert for confirmation. On confirmation:
`status = 'done'`, `last_transition_at = NOW()`, transition row written
(`triggered_by = 'robert'`), entry appended to `docs/GUILD_BUILD_LOG.md`.

---

## Part 3 — CoS build-discipline mandate (port 8769)

### 3a — Staleness check (Loop F, daily)

```sql
-- Items past threshold without progress
SELECT id, spec_file, status, last_transition_at
FROM guild.design_log
WHERE status IN ('spec_ready', 'in_build')
  AND last_transition_at < NOW() - INTERVAL '3 days';
```

For each match: `status = 'blocked'`, `blocked_reason = 'stale: no progress in 3+ days'`,
transition row (`from_status` = previous, `to_status = 'blocked'`,
`triggered_by = 'cos_staleness'`).

Threshold from `cos_context.json` (see Part 5).

### 3b — Build state query (for Daily Briefing, when that's built)

```sql
SELECT status, COUNT(*), array_agg(spec_file) as items
FROM guild.design_log
WHERE status IN ('spec_ready', 'in_build', 'blocked', 'incomplete')
GROUP BY status;
```

This is the data layer for the Daily Briefing's "Build" section
(Active/Queued/Needs Decision rows). The briefing UI itself is separate,
not-yet-built work — this query is what it will call.

### 3c — `/chat` — "what's in flight"

Add `guild.design_log` (filtered to active statuses) to the context CoS
reads for `/chat`, so "what's in flight" / "what's blocked" get real answers.

---

## Part 4 — Build Clarity pages

**Reuse Career's components — do not redesign.** Same collapsible card
pattern, same column/status-dropdown pattern, same parchment styling.
Copy `templates/guild/career_positions.html` and
`templates/guild/career_active.html` as starting points.

### 4a — Build Log (`/guild/build`, table, default)

Same role as Positions: full inventory from `guild.design_log`, all statuses
including `done`/`deferred`, sortable/filterable, reporting surface.

Columns: **spec_title** (display — falls back to `spec_file` if title
extraction ever returns empty), status, age
(`NOW() - last_transition_at`), GitHub issue link (if `github_issue` set),
link to the spec file (`_working/` or `_working/archive/`, via `spec_file`).

```python
@app.route('/guild/build')
@owner_required
def guild_build():
    status_filter = request.args.get('status', 'all')
    query = "SELECT * FROM guild.design_log"
    params = []
    if status_filter != 'all':
        query += " WHERE status = %s"
        params.append(status_filter)
    query += " ORDER BY last_transition_at DESC"
    items = db_query(query, params)
    return render_template('guild/build_log.html', items=items,
                           status_filter=status_filter)
```

### 4b — Queue (`/guild/build/queue`, Kanban)

Same role as Active Pipeline. Filter:

```sql
SELECT * FROM guild.design_log
WHERE status IN ('spec_ready', 'in_build', 'blocked', 'incomplete')
   OR (status = 'done' AND last_transition_at > NOW() - INTERVAL '3 days')
ORDER BY
  CASE status
    WHEN 'blocked' THEN 1
    WHEN 'in_build' THEN 2
    WHEN 'spec_ready' THEN 3
    WHEN 'incomplete' THEN 3
    WHEN 'done' THEN 4
  END,
  last_transition_at DESC
```

**Four columns:** Spec Ready (includes `incomplete` with warning tag) /
In Build / Blocked (with `blocked_reason` tag) / Recently Done (0.65 opacity,
drops off after 3 days).

**Status dropdown on each card** = the manual override mechanism (Part 1's
`design_log_transitions`). On change:

```python
@app.route('/guild/build/items/<int:item_id>/status', methods=['POST'])
@owner_required
def update_build_status(item_id):
    new_status = request.form.get('status')
    note = request.form.get('note') or None
    valid = {'spec_ready','in_build','blocked','done','deferred','incomplete'}
    if new_status not in valid:
        return redirect(url_for('guild_build_queue'))

    current = db_query("SELECT status FROM guild.design_log WHERE id=%s",
                       (item_id,))[0]['status']

    db_execute(
        "UPDATE guild.design_log SET status=%s, last_transition_at=NOW(), "
        "blocked_reason=%s WHERE id=%s",
        (new_status, note if new_status == 'blocked' else None, item_id)
    )
    db_execute(
        "INSERT INTO guild.design_log_transitions "
        "(design_log_id, from_status, to_status, triggered_by, reason) "
        "VALUES (%s, %s, %s, 'robert', %s)",
        (item_id, current, new_status, note)
    )
    return redirect(request.referrer or url_for('guild_build_queue'))
```

### 4c — GitHub links

Page-level "View GitHub Issues →" link (header, both pages) to
`https://github.com/robertvanstedum/personal-ai-agents/issues`.
Per-card: if `github_issue` is set, small linked badge to that issue.

### 4d — Navigation

```html
<div class="guild-subnav">
    <a href="/guild/build">Build Log</a>
    <a href="/guild/build/queue">Queue</a>
</div>
```

Add "Build" to the main Guild nav alongside "Career Focus."

---

## Part 5 — Config

`cos_context.json`:

```json
{
  "build_discipline": {
    "_note": "staleness_days: how long a spec_ready/in_build item can go without progress before CoS flags it blocked. recently_done_grace_days: how long a done item stays on the Queue board before dropping to Build Log only. Currently both 3 — independent values, can diverge if useful.",
    "staleness_days": 3,
    "recently_done_grace_days": 3
  }
}
```

---

## Definition of Done

**Schema:**
- [ ] `guild.design_log` has new columns (`status`, `spec_file`, `spec_title`,
      `last_transition_at`, `blocked_reason`, `github_issue`)
- [ ] `guild.design_log_transitions` table exists
- [ ] `test_schema.py` passes, `robert_ro` grants confirmed

**Design/Dev:**
- [ ] Completeness check runs on handoff/spec classification — 4 extractions
      (spec_title, has_dod, has_commit, referenced_files), 3 checks
      (has_dod, has_commit, referenced files exist on disk)
- [ ] `spec_title` populated for every classified spec/handoff, including
      `incomplete` ones
- [ ] `incomplete` items notify Robert + Claude.ai with specific failures
- [ ] `/start-build` endpoint live, `scripts/start_build.sh` works
- [ ] Build log entry drafting on DoD confirmation (existing pattern + Haiku draft)
- [ ] Every status change writes a `design_log_transitions` row

**CoS:**
- [ ] Staleness check runs daily (Loop F), 3-day threshold from config
- [ ] Stale items → `status='blocked'`, `blocked_reason` set, transition logged
- [ ] `/chat` can answer "what's in flight" / "what's blocked" from `design_log`

**Build Clarity:**
- [ ] `/guild/build` (Build Log table) — all statuses, filterable, sortable
- [ ] `/guild/build/queue` (Kanban) — 4 columns per spec above
- [ ] `incomplete` shown as warning tag on Spec Ready cards
- [ ] `blocked_reason` shown as tag on Blocked cards
- [ ] Status dropdown = manual override, writes transition row with note
- [ ] Recently Done cards at 0.65 opacity, drop off after 3 days (Build Log retains)
- [ ] GitHub Issues link (page-level) on both pages; per-card badge if `github_issue` set
- [ ] Sub-nav (Build Log / Queue), "Build" added to main Guild nav
- [ ] Robert visual sign-off on both pages

---

## Commit

```bash
mkdir -p docs/design
cp design_build_discipline_2026-06-12.md docs/design/build_discipline_2026-06-12.md

git add docs/design/build_discipline_2026-06-12.md \
        _working/build_spec_build_discipline_2026-06-12.md \
        domains/guild/db/ \
        domains/guild/services/ \
        minimoi_portal/ \
        scripts/start_build.sh \
        domains/guild/config/cos_context.json \
        test_schema.py

git commit -m "feat: CoS build discipline — state model, transitions log,
Build Clarity (Log + Queue), Design/Dev completeness check + /start-build

Design: docs/design/build_discipline_2026-06-12.md
Reviewed by Grok (2026-06-12) — three refinements incorporated."
git push origin main
```

---

*Build spec · Guild · 2026-06-12*
*Design rationale: docs/design/build_discipline_2026-06-12.md*
