# TMF Open API Standards — Context & Personal Narrative

## What TM Forum is

TM Forum is the global industry consortium for communications service providers (CSPs).
800+ member organizations including every major carrier, Ericsson, Nokia, Netcracker,
Amdocs, Salesforce, and the hyperscalers. Their Open API program defines the standard
integration contracts used across the entire telco ecosystem.

## The two APIs that matter most for partner integration

### TMF620 — Product Catalog Management
- **What it does:** Manages the full lifecycle of product offerings — creation,
  versioning, pricing, availability. The "menu" of what a carrier can sell.
- **Who owns it:** BSS/product catalog systems (Netcracker, Amdocs, Oracle BRM)
- **Typical operations:** GET productOffering, GET productSpecification,
  query offerings by market/segment/category
- **In Salesforce:** Implemented as Communications Cloud catalog sync

### TMF622 — Product Ordering Management
- **What it does:** Standardized mechanism for placing product orders with all
  necessary parameters. The order flows from CRM/channel into fulfillment.
- **Who owns it:** CRM and order management systems (Salesforce, custom portals)
- **Typical operations:** POST productOrder, GET productOrder/{id}, PATCH (amend),
  event notifications on state changes
- **Async pattern:** Order submitted → 202/ACKNOWLEDGED → state transitions
  (INPROGRESS → COMPLETED/FAILED) via hub/notification callbacks
- **In Salesforce:** TMF622 is a certified API in Communications Cloud

## The integration seam (where the TPM lives)

```
Channel Partner / CRM          Integration Layer         Carrier BSS/OSS
(Salesforce, custom portal)    (API Gateway + TMF)       (Netcracker, Amdocs)

  TMF622 ProductOrder    →→→   translate + route   →→→   TMF620 catalog lookup
                                                          TMF641 service order
                                                          TMF640 activation
         ↑                                                       |
         └──────────── status callbacks / webhooks ──────────────┘
```

The TPM owns the integration layer contract:
- Which TMF fields are required vs optional for this implementation
- How the async state machine maps to partner-visible events
- What goes in the webhook payload at each state transition
- Versioning and breaking change policy

## Personal narrative anchor

**Claro Latin America / Netcracker project:**
Designed the field-level API integration between Netcracker's product catalog
(TMF620) and Salesforce CRM (TMF622) for a Latin American carrier.

Work included:
- Field discussions with the client (Claro) on order flow requirements
- Technical discussions with Netcracker architects on catalog data model
- Defining the field-level mapping between Salesforce order objects
  and Netcracker product offering structures
- Conceptual design of the integration contract (the layer between the two systems)

What was missing at the time: formal diagramming and OpenAPI spec authorship.
This toolkit closes that gap — same knowledge, now with the artifact discipline.

**Why this matters in interviews:**
Salesforce Communications Cloud now ships TMF620/622 as certified, production APIs.
What was designed conceptually for Claro in the field is now a standard product feature.
The experience is directly relevant to any role involving carrier API platforms,
BSS/OSS integration, or partner-facing product management.

## Key vocabulary for interviews

| Term | Definition |
|------|-----------|
| ProductOffering | A sellable product with price and characteristics (TMF620) |
| ProductOrder | A request to fulfill one or more product offerings (TMF622) |
| OrderItem | Line item within an order — each maps to one product offering |
| Hub/Notification | TMF's webhook pattern — register a callback URL for state events |
| ACKNOWLEDGED | Order received and validated — equivalent to 202 Accepted |
| INPROGRESS | Fulfillment underway in BSS/OSS |
| COMPLETED | All order items fulfilled |
| CharacteristicValue | The specific configuration of a product (e.g. data allowance, plan tier) |
| ProductSpecification | The template/blueprint a ProductOffering is based on (TMF620) |
