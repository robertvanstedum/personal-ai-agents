# Rooms on the shared memory pipeline: source, delivery boundary, policy

One memory system. Rooms is one more **source** on the path every other source uses
(source -> Bundle -> scrub -> `Shelf.ingest` -> ledger/status), not a second ingestion system. Code: `core/memory_shelf/rooms.py`.
Synthetic tests only so far (`tests/memory_shelf/test_rooms.py`); **nothing here has read a real Rooms bundle**.

## Where bundles come from (delivery boundary, to settle before live activation)
Records' own exporter publishes bundles (`prototype-lab/projects/project-records-room-poc`: `transcript_publish.publish`,
run by `manage.py publish-cycle` or the opt-in `watch-transcripts` worker) into `<records data dir>/transcripts/`, one
immutable directory per snapshot: `<session>-r<revision>-<sha256>/{manifest.json, transcript.json, transcript.md}`.

* The adapter reads that folder **read-only**, as a source named `rooms`. It never opens Records' SQLite store, never runs the
  exporter, never writes to the folder, and has no network or subprocess code (a test checks that).
* **Host and path are not decided here.** The config `root` is the transcripts folder on the host where Records runs. If Records
  runs on the same Mac as the memory watch, that is a local path; if not, the folder must first be delivered read-only
  (a mount or a one-way copy) by whoever owns that host. Confirm the host and the path with Robert and Codex before the first dry run.
* **Freshness is the exporter's cadence, then the job's.** The daily job (04:00) sees whatever bundles exist. If Rooms needs to
  be fresher, run the exporter more often (Records' own worker or `publish-cycle`) and, if wanted, the watch more often: both are
  configuration of existing pieces, not a second loop. A daily job does not give live Rooms continuity.
* Records' exporter needs a database write (journal tables) and takes the store's own lock; invoking it is Records' job, not this
  adapter's. If the exporter is not otherwise scheduled, coordinate one bounded `publish-cycle` before the memory watch (separate
  decision; not built).

## Configuration (the source entry)
```json
"rooms": {"kind": "rooms", "root": "<records data dir>/transcripts",
          "source_instance_id": "<the store's transcript_origin id>",
          "owner_ids": ["<Robert's participant id in Records>"]}
```
`owner_ids` is the **trusted** list of who counts as the owner (D2). `source_instance_id` is the one store the approval is for; bundles
from any other store are refused (`wrong_source`), and with no instance configured nothing is accepted. Both, and the policy version,
are part of the **approval fingerprint**: change either and the source goes `stale` until the owner approves again.
Then, as for every source: `moi dry-run rooms` (ids and counts only; D2 is applied, so it shows how many meetings would be kept,
excluded or refused), `moi approve-source rooms` (typed yes), then it runs with the daily job (listed only when configured).

## Policy
* **Identity:** `source_instance_id` + `session_id`. One record per session; every revision is an edition; earlier editions stay.
* **Revision order:** bundles are read oldest revision first (numerically). A snapshot older than the record's current revision is skipped
  (`older_revision`) and never becomes current; the same revision with different bytes is refused (`revision_conflict`), never guessed.
  An unchanged export read again changes nothing.
* **D2 (settled), before retention:** any participant who is a human other than an owner id excludes the whole meeting
  (`other_participant`); a participant whose identity cannot be established (kind unknown, not on the roster, bad actor id, an owner id
  that is not a human) fails closed as `unknown_participant`. Identity comes only from the exporter's roster (the store's principals) and
  `owner_ids`: never from a display name or from text. A guest joining later is excluded from that revision on; earlier editions stay,
  and a `rooms-exclusion` review item (counts and ids) says so. There is no Private mode in Rooms.
* **D8 (settled):** the shared scrub runs on every retained string: message text, notes, speaker labels, participant labels, references.
  The record carries the bundle's content hash and `redacted_turns` / `redacted_fields`.
* **Inert:** references are kept as data, never followed; attachments are not copied; instructions inside a transcript are only text.
  A recorded Rooms decision is attributed source evidence, never an owner-approval event, and no "file this" in a room designates it.
* **Refusals** (fixed codes, nothing else): `bad_bundle_files`, `bad_manifest`, `unsupported_schema`, `hash_mismatch`, `bad_identity`,
  `bad_transcript`, `too_large`, `wrong_source`. Links, scratch (`.pending-*`), quarantine and unknown names are counted and never opened.

## What the record keeps beyond a Claude Code or Codex record (additive, shared contract)
Per turn: stable speaker id (`who`), sequence, record id, kind, reply and correction links, timestamps (source time stays unknown when the
source did not have it; ingest time is kept separately) in the frame; the label as supplied and the other source fields in the edition.
Typed notes and references follow the turns as framed blocks. The record manifest has revision, state, through-sequence, participants
(id, kind, owner/agent role, label), counts and the exporter's declared coverage. Fidelity compares speaker identity as well as text.

## Counts and status
`moi watch rooms` prints `watch\trooms\tok\t{counts}`: `captured`, `edition-added`, `unchanged`, `excluded`, `refused`, the reason codes,
`passed_over_*`, `possible_gap` (a declared exporter omission or a record the reader did not take). The daily job turns **warn** for
`unknown_participant`, any `refused`, or a coverage flag; an ordinary guest exclusion alone is expected and does not warn.
