# MC handbook adoption and revision

Version: 0.1.1 · 15 October 2026
State: review candidate; not installed by this change.

## Source and versioning

The manifest names this candidate and hashes every release file. A deployed runtime
must pin an explicit reviewed version and content hashes; do not load a mutable
GitHub branch as live instructions. Keep earlier reviewed releases for provenance.
Use a new version for changed released instructions, with predecessor and reason;
Git diff/review history supplies the change record. Do not clone the handbook for
each environment or agent: keep common engineering guidance and verify the active
role/environment at runtime.

Source decisions: Robert's standing MC job description/working agreement and dated
MiniMoi 1.2 direction in initiative INIT-2026-0007-interaction-vision; repository
AGENTS.md; the existing services/mc_operations/workspace operational instructions.
This release makes those practices explicit for use, and does not authorize new
production operations or expand access.

## Loading is a separate build/verification step

Two distinct instruction surfaces were found during source review; their GitHub
availability differs:

- The separate operational MC workspace has integration-candidate files at
  services/mc_operations/workspace/AGENTS.md, its minimoi-operations skill and
  services/mc_operations/profile.py. These are not in this public base or included
  in this artifact-only PR; their runtime installation must be inspected separately.
- The public base includes docker/cos-agent-a/AGENTS.md and companion workspace
  files for the shared CoS runtime, seeded by docker/Dockerfile.cos-agent-a.

The existence of the first surface does not prove the shared CoS runtime reads it.
The shared image also uses persistent runtime state; changing a repository seed
file alone does not prove an existing session adopted the change. Room standing
briefs are another path, not evidence of direct MC/CoS session adoption.

Before activation, the builder must inspect the actual selected agent/workspace,
loading behavior, session reset/refresh behavior and granted tools. Prepare a
reviewed installation mapping for this project brief, skill and references; keep
previous files and version receipt. Avoid overwriting unrelated identity, memory,
credentials, sessions or owner state. This artifact-only PR changes none of them.

## Acceptance before calling the handbook active

1. Verify installed files against manifest hashes and record host, environment,
   selected agent/workspace, version and installation time privately.
2. Ask a fresh MC session to identify its role, environment, handbook version and
   available action paths. Its statement is not enough: compare it with installed
   hashes and actual request/tool evidence.
3. Exercise a real Board task: correct namespace and current note, actual bounded
   service check, honest unknowns, and a durable outcome receipt. Prose/fenced JSON
   without a receipt fails. Directory listings alone fail service availability.
4. Verify existing and fresh conversations follow the intended refresh behavior;
   record any session that still uses the prior version. Test CoS's shared runtime
   separately from a standalone MC or a meeting Room.
5. Obtain independent review and Robert's acceptance. Record built/reviewed,
   installed, behavior verified and owner accepted separately. Roll back only
   instruction files/version selection if needed, preserving owner data/history.

Unresolved integration work belongs to the existing MiniMoi 1.2 work record,
not a second backlog. These acceptance examples are operating criteria, not a
claim that automated tests or a live installation were delivered by this PR.
