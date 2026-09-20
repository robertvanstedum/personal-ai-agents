# Transcript export batch — operator utility, not automatic service

This batch adds `transcript_snapshot.capture`, `transcript_publish.publish` and
`recover`, plus operator CLI commands. Use only on disposable test stores until
the batch is independently reviewed. No live store has been migrated by this build.

```
python manage.py publish-transcript --data-dir <test-store> --session-id <uuid>
python manage.py recover-transcripts --data-dir <test-store>
```

The OS-account operator has owner access. This CLI is not an authenticated remote
service; never expose it as one. No new UI route, scheduler, central importer,
remote synchronization, or production CoS write path is installed.

## Snapshot behavior and deliberate limits

One `BEGIN IMMEDIATE` transaction reads session, records, notes, parent metadata,
participants, membership and attachment references. The writer reservation avoids
mixed snapshots but briefly serializes capture with other writers. Content is
rendered after that transaction. Existing transcript/receipt rows are unchanged.

First explicit capture creates `transcript_origin` and `transcript_snapshots`
metadata tables; this is an additive schema change on the selected store, even
though the old schema-version field is unchanged. Review deployment/migration
policy before enabling it on live stores. Origin is preserved by SQLite backup.
An independent fork must not copy this origin without an explicit fork policy.

Revisions advance on **observed snapshot changes**, including note and membership
changes. They are NOT mutation-time revisions for every intervening edit. Closing
that gap and installing durable publish-on-change scheduling are follow-up work;
this batch does not satisfy the automatic-publication acceptance case.

Legacy speaker labels weren't stored at submission, so stable actor IDs are used
and missing historical labels are declared in coverage. Current display names
remain in participant snapshots. Legacy source timestamps and closed_at are not
guessed; the latter stays null with declared unknown coverage. Note source IDs
expand the note's stored through-sequence coverage; they are not asserted to be
selective citations authored by the note writer. Attachment content is not copied.
JSON-looking connector message bodies remain exact literal text.

## Publication, recovery and limitations

Bundles are private directories under `<store>/transcripts`, named by session,
revision and content digest. Manifest lists fixed filenames, sizes and hashes;
no self-hash. Each file and directory is flushed before atomic rename. Repeated
publication of an unchanged snapshot returns the same bundle without overwriting
it. No mutable latest pointer is installed in this slice.

The SQLite `transcript_publications` journal records pending payloads before file
writes. Explicit recovery replays that payload, not current session content, and
checks an existing destination against its expected manifest. Interrupted scratch
directories may remain under `.pending-*`; preserve them until an operator confirms
the corresponding journal is recovered, then remove only identified orphan scratch.

A process lock serializes cooperating publishers. The database can still accept
newer contributions after capture: each bundle states its snapshot time/revision,
not a claim of current state. Local hashes detect corruption relative to the
manifest; they do not authenticate a maliciously replaced manifest/origin. Consumers
must apply the separately specified origin and access approval checks.

Exported membership is not an access grant. Files remain owner-private; copied
artifacts need independent destination authorization. No private material or cloud
destination is authorized merely by running the CLI. Old bundles are immutable
historical snapshots, not revocable live views.

## Backup boundary

Publication is not backup. Recovery needs consistent SQLite plus matching owner/
session keys, document source bytes, attempt journals and pending publications.
The existing local backup utility is not proof of off-device arrival or restore.
This batch has NOT configured or verified an off-device backup/monitor. Restore
approval and independent monitoring remain open operational work, not a passed
acceptance case.
