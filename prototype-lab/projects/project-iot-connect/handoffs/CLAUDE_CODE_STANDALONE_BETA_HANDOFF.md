# Claude Code Handoff — Connect HQ Standalone Beta

**Workstream:** WS1  
**Status:** Isolated sanitized candidate authorized 2026-09-02; repository import pending review  
**Target release:** `connecthq-v0.9.0-beta.1`

## Objective

First promote the existing Connect HQ IoT prototype from the ignored working
area into this tracked project directory and prove it runs here unchanged. Then
turn it into a portable, repeatable, GitHub-versioned standalone beta without
adding the fiber scenario or silently redesigning the demonstrated business behavior.

## Source

The current working prototype is Project Nightjar under the local Prototype Lab
working area. Inspect the actual source, tests, runbooks, generated artifacts,
and current runtime before proposing changes. Do not rely solely on prior agent
summaries.

## Required first output

Before editing code, return:

1. repository and runtime inventory;
2. current container/process topology;
3. current dependency and secret inventory;
4. existing test and reset behavior;
5. proposed tracked destination;
6. minimal migration sequence;
7. risks to the current demonstration; and
8. the exact file-level implementation plan.

Robert has authorized the audit and implementation of an isolated sanitized
candidate under `build/OVERNIGHT_STANDALONE_BUILD_PLAN_2026-09-02.md`. The
required first output must still be recorded before the candidate is changed.
No result may be copied into the tracked project or enter Git until Robert
reviews Codex's second-pass findings and the actual diff.

## Step 0 — Tracked-source promotion

The first implementation increment is the move out of `_working`:

1. Inventory the current prototype and identify source, generated output,
   runtime state, local environment, private context, and disposable caches.
2. Copy commit-safe source and artifacts into
   `prototype-lab/projects/project-connect-hq/` without overwriting the project
   control documents blindly.
3. Exclude `.venv`, `.pytest_cache`, `.DS_Store`, secrets, local databases,
   unsanitized evidence, and other machine-specific state.
4. Update only paths that must change for the application to run from its new home.
5. Start and exercise the current application from the new directory before
   containerization or structural refactoring.
6. Compare UI, API, database, integration-mock, test, reset, presentation, and
   evidence behavior with the original working copy.
7. Present the promotion diff and verification evidence to Robert.
8. Treat the old ignored directory as recoverable source until Robert explicitly
   approves its retirement or archival.

Step 0 is complete only when the existing demo runs from the tracked directory.

## Required capabilities

- One documented command to start the complete stack.
- One documented command to stop it without losing the baseline.
- One documented command to reset deterministic seed data.
- One documented command to report health and dependency state.
- One documented command to run smoke tests.
- Application, PostgreSQL, and synthetic external services run portably.
- No requirement for a host `.venv` or absolute user path.
- No committed secrets or live vendor credentials.
- Postman and Swagger endpoints use stable documented URLs.
- The existing IoT success and rollback scenarios remain functional.
- The existing summarized-billing behavior remains functional.
- Activation/integration outcomes survive application restarts when required by
  the demonstrated workflow.
- Presentation assets reference stable project paths rather than dated draft paths.
- Product naming is consistent in user-visible artifacts.

## Verification matrix

| Test | Expected result |
|---|---|
| Clean checkout | No missing local-only code or assets |
| Cold start | Entire stack reaches healthy state |
| Reset | Exact approved seed state is restored |
| Restart | Persisted business evidence remains coherent |
| IoT activation success | Network and billing policy outcomes are visible |
| IoT activation failure | Partial work is rolled back or visibly reconciled |
| Summarized billing | Subscription and bill-file behavior follows the policy |
| Postman/Swagger | Saved evidence calls return expected records |
| SQL evidence | Queries match UI and API results |
| Stop/start | Named database state behaves as documented |

## Constraints

- Preserve the user's unrelated working-tree changes.
- Do not modify protected repository documents without explicit authorization.
- Do not create issues, alter roadmap/change-log files, commit, push, or open a
  pull request without Robert's explicit reviewed-diff approval.
- Do not insert employer, interviewer, client, or real customer identities into
  code, fixtures, Git metadata, decks, or documentation.
- Do not add Salesforce or fiber scope to this release.
- Prefer a small packaging/refactoring diff over an architectural rewrite.

## Completion evidence

Provide the actual diff, test output, clean-checkout instructions, dependency
diagram, reset proof, and release checklist. A summary is not a substitute for
reviewing the diff.
