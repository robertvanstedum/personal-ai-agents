# Operational learning: Tier 2 production-support data loads

**Recorded:** August 3, 2026  
**Context:** German vocabulary and writing-history production catch-up

## Purpose

This note covers small, one-time corrections or catch-ups to existing
production data. These are production-support activities, not product builds
or schema migrations. They require production caution, but the solution should
remain proportional to the amount and complexity of the data.

## Lesson from the German history catch-up

The August 2026 German catch-up needed to add 5 vocabulary records and 8
writing-session records to two JSON files. Safety work was necessary because
dev and production used different user IDs and both live files needed backup
and rollback protection. The implementation nevertheless grew into a
German-specific program of roughly 600 lines.

The missing step was an upfront statement that the proposal effectively meant
building hundreds of lines of software to load a small number of records. That
tradeoff must be visible before implementation begins.

## Live validation: issue #176

The first catch-up completed safely, but its post-load check proved only that
the production phrasebook file contained the expected number of records. It
did not prove that the signed-in user could see them. After one additional
production save, the file contained 39 records while the authenticated Wörter
page showed only 10.

[Issue #176](https://github.com/robertvanstedum/personal-ai-agents/issues/176)
identified the cause: 29 older records still had a legacy `user` value of
`"robert"` or no owner value, while the application correctly filtered the
page to production user `"3"`.

The follow-up was handled as a Tier 2 data repair rather than another
migration:

- 39 records examined; 29 records required one field change
- One existing JSON file; no additions, deletions, or schema changes
- Dry run against the live file before approval
- Timestamped backup, atomic replacement, ownership and mode preservation,
  and automatic restore on failure
- Brief service stop, guaranteed restart, and health verification
- Final verification through both the stored data and the authenticated UI

The repair retained 39 records, assigned all 39 to production user `"3"`,
preserved `root:root` ownership and mode `0600`, and made all 39 entries visible
in Wörter. Once the task was classified and scoped correctly, the production
repair was completed in roughly 15–20 minutes using the safety primitives
already developed for the earlier load.

This closes the operational-learning loop: proportional process reduced the
work without reducing production safeguards. It also added one important
acceptance rule—aggregate counts are insufficient for identity-scoped data.
Verification must include the count and content visible to the intended user.

The repair script grew beyond its initial line-count estimate even though the
work remained quick. Future Tier 2 tasks must pause again if actual
implementation crosses an approval threshold, not only when the initial
estimate predicts that it will.

## Required upfront estimate

Before any Tier 2 data-load tooling is built, report:

- Records examined and records expected to change
- Destination files or tables
- Whether the task is one-time or recurring
- Required transformations, including user-identity rewrites
- Estimated effort for a reviewed manual/backend load
- Estimated new code, tests, and implementation time
- Failure impact and the simplest credible rollback
- Recommendation: narrow load, reusable utility, or formal migration

Pause for explicit owner approval before implementation if the proposal is expected
to exceed **150 lines of new operational code**, **one hour of build effort**,
or the effort required to review the data itself.

## Default approach for a small load

For an additive correction affecting a few stores and roughly 100 records or
fewer:

1. Export only the proposed additions into a human-reviewable file.
2. Resolve and verify the production user identity independently.
3. Show ownership distribution, duplicates, and conflicts before writing.
4. Back up the destination and verify its checksum.
5. Briefly stop the service if it can write the same data concurrently.
6. Load through a narrow backend utility that preserves ownership and
   permissions and writes atomically.
7. Restore automatically if any write or verification fails.
8. Restart and verify aggregate counts, identity-scoped visible counts, and
   one real save-and-refresh action.

Do not edit live production files interactively when a small reviewed loader
can make the same change safely and repeatably.

## When to build more

Create a reusable loader after a second credible use case exists. Use formal
migration tooling for schema changes, large relational updates, resumable
loads, or repeatable promotion across environments.

The term **migration** should be reserved for those cases. A small additive
record catch-up is a **data load** or **data repair**.

## Ownership

This is a Tier 2 daily operations pattern. An implementer may prepare the
reviewed export and narrow loader, but the project owner remains the approval
point for the exact data, the reviewed tooling, and the production write.
