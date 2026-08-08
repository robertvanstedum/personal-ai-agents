# mini-moi Specification Standard

**Version:** 1.0
**Status:** Adopted for pilot verification
**Adoption spec:** `spec_144_specification_standard_adoption_2026-08-08.md`

This is the shared pattern for design and implementation handoffs.
Markdown is the source of truth and should render cleanly on GitHub. Use only
the sections the work needs; a small defect should remain small, while a
cross-domain architecture may use the full structure.

## Writing rules

- Lead with the decision and intended outcome, not the history of the meeting.
- Prefer concrete verbs and observable outcomes over process narrative.
- Separate **decided**, **open**, and **out of scope** items clearly.
- Keep paragraphs short and information-dense. Prefer ordinary Markdown over
  decorative formatting.
- Use tables only for real comparisons, mappings, inventories, or test matrices.
- Use diagrams only when relationships are materially clearer than prose.
- Link related issues, roadmap items, specs, and verified source files directly.
- Include exact files or interfaces only when confirmed against the repository.
- Make verification observable: state what is tested and what proves success.
- End with acceptance criteria and the approval state.
- Git history is the change log. Add a revision summary only when a new version
  materially changes a prior decision.
- Integrate reviewer findings into the relevant section; do not append full
  review transcripts to the specification.
- Size the document to the work: roughly 50-120 lines for a defect or small UI
  change, 100-250 for a feature, and 200-350 for cross-domain architecture.
  These are guides, not limits. A long-lived governing architecture document
  may exceed 350 lines, but its material should be linked rather than repeated
  inside each feature spec. Otherwise, consider an appendix or a separate
  governing document when the main spec grows beyond the useful range.
- Name new files `spec_<queue-id>_<slug>_<YYYY-MM-DD>.md` when a queue ID
  exists, otherwise `spec_<slug>_<YYYY-MM-DD>.md`. Add `v2` only after a
  materially different version of the spec has already been shared or approved.
- PDF is an optional reading/export format, not a second source of truth. The
  default export should be dense and plain unless presentation quality is part
  of the request.

## Versioning

- Keep this template at a stable path so agent instructions do not break. Give
  the adopted template a version in its header and use Git history for ordinary
  wording changes.
- Bump the template's major version only when its required structure or approval
  contract changes. Record that change in the adoption specification.
- The initial adoption specification that makes this template a GitHub artifact
  and adds the agent-instruction pointer should follow this template itself as
  closely as practical.
- Edit a draft specification in place through review. Do not create `v2`, `v3`,
  or agent-specific copies for each review pass.
- Create a new spec version only when an already shared or approved decision is
  materially replaced and the old document must remain understandable.
- Mark the old document **Superseded** and link both versions to each other.

---

# Spec: <Short outcome-oriented title>

**File:** `<path when known>`
**Date:** `<YYYY-MM-DD>`
**Status:** `<Draft | In review | Blocked | Approved to build | Built | Shipped | Superseded>`
**Owner / decision point:** Robert
**Build queue:** `<ID or Not registered>`
**GitHub issue:** `<link or None>`
**Roadmap / governing spec:** `<link or section>`
**Dependencies / blockers:** `<brief statement or None>`
**Reviews:** `<for example: Grok 2026-08-08 (design); Pending implementation review>`

## 1. Decision summary

State the approved or proposed result in two to six bullets. A reader should
understand the intended outcome and the important boundary without reading the
rest of the document.

- `<decision or outcome>`
- `<important architectural or product boundary>`
- `<what remains unchanged>`

## 2. Context and evidence

Describe the present behavior, problem, user need, and evidence. Distinguish
verified repository or production facts from assumptions.

### Current state

`<what exists now>`

### Problem or opportunity

`<why a change is needed>`

## 3. Goals and non-goals

### Goals

- `<required result>`

### Non-goals

- `<explicitly excluded work>`

## 4. User experience or operational flow

Describe the behavior from the user's or operator's point of view. Use a short
flow or example when it removes ambiguity.

## 5. Design contract

Record the durable boundaries an implementer must preserve. Use only relevant
subsections. Only include subsections that contain actual constraints; omit
empty subsections.

### Architecture and ownership

`<components, responsibilities, and stable interfaces>`

### Data, memory, and persistence

`<canonical records, ownership, retention, and migration>`

### Security and permissions

`<identity, secrets, authorization, privacy, and audit requirements>`

### Provider or model independence

`<swap points, configuration, fallbacks, and prohibited coupling>`

## 6. Scope and implementation surface

List the expected files, services, routes, or components only after verifying
them. This is a change boundary, not a line-by-line coding prescription.

### In scope

- `<component or behavior>`

### Expected code surface

- `<verified path>` - `<expected responsibility>`

### Out of scope

- `<deferred or protected area>`

## 7. Dependencies, blockers, and open questions

### Dependencies and blockers

- `<item, owner, and effect>`

### Open questions requiring a decision

- `<question and who decides>`

Any unresolved decision that can change the implementation surface, security
model, data model, or provider contract must be listed here or under
Dependencies and blockers. Do not leave it only in prose or implementation
notes. If it can materially change the build, it is a blocker or an explicit
pre-build decision.

## 8. Delivery and rollback

### Sequence

1. `<smallest safe first step>`
2. `<dev validation or migration step>`
3. `<review and production step>`

### Rollback or failure behavior

`<how the system remains safe and how the change is reversed or disabled>`

## 9. Verification

### Automated

- `<test and expected assertion>`

### Manual / visual / real-device

- `<scenario and observable result>`

### Production sanity check

- `<health, version, data, or behavior confirmation>`

## 10. Acceptance criteria

1. `<binary, observable criterion>`
2. `<binary, observable criterion>`
3. `<no-regression or safety criterion>`

## 11. Review and approval record

- **Design review:** `<reviewer, date, result>`
- **Robert's design approval:** `<pending or date>`
- **Implementation diff review:** `<pending or reviewer/date>`
- **Robert's ship approval:** `<pending or date>`

## Optional: revision summary

Include only when this document supersedes a prior version and the changed
decisions would otherwise be hard to identify.

- `<material decision changed and why>`
