# Connect HQ Demo Program Brief

**Version:** 0.4 design candidate  
**Date:** 2026-09-02  
**Status:** Review required; no implementation authority  
**Decision owner:** Robert

**Current authorization:** An isolated, sanitized standalone-beta candidate may
be built by Claude Code and reviewed by Codex. Nothing may be copied into the
tracked project, committed, pushed, tagged, or released without Robert's review
of the resulting diff and evidence.

## Outcome

Create a reusable demonstration asset in two deliberate releases:

0. Promote the existing prototype from the ignored working area into this
   tracked project directory and prove it runs here unchanged.
1. Formalize the promoted Connect HQ IoT prototype as a stable standalone beta.
2. Start from that tagged release and build a complete fiber demonstration with
   an optional real Salesforce front end. Use Zuora as a serious billing and
   order-to-cash learning reference without making tenant access a prerequisite.

The second release is the first empirical test of the reuse boundary. Shared
components are extracted only when the fiber scenario demonstrates a real need.

Connect HQ keeps its product identity, but its responsibility changes in the
fiber scenario. The IoT demonstration presents a condensed full-stack BSS/OSS;
the fiber demonstration positions Connect HQ as the orchestration and
operational-control layer between Salesforce, Zuora, payments, and the access
network. The exact ownership, projection, command, event, and evidence boundary
must be approved in `design/CONNECT_HQ_FIBER_ROLE_BOUNDARY.md` before fiber code
is written.

## Workstream 0 — Promote the working prototype

Move the current Project Nightjar/Connect HQ implementation out of `_working`
and into `prototype-lab/projects/project-connect-hq/`, which becomes the single
tracked project home for code and artifacts.

The move must be performed as a controlled promotion rather than a destructive
first action:

- inventory and checksum the current source;
- copy only commit-safe project material;
- exclude virtual environments, caches, secrets, runtime databases, and
  unsanitized evidence;
- resolve collisions with the new project-control documents deliberately;
- run the application from the new directory using its current architecture;
- verify the UI, API, PostgreSQL connection, integration mocks, tests, reset,
  presentation, and evidence queries; and
- remove or archive the old ignored copy only after Robert reviews the result.

No portability refactor is mixed into the initial run-from-new-location proof.
This gives failures a narrow cause and establishes the tracked source of truth.

## Workstream 1 — Standalone demo formalization

Turn the current prototype into a known-good product artifact:

- containerized application, PostgreSQL, and synthetic integrations;
- single-command start, stop, reset, status, and verification;
- deterministic synthetic seed data;
- stable IoT activation and summarized-billing scenarios;
- automated smoke and end-to-end tests;
- portable configuration with no laptop-specific paths;
- safe Postman, Swagger, SQL, runbook, and presentation artifacts; and
- an approved GitHub release tag.

This workstream changes packaging and reliability first. It must not quietly
redesign the domain or introduce the fiber scenario.

## Workstream 2 — Fiber back-office IT research

Build practical knowledge of the systems and controls that turn a commercial
fiber order into installed service and trustworthy recurring billing.

The focus is BSS/OSS behavior rather than general optical-network education:

- customer, account, billing account, and service location;
- address qualification and serviceability;
- product catalog, offer, price, quote, and product order;
- product-to-service-to-resource decomposition;
- service orders, resource orders, work orders, and orchestration;
- field installation versus remote activation at an existing ONT;
- ONT registration, optical status, speed-profile provisioning, and testing;
- service inventory and activation completion;
- billing-start triggers, recurring speed-tier charges, proration, and discounts;
- fallout, jeopardy, retries, remediation, and customer communication; and
- reconciliation among commercial order, network service, subscription, and billing.

Robert's utility service-order experience is a primary reasoning asset. The
research must explicitly map premise, service point, field work, energization,
and billing commencement to their fiber equivalents.

## Workstream 3 — Zuora and Salesforce learning

This workstream has two coordinated tracks. Zuora covers subscription order
management, billing, payments, collections, and the downstream financial
boundary. Salesforce covers CRM, commercial catalog, quote, and order-entry
concepts. The same synthetic fiber offers and lifecycle events should be used
in both tracks so Robert learns the system boundaries rather than two isolated
products.

For the near-term Google interview, Track 3A is P0 and receives most of the
research and rehearsal time. Track 3B is useful upstream context but is secondary.

### Track 3A — Zuora fiber order-to-cash

Use current Zuora Product Documentation, Developer Center tutorials and API
references, Zuora University public material, and relevant communications or
broadband customer examples to build a production-shaped view of:

- product catalog, rate plans, charges, effective dates, and pricing models;
- customer and billing accounts, subscriptions, orders, order actions, and amendments;
- activation and service dates, invoice schedules, bill runs, invoice generation,
  credit/debit memos, proration, taxation, and account balances;
- payment methods, payment runs, refunds, failed payments, dunning, and collections;
- usage ingestion and rating, while keeping the base fiber product a recurring
  speed-tier charge rather than consumption billing;
- workflows, events, notifications, OAuth, REST APIs, Data Query, operational
  monitoring, and integration controls; and
- finance, revenue recognition, general-ledger, reporting, and reconciliation boundaries.

The research must identify how Robert can obtain legitimate hands-on access.
If a test drive or sandbox is unavailable before the interview, complete the
official tutorials, object and process maps, API/Postman walkthroughs, and a
fiber lifecycle mapping without claiming tenant-level experience.

Zuora remains outside fiber network fulfillment. Connect HQ owns the service
order and network state; the learning task must make the event and control
boundary between usable service and billable subscription explicit.

### Track 3B — Salesforce catalog and CPQ

Use a personal Salesforce Developer Edition when available. Hands-on learning
starts with the core platform and does not depend on licensed Salesforce Billing
or Communications Cloud features.

The learning path covers:

- Account, Contact, Opportunity, Product, Price Book, Quote, Order, Order Item,
  and Asset;
- custom fields, relationships, record types, and external IDs;
- users, profiles, permission sets, OAuth, and External Client Apps;
- Salesforce Flow, SOQL, REST, Composite API, and Postman;
- core catalog and pricing versus Salesforce CPQ, Industries CPQ, and current
  Revenue Management terminology; and
- public Communications Cloud concepts for catalog-driven product, service,
  resource, decomposition, orchestration, and fulfillment.

The learning artifact must distinguish what Robert exercised directly from what
was learned conceptually from public documentation.

## Workstream 4 — Fiber Salesforce, Zuora, and payments PoC

Build a complete, synthetic, vendor-neutral scenario:

1. A new customer orders fiber at a qualified location with an existing ONT.
2. Salesforce captures the account, selected speed tier, price, and order.
3. Connect HQ accepts and validates the product order.
4. Connect HQ creates the orchestration, service-order, correlation, integration-
   evidence, and fallout state required to control fulfillment; it does not
   become the master for Salesforce customer/order or Zuora billing records.
5. The order is decomposed into network-activation work and Zuora billing
   prerequisites, with explicit dependencies and completion policy.
6. A synthetic access-network API registers the ONT, confirms optical light,
   provisions the speed profile, and returns a service-test result.
7. Connect HQ calls a synthetic Zuora service that implements the approved
   subset of Zuora's published API contract and responses.
8. The synthetic Zuora service creates or advances the billing account, order,
   subscription, and billing state only after the approved completion condition.
9. Synthetic Zuora calls a contract-aligned synthetic payment gateway for the
   approved payment scenario and emits normalized payment or collection events.
10. Connect HQ applies only policy-authorized service consequences and returns
    fulfillment state to Salesforce.
11. The same correlation ID is visible in Salesforce, Connect HQ, the synthetic
    access-network service, synthetic Zuora, the synthetic payment gateway,
    integration evidence, and PostgreSQL.

The synthetic Zuora service is an official component of the fiber demo. Its
Swagger UI, Postman requests, request validation, and response shapes must be
derived from a pinned version of Zuora's published OpenAPI material. It must be
prominently labeled `Synthetic Zuora — contract-aligned; no live tenant`.
Connect HQ calls it through a Zuora adapter boundary so a future sandbox adapter
can replace it without changing order orchestration or domain logic.

Payments are also an official fiber-demo concern. The portable default includes
one contract-aligned synthetic gateway behind a provider-neutral payment port,
with an Adyen-shaped profile first and Stripe retained as a later profile or
hands-on fallback. Browser-hosted payment components own payment-detail capture;
neither Connect HQ nor its logs or database may receive PAN data. Ledger-affecting
charges and refunds flow through the Zuora boundary so billing and accounts
receivable remain coherent. Connect HQ consumes normalized payment/dunning events
and coordinates service actions only when an approved policy explicitly requests them.

Required exception paths:

- ONT or optical activation failure holds the order and prevents billing start.
- Network service active without a billing-start record creates a reconciliation exception.
- Duplicate order submission is idempotent.
- A synthetic Zuora timeout or rejected request remains retryable and does not
  erase the completed network outcome.
- Gateway outage is distinguished from a customer decline, retries are
  idempotent, and duplicate asynchronous events produce one state transition.
- Payment failure does not automatically suspend service; an explicit,
  auditable dunning-policy event is required.

## Sequence and dependency

- Workstream 0 must pass before Workstream 1 changes packaging or runtime design.
- Workstream 1 must reach an approved tag before Workstream 4 changes code.
- Workstreams 2 and 3 can proceed while Workstream 1 is being implemented.
- Workstream 4 begins only after the baseline tag, initial domain design, and
  Connect HQ fiber-role boundary are approved.
- The IoT scenario must continue to pass after shared components are extracted.

## Interview-week priority

Before the near-term interview, prioritize the knowledge asset over an unreliable
feature sprint. Robert should be able to explain system ownership, order
decomposition, physical versus logical activation, billing commencement,
fallout, and reconciliation even if the full Salesforce PoC is still underway.
He should also be able to walk through Zuora's major order-to-cash processes,
map a fiber offer and lifecycle into its object model, identify the CRM,
fulfillment, billing, payment, and finance boundaries, and state honestly which
parts were learned from documentation versus exercised in a tenant.
Payment preparation must cover tokenization and PCI scope, payment runs,
gateway result versus transport failure, retries, dunning, suspension/resumption,
refunds, disputes, settlement reconciliation, and the boundary between Zuora,
the gateway, Connect HQ, and the bank.

## Non-goals

- Reproducing any operator's private architecture.
- Making Salesforce Billing or a live Zuora tenant a prerequisite for the first
  fiber demonstration; the contract-aligned synthetic Zuora service is in scope.
- Requiring a live payment account or a second gateway profile for the first
  portable release; one contract-aligned synthetic profile is sufficient.
- Building a generic low-code integration platform.
- Modeling detailed optical engineering that does not affect the service order.
- Moving private interview material into GitHub.
- Promoting Connect HQ into a production mini-moi domain in this phase.
