# Spec: Adopt a Shared Specification Standard for All Agents

**File:** `docs/specs/spec_144_specification_standard_adoption_2026-08-08.md`
**Date:** 2026-08-08
**Status:** Built - verification pilot active
**Owner / decision point:** Robert
**Build queue:** #144
**GitHub issue:** None
**Roadmap / governing spec:** `AGENTS.md`; `docs/specs/SPEC_TEMPLATE.md`
**Dependencies / blockers:** No implementation blocker; closure requires Robert's pilot verification
**Reviews:** Grok 2026-08-08 (design, approved with refinements); Robert 2026-08-08 (design approval); pending pilot verification
**Implementation diff review:** Robert 2026-08-08 (approved scoped documentation/process diff)
**Robert ship approval:** 2026-08-08

## 1. Decision summary

- Adopt `docs/specs/SPEC_TEMPLATE.md` as the shared specification pattern for
  Claude, Codex, OpenClaw, Grok, and future agents working in this repository.
- Make Markdown in GitHub the source of truth. PDF remains an optional, dense
  reading export rather than a maintained parallel document.
- Add a durable pointer in `AGENTS.md`; do not duplicate the full standard in
  agent instructions.
- Run a human pilot over the next three specifications, including at least one
  authored by Codex and one by Claude. Robert decides whether the standard is
  being followed usefully rather than mechanically.
- Register the pilot as Guild build-queue item #144 and leave it active until
  Robert completes that verification.

## 2. Context and evidence

### Current state

The repository contains specifications ranging from roughly 60 to more than
600 lines. Strong documents share useful elements - intent, scope, verified
implementation surface, testing, and definition of done - but metadata,
section order, status language, review records, and treatment of open questions
vary substantially.

Earlier documents also show recurring drift:

- small changes become over-specified;
- review transcripts are appended instead of integrating their decisions;
- unresolved protocol or security questions remain buried in prose;
- PDFs or formatted handoffs risk becoming a second representation;
- `v2` and `v3` files can multiply during review even when one draft would do;
- acceptance language sometimes describes activity rather than an observable
  result.

### Problem or opportunity

Every agent should produce a handoff that Robert can scan quickly, another agent
can implement without guessing, and GitHub can preserve as the durable record.
The standard needs enough structure for architecture work without forcing a
small defect into a long document.

Grok reviewed the draft standard and approved it with light refinements. Those
refinements are incorporated: `Blocked` and `Superseded` statuses, consistent
review notation, stronger treatment of implementation-changing open questions,
explicit omission of empty design subsections, proportional size guidance, and
a stable template-versioning strategy.

## 3. Goals and non-goals

### Goals

- Give every agent one canonical template and writing standard.
- Keep specs decision-first, dense, proportional to the work, and readable on
  GitHub without special tooling.
- Make decided, open, blocked, and out-of-scope items unmistakable.
- Require observable verification and binary acceptance criteria.
- Preserve Robert's separate design-approval and reviewed-diff approval gates.
- Test the standard through real use before calling the process complete.

### Non-goals

- Reformatting or rewriting the historical specification archive.
- Adding a CI linter or blocking builds automatically during the pilot.
- Requiring every optional template section in every document.
- Replacing issues, roadmap entries, or the Guild build queue.
- Making PDF generation part of the required spec workflow.
- Evaluating the technical correctness of the next three specs under this item;
  #144 evaluates whether their handoff structure follows the standard.

## 4. User and agent flow

1. An agent reads `AGENTS.md` and follows its link to the canonical template.
2. The agent copies the pattern conceptually, omitting sections that contain no
   real constraint or decision.
3. The resulting Markdown spec is reviewed through the normal design process.
4. Reviewer findings are incorporated into the relevant sections rather than
   pasted as a transcript.
5. Robert approves the design before implementation and later approves the
   reviewed implementation diff before shipping.
6. During the pilot, Robert checks the next three specs against the short
   verification rubric in this document and records the result in queue item
   #144's notes or history.

## 5. Design contract

### Architecture and ownership

- `docs/specs/SPEC_TEMPLATE.md` is the canonical artifact and keeps a stable
  path so references from agents and handoffs do not break.
- `AGENTS.md` contains only the durable requirement and link. The detailed
  standard is not copied into multiple instruction files.
- Git history records ordinary template changes. A major version changes only
  when required structure or the approval contract changes.
- Draft specs are edited in place. A new spec version is created only when an
  already shared or approved decision is materially replaced and the prior
  document must remain understandable; the two versions cross-link and the old
  one is marked `Superseded`.

### Process boundary

The template guides judgment; it is not a checklist that rewards empty
headings. The author remains responsible for verifying repository facts,
identifying genuine blockers, scaling detail to risk, and writing testable
acceptance criteria.

PDF export is optional. When requested for reading or external review, the
default is a dense rendering of the Markdown, without presentation formatting
unless Robert asks for it.

## 6. Scope and implementation surface

### In scope

- Publish the canonical specification standard.
- Point all repository agents to it through `AGENTS.md`.
- Register queue item #144 for the human pilot.
- Evaluate the next three specs, with Codex and Claude both represented.

### Expected code surface

- `docs/specs/SPEC_TEMPLATE.md` - canonical standard and reusable template.
- `docs/specs/spec_144_specification_standard_adoption_2026-08-08.md` - adoption
  decision, pilot, verification, and closure criteria.
- `AGENTS.md` - durable all-agent instruction and template link.
- `data/guild/build_queue.json` - queue item #144.

### Out of scope

- Historical spec cleanup.
- Changes to Guild status values or queue application code.
- Automated validation of Markdown structure.
- Changes to role ownership in `AGENTS.md`.

## 7. Dependencies, blockers, and open questions

### Dependencies and blockers

- **Pilot evidence:** #144 cannot close until three new specifications have been
  reviewed, including at least one from Codex and one from Claude.
- **Robert's judgment:** mechanical section compliance is not sufficient;
  Robert decides whether each handoff is genuinely clearer and easier to use.

### Open questions requiring a decision

- After the pilot, decide whether human review is sufficient or whether a small
  optional checker would add value without encouraging boilerplate.
- After the pilot, decide whether any section or size guidance needs adjustment
  based on actual use.

## 8. Delivery and rollback

### Sequence

1. Commit the four process artifacts and the separately approved #145 first
   pilot specification as one scoped documentation/queue change.
2. Confirm the files render correctly on GitHub and queue item #144 is visible.
3. Apply the standard to the next three new specifications.
4. Robert records pass/fail observations and either closes #144 or requests a
   focused template revision.

### Rollback or failure behavior

If the standard produces boilerplate or makes handoffs harder to read, remove
the `AGENTS.md` requirement and revise the template from the pilot evidence.
Existing specs and implementation workflows remain usable; no application or
production runtime depends on this process artifact.

## 9. Verification

### Repository checks

- Markdown renders without broken headings, tables, links, or placeholder
  syntax in the adopted standard and adoption spec.
- `AGENTS.md` links to the exact stable template path.
- Queue item #144 parses correctly and appears as active work.
- The scoped commit contains only the four intended process artifacts and the
  separately approved #145 pilot specification.

### Human pilot

For each of the next three specifications, Robert checks:

- the decision and boundaries are understandable from the opening section;
- decided, open, blocked, and out-of-scope work are separated;
- detail is proportional to the change;
- verified implementation surfaces are distinguished from assumptions;
- material open questions are not buried in prose;
- verification describes observable evidence;
- acceptance criteria are binary enough to approve or reject;
- review findings are integrated rather than pasted as a transcript;
- Markdown is the complete source of truth and reads well on GitHub.

### Production sanity check

None. This change affects repository process and Guild queue data only; it does
not deploy application code or alter production behavior.

## 10. Acceptance criteria

1. The canonical template is committed at `docs/specs/SPEC_TEMPLATE.md`.
2. `AGENTS.md` instructs all agents to use the template for new or materially
   revised specifications.
3. Guild queue item #144 remains active with a test-only verification note.
4. The next three specifications include at least one from Codex and one from
   Claude and are reviewed by Robert using the pilot rubric.
5. Robert confirms that the standard improves clarity without encouraging
   unnecessary length or empty sections.
6. Any pilot adjustments are incorporated and #144 is marked `done`, or the
   item records the specific unresolved deficiency.

## 11. Review and approval record

- **Design review:** Grok 2026-08-08 - approved with light refinements, all
  incorporated.
- **Robert's design approval:** 2026-08-08.
- **Implementation diff review:** Pending Robert's review of the scoped diff.
- **Robert's ship approval:** Pending explicit approval of the reviewed diff.
