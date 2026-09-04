# Connect HQ — Engineering and Design Review

**Status:** Initial evidence-based review

**Date:** 2026-09-01

**Review target:** Current working tree after the Connect HQ demonstration

**Purpose:** Determine whether the application is ready to serve as a Guild
best-practice model and a portable AWS beta

## 1. Executive assessment

Connect HQ has a credible application skeleton and more engineering substance
than a disposable demo. It has strict API models, a consistent error envelope,
service-to-repository dependency direction, transactional repository support,
typed external contracts, deterministic logical seed data, separate memory and
PostgreSQL adapters, and 66 passing tests.

It is **not yet ready** to be described as a hardened Guild reference or a
solid AWS beta. The principal gaps are deployment packaging, authentication,
schema evolution and integrity, durable/idempotent external workflows,
operational health and observability, and internal module size/type discipline.

The recommended course is controlled hardening of the existing modular
monolith. A rewrite or microservice decomposition is not justified. The current
behavior should be protected with characterization tests while the deployment,
data, security, workflow, and module boundaries are improved in bounded work
packages.

## 2. Review basis

The review inspected the current working tree, including:

- `app/main.py`, `app/api.py`, `app/api_models.py`, and dependencies;
- domain, service, integration, and repository modules;
- PostgreSQL schema and runtime scripts;
- synthetic FlowOne and Legacy Billing services;
- automated tests and current Git state;
- the existing reusable-skeleton architecture document.

Verification on 2026-09-01:

```text
66 passed, 1 upstream Starlette TestClient deprecation warning
git diff --check: clean
```

The working tree contains substantial uncommitted application, UI, test,
Postman, SQL, and presentation changes. This review does not approve or commit
that diff. The baseline must be curated and reviewed before implementation
hardening begins.

## 3. Strengths to preserve

### 3.1 Clear dependency direction

`app/main.py` constructs repositories, services, and integration gateways and
injects them into the API router. Tests can replace these adapters. This is the
correct basic shape for a modular application.

### 3.2 Strict transport contracts

`app/api_models.py:11-14` trims strings and rejects undocumented fields.
Specific request models constrain identifiers, enumerations, lengths, and
formats. The application also has a consistent error envelope with a request
identifier in `app/main.py:92-135`.

### 3.3 Real external boundaries

FlowOne and Legacy Billing are accessed through gateway protocols and typed
contracts. The clients have explicit timeouts and translate transport failures
to application integration errors. The synthetic FlowOne service models
per-network-element outcomes and rollback evidence rather than returning a
single arbitrary success flag.

### 3.4 Transactional repository capability

`app/repositories/postgres.py:96-115` supplies an explicit transaction context,
and service operations use it for multi-row changes. SQL values are
parameterized, and identifier construction uses `psycopg.sql` rather than raw
string interpolation.

### 3.5 Identity and deterministic scenario design

Internal UUIDs and human-readable business numbers are distinct. Seed IDs are
deterministic, and both memory and PostgreSQL adapters implement the same
logical scenario.

### 3.6 Useful characterization coverage

The tests cover API validation, identity, account policy, activation success
and rollback, Legacy Billing submission, billing modes, reconciliation,
repository adapters, role-oriented pages, and end-to-end behavior. This is a
strong base for incremental hardening.

## 4. Findings

Priority definitions:

- **P0:** Blocks a safe, credible AWS/Guild beta.
- **P1:** Must be addressed for the application to serve as a best-practice
  reference, but can follow the first deployable skeleton.
- **P2:** Important maintainability or consistency improvement.

### F-01 — P0: The application is not packaged as a portable runtime

**Evidence**

- `docker-compose.postgres.yml` defines only PostgreSQL.
- `scripts/run-postgres-demo.sh:9-29` requires a host `.venv`, starts only the
  database in Docker, and binds Uvicorn to `127.0.0.1`.
- `scripts/run-integration-mocks.sh:9-20` also requires the host virtual
  environment and starts the mocks outside Docker.
- There is no application Dockerfile or complete Compose stack.

**Risk**

The runtime depends on one laptop's Python installation, filesystem, ports,
and process management. It cannot be reproduced reliably in Guild, ECS, or a
clean local machine.

**Required correction**

Build versioned application and integration images, add a complete Compose
stack, validate environment at startup, run as a non-root user, add image
health checks, and implement the single lifecycle command defined in the v0.9
specification. Prove the image locally before deploying the same digest to AWS.

### F-02 — P0: Demo headers are not an authentication boundary

**Evidence**

- `app/dependencies.py:20-43` trusts caller-provided `X-Demo-Role` and
  `X-Demo-Account-ID` values.
- Several account, subscription, activation, billing, evidence, provisioning,
  and compatibility reads or writes in `app/api.py` have no actor dependency.
- Destructive reset is protected only by the self-asserted admin header at
  `app/api.py:482-488`.

**Risk**

Any caller can impersonate an administrator or customer. Several operations
are unauthenticated. Public AWS exposure would permit data disclosure,
workflow execution, and potentially destructive reset.

**Required correction**

Introduce a trusted identity adapter for Guild and AWS, derive role/account
authorization from verified claims, inventory every route by access policy,
protect or disable reset and failure injection outside non-production, and add
authorization matrix tests. Demo headers may remain only behind an explicit
local-development mode that cannot be enabled accidentally in AWS.

### F-03 — P0: Database schema evolution and integrity are insufficient

**Evidence**

- `postgres/01_schema.sql` is reapplied as both initialization and change
  mechanism.
- The schema contains many identifier relationships but no foreign-key
  declarations and no state/domain check constraints.
- `scripts/run-postgres-demo.sh:15-18` reapplies the initialization file at
  startup rather than checking and applying ordered migrations.

**Risk**

Orphaned and contradictory rows can be inserted despite correct application
intent. Schema changes cannot be promoted, audited, or rolled back safely in a
persistent AWS database.

**Required correction**

Adopt Alembic or an equivalent ordered migration mechanism. Establish foreign
keys, check constraints, indexes, and deletion rules. Add migration tests for
empty database creation and upgrade from the frozen baseline. Use separate
application and migration credentials where feasible.

### F-04 — P0: External workflows are not durable or idempotent across restart

**Evidence**

- `app/services/provisioning.py:13-54` stores direct activation results in an
  in-process dictionary.
- `app/services/legacy_compatibility.py:13-50` stores compatibility results in
  another in-process dictionary.
- A restart or a second worker therefore cannot serve the corresponding GET.
- `app/services/ordering.py:236-373` performs FlowOne, database updates, and
  optional Legacy Billing submission in a long sequential workflow without a
  durable attempt/outbox record or client idempotency key.
- `app/services/ordering.py:350-355` catches every exception from the Legacy
  Billing path and embeds the raw exception text in a persisted user message.

**Risk**

A process failure after an external success but before local persistence can
cause an unknown outcome or duplicate request on retry. A batch may remain
`IN_PROGRESS` indefinitely. Multi-worker behavior is inconsistent, and raw
exception text may expose implementation detail.

**Required correction**

Persist integration operations and attempts. Record intent before the call,
use stable idempotency/correlation keys, record sanitized results, and add an
explicit recovery/retry state machine. Define the business semantics of
network-active/legacy-pending rather than relying on a broad exception catch.
Tests must cover restart and replay at each failure boundary.

### F-05 — P0: Health reporting is too shallow for orchestration

**Evidence**

- `app/api.py:45-52` reports `ok` without querying PostgreSQL, checking schema
  level, or validating required integration configuration.
- There is no separate liveness and readiness contract.

**Risk**

ECS or Guild may send traffic to a process that cannot access its database or
required services. A broken migration or configuration can appear healthy.

**Required correction**

Add cheap liveness and meaningful readiness endpoints. Readiness must verify a
database query, expected migration version, and required configuration. Use
these endpoints in Compose, ECS, and load-balancer health checks.

### F-06 — P1: Application services and repository contracts are too broad

**Evidence**

- `app/services/demo.py` is 1,036 lines and combines account setup, catalog
  views, subscription upload, billing, billing files, reconciliation, and
  statement assembly.
- `app/services/ordering.py` is 551 lines and combines resource allocation,
  subscription creation, orchestration, recovery decisions, evidence
  assembly, and lookup helpers.
- `app/repositories/protocols.py` exposes one 68-method repository surface.
- Domain state is passed primarily as mutable dictionaries and status strings.

**Risk**

Changes have a large blast radius, dependencies are hidden, and domain
invariants depend on call-site discipline. The architecture becomes difficult
to explain, type-check, test in isolation, or reuse as a Guild model.

**Required correction**

Split by bounded capability: account configuration, inventory/subscription,
activation workflow, billing, and evidence. Introduce small repository ports
or a unit of work that exposes them. Add typed domain/application records and
state enums at the hardened core. Keep the modular monolith; do not turn these
modules into network services.

### F-07 — P1: Synchronous PostgreSQL access blocks asynchronous workflows

**Evidence**

- `app/services/ordering.py:236-373` is asynchronous because it awaits external
  calls, but it performs synchronous repository queries and writes around
  those awaits.
- `app/repositories/postgres.py:102-123` creates synchronous psycopg
  connections and executes blocking operations.
- Each external request creates a new `httpx.AsyncClient` in
  `app/integrations/flowone.py:45-58` and
  `app/integrations/amdocs_middleware.py:48-57`.

**Risk**

Blocking database work can stall the event loop during concurrent activation
requests. Per-call database and HTTP connections add latency and resource
pressure. The current design will behave poorly under even moderate concurrent
use.

**Required correction**

Choose one consistent execution model: async PostgreSQL with a bounded pool,
or explicit worker/thread boundaries around synchronous units of work. Manage
HTTP clients through FastAPI lifespan and reuse connection pools. Add
concurrency and pool-exhaustion tests appropriate to beta scale.

### F-08 — P1: Observability stops at a response request ID

**Evidence**

- `app/main.py:92-97` creates and returns an `X-Request-ID`.
- There is no application logging configuration, structured event format,
  metric surface, trace propagation standard, or persisted general integration
  ledger.

**Risk**

AWS and Guild failures will require ad hoc reproduction and SQL exploration.
One activation cannot be followed consistently across ingress, application,
FlowOne, Legacy Billing, and database changes.

**Required correction**

Add structured logs, correlation propagation, sanitized integration attempt
records, core metrics, and a transaction-evidence query. Send container logs to
CloudWatch in AWS and use the same field names locally.

### F-09 — P1: Reproducibility and automated engineering gates are incomplete

**Evidence**

- `requirements.txt:1-6` uses broad compatible ranges and mixes runtime and
  test dependencies without a lock/constraints artifact.
- `pyproject.toml` configures only pytest.
- There is no repository-local CI definition for lint, types, migrations,
  image build, scanning, or clean-stack smoke tests.
- The current test warning indicates a pending TestClient compatibility change.

**Risk**

Two clean builds can resolve different packages. Style and type regressions are
reviewer-dependent. Container, migration, and dependency failures may be found
only during deployment.

**Required correction**

Adopt a reproducible dependency workflow, separate runtime/development groups,
and add CI gates for formatting, linting, types, tests, migration upgrade,
container build, vulnerability/secret scanning, Compose smoke, and diff
hygiene. Resolve or explicitly pin around the TestClient deprecation.

### F-10 — P1: Current tests do not prove the deployable system

**Evidence**

- The 66 tests provide good in-process behavior coverage.
- The runtime test script starts PostgreSQL but does not build and exercise a
  complete application-plus-integrations container stack.
- There is no browser automation for the critical workflow and no restart,
  replay, migration-upgrade, authenticated-ingress, or AWS deployment test.

**Risk**

The Python behavior can pass while packaging, service discovery, health,
authentication, migrations, static assets, or restart behavior is broken.

**Required correction**

Retain the fast suite and add a smaller set of high-value system tests: clean
Compose startup, migration, API smoke, browser workflow, external-call failure,
restart persistence, replay/idempotency, and an AWS post-deploy smoke test.

### F-11 — P1: Presentation and runtime assets are coupled to dated filesystem paths

**Evidence**

- `app/main.py:31-50` points application routes at dated slide and output
  filenames outside the application static tree.
- Some referenced presentation outputs are currently untracked working-tree
  files.

**Risk**

An image built from a clean commit can omit required assets or expose stale
dated artifacts. Application startup succeeds even though download routes may
fail later.

**Required correction**

Define an explicit packaged-assets manifest or move approved release assets
under one versioned static/artifact boundary. Validate required files at build
or startup and decouple the core API runtime from optional presentation files.

### F-12 — P1: Destructive reset needs an environment safety boundary

**Evidence**

- `app/repositories/postgres.py:174-203` truncates all application data and
  reseeds it.
- `app/api.py:482-488` exposes the reset through an API route protected only by
  the demo admin header.

**Risk**

An exposed or misconfigured AWS instance can lose all beta data. A script or
client can invoke reset against the wrong environment.

**Required correction**

Disable reset by default, require an explicit non-production capability,
surface the target environment, use an additional confirmation/token for CLI
operation, and deny the API route in public AWS mode unless a narrowly secured
administrative design is approved.

### F-13 — P2: Version and product naming are inconsistent

**Evidence**

- `app/main.py:82-89` reports Connect HQ version `1.0.0`.
- The current hardening target is v0.9 Beta, while several files retain WhAM,
  WDH, Nightjar, and dated presentation names.

**Risk**

Operators and API consumers cannot determine the real release identity.
Internal names leak into artifacts and complicate support.

**Required correction**

Create one build/release version source and return it in OpenAPI, health, image
labels, and logs. Preserve internal history where useful, but define Connect HQ
as the external product name and document remaining domain terminology.

### F-14 — P2: Optional storage adapters widen the beta support surface

**Evidence**

- `app/main.py:53-65` supports memory, PostgreSQL, and Snowflake runtime stores.
- PostgreSQL is already described as the local transactional system of record.

**Risk**

Every service and repository refactor must preserve three behavioral adapters,
including one that is not appropriate for the core transactional AWS path.

**Required correction**

Make PostgreSQL the required v0.9 runtime. Retain memory for fast tests. Decide
explicitly whether Snowflake is an optional demonstration adapter with its own
contract tests or outside the v0.9 operational support statement.

## 5. Recommended target design

The target is a modular monolith with five capability modules:

```text
accounts         customer, account, contract, billing policy
inventory        SIM/MDN resources and assignment
subscriptions    service instances and resource associations
activation       batch workflow, FlowOne, Legacy Billing compatibility
billing          charges, posting output, reconciliation
```

Cross-cutting modules provide:

```text
identity and clock
unit of work and repository ports
integration operation/outbox records
authentication and authorization
logging, metrics, health, and error policy
```

Each capability should own application commands/queries, typed domain state,
ports, and tests. PostgreSQL tables may remain in the existing schemas during
v0.9, but their ownership and foreign-key relationships must be explicit.

The frontend, API, and synthetic services remain deployable components, not
new domain microservices. This retains portability and learning value while
showing strong internal architecture.

## 6. Recommended remediation sequence

### Work package 0 — Baseline gate

- Curate the current dirty tree.
- Review and approve the exact diff.
- Preserve the 66-test baseline.
- Tag the accepted pre-hardening behavior.

### Work package 1 — Portable skeleton

- Application and integration Dockerfiles.
- Complete Compose stack.
- Runtime configuration validation.
- Single lifecycle command.
- Reproducible dependencies and clean-stack smoke test.

### Work package 2 — Migrations and constraints

- Migration baseline from the approved schema.
- Foreign keys, checks, indexes, and schema-version readiness.
- Empty-create and upgrade tests.

### Work package 3 — Durable activation workflow

- Integration operation ledger.
- State machine and recovery semantics.
- Idempotency keys and replay tests.
- Durable GET behavior across restart and multiple workers.

### Work package 4 — Security and observability

- Trusted identity adapter and route authorization matrix.
- Reset/failure-control isolation.
- Structured logs, correlation propagation, readiness, and metrics.
- Sanitized error policy.

### Work package 5 — Module refactoring

- Split the broad services and repository protocol by capability.
- Add typed core records and enums.
- Preserve API compatibility and behavior tests.

### Work package 6 — Guild and AWS release

- Guild always-on deployment and operating checks.
- AWS infrastructure as code, ECR/ECS/RDS/ALB/secrets/logging.
- External-client, restart, migration, and post-deploy smoke tests.
- v0.9 release review and tag.

## 7. Architecture-review gate

The design is suitable to become a Guild model if the P0 findings are closed
and the P1 findings are either closed or represented by explicit, time-bounded
decisions. An architect should be able to verify:

- clear capability and dependency boundaries;
- explicit transaction and external-side-effect semantics;
- enforced data integrity;
- trusted identity and least-privilege access;
- reproducible build, migration, deployment, and rollback paths;
- restart-safe and observable workflows;
- tests that cover both business behavior and the deployed system;
- a documented distinction between beta limitations and production claims.

The present code is a strong starting point for that result. It should not be
presented as having reached it until the evidence above exists.
