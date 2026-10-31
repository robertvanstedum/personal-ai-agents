# Master Craftsman container release: next tag `mc-agent-v0.1.23-1`

**Not tagged.** A pushed `mc-agent-v*` tag deploys production at once (`.github/workflows/deploy-mc-agent.yml` builds,
pushes and deploys in one run; no approval gate). The tag waits for Robert's word, after staging has run this release.

## What changes for MC

- **One persona again: 0.1.23.** It merges the two lines that each branched from 0.1.16:
  - 0.1.17, outside reports and GitHub issues;
  - 0.1.18–0.1.22, the voice card (OpenAI marin, Grok leo), byte for byte.
- **The handbook is in what MC's container loads.** The persona release names MC handbook 0.1.1 by the SHA-256 of
  `services/mc_operations/handbook/manifest.json` (`f6e9575e…`). On every start, `start.sh`:
  - checks the image's handbook against that hash, and every file against the manifest;
  - lays the files out in MC's workspace (`/home/node/.openclaw/workspace-mc/handbook/`), since mc-chat's file tools
    are workspace-only;
  - ends `AGENTS.md` with the handbook block: a header naming the pinned folder, **the manifest's path** (finding 6)
    and its hash, then the engineering SKILL.md verbatim. It is the block native mc-chat has loaded since 30 Oct.
    PROJECT.md, ADOPTION.md and the references are read at their paths.
- **Counted in the budget:** the projection is 14,362 of the 16,000 characters start allows (OpenClaw's per-file cap
  is 20,000).
- **One copy of the outside-reports rule, not two.** SKILL.md carries 0.1.17's section word for word. 0.1.17 put a
  copy in AGENTS.md only because the container did not load the handbook (review fix M1). So 0.1.23's AGENTS.md is
  0.1.16's apart from its header, and the rule arrives through the handbook block.
- **Start refuses** (exit 78) when the release names no handbook, or any handbook byte differs from the pin.

Production and staging today run `mc-agent-v0.1.16-5`: persona 0.1.16, no handbook.

## Not changed

- MC's config (`openclaw.json`), tools, mounts, egress and sign-in.
- Native MC on the Mac: it has its own projection (persona 0.1.13 plus the native binding) and its own handbook install.
  Its `AGENTS.md` headroom under OpenClaw's 20,000 cap stays 1,113 characters.
- The portal's voice: it reads the card through `pin.json` on the voice line, not in this release. When that line
  merges, its pin moves to 0.1.23. The card is the same bytes.
- CoS: her handbook comes from `docker/cos-agent-a/build-mc-handbook.mjs`, unchanged.

## Checks

- **Tests:** `tests/mc_persona/test_container_persona.py` runs `start.sh`'s own persona step on a copy of what the
  Dockerfile copies.
  - **Pass with:** the handbook block is in the loaded `AGENTS.md`, pinned by the manifest hash, with the manifest's
    path named. Every file the block names matches the manifest. The rule appears once, the handbook's words. The merged
    persona carries every line of 0.1.17's contract and 0.1.22's card. Tampering and a missing pin each refuse. A
    workspace `handbook/` that MC changed, symlinked or locked (chmod) is replaced at the next start, so start never
    wedges on it.
  - **Fail without:** the same tests on the code before this release fail. With only `start.sh` reverted, the
    handbook block is missing and 0.1.23 would lose the outside-reports rule. Both caught.
- **In the image:** the workflow runs `start.sh` steps 1–2 inside the built image and requires the handbook block before
  it pushes anything. `scripts/ops/mc_container_security_test.py` gains check 6c: start refuses a handbook that
  differs from the pin.

## Rollout

1. **Merge** (Robert). This PR is stacked on the handbook 0.1.1 PR (`claude/mc-handbook-0-1-1`), which goes first.
   Main's classifier calls it a full release, because persona folders sit under `services/`. MC's container changes
   only through its tag.
2. **Staging first** (operations). The tag can't be pulled before production gets it: the image reaches ECR only in
   the same run that deploys production. So staging builds the image on the host from the merged commit, with the
   Dockerfile and the image check the workflow uses. Then it runs it in staging's `minimoi-mc` project in place of
   `mc-agent-v0.1.16-5`. Then:
   - `start.sh` logs `handbook 0.1.1 loaded` and `persona 0.1.23 laid out`;
   - the security test passes 6a, 6b and 6c;
   - in a new Guild conversation, ask MC which handbook version it works from and the manifest hash prefix. It passes
     with 0.1.1 and `f6e9575e`, read from its context or checked against the manifest at the named path;
   - ask "Someone outside opened an issue on our repo. What do we do first?" It passes if the answer is that Robert
     replies first and nothing links to the issue before that.
3. **Production** (on Robert's word): push `mc-agent-v0.1.23-1` at the commit staging ran. Then the same checks.

**Rollback:** redeploy `mc-agent-v0.1.16-5` with the workflow's `workflow_dispatch` (an existing approved tag). MC's
volumes are untouched. The workspace `handbook/` folder stays behind, unread by 0.1.16.
