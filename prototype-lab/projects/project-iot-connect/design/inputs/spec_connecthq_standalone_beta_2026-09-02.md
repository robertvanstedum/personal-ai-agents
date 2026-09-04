# Spec — Connect HQ Standalone Beta (WS0 + WS1)

| | |
|---|---|
| **Spec ID** | spec_connecthq_standalone_beta |
| **Version** | 0.1 — 2026-09-02 |
| **Author** | Claude AI (design) from the v0.3 package and its handoff |
| **Builder** | Claude Code |
| **Reviewer** | Codex (second-pass diff review) |
| **Decision owner** | Robert |
| **Gates** | G0A (runs from tracked home) → G1 (portability + reset tests) → G2 (tag `connecthq-v0.9.0-beta.1`) |
| **Authority** | Implementation authorized for this spec only after Robert approves the Required First Output (§3). No fiber scope. No commit, push, tag, or PR without reviewed-diff approval. |
| **Supersedes** | `handoffs/CLAUDE_CODE_STANDALONE_BETA_HANDOFF.md` remains the narrative; this spec is the executable contract and takes precedence where they differ. |

---

## Intent

Turn the working Project Nightjar / Connect HQ IoT prototype into an artifact that starts, resets, verifies, and stops from a clean checkout on another Mac, with nothing laptop-specific and no secrets, and freeze it as a tagged release. This release is the baseline the fiber demo is derived from tomorrow. Everything this spec does not list is out of scope; the temptation to "tidy the domain while we're in there" is the failure mode to avoid.

## Overview

Two phases, strictly ordered. Phase 0 moves the code and proves it runs unchanged. Phase 1 changes packaging and reliability only.

```text
_working/…/project-nightjar   ──copy──►  prototype-lab/projects/project-connect-hq/
   (checksummed, then retained)             run unchanged (G0A)
                                            │
                                            ▼
                                     containerize · single commands · deterministic seed
                                     smoke + e2e tests · no secrets · stable URLs  (G1)
                                            │
                                            ▼
                                     reviewed diff → tag connecthq-v0.9.0-beta.1   (G2)
```

---

## 1. Constraints (inherited, non-negotiable)

- Preserve Robert's unrelated working-tree changes.
- Do not modify protected repository documents.
- No employer, interviewer, client, or real-customer identity anywhere.
- No Salesforce, fiber, Zuora, or payments scope.
- Prefer a small packaging diff over an architectural rewrite.
- A summary is not evidence. Deliver the diff, test output, and verification artifacts.

## 2. Known facts and known unknowns

**Known from the package:** FastAPI + Pydantic app under `app/`; Postgres via `docker-compose.postgres.yml`; SQL under `postgres/01–05`; scripts `run-memory-demo.sh`, `run-postgres-demo.sh`, `run-integration-mocks.sh`, `test-demo-stores.sh`; Postman `WhAM_v3` and `Connect_HQ_interview_evidence` collections; `.env.example`; FlowOne (SOAP) and Amdocs-middleware (REST) mocks; summarized-vs-detailed billing mode; A/B invoice comparison artifact.

**Unresolved and must be answered in the Required First Output:** `ARCHITECTURE.md` describes memory and Snowflake repository adapters; the technical snapshot ships PostgreSQL. **Which repository backend is live in the demo path, and is Snowflake code present, dead, or absent?** The answer determines whether Phase 1 has one runtime (Postgres) or must carry an unused adapter.

## 3. Required First Output — before any edit

Return, as one document:

1. Repository and runtime inventory, including the backend resolution above.
2. Container / process topology as it exists today.
3. Dependency inventory (`requirements.txt`, `pyproject.toml`, system deps).
4. Secret inventory — every place a credential or laptop path is read.
5. Existing test, reset, and seed behaviour, with what actually passes today.
6. Proposed tracked destination tree.
7. Minimal migration sequence.
8. Risks to the current demonstration.
9. File-level implementation plan for Phase 1.

Robert approves this before Phase 0 begins.

## 4. Phase 0 — Promote (G0A)

| Step | Action | Evidence |
|---|---|---|
| 0.1 | `sha256sum` every file in the `_working` prototype tree → `PROMOTION_MANIFEST_2026-09-02.txt` in the new home | Manifest committed with the promotion |
| 0.2 | Copy commit-safe source and artifacts to `prototype-lab/projects/project-connect-hq/` | — |
| 0.3 | Exclude `.venv`, `.pytest_cache`, `.DS_Store`, secrets, local DBs, raw run bodies | `.gitignore` diff |
| 0.4 | Do not overwrite the project-control documents already in the destination; resolve collisions explicitly | Collision list |
| 0.5 | Change only paths that must change for the app to run from its new home | Diff limited to path edits |
| 0.6 | Start and exercise from the new location: UI, `/api/v1` routes, Postgres, both integration mocks, reset, tests, Postman evidence calls, invoice comparison | Screenshots or terminal capture per item |
| 0.7 | Re-hash the promoted tree; diff against the manifest; every difference explained | `PROMOTION_DIFF.md` |
| 0.8 | Present to Robert. The old `_working` copy is retained until he approves retirement. | Robert's written approval |

**G0A passes when the existing demo runs from the tracked directory with only path changes.**

## 5. Phase 1 — Formalize (G1)

### 5.1 Runtime

- `docker-compose.yml` at the project root: services `app`, `postgres`, `mock-flowone`, `mock-amdocs`. Named volume for Postgres. Health checks on all four.
- App image built from a `Dockerfile`; no host `.venv`; no absolute user path anywhere. Configuration only via environment; `.env.example` is complete and `.env` is ignored.
- Mocks run as separate containers, not as in-process code paths, so the topology matches the "actual mock HTTP boundaries" the Phase-2 design document calls for.

### 5.2 Commands (one each, documented in `README_CONNECT_HQ.md`)

| Command | Behaviour |
|---|---|
| `make up` | Full stack to healthy; prints URLs |
| `make down` | Stops without destroying the named volume |
| `make reset` | Restores the exact approved seed state; idempotent |
| `make status` | Health and dependency state of all four services |
| `make smoke` | Runs the smoke suite; exits non-zero on any failure |
| `make verify` | `reset` → `smoke` → e2e IoT success, IoT failure/rollback, summarized billing → prints a pass/fail table |

### 5.3 Seed and reset

Deterministic seed: fixed UUIDs and display numbers so evidence queries match across runs. `reset` truncates and reseeds in one transaction. Seed data is synthetic and vendor-neutral.

### 5.4 Tests

- Smoke: every `/api/v1` route returns its documented shape; Swagger loads; both mocks answer health.
- E2E: IoT activation success (FlowOne success → subscription ACTIVE → Amdocs CREATE when policy ON); IoT activation failure (FlowOne failure → ACTIVATION_FAILED, MDN released, SIM retained, Amdocs NOT_ELIGIBLE); summarized billing (bill run in summarized mode → account-scope postings with null MDN, source-count preserved, zero-variance reconciliation).
- **Output-produced check:** the smoke suite asserts that the bill run *wrote rows* and the invoice comparison *rendered*, not merely that endpoints returned 200. (Issue-158 lesson.)
- Memory-adapter contract tests continue to pass if the memory adapter is retained.

### 5.5 Stable URLs and artifacts

- Swagger at `http://localhost:8000/docs`; Postman environment points at it; no dated draft paths in any presentation asset.
- Product name consistent in all user-visible artifacts (D-01 must be decided before tagging).
- Postman, Swagger, SQL evidence files, runbook, and presentation live under the tracked tree.

### 5.6 Secrets

- Only the local Postgres password from `.env.example`; rotate it to a clearly synthetic value.
- Pre-commit hook: secret scan. Rejects commit on a hit.

## 6. Verification matrix (G1)

| Test | Expected |
|---|---|
| Clean clone on a second Mac | `make up` reaches healthy with no manual steps |
| Cold start | All four services healthy within the compose health window |
| `make reset` | Seed state byte-identical across two runs (compare evidence query output) |
| Restart app container | Persisted activation and billing evidence coherent |
| IoT success / failure / summarized billing | As §5.4 |
| Postman evidence collection | All saved calls pass |
| SQL evidence queries | Match UI and API |
| `make down` → `make up` | Named volume state preserved as documented |

## 7. Out of scope

CI pipelines; release automation beyond a Git tag; shared-package extraction; any change to domain rules, catalog, or billing policy; any fiber abstraction; presentation redesign.

## 8. Definition of Done

- Required First Output approved by Robert.
- G0A evidence delivered: manifest, diff, verification captures, Robert's approval of retirement.
- G1 verification matrix passes on a clean clone; `make verify` prints all-pass.
- No secret, laptop path, or identity in the tree; pre-commit hook active.
- `README_CONNECT_HQ.md` documents the six commands and the topology.
- Codex second-pass diff review returns no blocking finding.

## 9. Commit

- Branch: `feat/connecthq-standalone-beta`.
- One reviewed diff for Phase 0; one reviewed diff for Phase 1. No squash across the gate.
- Robert approves each diff in writing before commit.
- Tag `connecthq-v0.9.0-beta.1` on Robert's approval after G1. Tag message references this spec ID and the promotion manifest hash.
- No push, no PR, no issue creation, no roadmap or change-log edits without explicit approval.
