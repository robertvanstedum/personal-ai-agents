---
id: 01M1J6M79H78ZR99BFGZH5BSCH
series: decision-orientation-file-naming
slug: decision-orientation-file-naming--gzh5bsch
type: decision
title: "Orientation files use a README_ prefix; only the repository root keeps README.md"
created: 2026-09-02
decided_by: robert
recorded_by: claude
status: recorded
roadmap_authority: none
implementation_authority: none
domains: [cos, guild, curator, platform]
tags: [planning-studio, naming, orientation, readme, decision]
relations:
  - type: related
    target: 01M1HMVNJBPVAWXZSGPKS773W2
  - type: related
    target: 01M1J6DRW6X4GDDAY0JY330NYB
---

# Decision — Orientation File Naming

## Problem

The v0.9 package contains fifteen files named `README.md`. In a file listing,
a search result, an editor tab bar, or an agent's directory scan, the name
carries no information about content — only about location. This is the same
defect as centrally allocated sequential IDs: the name tells you where a thing
is, not what it is.

The subfolder-`README.md` convention is standard for code repositories, where
the folder is the unit and navigation happens by clicking into folders in a
web UI that auto-renders the file. This repository is navigated by agents
reading listings, by search, and by Robert in an editor. The convention serves
a navigation mode this repository does not use.

## Decision

**Only the repository root keeps `README.md`.** Every other file is named for
its content.

Two naming families, which never look alike in a listing:

| Family | Form | Meaning |
|---|---|---|
| Record | `<descriptive-slug>--<short-ulid>.md` | Content. Has frontmatter, a ULID, a place in a series. |
| Orientation | `README_<what-it-orients>.md` | Navigation. Signposts to records. Generated where the content is derivable. |

The `README_` prefix is consistent with the existing house convention for
top-level documents (`ROADMAP_`, `ARCHITECTURE_`, `OPERATIONS_`).

## Application to the v0.9 package

**Nine placeholder READMEs are removed.** `archive/`, `inbox/`, `patterns/`,
`registry/`, `schemas/`, `library/`, and the empty `decisions/` and `reviews/`
folders each carry a one-sentence "this directory is for X" file. The root
`README.md` already describes the directory structure and owns that role. A
renamed placeholder is still a placeholder.

**Two initiative READMEs merge into their initiative records.** An initiative
is content, not a signpost. `INIT-2026-0001/README.md` and its
`initiative.json` become one record — `central-personal-repository--<ulid>.md`
with `type: note`, `kind: initiative` — per the v0.9 rule that metadata lives
in the file. Same for `INIT-2026-0002`. The JSON sidecars are retired.

**Three series indexes are renamed and generated.** The source-history tables
become `README_terminal-pleistocene-briefing.md`,
`README_curator-roadmap.md`, and `README_planning-studio-design-package.md`.
They are emitted by the registry generator authorized in
`decision-authorize-registry-generator-for-first-experiment--jy330nyb`, never
hand-edited, and committed so they remain visible with nothing running — which
satisfies `decision-preserve-visible-source-version-history--qdbgsn88`.

Fifteen `README.md` files become one.

## Accepted cost

GitHub and similar web UIs auto-render only a file named exactly `README.md`
in subfolder views. `README_<name>.md` loses that. In a private repository
read primarily by agents and in an editor, the loss is negligible.

## Effect on the v0.9 candidate

- §10 (candidate repository shape) is amended to remove per-directory READMEs
  and to show `README_<series>.md` under `library/sources/<series>/`.
- The artifact retention and naming practice (`--pks773w2`) gains a section on
  orientation files.
- The registry generator's scope, already recorded, includes emitting the
  series orientation files.
