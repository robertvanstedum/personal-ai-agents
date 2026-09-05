---
id: 01M1JB8V486QS99B6XNTA4C49H
series: claude-code-github-publication-handoff
slug: claude-code-github-publication-handoff--nta4c49h
type: note
kind: handoff
title: "Claude Code handoff — publish Planning Studio within mini-moi"
revision: 1
created: 2026-09-02
authors: [robert, codex]
status: ready_for_handoff
decision_owner: robert
publication_authority: approved
implementation_authority: repository_publication_only
domains: [guild, cos, curator, platform]
tags: [planning-studio, github, publication, handoff, privacy]
relations:
  - type: derived_from
    target: 01M1J8V5KZXNS7MKSA0EFMWP2J
---

# Claude Code handoff — publish Planning Studio within mini-moi

## Robert's direction

Planning Studio is a capability deeply tied to mini-moi and expected to feed
its domains. Do **not** create or position it as a standalone product or
standalone Planning Studio repository.

The earlier “vault” language was exploratory thinking, not an approved product,
directory, or repository name. Do not introduce that name into the design.

If another person's mini-moi is created later, it should receive reusable
capability code and approved cold-start material only. Robert's accumulated
sources, decisions, and personal data must not be included.

Robert has authorized publication of the current Planning Studio design package
to the appropriate private GitHub location for mini-moi.

## Current local state

- Repository root: `~/Projects/personal-ai-agents`
- Current branch: `codex/cos-agent-a-runtime`
- `planning-studio/` is currently untracked.
- The current worktree has unrelated modified and untracked files. Do not stage,
  alter, discard, or commit them.
- `origin` points to `robertvanstedum/personal-ai-agents`.
- `private` points to `robertvanstedum/mini-moi-private`.
- Local `private/main` is substantially behind `origin/main`; do not silently
  synchronize or rewrite it as part of this publication.
- The GitHub CLI token was invalid when Codex checked it. Re-authentication may
  be required.
- A dedicated `robertvanstedum/mini-moi-planning-studio` repository was checked
  and does not exist. Robert's current direction is not to create it.

## Publication objective

Publish the complete current `planning-studio/` directory as part of mini-moi
without exposing it through a public remote or mixing it with unrelated local
changes. Preserve its existing file paths, source bytes, IDs, checksums, and
visible revision history.

The current package includes:

- the preserved v0.1 scaffold;
- the original v0.9 design candidate;
- the revised v0.9 candidate, repository revision 2;
- Claude, Codex, and Grok review material;
- Robert's recorded decisions;
- exact Curator and Grok source artifacts;
- the two-to-three-week thin-slice direction; and
- the Word brief prepared for Grok.

## Required preflight

1. Read the repository `AGENTS.md` and `_NewDomains/PROJECT_STATE.md`.
2. Inspect the actual remotes, default branches, remote visibility, and current
   divergence. Do not infer privacy from the remote name.
3. Confirm which existing private mini-moi remote is the intended destination.
   Prefer the existing private mini-moi boundary if it is sound; do not create a
   standalone Planning Studio repository.
4. Use a clean worktree or equivalent isolation based on the appropriate
   current base. Do not commit from the dirty `codex/cos-agent-a-runtime`
   worktree.
5. Copy only `planning-studio/` into the clean publication worktree and verify
   that no unrelated paths are staged.
6. Verify the preserved Grok source checksum:

   `aa29a41e08f4ae595da9acddcb96f47426f6c1992f15e5bb321b899bfbc73d73`

7. Verify that frontmatter IDs are unique and that relative links resolve.
8. Check the package for credentials, tokens, private keys, and unintended
   personal data before any push.
9. Show Robert the exact staged diff and proposed remote/branch before the
   commit and push if the destination or visibility differs from the existing
   private mini-moi expectation.

## Commit boundary

The commit must contain only the Planning Studio package. Suggested commit
subject:

```text
docs: add Planning Studio design and evidence package
```

Do not modify protected root documentation, the Guild queue, changelog,
roadmaps, application code, Curator runtime data, or unrelated `.gitignore`
rules in this publication commit.

## Privacy and portability boundary

This publication records the current design and its first evidence sources. It
does not settle the final physical storage design for future personal data.
Before adding broader personal material, return a concrete proposal covering:

- what stays in the reusable mini-moi repository;
- what is private instance data;
- how private data is excluded from a spawned person's cold start;
- version history and immutable-source retention;
- backup and restore; and
- prevention of accidental publication to a public remote.

Do not build that storage mechanism in this publication task.

## Required completion report

Return:

1. the selected GitHub repository and confirmed visibility;
2. branch name;
3. commit SHA;
4. pushed remote URL;
5. the exact path scope committed;
6. checksum verification result;
7. secret-scan result;
8. confirmation that unrelated dirty files were untouched; and
9. any remaining privacy or repository-topology concern.
