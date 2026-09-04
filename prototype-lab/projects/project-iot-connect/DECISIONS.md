# Connect HQ Demo Program Decisions

**Version:** 0.4  
**Date:** 2026-09-02  
**Status:** Initial decisions captured for Robert's review

## Accepted direction

1. The current prototype is promoted from `_working` into
   `prototype-lab/projects/project-connect-hq/` before it is refactored.
2. The promoted application must run from its new location before packaging changes begin.
3. The current Connect HQ IoT prototype is formalized before fiber code begins.
4. The approved standalone beta is preserved as a tagged release.
5. The fiber demo is derived from that tag and does not overwrite the IoT demo.
6. Fiber research emphasizes back-office order-to-service-to-bill architecture.
7. Salesforce is an external CRM and order-entry option, not the billing engine.
8. The first fiber scenario uses a qualified location with an existing ONT.
9. Optical light and completed customer service are separate milestones.
10. Fiber billing uses recurring speed-tier charges rather than data consumption.
11. Private interview context, personal files, credentials, and raw evidence are
   excluded from committed artifacts.
12. Repository edits are serialized; design research may proceed in parallel.
13. Zuora and Salesforce are paired learning tracks: Zuora represents
    subscription order-to-cash and is the P0 near-term interview priority;
    Salesforce represents CRM and commercial order entry and is secondary.
14. A live Zuora tenant is not required for the first fiber demo. The fiber demo
    will include a synthetic, contract-aligned Zuora service behind a replaceable
    adapter and will label it clearly as synthetic.
15. The synthetic Zuora service will implement only the approved API subset
    needed for the scenario, derived from a pinned published OpenAPI version and
    protected by request/response contract tests. A live sandbox adapter is a
    later replacement, not a separate orchestration path.
16. Payments are an official fiber-demo domain and use a provider-neutral adapter.
17. The first synthetic gateway profile is Adyen-shaped because a public fiber-
    operator engineering posting names Adyen with Zuora and because Adyen
    publishes a versioned, MIT-licensed OpenAPI contract. Stripe is the secondary
    profile and near-term hands-on fallback, not a duplicate first-release build.
18. Payment-detail capture targets a hosted-component pattern. Connect HQ stores
    tokens and external references only and never receives or records PAN data.
19. Billing-account charges, payment creation, and refunds flow through the
    Zuora boundary. The gateway processes the payment; Connect HQ consumes
    normalized results and coordinates service consequences.
20. Retry exhaustion does not universally suspend service. Suspension and
    resumption require explicit, effective-dated policy events and auditable
    coordination with network state.
21. Connect HQ's fiber role is not a relabeled copy of its IoT role. The working
    target is a service-orchestration and operational-control layer; its exact
    system-of-record, projection, command, event, and evidence boundaries require
    approval at G3A before fiber implementation.
22. User-facing product branding is `Connect HQ`; the tracked project folder is
    `project-connect-hq`; `Nightjar` is historical provenance only.
23. Robert authorized Claude Code on 2026-09-02 to produce an isolated,
    sanitized standalone-beta candidate with Codex as verifier. Repository
    import and every Git action remain subject to Robert's diff review.

## Open decisions

| ID | Decision | Needed by |
|---|---|---|
| D-01 | Resolved: user-facing product is `Connect HQ`; tracked folder is `project-connect-hq` | G2 |
| D-02 | Resolved: tracked project home is `prototype-lab/projects/project-connect-hq/` | G0A |
| D-03 | Salesforce order intake: secure HTTPS callback, scheduled pull, or event subscription | G3 |
| D-04 | Standard Salesforce Order objects versus a small custom fiber-order model | G3 |
| D-05 | Exact event and controls authorizing billing commencement | G3 |
| D-06 | Whether field-installation is release-two scope or a later scenario | G3 |
| D-07 | Whether the fiber and IoT demos share one runtime with profiles or ship as separate bundles | G4 |
| D-08 | Minimum payment scenarios in the first fiber release versus the next increment | G3 |
| D-09 | Install-fee timing, waiver behavior, and refund treatment | G3 |
| D-10 | Dunning grace period and exact event authorizing network suspension/resumption | G3 |
| D-11 | Whether a live Adyen or Stripe test session is included or remains a separate learning lab | G4 |
| D-12 | Final source-of-truth and projection boundary for Salesforce, Connect HQ, Zuora, access-network inventory, payment gateway, and ERP/GL | G3A |

## Deferred decisions

- AWS deployment topology.
- Salesforce Communications Cloud licensing or trial access.
- Live Zuora sandbox or production integration.
- Live payment-gateway integration and production merchant onboarding.
- Promotion into a production mini-moi domain.
