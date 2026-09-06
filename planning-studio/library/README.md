# Library

The library is the canonical home for durable sources that may support more
than one initiative.

```text
library/
  sources/   exact source files, grouped into human-readable series
  records/   provenance, checksum, identity, and relationship records
```

Successive versions remain visible beside one another in a series directory.
Hashes verify the exact files but do not replace them with opaque paths.
Initiatives link to the canonical source and record rather than keeping their
own copies.

The first retained source sets are:

- [Terminal Pleistocene briefing](sources/terminal-pleistocene-briefing/README.md)
- [Curator roadmap proposal](sources/curator-roadmap/README.md)
- [Planning Studio initial design package](sources/planning-studio-design-package/README.md)

This is documentary application of the v0.9 candidate. It does not approve or
implement any registry, hook, database, or automated write path.
