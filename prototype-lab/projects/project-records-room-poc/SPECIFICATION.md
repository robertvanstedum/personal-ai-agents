# Records & Rooms: public specification baseline

This public summary is derived from the reviewed Revision 4 design. Private
discussion/review records are not part of this package. Registration does not
certify final acceptance or grant production deployment authority.

## Purpose and boundaries

Preserve deliberate working sessions and their reasoning, with authenticated
contributors, receipts, source attribution, exact artifact references and
derived notes. A future communication surface is replaceable; durable records
remain authoritative. Ordinary one-to-one conversation is not silently captured.
Accepted discussion in an explicitly opened recording session is retained in
full. Notes and summaries do not replace transcripts. A recorded decision is
not an artifact disposition, execution permission or deployment approval.

## Current implementation

Local SQLite store; room-scoped access; transactional idempotent writes;
pause/close guards; immutable uploaded bytes with hashes; explicit external
artifact references; separately versioned notes; source classifications and
caller-declared execution metadata; JSON/Markdown exports; local backup tools.
Room state is stored in mutable columns with corresponding audit events, not
claimed to be a fully event-derived projection. An actor identity and declared
runtime metadata are distinct; declarations do not prove actual execution.

Schema 5 separates persistent containers from sessions at the storage/API layer.
The legacy `rooms` table holds sessions; `persistent_rooms` holds their parent
containers. Each historical session receives a parent with the same ID. Existing
foreign keys and receipts remain unchanged; the table name is retained for
compatibility, not evidence that room and session remain one entity.

`GET/POST /api/v2/rooms` lists/creates persistent containers; `GET
/api/v2/rooms/<id>` lists only sessions visible to the caller; `POST
/api/v2/rooms/<id>/sessions` opens an explicitly acknowledged session;
`GET /api/v2/sessions/<id>` reads it. Existing v1 `/rooms/<id>` endpoints remain
session aliases for messages, notes, access and exports. Creating a container
does not record discussion or start execution. Only the owner opens sessions
in this slice; no membership is inherited between siblings. Container title
and purpose are shared metadata visible to members of any child session;
sibling content, counts and update timestamps are not exposed to them.

Closed sessions cannot reopen. Paused sessions can resume; a later meeting
gets a new session ID. The current UI remains a session list; persistent-room
navigation and opening sibling sessions through the UI are the next slice.

Limitations: the owner is hard-coded.
There is no production replication, independent automatic backup, unattended
agent coordination or verified actual-CoS participation. Local backup alone is
not protection against loss of the host. This prototype is not production ready.

## Ordered build increments

1. Separate persistent rooms from bounded sessions, preserving prior records,
   links, receipts and access. Quiet rooms do not run paid model loops.
2. Verify the development route to the real OpenClaw-backed CoS. Capture trusted
   integration execution evidence for contributions and source-linked briefings;
   platform acknowledgements and a separate gateway responder do not qualify.
3. Establish independent backup and demonstrated restore; then bounded mandates,
   unattended discussion and explicit pause/close/stop semantics. Closing a
   session does not cancel separately authorized work; stop-all must be explicit.
4. Execute real bounded worker tasks and hand exact artifacts to a distinct real
   reviewer, with acknowledged receipt, bounded revisions and outcome reporting.
5. Prove recovery, cancellation, permission checks, circuit breakers and failure
   classification. Do not blindly retry writes whose outcome is uncertain.
6. Freeze the full build and acceptance procedure for an independent run and
   explicit human acceptance or rejection. Dates do not reduce functionality.

Production operation while the workstation is off is a later, separate gate.
Open-source communication-surface augmentation follows the local foundation;
the built-in UI remains a fallback and diagnostic surface.

## Full local harness acceptance (H1)

All are required; registration and unit tests alone satisfy none of the full
integration acceptance claims:

| Case | Required evidence |
|---|---|
| H1-01 | Persistent rooms, distinct bounded sessions, continuity without idle loops |
| H1-02 | Actual CoS participation with integration-captured execution evidence |
| H1-03 | Complete accepted session discussion; casual chat excluded |
| H1-04 | Real worker executes authorized bounded work and preserves exact output |
| H1-05 | Distinct real reviewer acknowledges and reviews that exact version |
| H1-06 | Stop, limits and cancellation outcomes; surviving work explicitly authorized |
| H1-07 | Interruption, quota, malformed artifacts and uncertain writes handled safely |
| H1-08 | Unauthorized, revoked, stale and closed-session contributions controlled |
| H1-09 | Notes remain separate from transcript and cannot confer approval |
| H1-10 | Actual CoS briefing links sources, disagreements and gaps |
| H1-11 | Records and artifacts restored from an independent location |

An independent runner uses an identified frozen build, isolated copy and
approved test revision. It may inspect the source but must not repair during
the run. Results are pass/fail/blocked/not-run. Failure injections are labeled;
successful real-agent cases require real participants. Human acceptance is a
separate recorded decision, not an automatic consequence of passing tests.
