# Session-to-CoS connector component

`cos_agent_responder.py` joins an authorized Records snapshot, the reviewed
OpenClaw evidence method, a durable local attempt journal and a Records write
receipt. It installs no HTTP endpoint, background listener or live configuration.

## Call boundary

A trusted platform caller authenticates Robert, obtains explicit intent and a
stable request UUID, then calls `respond` with the exact session UUID. Never
take `owner_authorized` or the synthetic-session allowlist from request JSON.
`SyntheticSessionPolicy` denies all sessions by default. An operator may
allowlist a specifically reviewed non-private synthetic session for the watched
development test. This is a content attestation, not a content scanner or a
runtime sandbox. Private-room support remains disabled.

Use the dedicated cos-dev Records credential and `OpenClawMeetingModel` wrapping
the actual configured `OpenClawBackend`, not GatewayModel. The existing RoomClient
supports only the local test service on port 18880. This does not connect the
old preview on 18881, and no existing meeting is automatically selected.

## Execution and uncertainty

1. Verify owner authorization, configured session allowlist and CoS identity.
2. Fetch only a session that credential can read; resolve a prior receipt before
   considering new inference. Require active capture and contributor access.
3. Reserve the request in a private SQLite journal before any runtime call.
   Store the bounded snapshot hash and its version/record-sequence guard.
4. Call `call_backend_with_evidence` using a distinct runtime conversation key
   for this session and request. Do not inject ordinary CoS chat context.
5. Validate request/runtime identities; durably save the exact intended event
   before attempting its write. Unknown extra response fields are discarded.
6. Records rechecks access, active capture and context guard inside its write
   transaction. Return separate request, runtime run, record and receipt IDs.

The journal reuses the existing reviewed TurnJournal. It is an execution cache,
not a second authoritative transcript. A started attempt without a saved result
is uncertain and never automatically re-inferred. A generated result can retry
the same write; a committed operation resolves from the server receipt without
re-inference. Context changes block a previously generated, uncommitted write.
Creating a new UUID to bypass uncertainty is not automatic reconciliation and
requires an explicit operator decision. No scheduling/retry loop is installed.

## Provenance and briefing

The event remains `agent_draft` with caller-declared `agent_response` origin.
Records does not independently attest OpenClaw: the trusted connector captures
that evidence. The JSON body keeps runtime evidence, snapshot hash, included
record IDs, covered sequence and omissions. The authoritative receipt is
returned by Records after commit. A source-linked briefing is requested with
`action="brief"`; real briefing quality and correct citations need the watched
live test, not just unit assertions. No decisions or tasks are approved.

The adapter requests no external search, but prompt wording is not enforcement.
The current runtime still permits web_search and session_status. The stored
envelope explicitly states `tool_policy_enforced: false` and `synthetic_only:
true`. Session separation avoids shared runtime conversation keys; it is NOT
a claim that OpenClaw has no workspace memory or tool access.

## Remaining activation gates

Independent frozen-diff review; approved development-only route wiring against
the actual dirty CoS baseline without overwriting its capture work; explicit
operator allowlist and credential provisioning; verified runtime route and
permissions; then Robert-watched synthetic contribution/briefing with receipt
and runtime evidence. Ordinary chat must remain outside capture. Nothing in
this component, its test doubles or its JSON mode field establishes H1-02.
