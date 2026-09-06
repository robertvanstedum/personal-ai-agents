# Handoff to Claude Code — register Mini-moi Work in Prototype Lab

**Prepared by:** OpenAI Codex  
**Date:** September 5, 2026  
**Decision owner:** Robert van Stedum  
**Authorized scope:** Prepare and verify the Prototype Lab project diff  
**Not authorized:** Merge, production deployment, CoS integration, or release

## Intent

Register the current Mini-moi Work proof of concept under the existing Guild
Prototype Lab. This is one experiment with two deliverables:

1. the Mini-moi Work code/capability; and
2. the agent-and-Robert team process, whose current artifact is the reviewed
   case-study chain and whose possible later destination is Master Craftsman
   material.

The project is in the `prove` portion of `try → prove → keep`. Do not create a
new studio, product area, or second codebase.

## Target tree

Copy this reviewed candidate directory unchanged to:

```text
prototype-lab/projects/project-mini-moi-work-poc/
```

Required root files:

- `START_HERE.md`
- `DECISIONS.md`
- `project.yaml`
- `build/POC_VALIDATION_PLAN.md`
- `spec/`
- `docs/CASE_STUDY_AGENT_TEAM.md`
- `docs/MINI_MOI_WORK_VISION.md`
- `evidence/case-study/`
- `handoffs/HANDOFF_TO_CLAUDE_CODE.md`

The implementation remains authoritative at `domains/cos/work/` and its tests
under `tests/cos/`. Do not copy implementation code into Prototype Lab.

## Repository facts to respect

- W0a is merged as PR `#200`, commit `476568d`.
- W0b is independently cleared at `c9cdde7`; PR `#201` was still open and
  unmerged at 10:45 CDT on September 5. Recheck before preparing the diff.
- The primary checkout contains unrelated user changes. Use a clean worktree
  from current `origin/main`.
- The design originated under local Planning Studio initiative
  `INIT-2026-0004`, but this project must not depend on Planning Studio becoming
  a production system. The accepted specification is included in `spec/`.
- Planning Studio's publication is currently carried by open PR `#192`. Do not
  expand, rebase, or force-push that PR in this registration slice.

## Known registration defect — do not guess

`data/guild/experiment_projection.json` currently uses `INIT-2026-0004` as a
placeholder identity for IoT Connect. The preserved design provenance for this
project also uses `INIT-2026-0004` for Mini-moi Collaborative Work. Adding this
project to the Guild Experiment page without resolving that collision would
create two different activities with one identity.

For this slice:

- create the tracked Prototype Lab project home;
- do not change the projection fixture or Guild UI;
- record the collision in the build report; and
- propose the smallest follow-up identity/projection correction for Robert and
  Codex to review. Do not invent replacement initiative IDs.

## Verification

1. Confirm the target tree contains no `.DS_Store`, ZIP, cache, secret, private
   Career source, resume, job posting, or cover-letter body.
2. Confirm every internal Markdown link resolves within the target tree.
3. Parse `project.yaml` with the repository's available YAML parser.
4. Confirm the case-study source v9 hash is
   `c9af69fc5a416d008215a928f6ee5f870c6a4ba630db2d67a17a4cbab742e4e6`.
5. Recheck every PR status cited by the current case study, especially `#201`.
   If `#201` changed, update all related passages together using the included
   public-readiness note.
6. Run the repository's document/release classification checks relevant to the
   added path. Do not trigger a production service deployment for a docs-only
   Prototype Lab registration.
7. Report the complete diff, file list, line/byte counts, checks, and candidate
   commit hash to Codex for independent review.

## Gate after implementation

Do not push or merge from this handoff alone. Robert reviews the independently
reviewed diff and explicitly authorizes the next Git operation. After the
project-home diff is accepted, the next work is the file-level test in
`build/POC_VALIDATION_PLAN.md`, followed later by the thin Chief of Staff
“Start cover letter” integration.
