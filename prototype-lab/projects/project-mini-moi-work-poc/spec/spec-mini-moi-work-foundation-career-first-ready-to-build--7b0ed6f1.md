---
id: 01M1Q6CCR74M9Z9YKN7B0ED6F1
series: mini-moi-work-foundation-specification
slug: spec-mini-moi-work-foundation-career-first-ready-to-build--7b0ed6f1
type: note
kind: specification
title: "Mini-moi Work Foundation — reconciled build specification, Career first"
revision: 2
created: 2026-09-04
authors: [claude_code, codex]
status: final
decision_owner: robert
roadmap_authority: none
implementation_authority: w0a_only_claude_code
domains: [cos, planning_studio, platform]
relations:
  - type: governed_by
    target: 01M1Q6CCR54M9Z9YKN9BC367FD
  - type: incorporates
    target: 01M1Q6CCR44M9Z9YKN43FC81E1
  - type: constrained_by
    target: 01M1Q6CCR64M9Z9YKN1DD62C92
  - type: reviewed_by
    target: 01M1Q6CCR34M9Z9YKNCA315583
---

# Mini-moi Work Foundation

## Reconciled build specification, Career first

## Specification form

This is the controlling release specification for a future build decision. It
incorporates Claude Code's detailed candidate (`43fc81e1`) in full, with the
normative corrections in the Codex reconciliation (`1dd62c92`). A builder must
read all three controlling documents in this order:

1. Robert's product vision (`9bc367fd`);
2. the reconciliation (`1dd62c92`);
3. this release specification, followed by Claude's detailed candidate for
   line-level implementation detail.

Where the Claude candidate conflicts with the reconciliation, the
reconciliation controls. This composition preserves the submitted review trail
without silently editing Claude's work.

Robert has separately authorized Claude Code to implement W0a only. W0b, W1,
and W2 remain unauthorized. The reviewed W0a diff still requires Robert's
approval before merge or release.

## Outcome

Robert and Chief of Staff can turn an ordinary voice or text conversation into
bounded, durable work; use authorized personal and external sources; develop an
artifact through repeated collaboration; stop; resume independently of a model
session; and retain a result under Robert's authority.

Career cover-letter collaboration is the first real slice. It validates the
foundation but does not define its vocabulary. The same foundation must remain
fit for a decision memo from local files without common-layer Career branches.

## Product contract

- Chief of Staff is the relational front door. `Work` is a lightweight visible
  context, not a task manager, queue, board, or separate assistant.
- Most conversation remains conversation. Durable work begins only on the
  explicit triggers already specified in the detailed candidate.
- Robert owns the sources, work, edits, approvals, and all external action.
- Approval retains an exact artifact. It never means send, submit, email,
  schedule, post, or contact.
- Mini-moi owns the durable contract. Models, runtimes, adapters, channels, and
  interfaces are replaceable.
- No real Career data enters W0a or W0b. No source is copied or migrated merely to
  establish the foundation.

## Common contract

The eight effects in Claude's §8.2 remain the closed operation set:

`open_work`, `attach_source`, `search_sources`, `read_source`,
`write_artifact`, `request_disposition`, `record_disposition`, and
`use_robert_edit`.

The request/response envelope is version 1. `work_id` and `operation_id` are
UUID4 values. Grants are turn-scoped, effect- and resource-specific, and carry
`data_class: private_personal | external_public` plus the only permitted egress
value, `none`.

There is no list, delete, rename, move, export, send, schedule, delegate, or
automatic-ingest operation.

`search_sources` and `read_source` accept only deployment-configured root
references. In addition to named source roots, they may address the virtual
root `approved:<subject>`. That root is a bounded, read-only projection over
canonical work records and returns only the exact artifact named by an
`approved_text` disposition. It never exposes continuing work or unapproved
artifacts and does not create a model-visible list-all-work operation.

## Canonical record

The person-owned private file tree is canonical. It stores:

- the minimal conversation-to-active-work binding;
- subject work directories;
- `work.json` with common identifiers, intent, state, immutable source refs,
  immutable artifact refs, pending approval, and disposition;
- content-free operation records and receipts; and
- the source and artifact bytes themselves.

It does not store `subject_extension`, provider/model/runtime fields,
`adapter_binding`, Career workflow state, role-family enums, scores, or agent
claims of approval.

Approval changes an artifact's eligibility for later retrieval; it does not
change authorship. An approved `agent_draft` can be reused as an approved base
while remaining an agent draft. A `coauthored_output` remains distinguishable
and may carry stronger evidence of Robert's voice. Every approved-root result
returns its original provenance class and disposition reference.

The common dispositions remain `continuing`, `approved_text`, `closed`, and
`unresolved`. “Do not apply” is a successful Career outcome recorded as a
free-text reason with common state `closed`, not a new common lifecycle state.

## Authorship and editing

Stored provenance classes are `robert_source`, `external_source`,
`agent_draft`, and `coauthored_output`. Current instruction and inference are
turn-scoped.

Sources and published artifact revisions are immutable. An agent draft never
becomes Robert-authored material merely because it exists or resembles his
voice.

Robert can edit a displayed or downloaded working copy. He returns the edited
bytes by paste, explicit upload, or an explicitly allowed file reference.
`use_robert_edit` then creates a new immutable `coauthored_output` revision,
records the superseded reference and hashes, and preserves every earlier
revision. There is no automatic watch, overwrite, merge, or ingestion.

## Safety and isolation

- `COS_WORK_ROOT` must be absolute, owner-private, non-symlinked, and outside
  the repository checkout. Failure disables Work without changing ordinary
  conversation.
- Authorized source roots are distinct read-only capabilities declared in
  deployment configuration, never by a model. A source root may be inside the
  checkout only if it is owner-private, ignored by the enclosing public Git
  repository, and contains no files tracked by that repository. A nested
  private repository is allowed; its VCS metadata is never searched or exposed.
  Startup validation checks the enclosing public repository's ignore/tracked
  state and fails closed. A work grant may narrow a configured root but cannot
  add or widen one.
- Every path is confined and checked component-by-component; unsupported,
  oversized, non-regular, symlinked, or escaping paths fail closed.
- Every Work route authenticates Robert explicitly.
- A private-work turn runs only with a platform-verifiable egress-free runtime
  profile. Prompt-only promises do not count.
- If that profile is unavailable, safe local read/resume/approval operations
  may continue, but private drafting is refused before a runtime call.
- Private-work context omits Guild build state, Operations state, and action
  tool schemas. It may read bounded relationship context but cannot write
  model-judged work summaries to relationship memory.
- Public research happens in a separate turn with no private source bodies.
- Existing CoS-to-Guild mutation is not exposed, reused, or repaired by this
  slice.

## Durability and recovery

Writes use a single writer per work item, expected hashes, atomic same-filesystem
publication, fsync, immutable revisions, idempotent operation records, and
content-free receipts. Nothing reports success without a committed operation.

`open_work` lazily detects and reconciles interrupted operations. A separate
recovery CLI is optional and should be added only if implementation or real
operations demonstrate a need.

## Career-local guidance

Career contributes configured read-only roots and conversational guidance, not
common schema. The first real work may use a named current resume, a supplied
posting, a loose `other-responses` directory, prior approved/co-authored work,
and Robert's current instructions—only from roots Robert authorizes.

The guidance remains:

1. What does this company or role need proven?
2. Which parts of Robert's history prove it truthfully?
3. Why does that evidence matter commercially or operationally here?
4. What insight or language would only Robert add?

Important and exploratory collaboration use the same service. They are
conversational modes, not common state. Natural role families may influence
retrieval and emphasis, but are not required categories, enums, or intake
fields.

Every material claim is supported by a Robert source, the current resume, or
something Robert said in the work. Otherwise Chief of Staff asks or omits it.

## Accumulation and n+1

The first important letter may take ten passes. That is expected calibration,
not failure. Approved and co-authored work may help later related work start
farther ahead: carrying Robert's evidence, positioning, trusted language, and
prior decisions while reasoning freshly about the new opportunity.

The 80/20 idea is directional, not a score. Reuse must never become generic
template prose, and unreviewed agent drafts never become evidence of Robert's
voice. Lived usefulness—especially whether the next collaboration requires
less repeated explanation—is the acceptance signal. No quantitative learning
system is introduced.

### How work accumulates

Accumulation is a distinct logical capability consumed by Work. Its eventual
person-owned layers are Robert's authorized sources, approved/co-authored
outputs, decisions with reasons, and explicitly affirmed relationship memory.
It preserves provenance across those layers and offers bounded reference,
search, and read—not automatic extraction or an inferred profile.

The first reference implements only the Career needs proven here: configured
read-only roots and the `approved:<subject>` view. The first foundation does not invent a general
memory schema, vector index, cross-domain registry, or automatic durable-lesson
writer. An unreviewed draft never enters the Robert-source layer. A possible
durable lesson is written only after Robert explicitly affirms it through a
separately approved capability.

This boundary is designed for reuse but is not promoted into a shared `core/`
framework during the first slice. Career use adjusts the reference first. A
second area with a real approved artifact is the evidence required to extract
or promote shared platform infrastructure.

## Minimum visible experience

The W1 Work surface remains conversation-first and shows only the active work,
authorized sources in use, latest artifact, common disposition, and verified
receipt or conflict. It supports reading, copying/downloading, and an explicit
path for Robert to return edited text. It adds no board, pipeline, bulk intake,
repository browser, or form-driven workflow.

Plain Markdown/text is the first artifact format. Email, Telegram, watched
folders, richer output formats, and broader browse/search remain later slices
informed by Career Workbench v0.4.

## Required tests

The coverage in Claude's §18 remains required, with these corrections:

- common code and records contain no required product identity;
- configured write and read roots obey their distinct policies, and a model
  cannot widen either boundary;
- the approved-output projection returns only disposition-pinned artifacts,
  excludes continuing/unapproved work, and preserves authorship class;
- the non-Career decision-memo fixture uses different guidance and source
  roots without `subject_extension`;
- the Robert-edit fixture returns changed bytes and creates a new co-authored
  revision without modifying an artifact;
- adapter removal leaves canonical records unchanged because no canonical
  receipt contains adapter metadata;
- Career accumulation proves prior approved/co-authored material can be
  retrieved while unreviewed agent drafts are excluded as Robert-source
  evidence; and
- no test imposes an 80/20 score, edit quota, or predetermined role taxonomy.

Every W0a and W0b test is named and structured for pytest collection and runs
under the repository's normal CI invocation. Existing script-only conformance coverage
must be converted to a collectable test or receive a collectable twin. Running
successfully only beneath `if __name__ == "__main__"` does not satisfy the gate.

Test modules may be grouped differently from Claude's proposed file list. The
behavioral contract and security coverage, not the number of modules, control.

## Delivery gates

### W0a — accumulation reference

Deployment-configured read-only source roots with path and public-Git exposure
validation; provenance-preserving bounded search/read; the subject-scoped
approved-output projection; and synthetic tests proving that Robert sources
and disposition-pinned outputs are retrievable while unapproved drafts are not
presented as Robert. The implementation remains Career-first and is not placed
in a universal shared `core/` framework.

Gate: collectable tests run in normal CI; reviewed diff; Robert's approval.

### W0b — Work service

Provider-neutral records, configuration/root checks, path confinement,
grants, the eight effects, immutable snapshots, atomic/idempotent writes,
content-free receipts, lazy recovery, the product-free in-process adapter, and
fully synthetic Career and decision-memo fixtures. Work consumes W0a's
root/search/read boundary rather than creating a second retrieval mechanism.
No runtime adapter, UI, personal data, source migration, or external model.

Gate: W0a's reviewed contract remains intact; reviewed diff; Robert's approval.

### W1 — runtime adapter and Work surface

The replaceable adapter for a selected CoS runtime; verifiable egress-free
profile; reduced private-work context; explicit identity on Work routes; the
minimal Work surface; suppression of backend-judged memory writes; and a
demonstration that canonical work survives adapter removal.

### W2 — first real Career collaboration

Robert selects the opportunity and important/exploratory mode, private root,
approved reasoning route and disclosed retention boundary, current resume, and
authorized read-only source roots. Existing Career sources may be referenced in
place. One real collaboration produces Robert's lived disposition; it does not
authorize submission or broader automation.

Each gate requires a separately reviewed diff and Robert's explicit approval.
Planning approval is not build approval, and build approval is not permission
to ship.

## Resolved and deferred choices

Resolved for the specification:

- visible working label: `Work`;
- deterministic closed approval/close phrase set plus slash commands;
- safe degraded mode when private drafting cannot be enforced; and
- suppression of automatic backend memory writes during work turns.

Deferred to W2 because real private use determines them:

- actual private-root path or mount;
- allowed model/reasoning route and retention disclosure;
- first real opportunity and collaboration mode; and
- the exact existing Career folders Robert authorizes in place.

These W2 selections do not block synthetic W0a or W0b builds. They do block use
of real Career bodies.

## Stop condition

The planning package is final. Robert authorized Claude Code to implement W0a
on 2026-09-04. No W0b work, source migration, letter production, external
research, runtime integration, or other implementation is authorized. Codex
reviews the checkpoint and final diffs; Robert approves the reviewed W0a diff
before merge or release.
