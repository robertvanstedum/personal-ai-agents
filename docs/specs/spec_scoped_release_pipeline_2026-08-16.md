# Specification: Scoped Document and Domain Release Pipeline

**Registered:** Build queue #151
**Date:** 2026-08-16
**Status:** Implemented and verified in dev; production review and acceptance pending
**Owner:** Robert
**Scope:** GitHub Actions release classification, production document sync, and full-deployment safety

## Purpose

Publishing a document or changing one domain must not rebuild every image or
restart unrelated healthy containers. A German release must not interrupt COS;
a COS release must not interrupt German or Portuguese. Shared changes still
receive the complete conservative deployment when ownership is ambiguous.

This capability also hardens full deployments after the COS Agent A beta rollout
showed that a valid EC2 deployment can exceed the workflow's ten-minute polling
window. A CI timeout must not be mistaken for a failed deployment while an SSM
command is still running.

## Release classes

### Document-only release

A push is document-only when every changed path is one of:

- the four maintained root Markdown documents and their generated PDFs;
- `docs/**`;
- `data/guild/build_queue.json`; or
- the document renderer, sync script, and regression tests that exclusively
  govern document publication.

The deployment workflow, release classifier, and host deployment script are not
document-only. A change to release machinery deliberately selects one full
deployment so the host receives the matching runtime script before any later
domain-scoped release can call it. This conservative bootstrap prevents a
successful docs sync from leaving CI ahead of the production host.

The workflow runs the complete regression and documentation validation suite,
then calls `scripts/sync_docs.sh`. The sync publishes `docs/design/`,
`docs/specs/`, and the build queue to their host-mounted production paths. Root
portfolio documents and PDFs publish through GitHub; they are not copied over a
different file under `docs/` merely to make them appear in the Guild viewer.

The document-only path must not build or push images, run Docker Compose, prune
images, or restart containers.

### Domain-scoped release

Repository paths map to explicit production services, including real coupling:

| Changed area | Selected services |
|---|---|
| `domains/german/**` | `german`, `system-bot` |
| `domains/portuguese/**` | `portuguese` |
| `domains/curator/**`, `config/curator/**` | `curator`, `system-bot` |
| `domains/cos/**` | `cos-bot`, `cos-scheduler` |
| `minimoi_portal/**` | `portal` |
| `services/model_gateway/**` | `model-gateway`, `cos-bot`, `cos-scheduler` |
| `docker/cos-agent-a/**` | `cos-agent-a` |
| `domains/guild/**` | `portal`, `cos-bot`, `cos-scheduler` |
| shared realtime voice | German, Portuguese, COS scheduler |
| shared Telegram code | Curator, system bot, COS bot |

Dockerfile and per-service requirement changes map to the same owning service.
The classifier unions mappings for a mixed-domain commit, builds only those
immutable images, pulls only those services, and recreates them with
`--no-deps`. Unselected containers keep their existing image and start time.

### Full application release

Shared core/utilities, Compose topology, or any unrecognized path selects every
application service. This is intentionally conservative: uncertain dependency
ownership costs a full deployment rather than creating a partial inconsistent
release. A mixed document-and-domain commit uses the domain scope; a mixed set of
known domains uses their union.

A manual workflow dispatch remains an explicit full application deployment.

## Full-deployment safety

1. Build and pull only the classified immutable image set before removing unused images.
2. Start the services and validate image identity, container health, and HTTP
   health endpoints.
3. Prune only images that remain unused after the healthy release is running.
4. Allow up to 30 minutes for the SSM command and workflow poll. A terminal SSM
   failure still fails immediately and prints its error output.
5. Preserve all host-mounted state, the PostgreSQL container, and every
   unselected application container.
6. Keep classification in a small application-owned Python module with unit
   tests; do not hide dependency policy inside a long workflow expression.

## Data rule

This release mechanism does not migrate application state. Consistent with the
COS memory follow-up, durable domain records remain text/JSON first and may be
projected into PostgreSQL second unless an explicit design decision says
otherwise.

## Acceptance criteria

- a document-only commit passes tests and synchronizes served documents without
  an ECR build or container restart;
- a German-only change builds/restarts German and its coupled system bot, but not
  COS, Portuguese, Curator, or portal;
- a COS-only change builds/restarts COS services, but not either language domain;
- a shared voice change selects all known realtime-voice consumers;
- an unknown runtime path falls back to the full immutable-image deployment;
- a mixed known-domain commit deploys the union and includes the doc sync;
- a manual dispatch follows the full deployment;
- image pruning occurs only after service health checks;
- the full deploy poll allows 30 minutes;
- automated tests protect dependency classification, unknown-path fallback,
  scoped `--no-deps` recreation, image identity, health, and command ordering;
- after the release machinery has been bootstrapped through a full deployment,
  the first subsequent document-only production run is checked in GitHub Actions
  and the served new specification is opened from the production Guild route.

## Rollback

Revert the workflow, classifier, scoped deploy script, and sync-script commit.
This restores the prior always-full deployment behavior; it does not alter
running containers or persisted data by itself.
