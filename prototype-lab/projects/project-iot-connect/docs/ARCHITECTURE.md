# Reusable Prototype Lab Application Skeleton

**Candidate:** IoT Connect v3 alpha  
**Purpose:** Keep API mechanics consistent and make the application shape
reusable when the business context and tables change.  
**Status note (2026-09-03):** Snowflake is unsupported as an application database in IoT Connect v0.9 — PostgreSQL operates the demo and memory serves tests; the Snowflake references below record the original design only.

## Mini architecture

```text
app/static/
  Browser pages and one shared API client
          │ HTTP + JSON/CSV
          ▼
app/api.py + app/api_models.py + app/dependencies.py
  Versioned routes, strict request models, actor dependency, error contract
          │ typed method call
          ▼
app/services/demo.py
  Business preconditions, identity allocation, transactions, outcomes
          │ repository protocol
          ▼
app/repositories/
  protocols.py | memory.py | snowflake.py
          │
          ▼
  deterministic memory state or Snowflake SQL tables
```

## System-of-record boundary

The new IoT platform is authoritative for enterprise IoT provisioning,
subscriptions, service counts, products and offers, rating, billing dollars,
line-level charges, and the transformation and reconciliation of the billing
output sent downstream.

The narrowed Legacy Billing boundary retains:

- customer invoice and statement production;
- tax calculation;
- customer-facing invoice history;
- account and financial interfaces into SAP and related processes; and
- the established functions that remain attached to that boundary.

```text
New IoT platform
  provisioning → subscriptions → rating → billing dollars → reconciled feed
                                                   │
                                                   ▼
Legacy Billing
  tax → invoice/statement → account/financial interfaces → SAP downstream
```

Reporting, commissions, IT data warehouses, analytics, and roaming settlement
are not authoritative Legacy Billing capabilities after the full Phase 2
transition; they must be repointed to WDH or an appropriate replacement.

The HTML statement and A/B comparison are explicitly illustrative artifacts
generated from reconciled legacy billing rows. They show what Legacy Billing
could produce; they are not an invoice engine added to the IoT solution.

## Canonical next-revision integration boundary

The current alpha models activation outcomes in one application service. The
next revision adds actual mock HTTP boundaries for synchronous FlowOne
activation and asynchronous Amdocs compatibility-subscription creation. See
`PHASE1_TO_PHASE2_DEMO_V2_DESIGN_2026-08-28.md` for the canonical design and
build acceptance criteria. No implementation should infer the new integration
behavior from this reusable-skeleton document alone.

## API discipline

1. All public operations live under `/api/v1`.
2. `app/api_models.py` is the single location for reusable Pydantic request and
   response models. Models strip surrounding whitespace and reject extra
   fields.
3. `app/dependencies.py` is the single location for actor/role dependencies.
4. `app/main.py` creates the app, request IDs, shared exception handlers,
   routes, and static mounts. It contains no business rules or SQL.
5. `app/api.py` contains thin HTTP routes. Each route validates transport
   details and calls one application-service operation.
6. Every known API error has the same shape: `code`, `message`, `request_id`,
   and optional `details`.
7. Swagger is generated from the same routes used by the UI and Postman.
8. `app/static/api-client.js` is the only browser HTTP client. Individual
   screens do not implement competing fetch/error/header conventions.
9. Admin mutation routes are grouped under `/api/v1/admin` and require the
   explicit `X-Demo-Role` header.
10. Database credentials never cross the API boundary.

These conventions are candidates for a future Prototype Lab shared package.
They are local and readable first; extraction should wait until a second
prototype proves that the interface is truly common.

## Identity contract

| Object | Permanent internal ID | Human-readable number | External/source reference |
|---|---|---|---|
| Account | `account_id` UUID | `ACCT-000300` | optional CRM/customer reference |
| Contract | `contract_id` UUID | `CTR-000300` | future external contract reference |
| Subscription | `subscription_id` UUID | `SUB-0000001` | CSV `source_subscription_ref` |
| Activation batch | `batch_id` UUID | `BAT-0000001` | request evidence |
| Bill run | `bill_run_id` UUID | `RUN-0000001` | bill cycle plus execution |

The database and repository enforce:

```text
UNIQUE account_number
UNIQUE contract_number
UNIQUE subscription_number
UNIQUE sim_id
UNIQUE (account_id, source_subscription_ref)
```

Therefore two customers may each send `DEVICE-001`; their resulting
subscriptions remain different and permanently attached to the right account
and contract.

## Catalog contract

The file contains:

```text
source_subscription_ref,sim_id,rate_plan_id
```

It does not contain price or GL mapping. `rate_plan_id` resolves to a controlled
catalog record. Both memory and Snowflake use the same four definitions in
`app/domain/catalog.py`; the Snowflake setup mirrors them for inspection.

## Billing-posting control

Account creation produces an IoT account, contract, and matching legacy account
reference in one unit of work. It always starts in detailed mode. In detailed
mode, each subscription posting carries an MDN that must be active under the
matching Amdocs account. In summarized mode, source subscription charges retain
their identity and traceability in WDH but are aggregated and posted to the
Amdocs account with no MDN. The mode change remains admin-controlled and
audited; no synthetic golden line is required.

## Demonstration artifacts

`/artifacts/invoice-comparison` renders two synthetic telecom statements after
the prepared bill runs exist:

- Aster: detailed processing and five billed legacy service lines.
- Boreal: summarized processing and consolidated account-level posting with no
  individual subscription MDNs in the Amdocs feed.

Both statements preserve the output amount and source traceability. Their
purpose is to make the downstream customer and AR implication visible without
claiming that statement production moved out of Legacy Billing.

## Reuse boundary

For a different Prototype Lab use case, retain:

- application factory and request-ID/error convention;
- strict API base models and actor dependency pattern;
- shared browser API client;
- service-to-repository dependency direction;
- UUID plus display-number identity factory;
- memory adapter contract tests; and
- Snowflake SQL API client and type-conversion boundary.

Replace:

- domain catalog and rules;
- application-service use cases;
- repository operations and tables specific to those use cases;
- fixtures; and
- screen content.

Do not extract a large generic framework. Reuse the skeleton and small proven
helpers.
