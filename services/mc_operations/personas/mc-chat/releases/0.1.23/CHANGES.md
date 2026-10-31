# 0.1.23 — one persona again, with the handbook in what the container loads (30 Oct)

- Merges the two lines that each branched from 0.1.16: 0.1.17 (outside reports and GitHub issues) and 0.1.18–0.1.22
  (the voice card). voice.json is 0.1.22's, byte for byte (OpenAI marin, Grok leo). SOUL.md and RUNTIME.openclaw.md are
  unchanged, byte for byte (the same in both lines).
- manifest.json names the handbook this release loads: MC handbook 0.1.1, by the SHA-256 of
  services/mc_operations/handbook/manifest.json. The container's start step checks the image's copy against that hash
  and every file against the manifest, lays the files out under the workspace (handbook/), and ends AGENTS.md with the
  handbook block: a header naming the pinned folder, the manifest's path and its hash, then the engineering SKILL.md
  verbatim (the block native mc-chat has loaded since 30 Oct). The block counts in the 16,000-character budget. Start
  refuses when the release names no handbook or any byte differs.
- 0.1.17's "Outside reports and GitHub issues" section now comes from the handbook block: SKILL.md carries the same
  section word for word, and 0.1.17 put a copy in AGENTS.md only because the container did not load the handbook
  (review fix M1). One copy is loaded, not two, so AGENTS.md is 0.1.16's apart from the header version.
- Supersedes 0.1.17 and 0.1.22 as the container's and the voice card's release. The portal's voice pin (pin.json)
  moves to 0.1.23 when the voice line merges; the card is the same bytes.
