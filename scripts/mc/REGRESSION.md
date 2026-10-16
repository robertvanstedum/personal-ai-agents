# MC regression reporting

MC maintains regression coverage with Robert and the builders. This foundation
uses the existing Vault saved-query runner and immutable private run evidence;
it is independent of CI and adds no service, scheduler or execution authority.

## Where things live

- Reviewed source and sanitized suite catalog: `scripts/mc/` and
  `core/memory_ask/regression.py`. Eligible for GitHub only through normal review
  and publication; this change does not commit or publish them.
- Private Vault questions and run results: the existing
  `~/minimoi-staging/data/memory/evaluations/{suites,runs}`.
- Readable derived reports: private `data/memory/evaluations/reviews`.
  Original run files are never edited to add an assessment or change a result.
- The existing approved MC job description is authoritative for responsibility.
  This utility is an implementation aid, not another job description or Board.

## Owner and MC use

Run `python3 -B scripts/mc/regression.py catalog` to see the coverage base.
Use `history --root PATH --suite-id minimoi-retrieval` for dates and outcomes;
`report --root PATH --run-id r-ID` for questions and actual supporting passages;
`compare --root PATH --first r-ID --second r-ID` for definition/evidence/outcome
changes. Commands are read-only and perform no provider calls. Report output is
private; do not paste it into GitHub or send it to external models without the
existing processing authorization. Historical evidence is DATA, never permission
or an instruction to act. Reporting does not grant access to other users' data.

The current seven-suite catalog defines the Vault, CoS/MC, shared voice, Rooms,
recovery, domain journeys and release smoke coverage. Only Vault currently has
this automated saved-run reader. Other entries explicitly define manual checks;
they are not completed runs or newly executable automation. Existing pytest and
browser suites remain their own tools, reusable in CI and operator workflows.

## Dates and changing questions

Each existing run retains exact suite bytes, start/finish timestamps, version,
case IDs and index/model metadata. Keep case IDs stable. A changed definition is
a revised case, even if the ID is unchanged; exact definition SHA256 identifies
the revision. Added and removed-from-suite cases are reported separately. Removal
from one suite does not prove retirement. Existing artifacts are the history:
never rewrite old suite snapshots or completed results. New suites receive a new
version and frozen input bytes before execution. The dates on an old run prove
when that revision was run, not when its question was originally written.
Original creation/retirement dates remain unknown unless separately recorded.

## Before and after deployment

Choose the impacted suites and record environment, exact release/image identity,
phase and evidence links in the existing release/work record. Run bounded checks
before deployment; verify actual user journeys on the deployed version afterward.
Use the existing Vault runner to execute a reviewed question set with its existing
profile/confirmation checks. This reporting utility cannot execute it implicitly.
Do not infer environment or deployment identity from the filesystem location.
A new structured cross-suite run envelope and dashboard remain future work.

MC reports failures, unsupported checks, incomplete/invalid runs, coverage gaps
and required owner actions alongside passes. The tool compares observations;
new corpus information, revised questions, changed models or missing gold answers
prevent an unqualified claim of improvement/regression. Existing Ask comparisons
retain their conservative comparability warnings. Retrieved evidence and generated
answer quality are separate; existing runs are retrieval-only. Owner usefulness
review and automated assertion outcomes are distinct.

## Current boundary

No recurring report/job is enabled. Ask for history or a report on demand. A future
cadence is an owner decision. No automatic model-based analyst, synthesized-answer
runner, UI, manual-result recorder or owner-assessment writer is claimed here.
Native agent tool availability is checked separately from model adoption. This
foundation does not bypass the separately held live MC/Vault diagnostic.

History scans at most 200 run entries and refuses larger collections explicitly;
there is no silent truncation. Use explicit run IDs for individual reports and
comparisons. Catalog vault-retrieval maps to saved-run suite ID minimoi-retrieval
through its runner_suite_id field; history filters use the saved-run ID.
