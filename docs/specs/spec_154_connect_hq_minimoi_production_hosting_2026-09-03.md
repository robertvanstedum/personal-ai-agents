# Spec #154: Connect HQ on mini-moi Production Hosting

**Version:** v0.9 design baseline  
**Date:** 2026-09-03  
**Status:** Proposed production-change spec; implementation not yet authorized  
**Decision owner:** Robert  
**Parent capability:** Guild / Prototype Lab  
**First hosted release:** `connecthq-v0.9.0-beta.1`  
**Production URL:** `https://minimoi.ai/app/connecthq`

## 1. Executive decision

Host the approved Connect HQ standalone beta on the existing mini-moi AWS
environment. Connect HQ is governed and presented by **Guild / Prototype Lab**,
but it runs as a separately bounded application stack rather than as code inside
the Guild web process.

The initial production release reuses the existing EC2, Docker Compose, ECR,
SSM, portal authentication, and deployment mechanisms. It does not introduce
ECS, RDS, a second public site, or a second production deployment platform.

Connect HQ receives:

- a Guild reference-demo entry with release and health information;
- an authenticated launch link at `/app/connecthq`;
- an independently versioned application image;
- a private synthetic-integration service;
- a private, dedicated PostgreSQL database and volume; and
- a release-specific deployment path that normal mini-moi releases do not
  silently advance.

The first AWS release is **owner-only**. Guest or interviewer access is a later,
separately reviewed change.

### 1.1 Guild documentation and implementation tracking

This specification is the durable Guild design record for the production
change. Because it lives under `docs/specs/`, it will appear in
**Guild → Build → Docs** after it is reviewed, committed, and synchronized to
AWS.

A GitHub issue or Guild build-queue item may be created after approval to track
the implementation. That ticket must link to this specification and summarize
status, ownership, and acceptance evidence; it must not become a second copy of
the design. After deployment, the Guild Reference Demo entry described below
becomes the operational record and launch point.

## 2. Relationship to the standalone beta

This spec begins only after the isolated standalone candidate passes its final
review and Robert approves its exact promotion diff.

The standalone package proves that Connect HQ can start, stop, reset, report
status, run smoke tests, and preserve evidence without a host Python virtual
environment. This production spec adds the mini-moi hosting adapter around that
approved artifact. It must not reopen the standalone refactor or add Fiber,
Salesforce, Zuora, payment, Snowflake, or presentation-export scope.

The production adapter should be a small, separately reviewable change so the
portable standalone release remains useful outside mini-moi.

## 3. Product placement and user experience

### 3.1 Organizational home

Connect HQ belongs to:

`Guild → Improve → Prototype Lab → Reference Demos → Connect HQ`

The first implementation replaces the current Improve-page planning placeholder
with a restrained **Prototype Lab** section and one Connect HQ reference-demo
card. It does not build the full Prototype Lab library UI. The card displays at
minimum:

- product name: Connect HQ;
- classification: Reference Demo;
- scenario: Enterprise IoT activation and summarized billing;
- environment: AWS demo;
- release: `connecthq-v0.9.0-beta.1`;
- health: available or unavailable; and
- action: **Launch Connect HQ**.

Guild Improve is the discovery and launch home. Guild Build retains the spec,
implementation status, and review trail. The launch opens the standalone
application; Connect HQ is not rendered as a Guild template and does not share
Guild's application database.

### 3.2 Navigation

Connect HQ does not initially become a permanent top-level workspace beside
Curator, Mein Deutsch, Meu Português, Guild, and Chief of Staff. The canonical
launch is from Guild Improve. A top-level navigation entry can be considered
after the Prototype Lab contains more than one maintained reference demo.

### 3.3 Canonical routes

| Purpose | Authenticated URL |
|---|---|
| Launch page | `https://minimoi.ai/app/connecthq` |
| Presentation | `https://minimoi.ai/app/connecthq/presentation` |
| Application Swagger | `https://minimoi.ai/app/connecthq/docs` |
| Admin/workbench | `https://minimoi.ai/app/connecthq/admin` |

All current Connect HQ pages and API routes must remain reachable under the
same `/app/connecthq` prefix. The synthetic integration Swagger, integration
endpoints, WSDL, and PostgreSQL are not public.

## 4. Access and identity boundary

### 4.1 Initial policy

- Signed-out visitors are redirected to the mini-moi login.
- Only the mini-moi owner can open `/app/connecthq` in the first release.
- The owner can use all seeded customer and operator demonstration views.
- Reset, configuration changes, activation, bill runs, Swagger execution, and
  other mutations remain owner-only.
- No anonymous, guest, or share-link access is included.

This preserves the fastest safe path to an always-available demonstration.
Interviewer guest accounts and read-only sharing require a later access spec,
including a decision about whether guests may mutate a shared demo dataset.

### 4.2 Future external prototype access

External access to a prototype must not grant access to Guild, its Build area,
its documents, or other mini-moi domains. If Connect HQ or a later prototype
needs to be shared with interviewers, clients, or reviewers, treat that as a
separate publishing capability with its own entry point and access policy.

The preferred direction to evaluate is a dedicated demonstration site or
hostname, such as `demos.minimoi.ai`, which can route to approved Prototype Lab
releases without exposing the personal mini-moi portal. It may reuse the same
frozen application images and AWS host initially, but it requires independently
reviewed authentication, audience grants, mutation rules, data/reset isolation,
expiration, logging, and revocation.

This external-demo gateway is deferred. It is not implemented by granting an
external user a broad mini-moi or Guild account.

### 4.3 Trusted identity adapter

The current standalone application intentionally uses `X-Demo-Role` and
`X-Demo-Account-ID` for local demonstration personas. Those headers are not a
production authentication mechanism because a browser can supply them.

Production hosting therefore has two explicit modes:

| Mode | Purpose | Accepted identity |
|---|---|---|
| `local_demo` | Standalone laptop/demo package | Existing `X-Demo-*` behavior |
| `minimoi_proxy` | AWS behind the portal | Verified `X-Minimoi-*` identity supplied by the portal |

In `minimoi_proxy` mode:

1. The portal authenticates the session and applies the owner-only route rule.
2. The portal removes any client-supplied `X-Minimoi-*` and `X-Demo-*` headers.
3. The portal adds its verified `X-Minimoi-*` identity headers.
4. Connect HQ maps the verified owner identity to its administrator and seeded
   demo-persona capabilities.
5. Connect HQ ignores or rejects browser-supplied demo-role headers.

The application port is bound to EC2 loopback and is never reachable directly
from the internet. The trusted proxy behavior must be covered by tests; merely
showing the login page is not sufficient evidence.

## 5. Prefix-aware application routing

The existing Connect HQ pages use absolute browser paths such as `/static`,
`/presentation`, and `/api/v1`. The shared mini-moi proxy rewrites many HTML,
CSS, and JavaScript URLs, but Connect HQ also declares an absolute JavaScript
API base (`/api/v1`) that the generic rewrite layer does not safely transform.

Consequently, setting FastAPI `root_path` alone is not an accepted solution.
The hosted application must have one tested base-path contract:

- browser navigation, assets, API calls, redirects, downloads, Swagger, and
  OpenAPI all resolve beneath `/app/connecthq`;
- the standalone local URLs remain unchanged;
- no Connect HQ call falls through to mini-moi's existing top-level `/api/*`
  passthrough; and
- no Connect HQ-specific exception is added to a broad top-level catch-all.

The implementation may combine FastAPI `root_path`, a runtime base-path value,
and the existing portal proxy. The acceptance tests, rather than a particular
one-line framework setting, determine whether the contract is satisfied.

## 6. AWS runtime topology

```text
Internet
   |
Cloudflare / existing mini-moi entry point
   |
mini-moi portal (login, authorization, trusted identity)
   |
   +-- /app/connecthq/* --> Connect HQ app :8095
                                  |
                                  +--> synthetic integrations :8096
                                  |
                                  +--> Connect HQ PostgreSQL :5432
```

Three services are deployed:

1. **Connect HQ app** — the only Connect HQ service proxied by the portal;
2. **synthetic integrations** — FlowOne and Legacy Billing/Amdocs-compatible
   simulations, reachable only on the Docker network; and
3. **Connect HQ PostgreSQL** — dedicated database and persistent volume,
   reachable only on the Docker network.

Production requirements:

- App port 8095 binds only to `127.0.0.1` on EC2.
- Integration port 8096 is exposed only between containers; no host port.
- PostgreSQL port 5432 is exposed only between containers; no host port.
- All services use health checks and `restart: unless-stopped`.
- Connect HQ uses its own database, user, password, schema, and volume.
- Reset operations can affect only the Connect HQ database.
- The application and integrations may use the same immutable runtime image
  with different commands, as in the standalone package.

The existing shared `postgres-ai-agents` container is not used by Connect HQ.

## 7. Secrets and data

- Generate a new AWS-only PostgreSQL password; do not reuse the published local
  demo default.
- Store the credential under the established `/minimoi/production/` SSM
  parameter hierarchy and materialize it into `/opt/minimoi/.env` through the
  existing secret-sync mechanism.
- No secret value is committed, printed in CI output, or returned by a health
  endpoint.
- Only synthetic demo data is hosted.
- The PostgreSQL volume persists across container restarts and normal mini-moi
  deployments.
- `reset` deterministically restores the approved seed state without dropping
  or modifying any other mini-moi data.

Backup automation is not required for the first beta because the data is
deterministic and synthetic. The seed and reset procedure are the recovery
mechanism. Volume snapshotting may be added later if the demo begins retaining
valuable run history.

## 8. Image registry, release, and deployment

### 8.1 Registry

Use a dedicated ECR repository:

`minimoi/connecthq`

Extend the deployment principal only with the minimum actions required for that
repository. Reusing another component's repository with an unrelated tag prefix
is not the intended long-term identity for this maintained reference demo.

### 8.2 Version pinning

The production Compose definition uses a Connect HQ-specific variable:

`CONNECTHQ_IMAGE_TAG=connecthq-v0.9.0-beta.1`

It must not inherit `MINIMOI_IMAGE_TAG`. A normal push-to-main deployment may
refresh other mini-moi images but must leave the approved Connect HQ image and
data volume unchanged.

The underlying Python base image and Python dependencies remain pinned as
approved by the standalone-beta review.

### 8.3 CI/CD isolation

Connect HQ receives a release workflow triggered only by:

- an approved `connecthq-v*` Git tag; or
- an explicit manual dispatch for recovery of that exact approved tag.

The workflow:

1. checks out the tagged source;
2. runs the standalone verification suite;
3. builds the Connect HQ runtime image;
4. pushes the immutable tag to `minimoi/connecthq`;
5. confirms the image digest;
6. deploys only the Connect HQ services through SSM;
7. waits for app, integration, and database health; and
8. runs authenticated production smoke checks without resetting data.

The existing mini-moi deployment must not rebuild Connect HQ on unrelated main
branch changes. The Connect HQ deployment must not restart or replace unrelated
mini-moi services.

Before implementation, the builder must document the safe first-release
sequencing so the image exists in ECR before an active Compose service attempts
to pull it.

## 9. Required code/configuration surfaces

The reviewed implementation is expected to touch only the minimum surfaces:

- promoted Connect HQ source for base-path and trusted-identity adaptation;
- mini-moi portal configuration and owner-only proxy routes;
- a restrained Guild Reference Demo entry;
- production Compose configuration for the three isolated services;
- SSM secret synchronization for the Connect HQ database credential;
- a Connect HQ-specific build/deploy workflow; and
- automated portal, identity, route-prefix, deployment-isolation, and smoke
  tests.

Changes to Fiber behavior, billing calculations, presentation content, general
Guild information architecture, other domains, or shared databases are outside
scope.

## 10. Acceptance criteria

### 10.1 Standalone release gate

- Robert approves the reviewed standalone-candidate diff.
- The candidate is promoted to
  `prototype-lab/projects/project-connect-hq/` without local/runtime files.
- The standalone verification suite passes from the tracked location.
- Git tag `connecthq-v0.9.0-beta.1` identifies the exact approved source.

### 10.2 Portal and access

- Signed-out access redirects to login.
- A non-owner authenticated account receives a denial.
- The owner reaches the Connect HQ launch page from Guild Improve.
- Client-supplied `X-Minimoi-*` and `X-Demo-*` headers cannot elevate access.
- Direct public access to ports 8095, 8096, and 5432 is unavailable.

### 10.3 End-to-end route-prefix checks

Through `https://minimoi.ai/app/connecthq`, the owner can:

- open the launch page and presentation;
- load all CSS, JavaScript, images, and slide assets;
- navigate customer and operator views;
- open application Swagger and retrieve OpenAPI;
- reset the demo;
- activate the approved seeded SIM scenario;
- change summarized-billing mode;
- execute a bill run and retrieve its evidence; and
- inspect API activity without any request escaping to mini-moi's unrelated
  `/api/*` routes.

### 10.4 Persistence and isolation

- App-only restart retains the last approved demo state.
- Full Connect HQ stop/start retains state.
- Deterministic reset restores the seed state.
- A normal mini-moi deployment leaves the Connect HQ image, containers, and
  data unchanged except where Compose must re-evaluate an unchanged definition.
- A Connect HQ deployment leaves all unrelated mini-moi containers healthy and
  on their prior image tags.

### 10.5 Evidence

Retain:

- reviewed production diff;
- CI run URL and image digest;
- redacted SSM/Compose configuration proof;
- container and health output;
- authenticated route and workflow results;
- isolation checks for unrelated services;
- rollback result; and
- known gaps clearly labeled as not proven.

## 11. Rollback

Rollback does not delete data.

1. Restore the prior portal and mini-moi deployment image tags.
2. Stop the Connect HQ app and integration services.
3. Leave the Connect HQ PostgreSQL volume intact.
4. Remove or disable the Guild launch entry and `/app/connecthq` route if the
   backend is intentionally unavailable.
5. Confirm every pre-existing mini-moi service remains healthy.

The previous approved Connect HQ image remains in ECR. A later redeployment may
reattach the retained volume or reset from the deterministic seed.

## 12. Explicit non-goals

- ECS, RDS, ALB, Terraform, Kubernetes, or a separate AWS account;
- public integration Swagger, WSDL, database, or raw container ports;
- anonymous or guest demo access;
- multi-tenant production identity;
- production customer data;
- Snowflake activation persistence;
- Fiber, Salesforce, Zuora, or payment integrations;
- PDF/PPTX presentation export;
- redesign of the current Connect HQ demo; or
- a full Prototype Lab management UI.

## 13. Delivery gates

| Gate | Decision | Owner |
|---|---|---|
| P0 | Standalone revision passes independent review | Codex review; Robert decides |
| P1 | Promotion diff into tracked project is approved | Robert |
| P2 | This hosting spec and implementation plan are approved | Robert |
| P3 | Production adapter diff and tests are independently reviewed | Non-builder reviewer |
| P4 | Exact production diff and release tag are approved | Robert |
| P5 | AWS deploy and smoke evidence pass | Implementer + reviewer |
| P6 | Connect HQ declared an available Guild reference demo | Robert |

No production write, Git tag, merge, ECR/IAM/SSM change, or AWS deployment is
authorized merely by the existence of this specification.
