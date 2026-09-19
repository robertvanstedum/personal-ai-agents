# Working Room / connected design-build environment — bibliography

Version 1 · Established 19 September 2026 · Primary sources checked on 19 September 2026

This is the shared reference register for the orienting design and later official specification. Stable IDs below should be cited in design decisions and acceptance rationale. Sources support particular principles or mechanisms, not certification of mini-moi. Distinguish documented facts from our architectural inference. Do not renumber IDs when a source is superseded; add a dated entry or version.

## Primary references

### REF-001 — Local-first lineage

- **Authors:** Martin Kleppmann, Adam Wiggins, Peter van Hardenberg, Mark McGranaghan.
- **Title:** Local-first software: You own your data, in spite of the cloud.
- **Publication:** Ink & Switch, April 2019; Onward! 2019 proceedings, pp. 154–178.
- **Canonical source:** https://www.inkandswitch.com/essay/local-first/
- **DOI:** https://doi.org/10.1145/3359591.3359737
- **Status:** Original research/design essay; checked at the author's organization.
- **Supports:** Local ownership, offline operation, long-term preservation and collaboration as compatible design goals.
- **Design alignment:** Direction §§1–3, 5; local working copy with authorized central availability.
- **Limit:** Our single-authority/versioned-bundle approach is our design choice. This source does not certify it or require us to adopt CRDTs. Do not claim the prototype meets every local-first ideal.

### REF-002 — Consistent SQLite backups

- **Publisher/title:** SQLite project, SQLite Backup API.
- **URL:** https://www.sqlite.org/backup.html
- **Edition:** Living technical documentation; publication date not asserted.
- **Status:** Official implementation documentation; checked.
- **Supports:** Supported online database backup mechanism and snapshot behavior, rather than an uncontrolled copy of a live database file.
- **Design alignment:** Vision v1 §8; R1 backup consistency and interrupted-operation handling.
- **Limit:** A consistent local SQLite backup does not establish attachment completeness, off-device delivery or successful application restoration.

### REF-003 — Recovery testing, not backup existence

- **Publisher/title:** AWS Well-Architected Framework, REL09-BP04: Perform periodic recovery of the data to verify backup integrity and processes.
- **URL:** https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_backing_up_data_periodic_recovery_testing_data.html
- **Edition:** Living vendor guidance; publication date not asserted.
- **Status:** Official vendor guidance; checked through the official indexed page.
- **Supports:** Recovery tests against recovery objectives and checking that restored data is usable.
- **Design alignment:** Vision v1 §8; R1-08 performed restore with representative data and dependencies.
- **Limit:** Does not prove our backups work or prescribe our cadence. RPO/RTO, retention and operational owners remain our decisions. The earlier reliability-pillar URL failed retrieval; the framework URL above is the verified replacement.

### REF-004 — JSON-to-relational implementation reference

- **Publisher/title:** PostgreSQL Global Development Group, PostgreSQL 18, §9.16 JSON Functions and Operators.
- **URL:** https://www.postgresql.org/docs/18/functions-json.html
- **Edition:** Version 18 documentation; this is not a statement of our deployed server version.
- **Status:** Official technical documentation; checked.
- **Supports:** Database facilities for extracting typed records from JSON and querying structured JSON data.
- **Design alignment:** Transcript data contract and central import mapping.
- **Limit:** Loading our transcript requires a defined mapping, validation, access policy and duplicate/conflict handling. A JSON file is not an automatic relational schema or PostgreSQL replication stream.

### REF-005 — Provenance vocabulary

- **Publisher/title:** W3C, PROV-Overview.
- **Edition:** W3C Working Group Note, 30 April 2013.
- **Dated URL:** https://www.w3.org/TR/2013/NOTE-prov-overview-20130430/
- **Status:** Official standards-body overview; checked. It is a Working Group Note, not itself a blanket conformance certification.
- **Supports:** Distinguishing entities, activities, agents and derivation/attribution relationships.
- **Design alignment:** Transcript versus derived notes, submitting actor versus claimed speaker, source/artifact links.
- **Limit:** We borrow conceptual distinctions; we have not implemented or tested PROV conformance. Our JSON vocabulary and assurance levels are application contracts.

### REF-006 — Agent/tool authorization boundaries

- **Publisher/title:** Model Context Protocol, Security Best Practices.
- **Resolved URL:** https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices
- **Edition:** Versioned route 2025-11-25. The requested older 2025-06-18 security URL redirected here on access.
- **Status:** Official protocol-project guidance; checked.
- **Supports:** Explicit security boundaries around delegated access and token handling; relevant background for authorized agent/tool connections.
- **Design alignment:** Vision v1 §§3–4; bounded agent access and server-side authorization.
- **Limit:** This does not attest OpenClaw identity, enforce our tool policy, establish a workshop coordinator, or prove our implementation is MCP-compliant. Broad claims about a 2026 enterprise control-plane market are not established by this reference.

### REF-007 — Collaboration channel with governed business access

- **Publisher/title:** Salesforce Trailhead, Enhance Your Agent with Slack Integration and Actions.
- **URL:** https://trailhead.salesforce.com/content/learn/modules/agentforce-configuration-for-slack-deployment/configure-a-slack-agent-in-salesforce
- **Edition:** Living product training documentation; publication date not asserted.
- **Status:** Official vendor material; checked.
- **Supports:** Slack deployment of agents with actions and Salesforce access-control considerations.
- **Design alignment:** Vision v1 §4; separate communication surface from application authority.
- **Limit:** The architectural comparison is our inference. This source does not establish that every Slack action has our durable receipt semantics. Do not generalize it into claims about “AIforce” or “Headless 360,” or assert feature parity with mini-moi.

## Claim-to-reference index

| Design concern | Reference | Kind of alignment |
|---|---|---|
| Local work survives network loss and vendor changes | REF-001 | Design lineage |
| Consistent local database snapshot | REF-002 | Implementation mechanism |
| Usable restore must be demonstrated | REF-003 | Operational practice |
| Structured JSON can be mapped to relational records | REF-004 | Implementation facility, mapping still ours |
| Sources, actors and derived notes remain distinguishable | REF-005 | Conceptual vocabulary |
| Agent access needs explicit trust boundaries | REF-006 | Security guidance |
| Chat surface does not replace business authorization | REF-007 | Vendor-pattern comparison |

## Review claims not yet established by primary evidence

- “The design is ahead of common practice,” “nothing architectural is behind,” and general statements about what the agent market shipped in 2026 are reviewer judgments, not verified facts.
- The returned review files do not contain the specific Gwern/InfoQ links mentioned in the handoff. Do not reconstruct or invent their URLs. They are not accepted evidence here.
- Origin-authority transfer, the four-operations contract and per-kind freshness policy are our proposed architecture, not industry standards certified by these sources.
- Neo4j import support needs a versioned official reference and an executed example when that slice is built. No import test has been performed for this bibliography.
- The local migration classification table must be inspected and linked before its category names are normative. A reviewer naming categories is not policy adoption.

## Internal design sources — separate from external standards

- **INT-001:** [Planning Studio charter v0.1](../../../planning-studio/governance/GUILD_PLANNING_STUDIO_CHARTER_v0.1_2026-09-02.md), draft history; superseded within the stated ownership/record-authority scope by [v0.2](../../../planning-studio/governance/PLANNING_STUDIO_CHARTER_v0.2_2026-09-19.md). Robert explicitly places Planning Studio under CoS; the change is not inferred from reviewer consensus.
- **INT-002:** Internal migration-plan review, 19 September 2026. Its migration dispositions were verified to be separate from sensitivity defaults; it did not define sync authorization. Private source-location details are deliberately excluded from this public edition. The normative transfer rule is now in [revision 6 §6](RECORDS_ROOMS_v6.md).

## Maintenance

New entries record stable ID, author/publisher, exact title, canonical URL, edition/date, access date, supporting claim, affected design section and limitations. Record redirects. Prefer versioned sources where available. At spec freeze, reference these IDs and preserve the bibliography version in Git with the reviewed documents. Website access dates do not mean the pages are archived: no full external page snapshots were saved in this pass.
