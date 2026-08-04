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
building hundreds of lines of software to load a small number of records.
Robert wants that tradeoff visible before implementation begins.

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

Pause for Robert's approval before implementation if the proposal is expected
to exceed **150 lines of new operational code**, **one hour of build effort**,
or the effort required to review the data itself.

## Default approach for a small load

For an additive correction affecting a few stores and roughly 100 records or
fewer:

1. Export only the proposed additions into a human-reviewable file.
2. Resolve and verify the production user identity independently.
3. Show duplicates and conflicts before writing.
4. Back up the destination and verify its checksum.
5. Briefly stop the service if it can write the same data concurrently.
6. Load through a narrow backend utility that preserves ownership and
   permissions and writes atomically.
7. Restore automatically if any write or verification fails.
8. Restart and verify counts plus one real save-and-refresh action.

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
reviewed export and narrow loader, but Robert remains the approval point for
the exact data, the reviewed tooling, and the production write.
