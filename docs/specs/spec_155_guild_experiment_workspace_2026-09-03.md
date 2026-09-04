# Spec #155: Guild Experiment Workspace — One Working Page, From Idea to Operational

**Version:** v1.1 build baseline (v1.0 plus Codex's five final sign-off edits of 2026-09-03; equals working-draft v0.3)
**Date:** 2026-09-03
**Status:** Approved by Robert on 2026-09-03 for the **G1 read-only slice**. G2 (notes and stage writes) is designed here but not authorized; it requires the Central Personal Repository write contract (shared with CoS Career C0).
**Decision owner:** Robert
**Parent capability:** Guild
**Builder:** Claude Code, in chunks of about 40 minutes, one reviewed diff per slice
**Reviewers:** Codex (BUILD READY sign-off on v1.0 → v1.1, then delta check on the built diff), Robert
**Related:** Spec #154 (IoT Connect hosting; PR #193); Spec #153 (Prototype Lab); Planning Studio INIT-2026-0001 (Central Personal Repository); Planning Studio Charter and Record Lifecycle v0.1
**Revision 2026-09-04 (N3 hosting rename):** the hosted route is `/app/iotconnect` and the portal settings are `IOTCONNECT_*`; §2 decision 2 ("internal Connect HQ identifiers are not renamed") and the §12 out-of-scope line about renaming internals were superseded by the IoT Connect naming decision of 2026-09-03. Stage promotion is now derived at runtime: the joined row shows `operational` only when the release label is an approved `iotconnect-v*` tag and the health check is ok.

## 1. Executive decision

Add one practical **Experiment** area to Guild as the fourth and final landing card. It is a working surface where Robert sees efforts of any size, a behaviour worth trying, an integration, a tool, or a complete demonstration, from idea to a runnable result. Finished demonstrations stay visible and launchable, with their working surfaces. The first reference demo is **IoT Connect**. The Fiber demonstration appears as planned work with no artwork and no runnable application.

The first build is read-only. Guild renders a projection; Planning Studio in the Central Personal Repository owns the durable record.

## 2. Decisions taken (Robert, 2026-09-03)

1. Landing cards: `Build · Operate · Improve · Experiment`. Landing title, tagline, and the other cards unchanged.
2. Visible name on Guild surfaces: **IoT Connect**. *(Historical: this decision left the internal Connect HQ routes, repository names, image tags, and deployment identifiers unrenamed; superseded by the naming decision of 2026-09-03 — see the revision note above.)*
3. One page: an **Operational** section and a **working matrix**. No hierarchy, no Prototype Lab application, no gallery.
4. Lifecycle shown: `Idea → Tinkering → Built → Operational`, distinct from Planning Studio governance status.
5. Planning Studio owns the durable record; Guild renders a projection. No second registry.
6. Read-only first (G1). Notes and stage writes (G2) need a separately reviewed write contract.
7. Operate KPIs are a separate future spec.
8. Artwork is non-blocking: a placeholder in the Guild visual language for G1; IoT Connect may use small connectivity icons; Fiber has no image.
9. Wireframe-level acceptance; layout and styling adjusted in visual review.
10. **Guild G1 is a separate follow-up after PR #193 merges and is verified**, on a branch based on the resulting `main`. G1 depends on #193's routing, identity, health configuration, and hosted-demo contract; #193 is not enlarged.
11. **Launch opens the demo in the browser or returns a clear error if it is not running.** The Mac user test is: start the standalone demo, click Launch from Guild, see the demo; stop the demo, see unavailable and the error.
12. **The demo's workbenches are part of the package** and are linked beside Launch.
13. The personal store is the **Central Personal Repository**. No "vault" term.

## 3. Ownership and storage

### 3.1 Planning Studio and the Central Personal Repository
Planning Studio records live in the Central Personal Repository: person-owned, human-readable Markdown with frontmatter, automatic identity and revision, immutable original assets, as defined by the v0.9 design candidate (`planning-studio-central-personal-repository-design--0efmwp2j.md`) and Robert's decisions of 2026-09-02. It is not a GitHub repository. `personal-ai-agents` holds application code, schemas, validators, blank templates, and non-personal fixtures only. Guild renders a projection of approved fields; it is never the system of record.

### 3.2 Two sources, joined by identity
- **Design/working facts** (`initiative_id`, `title`, `summary`, `scope`, `experiment_stage`, `next_step`, `domains`, `updated_at`) come from the Planning Studio initiative record.
- **Runtime facts** for an operational demo (`launch_url`, `health_url`, workbench links, `release`) come from deployment configuration and existing health signals, never from hand-edited Planning Studio fields.
The page joins the two by `initiative_id` and release identity.

### 3.3 G0 and the G1 projection source
G0 is a contract check, not a storage implementation. It (1) cites the approved Central Personal Repository storage contract (Planning Studio INIT-2026-0001 and Robert's 2026-09-02 decisions); (2) verifies a read-only projection input with safe fixture data; (3) verifies that no personal data enters GitHub; and (4) records the production adapter as **unavailable** until its separately approved storage implementation exists. Location, adapters, encryption, backup, and restore are acceptance requirements of the storage initiative (with CoS Career C0), not deliverables or blockers of this spec.

Until the adapter exists, G1 reads a **generated, non-authoritative projection file** `data/guild/experiment_projection.json` containing only the approved fields of §3.2 for public-safe rows. It carries `"generated": true`, `"source": "planning-studio"`, and `"generated_at"`. It is regenerated by a script once the adapter exists; until then it is written by hand for exactly the initial rows and is treated as a fixture, not a record. Hand-maintaining it alongside Planning Studio for anything beyond the initial rows is prohibited.

### 3.4 Other Guild areas
Build owns specs, authorisation, implementation, review, release evidence. Operate owns health, deployments, incidents, access. Improve owns retrospectives and patterns. Experiment owns work whose purpose is learning or proving. One authoritative record per kind of fact; other areas link.

## 4. Information model

### 4.1 Projected initiative fields
| Field | Required | Values / purpose |
|---|---|---|
| `initiative_id` | yes | stable ID, e.g. `INIT-2026-0002` |
| `title` | yes | short activity name |
| `summary` | yes | one or two sentences |
| `experiment_stage` | yes | `idea` \| `tinkering` \| `built` \| `operational` |
| `scope` | yes | free label: behaviour, integration, tool, reference demo |
| `updated_at` | yes | ISO date of last meaningful record activity |
| `next_step` | no | the one current action or open question |
| `domains` | yes | list |
| `planning_url` | no | link to the Planning Studio record (Guild Build → Docs when it renders there) |

`experiment_stage`, `summary`, `scope`, `next_step` are a small extension to Planning Studio initiative metadata (decision recorded in §11).

### 4.2 Per-environment surface configuration (deployment-owned)
One configuration object per environment, owned by deployment configuration in the portal, never by repository content:

```text
base_url                       exact allow-listed origin or portal path
health_url                     probed server-side by Guild (1.5 s timeout, 30 s cache)
release_label                  see §5.3 stage rules
surfaces (fixed, reviewed relative paths, joined safely to base_url):
  launch:            /
  admin:             /admin
  billing_workbench: /workbench
  swagger:           /docs
```

| Environment | `base_url` | `health_url` |
|---|---|---|
| Mac (local portal + standalone demo) | `http://127.0.0.1:8095` (loopback origin; port configurable; opens in a new tab) | `http://127.0.0.1:8095/api/v1/health` |
| AWS | `/app/iotconnect` (owner-only portal route) | `{IOTCONNECT_BACKEND}/api/v1/health` |

Portal variables: `IOTCONNECT_SURFACE_BASE_URL`, `IOTCONNECT_HEALTH_URL`, `IOTCONNECT_RELEASE_LABEL`; defaults in `minimoi_portal/config.py` are the AWS values, the Mac overrides them locally. Only `http(s)://127.0.0.1[:port]`, `http(s)://localhost[:port]`, or a relative portal path are accepted for `base_url`; anything else fails at startup. Health is probed by the server, not by browser JavaScript.

Why two base URLs: the portal proxy (PR #193) forwards the full prefixed path because the hosted container runs with `IOTCONNECT_ROOT_PATH=/app/iotconnect`; the standalone Mac demo is unprefixed, so the proxied route returns 404 against it. Verified on the Mac on 2026-09-03 with revision 4.

### 4.3 Notes (G2, designed, not authorized)
"Add note" creates a new Planning Studio note record linked to the initiative: generated record ID, initiative ID, author from the authenticated session, timestamp, text verbatim, origin `guild_experiment_ui`. Additive; corrections are new notes; secrets rejected; owner-only. Requires the Central Personal Repository write contract.

## 5. User interface

### 5.1 Guild landing
Fourth card: title **Experiment**; kicker `Ideas · Tinkering · Reference demos`; description in the landing's working language; link `/guild/experiment`; placeholder illustration in the Guild family (`static/guild/guild-experiment.jpg`, final art reviewed separately). Card row adjusts to four; nothing else changes.

### 5.2 Experiment page
```text
BUILD · OPERATE · IMPROVE · EXPERIMENT                (section subnav gains Experiment)
Experiment                                            owner-only
OPERATIONAL
| Name        | What it proves                          | Release        | Runtime     | Actions                                 |
| IoT Connect | Enterprise IoT connectivity management  | see §5.3       | ● available / ○ unavailable | Launch demo (or Demo unavailable) · Admin workbench · Billing workbench · Swagger |
WORKING MATRIX          filters: stage · scope · domain        order: most recently updated first
| ID            | Activity                          | Scope          | Stage | Updated    | Next step / note        | Actions     |
| INIT-2026-00xx| Fiber order-to-cash demonstration | reference demo | Idea  | 2026-09-03 | G3/G3A after standalone | open record |
```
- **Unavailable-launch contract.** The action remains visible in both states. When healthy it is labelled **Launch demo** and opens the configured `base_url` + surface. When unhealthy it is labelled **Demo unavailable** and opens the Guild-owned diagnostic page, which returns HTTP 503 and shows the failed health check and timestamp. The page never sends the browser to an unhealthy backend. The Admin workbench, Billing workbench, and Swagger actions follow the same rule: healthy → their surfaces; unhealthy → the diagnostic page.
- Diagnostic page (`/guild/experiment/unavailable/<initiative_id>`): "IoT Connect is not running." with the health URL checked, the result, the time, and a link back. HTTP 503. Never a blank page, raw 503 body, or hang.
- Actions in G1: open record (if `planning_url`), Launch demo / Demo unavailable, Admin workbench, Billing workbench, Swagger. G2 adds note and stage change.
- Stale indicator after 30 days of no update; not a second status.

### 5.3 Initial rows
1. **IoT Connect**. Stage and label follow the truthful state of the package:
   - **local tested candidate** (today: revision 4, verified by Codex on 2026-09-03): `experiment_stage: built`, label `revision 4 candidate` or the verified image digest; shown in the working matrix, not in the Operational section; Launch/workbench actions still work against the local demo through the Mac configuration;
   - **approved immutable tag** (after promotion, commit, and tag): may display the release value `iotconnect-v0.9.0-beta.1`;
   - **hosted and healthy approved release** (after Spec #154's deployment gate, G3): `experiment_stage: operational`, eligible for the Operational section.
   G1 must not present `v0.9.0-beta.1` as an immutable operational release before that occurs. `initiative_id`: assigned in Planning Studio; G1 uses the placeholder `INIT-2026-0004` flagged as placeholder in the projection file.
2. **Fiber order-to-cash demonstration**: `experiment_stage: idea`, `scope: reference demo`, `next_step: "G3/G3A after standalone beta acceptance"`, no image, no launch.
3. Further rows only through Planning Studio intake.

### 5.4 IoT Connect meaning
Enterprise operations (fleet, meters, equipment) → API request → IoT Connect → provision/activate → SIM and mobile network. IoT Connect is the orchestration and control platform, not the carrier network and not a billing application.

## 6. Permissions, privacy, failure
Owner-only page and controls; client headers never establish identity; launch and workbench URLs come only from configuration (allow-listed), never from repository content. Only approved fields are rendered. Projection file missing or invalid → page renders with "working register unavailable" and the file's `generated_at` if present; one malformed row is skipped with a bounded warning; a failed health probe → unavailable, page still renders within the probe budget.

## 7. Files in scope (mini-moi repository)
| File | Change |
|---|---|
| `minimoi_portal/app.py` | `GET /guild/experiment` (owner-only), `GET /guild/experiment/unavailable/<initiative_id>`; projection loader with validation and failure handling; runtime join; health probe with cache |
| `minimoi_portal/config.py` | `GUILD_EXPERIMENT_PROJECTION` (default `data/guild/experiment_projection.json`), `IOTCONNECT_SURFACE_BASE_URL`, `IOTCONNECT_HEALTH_URL`, `IOTCONNECT_RELEASE_LABEL`, the fixed surface path table; `IOTCONNECT_RELEASE_LABEL` and `IOTCONNECT_BACKEND` exist from PR #193 |
| `minimoi_portal/templates/guild/guild_landing.html` | fourth card |
| `minimoi_portal/templates/guild/_section_subnav.html` | `Experiment` entry |
| `minimoi_portal/templates/guild/experiment.html` | new page |
| `minimoi_portal/templates/guild/experiment_unavailable.html` | diagnostic page |
| `minimoi_portal/static/guild/guild-experiment.jpg` | placeholder illustration |
| `data/guild/experiment_projection.json` | generated, non-authoritative projection with the two initial rows |
| `tests/test_guild_experiment.py` | §9 |
Protected root documents are not modified. PR #193's Improve card is removed in Chunk 3 once Experiment carries the demo (Improve returns to its placeholder until its own spec).

## 8. Build plan in 40-minute chunks (G1)

| Chunk | Deliverable | Done when |
|---|---|---|
| **1. Skeleton** | Landing card + subnav entry + `/guild/experiment` owner-only route + `experiment.html` rendering the Operational table and matrix from `experiment_projection.json` with the two rows; config variables; loader validation. | page renders locally with both rows; access tests pass |
| **2. Runtime join** | Server-side health probe with timeout and cache; the §4.2 configuration object; Launch demo / Demo unavailable and the three surface actions; diagnostic page route; stale indicator; filters and ordering. | Mac test passes both ways: demo up → all four surfaces open from `http://127.0.0.1:8095`; demo down → Demo unavailable and every action opens the diagnostic page |
| **3. Tests and tidy** | `tests/test_guild_experiment.py` complete (§9); remove the Improve card that PR #193 added; placeholder illustration; PR with the feature-named title. | all tests green locally; PR opened as draft for Codex delta check |
The feature branch is based on `main` **after PR #193 has merged and been verified** (decision 10). Each chunk ends with a commit on the feature branch so a location switch loses nothing. G2 and G3 are not in this plan.

## 9. Tests (`tests/test_guild_experiment.py`)
1. Access: signed-out → 302 login; guest, admin → owner_required; owner → 200.
2. Landing: exactly four cards, the fourth links to `/guild/experiment`; other three unchanged.
3. Subnav shows Experiment; Build's rows unchanged.
4. Projection: both rows render with all §4.1 fields; ordering by `updated_at`; filters by stage, scope, domain; stale indicator when `updated_at` older than 30 days (fixture).
5. Malformed row skipped with warning; missing/invalid file → "working register unavailable" page still 200.
6. Runtime join: health probe (server-side) monkeypatched healthy → the action reads "Launch demo" and all four surfaces (launch, admin, billing_workbench, swagger) are built from `base_url` + the fixed paths and return the expected content against a fixture backend; unhealthy → the action reads "Demo unavailable" and **none** of the four actions link to the backend, all go to the diagnostic route, which returns 503 with the message, health URL, and time; probe timeout respected (fake slow probe); cache hit within 30 s.
7. Safety: any URL-like field in the projection file is ignored (surfaces come only from configuration); a `base_url` that is not a loopback origin or a relative portal path is rejected at startup; surface paths cannot be overridden by environment or repository content.
8. Stage truthfulness: a row with `experiment_stage: built` renders in the working matrix with its candidate label and never in the Operational section; only `operational` rows render there.
9. Existing `tests/test_guild.py`, `tests/test_portal.py`, `tests/test_cos_guild_visual_polish.py` pass unchanged.

## 10. Acceptance (G1)
- Landing differs only by the fourth card; `/guild/experiment` owner-only; Operational and matrix sections render from the projection; the IoT Connect action is visible in both states, labelled **Launch demo** when healthy (all four surfaces reachable) and **Demo unavailable** when not (every action opens the diagnostic page, HTTP 503, failed check and timestamp; the browser is never sent to an unhealthy backend); the local candidate is labelled truthfully as a candidate and sits in the matrix, not the Operational section, until promoted and hosted; Fiber row has ID, stage, updated, next step, no image, no launch; malformed or missing projection degrades gracefully; all §9 tests pass; no protected file modified.
- **Mac user test (Robert):** with the standalone demo running, Guild → Experiment → Launch demo opens IoT Connect and the Admin, Billing workbench, and Swagger actions open their surfaces; with the demo stopped, the action reads Demo unavailable and opens the diagnostic page.

## 11. Decisions recorded / remaining
- Recorded: Planning Studio metadata extension (`experiment_stage`, `summary`, `scope`, `next_step`) is approved for the projection; the G1 projection file is a non-authoritative fixture until the repository adapter exists.
- Recorded: PR sequencing — merge and verify PR #193 first; G1 is a separate follow-up branch/PR based on the resulting `main` (decision 10).
- Remaining, not blocking G1: the Central Personal Repository adapter and AWS read path (with CoS Career C0); the G2 write contract; final illustration.

## 12. Out of scope
Redesigning Build, Build Log, Operate, Improve, or the Kanban; Operate KPIs; new monitoring; guest access; building the Fiber demo; notes and stage writes (G2); a gallery, marketplace, or multi-user platform.
