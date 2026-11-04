# Partnership task service contract, v1

Specification: partnership and team workflow v1.1 (sha256 `191df665…4130e`), sections 2, 3, 5 and 8. This contract is
independent of the frozen CoS contract (`contracts/cos/v1`) and of Records. The task service validates every command and
every stored record against these schemas at runtime.

| File | Holds |
|---|---|
| `common.schema.json` | ids, seats, scopes, environments, refs, runtime identity, return window |
| `records.schema.json` | source event, interpretation, request, task, coverage, assignment |
| `command.schema.json` | the command envelope, one payload per kind, results, receipt lookups |
| `evidence.schema.json` | journal entries (the evidence chain), exported chain heads, seat bindings |

Canonical digest: `core/canonical_json.digest` (sorted keys, compact separators, UTF-8), as everywhere in MiniMoi.

## Wire states (D04)

These names are the wire format. An external mapping (A2A or another protocol) maps from them and never collapses
an unknown execution into `failed` or `completed`.

| Object | States | Guarded transitions |
|---|---|---|
| Request | `recorded`, `active`, `answered`, `failed`, `withdrawn`, `promoted` | recorded → active → answered/failed; recorded/active → withdrawn; recorded/active → promoted (with task link). Refinement appends an edition and returns an answered request to `active`; earlier results stay with their edition. |
| Task | `submitted`, `working`, `waiting`, `completed`, `failed`, `canceled`, `rejected` | submitted → working (with pickup evidence); submitted/working → waiting (reason + who can unblock); waiting → working; working/waiting → completed only by `task.accept` from the current lead; submitted → rejected; any active → failed/canceled with evidence. A terminal task reopens only by `task.reopen`, as a new revision. |
| Interpretation | `proposed`, `adopted`, `corrected`, `rejected`, `unresolved` | proposed → any other by `interpretation.dispose`. A correction records a new interpretation that supersedes. |

`waiting.reason` ∈ `input`, `authorization`, `dependency`, `lead_unavailable`.

## Authority rules the writer enforces

- A command never names its actor or origin. The seat binding behind the caller's credential supplies both (A02).
- `task.accept` is valid only from the task's current lead (E07). A worker's report is a transition with evidence, never acceptance.
- Changing the lead requires `task.assign` with `role: lead` from the current lead or from an `owner_session` binding (D01).
- Promotion requires an `adopted` interpretation whose `authority.kind` is not `none` (Q02: adoption alone is not approval).
- Every correction bumps `fence_generation`; a transition, answer or dispatch carrying an older generation is refused as `fenced` (C06).

## Not in v1

Harness admission/outbox records, review bundles, delivery, obligation joins and cost receipts are batch B2; Practice Book
load receipts are B3. They join through `ref` pointers; this contract never duplicates a lease, grant or occurrence.
