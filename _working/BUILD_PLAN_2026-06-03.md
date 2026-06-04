# Build Plan — 2026-06-03
*mini-moi personal AI agent platform*
*Status: APPROVED — Robert sign-off 2026-06-03. Build in progress.*
*Reviewed by: Claude Code (author), Claude.ai, Grok*

---

## Framing

Two tracks, two branches, two risk classes:

- **Track A — Daily-use improvements** → `main`. Small, reversible, ship today.
- **Track B — Database spine POC** → `guild` branch. Additive, optional, **does not merge today.**
  The goal is to prove the concept, not to put a database into daily use.

The spine is official Guild platform infrastructure (per the Guild charter: the Postgres + graph
spine is shared platform, Guild-governed, Curator is its first consumer). It is framed and built
as early Guild setup, not as Curator backend.

### Three governing principles for the spine

1. **Daily use never depends on the database.** The system runs on JSON flat files today and must
   keep running on JSON if Postgres/Neo4j are down, broken, or absent. The DB is strictly additive
   and strictly optional. Nothing on a daily read path may depend on it.
2. **JSON is the source of truth; the DB is a rebuildable projection.** The flat files remain
   authoritative. Postgres/Neo4j are a view of them that can be dropped and regenerated from JSON
   at any time. This makes schema change safe — alter schema, re-run migrate, done. No future agent
   treats the DB as authoritative.
3. **First DB try → keep it limited.** Prove the spine works and the graph traverses real research
   data. Do not migrate everything. Smaller surface, less to go wrong.

### Spend follows attention
No automatic AI calls, no unattended pipeline steps. Applies to build too: no speculative
abstraction, no over-engineering. The schema is expected to evolve — build the minimum that
proves the POC.

---

## Context — where we are

- B-020 (German Test 8) quarantined; B-021 (Taiwan-relevance) demoted to non-gating ENV_CHECK.
- Baseline: German 48/48 (1 skipped). Curator UAT 5/5 gating pass.
- `main` clean, up to date with origin.
- Docker installed, not running. `docker-compose.yml` updated — Neo4j 512m heap, Postgres 256m,
  `restart: no` so containers never auto-start. Images not yet pulled.

---

# TRACK A — Daily-use improvements (branch: `main`)

*Small, reversible, real value, zero DB risk. If the day ends here, it was a good day.*

## Phase 1 — German core loop

### 1b. Full Grok Voice prompt delivery — HIGHEST VALUE, DO FIRST (~30 min)

**Files:** `domains/german/html_server.py`, `domains/german/templates/german_gesprache.html`

**Problem:** `getPromptText()` in the JS returns only the brief scene stub from
`speaking_prompts[key]`, not the full Grok Voice system prompt. Copy/Save deliver something
a user cannot actually paste into Grok Voice and run a session from.

**Fix:** Add `POST /api/gesprache-prompt` endpoint. Accepts `{persona_name, scene_key}`.
Calls existing `build_persona_prompt()` in `german_domain.py` (already assembles persona
description + style + register + scene context + memory injection). Returns the full assembled
prompt string. On scene selection in the JS, call this endpoint and populate `#prompt-text`
with the real prompt. Copy and Save-as-txt both use `getPromptText()` — they pick it up
automatically.

**Why first:** This is the core loop. Without it, the Gespräche page doesn't enable a real
Grok Voice session. Everything else in Phase 1 is polish; this is function.

### 1a. Landing page stale link (~5 min)

**File:** `domains/german/templates/german_landing.html`

Change: `/ueben` → `/gesprache`, label "Üben" → "Gespräche". The 301 redirect works but
this removes the rough edge on the public-facing entry point.

### 1c. Scene button German labels (~10 min)

**Files:** `domains/german/data/config/personas/*.json` (8 persona files)

**Problem:** Scene buttons display English key slugs (`cafe order`, `cafe bill`) via
`key.replace(/_/g, ' ')`. Should display German phrase hints.

**Fix (Option A — additive, zero risk):** Add a `labels` map to each persona JSON:
```json
"labels": {
  "cafe_order": "Ich hätte gerne…",
  "cafe_bill":  "Zahlen, bitte"
}
```
JS reads `p.labels?.[key] || key.replace(/_/g, ' ')`. No backend change. 8 persona files.

**→ STOP-GATE 1.** Commit Phase 1 to `main`. Run German suite (48/48). Clean place to pause.

---

## Phase 2 — Curator daily-pipeline gaps

### 2a. Subnav → Research link (~10 min)

Add a `Research` link pointing to `/research/dashboard` in the Curator subnav
(`Daily · Library · Scans & Dives · Observations · Focus · Research`). Simple HTML addition.
Known gap from CURATOR_STATE_2026-06-01.md.

### 2b. Dormant section routing (~20 min) — reads JSON, stays JSON

**File:** `curator_rss_v2.py`

After the top-20 score-ranked selection (completely unchanged), a second pass checks
remaining candidates against active Topic tags via **deterministic string-match only —
no AI call.** Matches surface in a collapsed "on radar" section below the main feed,
capped at ~10 items, score-ordered within the section.

**Rule:** One item, one place. If an article scores into the top 20, it is there — not
also in the dormant section.

**Reads from:** `_NewDomains/research-intelligence/data/threads/*/thread.json` (JSON).

**Stays on JSON permanently.** This is a daily production read path. Per principle #1 it
must not depend on the DB. Do not re-point it to Postgres in this build or any future build
without a deliberate decision that designs in a JSON fallback.

**Design ref:** CURATOR_DESIGN_REFERENCE.md — "Dormant section + radar routing" (Hybrid C,
decision settled).

**→ STOP-GATE 2.** Commit Phase 2 to `main`. Run Curator UAT (5/5 gating).
Run full German suite (48/48). **Track A complete.** Push `main` to origin.

---

# TRACK B — Database spine POC (branch: `guild`)

*Official Guild platform setup. Additive, optional, prove-the-concept.*
*JSON stays source of truth. Lives on `guild` branch and stays there — no merge today.*
*The bridge runs additively from the branch so the concept can be validated over several*
*days before anything touches `main`.*

**Create branch after Track A is committed and pushed:**
```bash
git checkout -b guild
```

(`guild` is the branch name. `feat/guild-spine-poc` is an acceptable alternative if
you prefer conventional feature-branch naming — the choice is Robert's.)

---

### Learning note — what is Neo4j actually for? (read before 3e)

- **Postgres** answers questions about *records you declared*: "list active Topics and their
  Source counts." Tables and joins. Well-understood.
- **Neo4j** is meant to answer questions about *connections you did NOT declare*: "Topics X
  and Y are two hops apart through gold-geopolitics — you never linked them directly."
  Traversal across declared edges to surface latent structure.
- **The POC's job is to test whether that is actually useful on your data, at your current
  scale.** With only 6 topics and a handful of sources, the graph may surface nothing a human
  couldn't see at a glance — that is a *successful* POC outcome, not a failure. The point is
  to learn, cheaply, whether and when the graph pays off. Do not assume it must justify itself
  today. Record the honest finding.

---

## Phase 3 — Postgres + Neo4j running, data in, queries verified

### 3a. Docker startup + image pull (~15 min)

```bash
open -a Docker
docker compose pull          # postgres:16 and neo4j:5 (~500MB, one-time)
docker compose down          # ensure clean state if anything was previously up
docker compose up -d
docker compose ps            # confirm both healthy before proceeding
```

Connection strings (both written to keychain, not hardcoded):
- Postgres: `postgresql://minimoi:simple123@localhost:5432/personal_agents`
- Neo4j Bolt: `bolt://localhost:7687` auth `neo4j / simple123`
- Neo4j browser: `http://localhost:7474`

**⚠️ Neo4j first-start warning:** The neo4j:5 image runs an initial setup step on first
boot that can look like it's hung. Give it **60–90 seconds** before assuming something is
broken. Use `docker compose logs neo4j` to watch progress. `docker compose ps` will show
`healthy` when it's ready.

### 3b. PostgreSQL schema — research objects only (~20 min)

**New file:** `db/schema.sql`

Six tables. All data already exists as Postgres-shaped JSON — this is transcription,
not redesign. Schema is expected to evolve; JSON regenerates it.

**Not in this schema:** `german_sessions`, `curator_signals` — not graph participants,
schema drift is a separate problem, deferred.

```sql
CREATE TABLE sources (
    id            TEXT PRIMARY KEY,
    type          TEXT,           -- article / post / paper / book
    title         TEXT,
    url           TEXT,
    origin        TEXT,           -- curator-found / pulled / added-by-robert
    date_recency  TEXT,
    tags          TEXT[],
    note          TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE topics (
    slug            TEXT PRIMARY KEY,
    name            TEXT,
    status          TEXT,         -- dormant / active-pull / paused / one-shot / closed / archived
    queries         JSONB,
    motivation      TEXT,
    tags            TEXT[],
    expires         DATE,
    schema_version  TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE groups (
    id                   TEXT PRIMARY KEY,
    name                 TEXT,
    member_tags          TEXT[],
    member_topic_slugs   TEXT[],
    created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE leanings (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    state       TEXT,             -- question / leaning / hold
    topics      TEXT[],
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE evidence (
    id          TEXT PRIMARY KEY,
    leaning_id  TEXT REFERENCES leanings(id),
    title       TEXT,
    url         TEXT,
    source      TEXT,
    stance      TEXT,             -- supports / complicates / neutral
    added       DATE,
    note        TEXT
);

CREATE TABLE tag_aliases (
    alias      TEXT PRIMARY KEY,
    canonical  TEXT
);
```

### 3c. Migration: JSON → Postgres (~30 min) — idempotent

**New file:** `db/migrate.py`

Upsert on primary key (MERGE / ON CONFLICT DO UPDATE). Safe to re-run at any time,
including after a schema change. Reads JSON (source of truth), projects into Postgres.

Run order (respects foreign keys):
1. `tag_aliases.json` → `tag_aliases`
2. `_NewDomains/research-intelligence/data/threads/*/thread.json` → `topics` (6 records)
3. `_NewDomains/research-intelligence/data/sources/sources.json` → `sources`
4. `_NewDomains/research-intelligence/data/groups/groups.json` → `groups`
5. `_NewDomains/research-intelligence/data/leanings/leanings.json` → `leanings` + `evidence`

Verify after: print row counts, confirm they match JSON record counts, log any
skipped/failed records with reason.

### 3c-verify. Run reconcile.py immediately after migration (baseline)

Run `db/reconcile.py` right after Step 3c completes — before touching the bridge.
This establishes a clean baseline: "migration complete, row counts match JSON."
The same script runs again after bridge changes to detect drift.
Two run points, same script: migration baseline + post-bridge drift check.

### 3d. Postgres CRUD layer (~30 min)

**New file:** `db/postgres.py`

Connection pool using **`psycopg2-binary`** (not psycopg3, not SQLAlchemy).
psycopg2-binary installs with no compile step — lowest friction for a first DB build.
psycopg3 is the modern choice but adds friction; SQLAlchemy ORM is unneeded overhead
given dict-shaped data. Revisit if psycopg2-binary causes issues.

```python
# get_connection() / connection pool
# get_topic(slug) / upsert_topic(data) / list_topics(status=None)
# get_sources(tags=None) / upsert_source(data)
# get_leaning(id) / upsert_leaning(data) / add_evidence(leaning_id, evidence)
```

Pure data layer. Tested standalone. No Flask wiring.

### 3e. Neo4j graph seed (~30 min)

**New file:** `db/graph_seed.py`

Reads from Postgres (not JSON directly). Writes declared edges to Neo4j.
Uses MERGE not CREATE — idempotent, safe to re-run.

```cypher
-- Node types
(:Topic {slug, name, status, tags})
(:Source {id, title, type, tags})
(:Group {id, name})
(:Leaning {id, title, state})
(:Tag {name})          -- canonical form, after alias resolution

-- Declared relationships (Robert's tagging + grouping decisions)
(:Topic)-[:MEMBER_OF]->(:Group)
(:Source)-[:LINKED_TO]->(:Topic)
(:Source)-[:TAGGED]->(:Tag)
(:Leaning)-[:ATTACHED_TO]->(:Group)
(:Leaning)-[:SUPPORTED_BY {stance}]->(:Source)
(:Tag)-[:ALIAS_OF]->(:Tag)     -- from tag_aliases
```

### 3f. Neo4j driver layer (~20 min)

**New file:** `db/neo4j_driver.py`

Uses the **official `neo4j` Bolt driver** (not py2neo — community-maintained, lags behind).

```python
# connect() — bolt://localhost:7687
# get_topic_neighbors(slug, hops=2) → connected nodes within N hops
# get_sources_for_leaning(leaning_id) → via Group → Topic → Source path
# get_related_topics(slug) → topics within 2 hops
```

### 3g. POC verification + honest read (~20 min)

**New file:** `db/poc_verify.py`

Runs three queries, prints results:

1. **Postgres join:** Active Topics with their Source counts.
2. **Neo4j traversal:** Topics within 2 hops of `gold-geopolitics`.
3. **Cross-system:** Sources supporting "Hungry and Poland" Leaning (Postgres)
   → other Topics those Sources connect to (Neo4j).

Pass = both DBs return data, no errors, results are sensible.

**Also capture the honest read:** did the graph surface anything a human couldn't see
at a glance from the JSON? Record the answer in a comment in `poc_verify.py`. That is
the actual learning output of the POC, regardless of what the answer is.

**→ STOP-GATE 3.** Commit to `guild`. Spine proven on the branch.

**This stop-gate is a real decision point, not a formality.**
The honest Neo4j finding recorded in `poc_verify.py` is what determines whether
Phase 4 (bridge) happens. If the graph surfaces nothing a human couldn't see at a
glance from the JSON — at current data scale — the correct call is to record that
finding and defer Phase 4 until the structure is richer. Do not proceed to Phase 4
by default. Stop, read the finding, then decide.

---

## Phase 4 — Dual-write bridge + drift check (~1 hour)

### Bridge

**New file:** `db/bridge.py`

Thin wrapper. Existing domain functions call `bridge.write(object_type, data)` **after**
their JSON write. Bridge calls the Postgres upsert and triggers a Neo4j edge update if
the object participates in the graph.

**Failure mode: log-and-continue.** JSON write already happened; production never breaks.
A bridge failure logs the error and returns — the caller never knows unless it checks.
This is the right call *because* `reconcile.py` (below) makes drift detectable.

**Wiring (on `guild` branch only):**
- `tools/curator_research.py` — after each `_save_*()` call
- `domains/german/german_domain.py` — after session save

**Untouched:** `curator_rss_v2.py`, all Flask routes, Telegram, launchd.

### Reconcile

**New file:** `db/reconcile.py`

Compares JSON record counts to Postgres row counts for all six tables. Reports any drift.
Run at end of day and after any bridge change. This is what makes log-and-continue safe —
drift is detected, not silently accumulated.

**→ STOP-GATE 4.** Commit to `guild`. Bridge runs additively from the branch.
Validate over several days before considering merge to `main`.

---

## What is NOT in scope today

- Merging `guild` to `main` (prove first, merge is a later deliberate decision)
- `german_sessions` + `curator_signals` migration (not graph participants, deferred)
- Postgres or Neo4j as a read source for anything (additive write-only today)
- 2b reading from Postgres (stays JSON permanently per principle #1)
- Leaning UI, dormant section UI beyond 2b routing
- Admin/Archiv tabs (German), persona memory
- Docker in production / launchd (local dev only)
- B-011 card edge definition (CSS) — if time, but deprioritised vs Track B

---

## Definition of done

### Track A (`main`) — the must-have

- [ ] 1b: `POST /api/gesprache-prompt` wired; full Grok Voice prompt delivered on scene select
- [ ] 1a: Landing page link updated (Gespräche, direct URL)
- [ ] 1c: German scene button labels on all 8 personas
- [ ] 2a: Research link in Curator subnav
- [ ] 2b: Dormant routing active in `curator_rss_v2.py`; reads JSON; one-item-one-place enforced
- [ ] German suite: 48/48 (1 skipped) on live MacBook after all commits
- [ ] Curator UAT: 5/5 gating checks pass after all commits
- [ ] `main` pushed to origin

### Track B (`guild` branch) — prove-the-concept, if time permits

- [ ] `docker compose up -d` clean; both containers healthy; memory caps respected
- [ ] `db/schema.sql` applied; all 6 tables exist in Postgres
- [ ] `db/migrate.py` runs clean; row counts match JSON record counts
- [ ] `db/poc_verify.py` returns sensible results from both DBs
- [ ] Honest read recorded: did the graph surface anything non-obvious?
- [ ] `db/bridge.py` wired on `guild`; `db/reconcile.py` shows zero drift after a test write
- [ ] Committed to `guild`. **Not merged to `main`.**

**Priority if the day runs short:** Track A complete > a half-finished spine.
Track A is the guaranteed-good outcome; Track B is the bigger prize but safely bracketed.

---

## Files

### `main` (Track A)
```
domains/german/html_server.py                    ← 1b new endpoint
domains/german/templates/german_gesprache.html   ← 1b JS wiring
domains/german/templates/german_landing.html     ← 1a
domains/german/data/config/personas/*.json       ← 1c labels (8 files)
curator_rss_v2.py                                ← 2b dormant routing
[curator subnav HTML]                            ← 2a
```

### `guild` branch (NEW — Track B)
```
docker-compose.yml                               ← already updated
db/schema.sql
db/migrate.py
db/postgres.py
db/neo4j_driver.py
db/graph_seed.py
db/bridge.py
db/reconcile.py
db/poc_verify.py
tools/curator_research.py                        ← bridge wiring (guild only)
domains/german/german_domain.py                  ← bridge wiring (guild only)
```

---

*Authored by Claude Code. Reviewed by Claude.ai + Grok. Approved by Robert 2026-06-03.*
*Build in progress — do not edit this file during the build session.*
