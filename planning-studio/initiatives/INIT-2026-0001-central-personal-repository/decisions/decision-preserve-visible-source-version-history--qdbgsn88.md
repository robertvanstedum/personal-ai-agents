---
id: 01M1J18VFF908D4A9EQDBGSN88
series: decision-preserve-visible-source-version-history
slug: decision-preserve-visible-source-version-history--qdbgsn88
type: decision
title: "Preserve version history as visible source files"
created: 2026-09-02
decided_by: robert
status: recorded
roadmap_authority: none
implementation_authority: none
domains: [cos, guild, curator, platform]
tags: [source-history, retention, naming, planning-studio]
---

# Decision — Preserve Visible Source-Version History

Robert requires the history from one source version to the next to remain
visible as source files.

Therefore:

- successive received source files remain together in a human-readable series
  directory;
- a new source version does not overwrite or hide the prior version;
- a reviewer can follow the sequence without a database, generated registry,
  opaque content-addressed path, or Git diff;
- hashes, ULIDs, provenance records, and Git history support integrity and
  retrieval but do not replace the visible source files.

The exact directory and metadata contract remains part of the v0.9 design
review. This decision establishes the retention requirement, not an approved
implementation.
