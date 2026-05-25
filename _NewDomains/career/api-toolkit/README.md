# mini-moi-agent-guild / Career Domain (Technical Toolbox)

**Domain status:** v0.9 — active build  
**Owner:** Robert Van Stedum, TPM  
**Repo:** github.com/robertvanstedum/personal-ai-agents  
**Public:** Yes — this domain is intentionally open as a portfolio artifact

---

## The Guild

The historical guild was a group of practitioners who shared knowledge,
maintained standards, trained each other, and produced work that individual
craftsmen could not produce alone. What died in the industrial age was the
physical co-location requirement. What the mini-moi-agent-guild rebuilds
is the knowledge-sharing and standards layer — with AI agents as the guild
members.

**mini-moi** — personal and specific. Not a general intelligence, a specific
one. Yours. The platform that knows you, built by you, for you.

**agent** — autonomous actors with defined roles and responsibilities.
Not tools. Members.

**guild** — accumulated craft knowledge, standards of practice, and work
that compounds over time. A guild doesn't just execute tasks. It builds
and preserves expertise across engagements, roles, and years.

The guild has four members and one charter:

| Member | Role |
|--------|------|
| Robert | TPM and decision point. Owns vision, strategy, and final call. |
| Claude.ai | Design and strategy. Architecture, artifact creation, conceptual work. |
| OpenClaw | Memory and execution. File system writes, context continuity, orchestration. |
| Claude Code | All git operations. Nothing else touches the repo. |

This is not a hierarchy. It is a division of craft. Each member does what
they do best. Robert is the master craftsman. The agents are the guild.

---

## Why this domain exists

I have 30 years of experience across telecom, satellite, and IoT — including
direct field-level API integration design work on TMF620/622 between Netcracker
and Salesforce for a Latin American carrier, years before Salesforce certified
those same APIs as a product feature in Communications Cloud.

The gap was never knowledge. It was formal artifact discipline — the ability
to translate field-level design discussions into OpenAPI specs, sequence diagrams,
and structured capability decompositions that an engineering team or interview
panel can evaluate on the spot.

This domain closes that gap. It is my portable TPM capability library:
company-neutral, built on industry standards (TM Forum Open API), and
designed to transfer directly to the next role — whether that's a carrier,
a BSS/OSS vendor, a platform company, or a B2B SaaS integration team.

**The broader thesis:** In the pre-AI era, breadth was a liability for
technical roles — you were expected to go deep on one stack. In the AI era,
breadth plus structured thinking plus the ability to produce artifacts quickly
is the differentiator. This domain is that capability made visible.

---

## What this domain is (and is not)

**It is:** A living toolkit of API design patterns, OpenAPI specs, sequence
diagrams, and interview-ready scenarios built on TM Forum standards.

**It is not:** Company-specific. No T-Mobile, Netcracker, or Claro IP.
All examples use generic carriers, generic account IDs, and public TMF
standard patterns.

**It connects to:** The mini-moi-agent-guild platform (personal-ai-agents repo),
which runs three domains:

1. **Curator** — geopolitics and finance intelligence briefing, in daily
   production. RSS ingestion, two-stage LLM scoring, Telegram delivery at 7 AM.
   The original domain that proved the pattern generalizes.
2. **language-german** — language coaching pipeline, v0.9, 60+ sessions,
   Vienna-tested. Second domain — same architecture, different knowledge layer.
3. **career (this domain)** — portable TPM capability library. Third domain.

The platform is the substrate. The domains are the knowledge layers.
Each one independent, all sharing the same local-first, model-agnostic design.

---

## Personal narrative anchor — the Claro/Netcracker project

The Salesforce ↔ Netcracker integration for Claro Latin America is the
formative experience behind this domain. The work involved:

- Field-level requirements discussions with the Claro team on order flow
- Technical discussions with Netcracker architects on the product catalog
  data model (TMF620)
- Conceptual design of the integration contract between Salesforce order
  objects and Netcracker product offering structures (TMF622)
- Field-level definition of required vs optional fields, state transitions,
  and error handling

What was missing at the time: formal diagramming and OpenAPI spec authorship.
Salesforce has since shipped TMF620/622 as certified, production APIs in
Communications Cloud. The experience predates the product.

This toolkit gives that experience its formal artifact layer.

---

## TMF standards foundation

| API    | Name                        | Role                                  |
|--------|-----------------------------|---------------------------------------|
| TMF620 | Product Catalog Management  | What can be ordered (Netcracker side) |
| TMF622 | Product Ordering Management | Place and track orders (CRM side)     |
| TMF641 | Service Ordering            | Fulfillment execution                 |
| TMF640 | Service Activation          | Network/BSS activation                |

See `tmf-standards-context.md` for full vocabulary and integration architecture.

---

## Directory structure

```
career/
  api-toolkit/
    README.md                          ← this file
    tmf622-product-ordering-api.yaml   ← OpenAPI 3.0 spec (TMF622 pattern)
    tmf-standards-context.md           ← TMF620/622 reference + narrative
    patterns/
      async-webhook-flow.md            ← 202 + webhook callback pattern
      oauth-client-credentials.md      ← B2B auth pattern (TODO)
      idempotency.md                   ← Safe retry design (TODO)
    diagrams/
      sequence/                        ← PlantUML sequence diagrams
    interview/
      scenarios/                       ← Practice scenarios + worked solutions
      briefs/                          ← Concept quick-reference sheets
```

---

## How to use the spec

Paste `tmf622-product-ordering-api.yaml` into:
- https://editor.swagger.io — live preview, no install required
- VS Code / Cursor with "OpenAPI (Swagger) Editor" extension

---

## Release roadmap

| Version | Status      | Description                                        |
|---------|-------------|----------------------------------------------------|
| v0.9    | In progress | Toolkit foundation — spec, patterns, diagrams      |
| v1.0    | Planned     | Full pattern library + interview scenario set      |
| v1.1    | Future      | Guild pipeline integration (job signal ingestion, skill gap analysis) |
