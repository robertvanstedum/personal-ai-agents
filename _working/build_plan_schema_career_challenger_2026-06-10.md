# Build Plan — Schema + Career Portal + Challenger Phase 2
*mini-moi · Guild · Curator*
*Authored: 2026-06-10 19:23 CDT (2026-06-11 00:23 UTC) — Claude.ai*
*Build sequence: Item 1 → Item 2 → Item 3*

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

**Step 3 — Add robert_ro grants (if not in schema_phase4.sql already):**
```sql
GRANT SELECT ON jobs.career_opportunities TO robert_ro;
GRANT SELECT ON guild.cos_agenda TO robert_ro;
GRANT SELECT ON guild.agent_feedback TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA jobs GRANT SELECT ON TABLES TO robert_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA guild GRANT SELECT ON TABLES TO robert_ro;
```
Per `docs/DB_SCHEMA.md` pattern — robert_ro grant in the same migration, not a follow-up fix.

**Step 4 — Update test_schema.py:**
Add checks for the three new tables:
```python
# Add to existing test_schema.py checks
("jobs", "career_opportunities"),
("guild", "cos_agenda"),
("guild", "agent_feedback"),
```
Run: `venv/bin/python3 domains/guild/db/test_schema.py` — expect 28/28 (25 existing + 3 new).

**Step 5 — Migrate JSON fallback data:**
Loop A has been writing to `data/guild/career_opportunities.json` since Phase 4 shipped.
Migrate any existing records to the new DB table before the next Loop A run:
```bash
python3 -c "
import json, psycopg2
# Read from JSON fallback, insert into jobs.career_opportunities
# Handle duplicates by url (UNIQUE constraint)
"
```
Check `data/guild/career_opportunities.json` exists and has records first.
If empty or absent, skip migration.

**Definition of done for Item 1:**
- [ ] `test_schema.py` passes 28/28
- [ ] `robert_ro` can SELECT from all three new tables
- [ ] JSON fallback data migrated (or confirmed empty)
- [ ] Next Loop A run writes to DB, not JSON

---

## Item 2 — Career Focus portal page

**Why second:** depends on schema from Item 1. Portal reads from `jobs.career_opportunities`.

### Route

`GET /jobs` — main pipeline view (already registered as a portal tab from Phase 1 scaffold)

```python
@app.route("/jobs")
def jobs():
    opps = db_query("SELECT * FROM jobs.career_opportunities ORDER BY fit_score DESC, date_found DESC")
    return render_template("jobs/jobs.html", opportunities=opps)

@app.route("/jobs/<int:opp_id>/status", methods=["POST"])
def update_job_status(opp_id):
    new_status = request.form.get("status")
    db_execute("UPDATE jobs.career_opportunities SET status=%s, date_updated=NOW() WHERE id=%s",
               (new_status, opp_id))
    return redirect(url_for("jobs"))
```

### Page design

Matches existing portal design system (parchment `#F5F0E8`, copper `#C68A5E`, dark nav `#2A1F14`, Georgia serif).

**Layout:**
```
┌─ Status filter tabs ──────────────────────────────────────────┐
│  All  Suggested  Reviewing  Applied  Interview  Offer  Archived │
└────────────────────────────────────────────────────────────────┘

┌─ Opportunity card ─────────────────────────────────────────────┐
│  Principal Engineer, Agentic AI — Comcast           9.0 / 10  │
│  Philadelphia, PA · Employment                  [⭐ Warm lead] │
│                                                                 │
│  "Strong telecom background directly applicable to Xfinity's   │
│   multi-agent orchestration mandate..."                         │
│                                                                 │
│  Found: Jun 9                                                   │
│  [Apply →]  [Reviewing ▼]                                       │
└─────────────────────────────────────────────────────────────────┘
```

**Status filter tabs:** All | Suggested | Reviewing | Applied | Interview | Offer | Archived | Rejected

Use query param: `/jobs?status=suggested` — tab highlights the active filter.

**Score color coding:**
- ≥ 8.0: copper `#C68A5E`
- 6.0–7.9: amber `#B8860B`
- < 6.0: grey `#888`

**Warm lead flag:** ⭐ copper star, small, inline with geo/type.

**Status dropdown:** simple `<select>` form on each card, POSTs to `/jobs/<id>/status`.
No page reload needed — but a redirect back to the filtered view is fine for now.

**Template structure:**
```
templates/jobs/
└── jobs.html         ← main page
```

### Template skeleton

```html
{% extends "base.html" %}
{% block content %}

<!-- Status filter tabs -->
<div class="jobs-filter-tabs">
  {% for s in ['all','suggested','reviewing','applied','interview','offer','archived','rejected'] %}
  <a href="/jobs?status={{ s }}"
     class="{{ 'active' if current_status == s else '' }}">
    {{ s | title }}
    {% if s != 'all' %}
      <span class="count">{{ opportunities | selectattr('status','eq',s) | list | length }}</span>
    {% endif %}
  </a>
  {% endfor %}
</div>

<!-- Opportunity cards -->
{% for opp in opportunities %}
{% if current_status == 'all' or opp.status == current_status %}
<div class="opp-card">
  <div class="opp-header">
    <span class="opp-title">{{ opp.title }} — {{ opp.company }}</span>
    <span class="opp-score" style="color: {{ score_color(opp.fit_score) }}">
      {{ opp.fit_score }} / 10
    </span>
  </div>
  <div class="opp-meta">
    {{ opp.geo }} · {{ opp.opportunity_type }}
    {% if opp.warm_lead %}<span class="warm-lead">⭐ Warm lead</span>{% endif %}
  </div>
  <div class="opp-narrative">{{ opp.fit_narrative }}</div>
  <div class="opp-actions">
    <span class="opp-date">Found: {{ opp.date_found | format_date }}</span>
    {% if opp.url %}
    <a href="{{ opp.url }}" target="_blank" class="opp-apply">Apply →</a>
    {% endif %}
    <form method="POST" action="/jobs/{{ opp.id }}/status" style="display:inline;">
      <select name="status" onchange="this.form.submit()">
        {% for s in ['suggested','reviewing','applied','interview','offer','archived','rejected'] %}
        <option value="{{ s }}" {{ 'selected' if opp.status == s }}>{{ s | title }}</option>
        {% endfor %}
      </select>
    </form>
  </div>
</div>
{% endfor %}

<!-- Empty state -->
{% if opportunities | length == 0 %}
<p style="color:#888; padding:2rem;">No opportunities yet — Loop A runs twice daily.</p>
{% endif %}

{% endblock %}
```

**Add `score_color` template filter to Flask app:**
```python
@app.template_filter('score_color')
def score_color(score):
    if score >= 8.0: return '#C68A5E'
    if score >= 6.0: return '#B8860B'
    return '#888'
```

**Definition of done for Item 2:**
- [ ] `/jobs` renders from `jobs.career_opportunities` table
- [ ] Status filter tabs work — `/jobs?status=suggested` filters correctly
- [ ] Status dropdown updates DB and refreshes view
- [ ] Score color coding correct
- [ ] Warm lead flag shows for warm_lead=true rows
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
git add templates/jobs/ [portal route file]
git commit -m "feat: career focus portal page — pipeline view, status management"

# Item 3 (separate session after Items 1+2 confirmed)
# See handoff_challenger_phase2_curator_2026-06-10.md for commit details
```

---

*Build plan · Guild + Curator · 2026-06-10 19:23 CDT*
*Sequence: schema → career portal → Challenger Phase 2*
