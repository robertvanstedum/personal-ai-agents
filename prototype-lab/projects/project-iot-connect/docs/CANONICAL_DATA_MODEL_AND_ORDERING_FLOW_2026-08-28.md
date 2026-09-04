# IoT Connect Canonical Data Model and Ordering Flow

**Date:** August 28, 2026  
**Status:** Implemented backend baseline  
**Scope:** WDH account setup, inventory assignment, connectivity ordering,
network activation, and legacy compatibility evidence

## Architecture skeleton

```text
Single activation form ─┐
CSV batch upload ───────┼─> FastAPI contract ─> OrderingService ─> Repository
Postman / Swagger ──────┘                              │               │
                                                      │               ├─ Memory
                                                      │               └─ Snowflake
                                                      │
                                                      ├─ SOAP -> FlowOne
                                                      └─ REST -> Amdocs middleware
```

The entry channel does not own business logic. A single form and a CSV file
both create the same activation-batch request and follow the same service and
repository path.

## Table ownership

### `LEGACY`

- `ACCOUNTS`: pre-existing Amdocs financial accounts. WDH validates and
  references them; WDH does not create them.
- `LINES`: compatibility subscriptions and the golden line.
- `BILLING_ROWS`: downstream billing-feed evidence.

### `IOT`

- `CUSTOMERS`: WDH customer party identity.
- `ACCOUNTS`: WDH operating account plus mandatory external Amdocs billing
  account and the account-level Amdocs-send policy.
- `CONTRACTS`: WDH commercial agreement identity.
- `SIM_INVENTORY`: SIM/ICCID/IMSI inventory and owner state.
- `MDN_INVENTORY`: independent number inventory and allocation state.
- `SUBSCRIPTIONS`: commercial product instances. SIM and MDN are deliberately
  absent so a subscription may have zero, one, or many resources.
- `SUBSCRIPTION_RESOURCES`: typed association from subscription to SIM, MDN,
  device, licence, or another future resource.
- `CHARGES`: calculated WDH charges.

### `CATALOG`

- `PRODUCT_OFFERINGS`: what the customer buys and fulfillment type.
- `RATE_PLANS`: commercial price attached to an offering.
- `NETWORK_PROFILES`: technical service and roaming entitlement.
- `OFFERING_RESOURCE_REQUIREMENTS`: required resource types and allocation
  methods. Connectivity selects a SIM and allocates the next available MDN;
  resource-free VAS products have no rows.

### `CONTROL`

- `ACTIVATION_BATCHES`: one-item or multi-item order envelope and totals.
- `ACTIVATION_BATCH_ITEMS`: per-subscription workflow state and integration
  outcomes.
- `FLOWONE_ELEMENT_RESULTS`: HSS/AAA/PCRF/SMSC/SIM-OTA evidence and rollback.
- `AUDIT_EVENTS`: administrator actions.
- `BILL_RUNS`: billing and reconciliation evidence.

## Canonical state flow

```text
LEGACY.ACCOUNTS already contains Amdocs billing account
  -> create IOT.CUSTOMERS + IOT.ACCOUNTS + IOT.CONTRACTS
  -> assign SIM: OPERATOR/AVAILABLE -> ACCOUNT/ASSIGNED
  -> create batch and subscription: PENDING_ACTIVATION
  -> reserve next MDN: AVAILABLE -> RESERVED
  -> associate SIM + MDN through SUBSCRIPTION_RESOURCES
  -> submit one synchronous FlowOne SOAP request per item

FlowOne failure
  -> subscription ACTIVATION_FAILED
  -> MDN AVAILABLE
  -> SIM remains ACCOUNT/ASSIGNED
  -> Amdocs NOT_ELIGIBLE_NETWORK_FAILURE

FlowOne success
  -> subscription ACTIVE
  -> SIM ACTIVE
  -> MDN ASSIGNED
  -> account policy ON: submit Amdocs middleware CREATE
  -> account policy OFF: SKIPPED_BY_ACCOUNT_POLICY
```

An accepted Amdocs middleware response ends the WDH compatibility step. A
later Amdocs fallout is an Operations exception and does not reverse active
network service.

## Implemented API path

```text
GET  /api/v1/inventory/sims/available
POST /api/v1/admin/accounts/{account_id}/sim-assignments
POST /api/v1/admin/accounts/{account_id}/activation-batches
POST /api/v1/admin/activation-batches/{batch_id}:submit
GET  /api/v1/activation-batches/{batch_id}
```

Direct diagnostic boundaries remain available:

```text
POST /api/v1/network-activations
POST /api/v1/legacy-subscription-actions
```

## Reuse rule

The reusable skeleton is channel -> validated API -> service/orchestration ->
repository -> database, with integrations behind explicit clients. A future
prototype changes catalog, tables, and service behavior without scattering
SQL or external calls through the UI.

## Next UI increment

Build two entry modes over the same activation-batch contract:

1. a one-subscription form; and
2. a CSV upload that maps each valid row to one batch item.

Both must show the created pending state before the operator submits the batch.
