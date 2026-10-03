# data/agent-memory: daily copies of agents' memory files

Written by the memory copier (`core/agent_memory/`, run daily as loop_m on cos-scheduler), never by hand.
Layout for each source (`cos-agent-a/`, `master-craftsman/`, `claude-code/`):

- `current/` the latest complete copy of the selected files, with `_manifest.json`
- `history/<UTC time>/` the files that changed in that run, with its own `_manifest.json`
  (a folder without a manifest is incomplete and ignored)
- `_status.json` last run, last success, data time, counts and a fixed failure code; never a file name
- `_approval.json` that Robert approved this source's dry run (the scheduler copies nothing before it exists)

A manifest (`schema_version: 1`) lists, for each stored file: relative path, sha256 and size of the **stored**
bytes, whether it was sanitized (and the original's sha256), the sanitizers applied, and a redaction count;
plus `deleted` paths and counts of skipped files by reason code. Rebuild the state at any snapshot by taking,
for each path, its newest copy at or before that snapshot, minus the deletions.

Copies that were changed by the payment scrub or the credential guard are **sanitized derivatives with
provenance**, not lossless originals. Deleting a line here does not erase older snapshots or backups
(see "Forgetting" in the Spec 160 runbook). Files are mode 0600 and folders 0700.
