# WDH-to-Amdocs Middleware Compatibility API — Working Specification

**Date:** August 28, 2026  
**Status:** Independent REST component implemented and tested locally  
**Historical fidelity:** Boundary and behavior follow Robert's direct
experience; names and identifiers are synthetic

## Purpose

After successful network provisioning, WDH submits a small compatibility
subscription action to middleware. The middleware owns its downstream Amdocs
interaction. WDH is finished when the middleware returns `OK`.

This call is outside the customer-service critical path. It does not activate
service, and WDH does not poll Amdocs during the demo. Any later downstream
fallout belongs to an Operations follow-up process before billing.

## WDH business route

```text
POST /api/v1/legacy-subscription-actions
```

```json
{
  "amdocs_account_number": "AMD-45001",
  "wdh_account_reference": "WDH-200",
  "mdn": "+13125550121",
  "imsi": "310150123456789",
  "action": "CREATE"
}
```

A ready-to-paste request is retained at
`fixtures/amdocs_middleware_create.json`.

`action` is `CREATE` or `DEACTIVATE`. These five fields are the entire demo
contract. WDH and Amdocs contract numbers remain internal to their respective
systems and are rejected if added to this request.

## Identity and acceptance semantics

- Amdocs account number + IMSI are the stable compatibility-subscription key.
- MDN is the service reference carried with the action.
- WDH account reference provides cross-system traceability.
- Middleware returns a request ID and `status: OK`.
- WDH records `wdh_status: SUBMITTED` and does no status polling.
- This response proves middleware acceptance, not downstream Amdocs completion.

## Mock middleware route

```text
POST /mock/amdocs-middleware/v1/subscription-actions
```

The mock returns HTTP 200 and a strict JSON response containing `OK`, the
middleware request ID, stable subscription key, accepted values, action, and
timestamp.

## Implementation

```text
contracts/amdocs_middleware.py
app/integrations/amdocs_middleware.py
app/services/legacy_compatibility.py
app/api.py + app/api_models.py
integration_stubs/main.py
tests/test_amdocs_middleware.py
```

## Deliberately deferred

- a downstream Amdocs simulator;
- WDH polling of Amdocs state;
- downstream order-management steps;
- contract-number exchange;
- Operations exception ingestion and retry; and
- automatic sequencing after FlowOne activation.
