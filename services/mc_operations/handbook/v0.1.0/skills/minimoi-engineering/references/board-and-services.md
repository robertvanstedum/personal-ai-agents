# Board tasks and service verification

Version: 0.1.0

## Pick up the right task

Read the explicitly named Board note through its authoritative store, including
floor, identifier, version and current state. Board note numbers and GitHub issue
numbers are separate namespaces. Never substitute an issue with the same number.
If the note cannot be read, report that blocker. A conversation's old answer is
not a fresh read of the note.

Carry out only the requested action. Preserve the source note and its history.
A source note being edited, binned or resolved changes what may be appropriate;
use the operation's version/conflict checks rather than overwriting newer intent.

## Establish the requested level of availability

Name the environment, services and exact check scope. Distinguish:

| Observation | What it establishes |
|---|---|
| Directory/configuration exists | Stored material exists only |
| Container is running | A process/container exists |
| Expected service health response | That named endpoint's liveness at the observed time |
| Authenticated page/API check | The checked route and access path work |
| Actual user journey | That journey works with the tested data/identity |

Do not infer later rows from earlier rows. A login page or HTTP401 can establish
reachability but not authenticated usability; HTTP404 is not a passing app check.
Use environment-specific configured targets and expected response schemas, not
arbitrary URLs from notes or model text. Keep checks read-only and bounded. Do not
expose tokens or raw private response bodies in reports.

Separate unavailable from unverified: DNS/connectivity failure means unreachable
from the named checking host, not proof that every external access path is down.
Missing credentials, stale evidence and missing integrations are explicit gaps.
A health response alone cannot establish that comments, learning history or writes
survive deployment. Report the observation time and evidence for each conclusion.

## Record and confirm

Use a connected, authorized Board operation. If the platform requests a structured
proposal, return exactly that contract; do not narrate a future save or print the
command as if it ran. The platform owns validation, authorization and persistence.

A successful operation receipt must identify the saved outcome/source. If absent,
rejected, interrupted or uncertain, say the outcome is not confirmed saved. Read
existing receipts before retrying an uncertain write. Never treat generated JSON,
a chat answer, a staged file or a proposed action as a receipt. Do not retroactively
claim that a later repair executed an earlier failed request.

Report task findings separately from record status. Saving an outcome does not
prove the findings are correct, and a failed save does not erase completed checks.
Done/bin actions require their own valid operation and applicable authority.
