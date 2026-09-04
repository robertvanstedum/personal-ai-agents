# Connect HQ v0.9 Beta — High-Level Specification

**Status:** Draft for architecture and implementation review

**Date:** 2026-09-01

**Owners:** Robert (product and acceptance), Claude Code (primary implementation),
Codex (design and implementation review)

**Target:** A portable, continuously runnable Connect HQ beta in Guild and AWS

## 1. Purpose

Connect HQ v0.9 Beta turns the current working proof of concept into a small,
durable application that demonstrates Guild engineering practices. It must be
simple enough to understand and operate, but solid enough for an architect to
review as a credible reference implementation.

The beta has two equally important outcomes:

1. **A live application and API sandbox.** Connect HQ can run continuously in
   Guild and AWS, or be started, stopped, restarted, reset, and verified with a
   single control command. Another application can call its documented APIs.
2. **A hands-on API and database laboratory.** Robert can use the real system to
   develop and demonstrate practical skill with API contracts, HTTP behavior,
   PostgreSQL schemas, transactions, migrations, integration evidence, and
   operational diagnosis.

This specification is about product hardening. It is not a retrospective on a
presentation or interview.

## 2. Product statement

Connect HQ is a compact connectivity-service BSS/OSS application that manages
accounts, resources, subscriptions, activation, billing policy, charges,
billing output, and reconciliation. Synthetic FlowOne and Legacy Billing
services exercise real network and billing integration boundaries without
requiring carrier systems.

The current telecom scenario is the first domain implementation. The design
must preserve clear seams for later reuse, but v0.9 will not claim to be a
universal platform or extract an unproven generic framework.

## 3. v0.9 goals

### 3.1 Runtime and portability

- Build the Connect HQ application as a versioned OCI image.
- Build the synthetic integration service as a versioned OCI image.
- Run the local and Guild stack with Docker Compose.
- Run the same application image in AWS without source changes.
- Use environment configuration and injected secrets for every deployment.
- Require no host Python virtual environment for the normal runtime.
- Support an always-on deployment and an explicit start/stop operating mode.

### 3.2 Engineering quality

- Preserve a modular-monolith architecture with explicit domain, application,
  port, adapter, and HTTP-interface responsibilities.
- Protect data integrity in PostgreSQL with migrations, foreign keys, unique
  constraints, check constraints, and transactional application operations.
- Make externally visible commands idempotent or explicitly non-repeatable.
- Persist integration requests, outcomes, and correlation identifiers.
- Provide structured logs, health/readiness checks, and sufficient evidence to
  follow one business transaction end to end.
- Establish automated formatting, linting, type, test, migration, container,
  and dependency/security gates.

### 3.3 Learning and demonstration

- Keep Swagger/OpenAPI accurate and usable.
- Provide a maintained Postman collection and environment.
- Provide saved, readable PostgreSQL learning and diagnostic queries.
- Include guided exercises that connect API operations to their database
  records and external-integration outcomes.
- Demonstrate at least one call from a separate client application.

## 4. Non-goals for v0.9

- Carrier-grade throughput, availability, or disaster recovery.
- Real FlowOne or Legacy Billing credentials or connectivity.
- Multi-region deployment.
- Simultaneous AWS, Google Cloud, and Azure production support.
- Microservices decomposition of the application domain.
- A generic framework extracted before a second domain proves the abstraction.
- Production customer data or personally identifiable information.
- Direct public exposure of synthetic integration controls.

## 5. Primary use cases

The v0.9 acceptance scenario will exercise the same contracts through the UI
and public application API:

1. Create or inspect an enterprise account and its billing policy.
2. Assign available resources to the account.
3. Create and submit an activation batch.
4. Call synthetic FlowOne synchronously and persist the per-element outcome.
5. Submit or skip Legacy Billing compatibility according to account policy.
6. Run detailed or summarized billing.
7. Reconcile source charges to billing output.
8. Retrieve evidence through API and PostgreSQL queries.
9. Reset a non-production environment to a deterministic baseline.

## 6. Architecture principles

### 6.1 Modular monolith first

The application remains one deployable service for v0.9. Internal boundaries
must be visible in code:

```text
HTTP / UI interface
        |
        v
Application commands and queries
        |
        v
Domain policies and workflow state
        |
        v
Ports: repositories, integrations, clock, identifiers
        |
        v
Adapters: PostgreSQL, FlowOne HTTP/SOAP, Legacy Billing HTTP, test doubles
```

FastAPI and Pydantic belong at the interface boundary. Domain rules must not
depend on FastAPI, HTTP headers, SQL, or browser concerns. PostgreSQL and
external services remain replaceable adapters behind explicit ports.

### 6.2 Production-shaped, beta-sized

The beta may use one application instance and a modest AWS footprint, but it
must use the same engineering mechanisms expected in a larger system:

- immutable images;
- migration-controlled schemas;
- secret injection;
- least-privilege network and database access;
- idempotency and transaction boundaries;
- health, logs, metrics, and correlation IDs;
- repeatable infrastructure and deployment.

### 6.3 Business identifiers and internal identifiers

APIs intended for human operation should accept stable display identifiers
such as `ACCT-000100` where appropriate. Internal UUIDs remain the durable
primary keys and correlation references. Callers must not need to discover or
copy an opaque UUID for a routine account-level query.

### 6.4 One source of behavior

The UI, Swagger, Postman collection, external client example, and SQL evidence
must operate against the same application behavior. Demo-evidence views may
assemble persisted information, but they must not fabricate a second outcome
or bypass the actual workflow.

## 7. Target runtime topology

### 7.1 Local and Guild

```text
Browser / external client
          |
          v
Connect HQ application container
          |                 |
          v                 v
PostgreSQL container   Synthetic integrations container
```

Guild runs the versioned stack continuously with restart policies and health
checks. A developer can run the same stack locally. Persistent database data
is stored in a named volume; deterministic reset is an explicit administrative
operation, never an implicit startup side effect.

### 7.2 AWS reference deployment

The first AWS target is:

- Amazon ECR for immutable application and integration images;
- Amazon ECS on Fargate for the application task;
- an internal synthetic-integration container or service;
- an Application Load Balancer with TLS for the Connect HQ web/API boundary;
- Amazon RDS for PostgreSQL in private subnets;
- AWS Secrets Manager or SSM Parameter Store for secrets;
- CloudWatch Logs and metrics;
- infrastructure as code, preferably Terraform;
- security groups restricting database and integration access to the
  application runtime.

The synthetic service is not exposed publicly. The application API may be
internet-accessible only after authentication, TLS, reset-control isolation,
and basic abuse protections are in place.

## 8. Operations contract

One repository-owned command is the normal control surface:

```bash
./connecthq up
./connecthq down
./connecthq restart
./connecthq status
./connecthq logs
./connecthq reset
./connecthq migrate
./connecthq smoke
```

Required behavior:

- `up` is idempotent and waits for readiness.
- `down` stops services without deleting persistent data.
- `restart` preserves intended state.
- `status` reports application, database, migration, and integration health.
- `reset` requires explicit confirmation or a non-production override and
  recreates deterministic seed data.
- `migrate` applies versioned forward migrations and reports the schema level.
- `smoke` executes a small read/write/read verification through the API.
- commands return nonzero exit codes on failure.

AWS deployment and operations will use the same image and health/smoke
contracts even when the underlying control command invokes AWS tooling rather
than local Compose.

## 9. API requirements

- All application APIs remain versioned under `/api/v1`.
- OpenAPI is generated from the implemented routes.
- Request and response models reject undocumented input and document examples.
- Authentication establishes the actor; callers do not self-assert a trusted
  role with an arbitrary header in the AWS environment.
- Authorization is enforced at the application boundary for every protected
  account, admin, billing, evidence, and reset operation.
- Mutation endpoints accept an idempotency key where replay could create a
  duplicate business or external side effect.
- Errors use one documented envelope and do not expose stack traces, secrets,
  connection strings, or raw integration exceptions.
- Pagination and bounded result sizes are required for list endpoints that can
  grow.
- API compatibility tests protect the published beta contract.

## 10. Data and transaction requirements

- PostgreSQL is the authoritative v0.9 operational store.
- Schema changes are applied through ordered migrations; initialization SQL is
  not the long-term migration mechanism.
- Relationships and allowed states are enforced in both the domain and
  database where practical.
- Application transactions use an explicit unit-of-work boundary.
- External calls are not treated as part of a database transaction.
- Durable workflow state records intent before an external call and records
  its outcome afterward.
- Integration requests have stable idempotency/correlation keys.
- Accepted external work can be recovered after application restart without
  blindly issuing a duplicate request.
- Seed/reset behavior is deterministic apart from explicitly documented clock
  fields.
- Destructive reset is disabled or separately protected in any public AWS
  environment.

## 11. Integration requirements

- FlowOne and Legacy Billing are accessed only through typed gateway ports.
- Base URLs, timeouts, credentials, and TLS behavior are environment settings.
- HTTP clients are reused and closed through application lifecycle management.
- Retry policy distinguishes safe retries from non-idempotent operations.
- Timeouts, connection failures, contract failures, and business rejections
  produce distinct persisted outcomes.
- Every request records correlation ID, target, operation, attempt, start/end
  time, status, and sanitized response metadata.
- The synthetic services implement deterministic success, failure, reset, and
  health contracts suitable for tests.

## 12. Security baseline

- TLS at the AWS ingress.
- Authentication through an AWS-compatible OIDC/JWT boundary or an equivalent
  Guild identity provider.
- Role and account authorization derived from trusted identity claims.
- Secrets excluded from source, images, browser code, logs, and Postman files.
- Separate application and migration database roles where feasible.
- Private database and synthetic-integration network paths.
- Restricted CORS origins.
- Request size limits and basic rate limiting at ingress.
- Dependency, image, and secret scanning in CI.
- Reset and failure-injection controls unavailable to ordinary users.

## 13. Observability and evidence

- Preserve or generate one request/correlation ID at ingress.
- Carry the correlation through the application and external calls.
- Emit structured JSON logs with service, environment, operation, outcome,
  duration, and correlation fields.
- Expose separate liveness and readiness endpoints.
- Readiness verifies the database, migration level, and required integration
  configuration; it must not report healthy solely because the process runs.
- Publish minimal operational metrics: request count/latency/errors, external
  call outcomes, activation outcomes, and database-pool health.
- Provide a persisted transaction-evidence query/view for learning and
  diagnosis.

## 14. Quality gates

Every approved implementation package must pass:

1. formatting and lint checks;
2. static type checks on the hardened core;
3. unit and API-contract tests;
4. PostgreSQL integration and migration tests;
5. synthetic-integration contract tests;
6. container build and vulnerability scan;
7. clean Compose startup plus smoke test;
8. browser end-to-end test for the primary workflow;
9. `git diff --check` and review of the actual diff;
10. Robert's explicit approval before commit or promotion.

Dependencies will be resolved through a reproducible lock or constraints file.
CI must run from a clean checkout rather than relying on an existing local
virtual environment or database volume.

## 15. Robert's API and PostgreSQL learning track

The beta includes exercises using the deployed application:

1. Call a read API through Swagger, Postman, and `curl`.
2. Submit an activation and interpret HTTP status, response schema, and IDs.
3. Follow one correlation ID through logs and integration evidence.
4. Query the associated account, batch, item, subscription, resource, and
   FlowOne rows in PostgreSQL.
5. Change summarized-billing policy and verify the audit and persisted state.
6. Add one small GET endpoint with a response model and contract test.
7. Build a small external client that calls Connect HQ.
8. Apply one schema migration and validate upgrade and restart behavior.
9. Trigger a controlled external failure and diagnose its recovery state.
10. Explain one transaction boundary and one idempotency decision from the
    implementation.

## 16. Milestones

### M0 — Baseline and review gate

- Curate and approve the current working diff.
- Preserve 66 passing tests as the characterization baseline.
- Record the architecture/design findings and approved remediation scope.

### M1 — Reproducible runtime

- Add images, Compose stack, environment validation, lifecycle command, pinned
  dependencies, and clean-checkout smoke test.

### M2 — Durable application foundation

- Add migrations and database constraints.
- Persist integration operations and correlation evidence.
- Add idempotency and recoverable workflow state for activation.
- Separate liveness and readiness.

### M3 — Security and observability

- Replace demo-header trust at the deployable boundary.
- Protect admin/reset controls.
- Add structured logging, metrics, sanitized errors, and client lifecycle.

### M4 — Guild beta

- Deploy the approved versioned stack in Guild.
- Run restart, persistence, reset, smoke, failure, and external-client checks.
- Complete the first API/database learning walkthrough.

### M5 — AWS beta

- Provision the reference AWS environment from infrastructure as code.
- Deploy the same application image with RDS and private integrations.
- Validate TLS, identity, migrations, restart/redeployment, logs, smoke tests,
  and external client access.

## 17. Definition of done for v0.9 Beta

Connect HQ v0.9 Beta is complete when:

- an approved commit and immutable image identify the release;
- the complete stack starts from a clean checkout with one control command;
- Guild can keep it continuously running and restart it without data loss;
- the same application image runs solidly in AWS with no source changes;
- another application successfully calls a protected, documented API;
- migrations can create and upgrade an empty PostgreSQL database;
- one activation can be traced through API, workflow, FlowOne, optional Legacy
  Billing, and PostgreSQL evidence;
- replay and restart tests demonstrate the approved idempotency behavior;
- the primary workflow passes automated API, database, integration, container,
  and browser tests;
- critical and high findings in the engineering review are closed or accepted
  explicitly with rationale;
- Robert can independently perform the core API and PostgreSQL walkthrough;
- Claude Code implementation and Codex second review are complete, and Robert
  has approved the actual release diff.

## 18. Open decisions for the implementation gate

- Guild runtime interface and target host/container conventions.
- AWS account, region, DNS name, certificate, and identity provider.
- Whether the AWS beta is continuously running or scheduled during its first
  cost-control period.
- Whether Snowflake remains in the v0.9 runtime matrix or is retained only as a
  separately tested optional adapter.
- The first external client application used for the cross-application API
  acceptance test.
