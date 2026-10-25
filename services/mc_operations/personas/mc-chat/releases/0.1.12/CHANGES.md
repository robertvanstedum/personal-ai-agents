# 0.1.12 — MC's own container, phase 1

- Portable AGENTS = 0.1.11 (capture included). The header is corrected to the release version; 0.1.6 had been carried forward unchanged since 0.1.6.
- Runtime binding rewritten for the container: mounted folders, core tools inside the wall, provider-only egress, MiniMoi tools deferred to phase 2, sign-in files off-limits.
- SOUL unchanged.
- Start refuses if any laid-out file's hash differs from this manifest, or if the AGENTS header version differs from the folder name.
