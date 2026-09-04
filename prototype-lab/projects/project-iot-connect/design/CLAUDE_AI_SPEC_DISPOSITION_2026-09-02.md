# Claude AI Specifications — Review Disposition

**Version:** 0.1  
**Date:** 2026-09-02  
**Reviewer:** Codex  
**Decision owner:** Robert  
**Status:** Standalone candidate build authorized in isolation; repository promotion pending review

## Source artifacts

The following Claude AI deliverables are retained unchanged under
`design/inputs/`:

- `spec_connecthq_standalone_beta_2026-09-02.md`;
- `spec_connecthq_fiber_demo_2026-09-02.md`; and
- `INTEGRATION_CONTRACTS_2026-09-02.md`.

`ROBERT_ACCESS_AND_SETUP_CHECKLIST_2026-09-02.md` is retained under the ignored
`private/` directory because it is a personal account-and-secret setup checklist.
It is not part of the commit-safe program plan or the Claude Code build input.

These source files are design inputs, not self-executing instructions. The
repository rules, Robert's decisions, this disposition, and the applicable
approved build plan govern where they differ.

## Overall verdict

The specifications are a strong transition from concept to an executable plan.
They correctly separate the standalone beta from the later fiber build, make
Connect HQ's changed fiber role concrete, establish adapter contracts, and keep
Salesforce, Zuora, the gateway, and the access network as distinct systems of
record.

The standalone specification is suitable for an isolated candidate build with
the changes below. The fiber specification and integration contracts remain
design candidates behind G3 and G3A; they are not part of the overnight build.

## Accepted

- Promote, prove unchanged, formalize, verify, review, and only then release.
- Preserve the original `_working` prototype until Robert approves retirement.
- Containerize the app, PostgreSQL, and synthetic integration runtime so no host
  `.venv` is required.
- Provide one-command start, stop, reset, status, smoke, and verification paths.
- Preserve deterministic seed data and visible restart-safe integration evidence.
- Remove absolute laptop paths and dated presentation-path dependencies.
- Treat Connect HQ as a different component in the fiber scenario: service
  orchestration and operational control, not a relabeled full-stack BSS/OSS.
- Keep Salesforce, Zuora, payment, and access-network adapters replaceable and
  keep vendor-mode branching out of domain orchestration.
- Keep PAN and live secrets out of Connect HQ, logs, fixtures, and Git.
- Use correlation, idempotency, persisted events, explicit policy actions, and
  reconciliation as first-class controls.

## Modified for the standalone candidate

1. **Isolated-build authority.** Robert authorized Claude Code to build an
   isolated candidate on 2026-09-02 with Codex as verifier. This does not
   authorize copying the result into the tracked project, committing, pushing,
   tagging, opening a PR, or deleting the source. Those actions remain gated by
   Robert's review of the actual diff and evidence.
2. **Stable URLs.** The beta preserves the demonstrated app and integration-docs
   URLs—ports 8095 and 8096—unless a verified collision requires an explicit,
   documented migration. “Stable” does not mean inventing a new port scheme.
3. **Mock topology.** The current FlowOne and Amdocs surfaces are implemented by
   one integration-stub application. The beta may containerize that application
   as one service. Splitting it into two services is deferred unless the audit
   shows a mechanical split with no domain or behavior change.
4. **Promotion manifest.** Hash only the selected commit-safe promotion set,
   after exclusions are applied. Do not hash or record `.venv`, caches, private
   documents, secrets, runtime databases, raw evidence, or generated temporary
   material. Use a portable command or script rather than assuming GNU
   `sha256sum` on macOS.
5. **Secret check.** A deterministic no-secret verification command is required.
   Installing a repository-wide pre-commit framework is not required for the
   isolated candidate and must not modify root repository configuration.
6. **Second-machine proof.** Claude Code must make the candidate clean-copy
   testable and record the procedure. A second-Mac test remains a release gate
   if a second host is not available overnight.
7. **Container count follows responsibility.** The v0.9 goal is a portable
   Compose application, not a particular number of containers. PostgreSQL, the
   Connect HQ app, and the existing integration-stub boundary must be independently
   healthy; further decomposition requires evidence.
8. **Naming is resolved.** User-facing product branding is `Connect HQ`; the
   tracked project folder is `project-connect-hq`; `Nightjar` is historical
   provenance only and should not remain in user-visible runtime surfaces.

## Fiber items that remain open

- The proposed system-of-record map is a strong default, but it does not by
  itself close G3A. In particular, ownership of active-service inventory after
  activation requires explicit review.
- The Salesforce event flow currently risks two sources for the canonical
  correlation ID. The preferred rule is for Connect HQ to generate its canonical
  correlation ID from a stable Salesforce order/event reference and write it
  back; Salesforce's own order and event IDs remain external idempotency keys.
- Developer Edition support, API versions, OAuth behavior, Pub/Sub availability,
  and permissions must be verified in Robert's actual org before the live CRM
  adapter is specified as executable.
- Zuora paths, request shapes, headers, error envelopes, trigger-date behavior,
  and redistribution terms remain hypotheses until the published contract is
  retrieved, pinned, hashed, and reviewed.
- The fiber P0 described in the source spec is substantial. Increments A–F must
  remain independently shippable rather than becoming one all-or-nothing sprint.
- The private access checklist contains proposed user actions. Robert should
  validate current vendor signup and licensing details before following them and
  never place resulting secrets in chat or Git.

## Current authority

Claude Code may perform the work in
`build/OVERNIGHT_STANDALONE_BUILD_PLAN_2026-09-02.md` inside the sanitized
isolated workspace. Codex may inspect the result, execute local verification,
and prepare a review report. All repository promotion and Git actions remain
pending Robert's morning decision.
