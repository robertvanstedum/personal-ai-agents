# Build Plan — Schema + Career Portal + Challenger Phase 2
*mini-moi · Guild · Curator*
*v1 authored: 2026-06-10 19:23 CDT — Claude.ai*
*v2 revised: 2026-06-10 — Claude Code codebase audit + Grok/Robert review*
*Build sequence: Item 1 → Item 2 → Item 3*

### Changes in v2
- Item 1 Step 4: test_schema.py already includes new table checks — no-op, skip it
- Item 1 Step 5: migration uses `ON CONFLICT (url) DO NOTHING` (cleaner than SELECT guard)
- Item 1 Step 5: JSON fallback confirmed at 11 records — migration target known
- Item 1 DoD: add post-migration COUNT verification (expect 11)
- Item 1: `date_found` column does not exist in schema — fix identified (see Step 2 note)
- Item 2: `/jobs` route and `career_focus.html` already exist from Phase 1 scaffold — Item 2 is completing the scaffold, not a new build
- Item 2: `base.html` does not exist — do not use `{% extends %}`, use inline CSS like all other Curator templates
- Item 2: clarified route file and template path

---

## Item 1 — Apply schema_phase4.sql

**Why first:** Loop A is writing to JSON fallback because the DB tables don't exist.
Applying the schema unlocks DB writes for career opportunities, cos_agenda, and
agent_feedback. Item 2 (portal page) reads from these tables, so schema must be live first.

### Steps

**Step 1 — Confirm Docker is up:**
```bash
docker compose up -d
pg_isready -h localhost -p 5432
```

**Step 2 — Apply schema:**
```bash
psql "postgresql://minimoi:simple123@localhost:5432/personal_agents" \
  -f domains/guild/db/schema_phase4.sql
```

> **⚠️ Column name fix required before or alongside this step:**
> The existing `/jobs` route in `curator_server.py` (line 1317) queries `date_found`, but
> `schema_phase4.sql` defines `created_at` only — no `date_found` column exists.
> Fix the route to use `created_at` (and alias it as `date_found` if the template references
> that name), or add `date_found TIMESTAMPTZ` to the schema. Do one or the other before
> Loop A writes to the live table or the route will throw on first DB hit.

**Step 3 — Add robert_ro grants (not in schema_phase4.sql — run these manually):**
```sql
GRANT SELECT ON jobs.career_opportunities TO robert_ro;
GRANT SELECT ON guild.cos_agenda TO robert_ro;
GRANT SELECT ON guild.agent_feedback TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA jobs GRANT SELECT ON TABLES TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA guild GRANT SELECT ON TABLES TO robert_ro;
```
Per `docs/DB_SCHEMA.md` pattern — robert_ro grant in the same migration, not a follow-up fix.

**Step 4 — Verify test_schema.py (no changes needed):**
> `test_schema.py` already includes checks for all three new tables as of Phase 4 build.
> Do NOT re-add them. Just run to confirm the schema applied cleanly:

```bash
venv/bin/python3 domains/guild/db/test_schema.py
```
Expect all checks to pass (count is dynamic — any failure will print explicitly).

**Step 5 — Migrate JSON fallback data (11 records confirmed):**
JSON fallback at `data/guild/career_opportunities.json` has **11 records** from Loop A runs.
Migrate to DB before the next Loop A run:

```python
import json, psycopg2

data = json.load(open('data/guild/career_opportunities.json'))
conn = psycopg2.connect("postgresql://minimoi:simple123@localhost:5432/personal_agents")
cur = conn.cursor()

for row in data:
    cur.execute("""
        INSERT INTO jobs.career_opportunities
            (title, company, geo, url, opportunity_type, fit_score, fit_narrative,
             warm_lead, warm_lead_contacts, cos_notes, source, model_used, status, created_at)
        VALUES (%(title)s, %(company)s, %(geo)s, %(url)s, %(opportunity_type)s,
                %(fit_score)s, %(fit_narrative)s, %(warm_lead)s, %(warm_lead_contacts)s,
                %(cos_notes)s, %(source)s, %(model_used)s,
                %(status)s, %(created_at)s)
        ON CONFLICT (url) DO NOTHING
    """, {**row, 'status': row.get('status', 'suggested')})

conn.commit()
cur.close()
conn.close()
print("Migration complete.")
```

`ON CONFLICT (url) DO NOTHING` handles dedup atomically — safe to run more than once.
If JSON file is absent or empty, skip this step.

**Definition of done for Item 1:**
- [ ] `venv/bin/python3 domains/guild/db/test_schema.py` — all checks pass
- [ ] `robert_ro` can SELECT from all three new tables
- [ ] JSON fallback data migrated (or confirmed empty)
- [ ] Post-migration count check:
  ```bash
  psql "postgresql://minimoi:simple123@localhost:5432/personal_agents" \
    -c "SELECT COUNT(*) FROM jobs.career_opportunities;"
  # expect: 11 (or 0 if JSON was empty)
  ```
- [ ] `date_found` column issue resolved (route fixed or column added)
- [ ] Next Loop A run writes to DB, not JSON

---

## Item 2 — Career Focus portal page

**Why second:** depends on schema from Item 1. Portal reads from `jobs.career_opportunities`.

> **⚠️ This is completing the Phase 1 scaffold — not a new build.**
> Both the route and the template already exist:
> - Route: `curator_server.py` line 1304, `@app.route('/jobs')` — already registered
> - Template: `templates/career_focus.html` — already exists with Kanban pipeline layout
> - `base.html` does NOT exist — do not use `{% extends "base.html" %}`. Use inline CSS
>   consistent with all other Curator templates (Source Sans 3 + DM Mono + Playfair Display,
>   `--accent: #8b5e2a`, parchment `#f5f0e8`).
>
> The work in this item is: wire the full data fields into the existing template, add the
> `score_color` filter, fix the `date_found` → `created_at` column name, confirm the status
> dropdown POST works end-to-end.

### What already exists

```
curator_server.py          — /jobs route at line 1304
templates/career_focus.html — Kanban pipeline, Phase 1 scaffold
```

### What to complete

**Route fix — `date_found` → `created_at`:**
```python
# curator_server.py, line 1317 — change:
"fit_score, status, date_found FROM jobs.career_opportunities "
"WHERE status != 'rejected' ORDER BY date_found DESC"
# to:
"fit_score, status, created_at FROM jobs.career_opportunities "
"WHERE status != 'rejected' ORDER BY created_at DESC"
```

**Add `score_color` template filter to `curator_server.py`:**
```python
@app.template_filter('score_color')
def score_color(score):
    if score and score >= 8.0: return '#8b5e2a'   # accent — matches design system
    if score and score >= 6.0: return '#b8860b'   # amber
    return '#9e9080'                               # text-dim
```
> Note: v1 plan used `#C68A5E` (portal copper) and `#B8860B`. The Curator design system uses
> `#8b5e2a` for accent. Use the Curator palette, not the portal palette.

**Add status POST route:**
```python
@app.route('/jobs/<int:opp_id>/status', methods=['POST'])
def update_job_status(opp_id):
    new_status = request.form.get('status')
    # update DB, then redirect back to /jobs
```

**Wire into existing `career_focus.html`:**
- `fit_narrative` field (currently likely stubbed or absent)
- `warm_lead` flag — show ⭐ inline with geo/type
- `url` → "Apply →" link, opens in new tab
- Score color coding via `score_color` filter
- Status dropdown `<select>` form posting to `/jobs/<id>/status`

**Status filter tabs** (query param `/jobs?status=suggested`):
All | Suggested | Reviewing | Applied | Interview | Offer | Archived | Rejected

**Definition of done for Item 2:**
- [ ] `/jobs` renders from `jobs.career_opportunities` — no DB errors
- [ ] Status filter tabs work — `/jobs?status=suggested` filters correctly
- [ ] Status dropdown updates DB and refreshes view
- [ ] Score color coding correct (Curator palette, not portal palette)
- [ ] Warm lead flag shows for `warm_lead=true` rows
- [ ] "Apply →" link opens in new tab
- [ ] Empty state shows when no records
- [ ] Robert signs off on the page visually before commit

---

## Item 3 — Challenger Phase 2 (Curator integration)

**Build after Items 1 and 2 are committed.**
Full spec already written: `_working/handoff_challenger_phase2_curator_2026-06-10.md`

**Summary of what Claude Code builds:**
- Wire `ChallengerService` into `generate_dive.py` — synthesis becomes Round 1, service handles Rounds 2 and 3
- Portal dive template — collapsed "Challenger review" section when `show_process=True`
- Regression gate: dive output with `enabled: false` must be byte-identical to today's output
- Pre-release test: 5 real dives in transparent mode, Robert reviews, 4/5 must pass

**Read the full handoff before starting:**
`_working/handoff_challenger_phase2_curator_2026-06-10.md`

---

## Commit sequence

```bash
# Item 1
git add domains/guild/db/schema_phase4.sql \
        domains/guild/db/test_schema.py
git commit -m "schema: apply phase4 tables — career_opportunities, cos_agenda, agent_feedback"

# Item 2
git add curator_server.py \
        templates/career_focus.html
git commit -m "feat: career focus portal — wire opportunities pipeline, status management"

# Item 3 (separate session after Items 1+2 confirmed)
# See handoff_challenger_phase2_curator_2026-06-10.md for commit details
```

---

*Build plan · Guild + Curator · v2 · 2026-06-10*
*Sequence: schema → career portal (complete scaffold) → Challenger Phase 2*
