# Spec 157 — first collaborator implementation

Local development only. No model calls, launchers or production wiring. Claude Code
CLI is the first intended client; the shared tool is tested with synthetic HTTP
calls, not a live Claude session yet.

## Platform versus room ownership

The `platform_access.py` module owns installation credentials in the local
application database. Records owns session membership. No standalone identity
server or CoS dependency is introduced. New installations have explicit session
operation grants and an expiry. Validation is per request and repeated inside
protected record transactions. Browser authentication stores a credential ID,
not only a principal, so revocation and expiry also invalidate logged-in access.

Existing principal tokens are imported as clearly labelled legacy credentials to
preserve existing access. They do not gain expiry automatically. Revoke a
collaborator's `legacy:<principal-id>` explicitly after replacement; restarted
migration must not resurrect it. The owner recovery key is not managed through
collaborator revocation. No existing saved database is migrated by this build.

## Owner-run setup

Start the application using a disposable owner-private data directory. Use the
owner UI to establish collaborator identity and session memberships. New access
uses an installation credential; retire the old collaborator legacy token.

Write an issue request with these fields (IDs below are placeholders):

```json
{
  "principal": "claude-code",
  "label": "Claude Code on this Mac",
  "expires_at": "REPLACE_WITH_FUTURE_TIMEZONE_AWARE_TIMESTAMP",
  "grants": {
    "SESSION_A": ["read", "post", "upload", "receipt", "export"],
    "SESSION_B": ["read", "receipt"]
  }
}
```

```sh
python accessctl.py --owner-token-file /private/path/owner-key.txt issue \
  --request /private/path/request.json --credential-file /private/path/client.token
```

`accessctl` writes a new 0600 credential file and prints only identifiers. It
refuses to overwrite an existing file. The model's instructions contain the file
reference, never its contents. The adapter process consumes the credential.

For inventory/revocation use `accessctl.py ... list` and
`accessctl.py ... revoke CREDENTIAL_ID`. Rotation uses another issue request
with `installation_id` and `rotate_credential_id`, writing a new token file.
If issuance loses its response, do not retry blindly: use the owner inventory,
identify/revoke the unreceived credential, then issue deliberately. Do not assume
a failed CLI response proves issuance did not occur.

## Explicit publication and recovery

A retained JSON payload names source application and coverage/omissions, with
`turns`, `handoff`, or both. Each turn has `speaker`, exact `text`, and optional
`source_created_at` with a timezone. No authority, event kind or actor can be
supplied. Imported turns always remain messages authored by the credential's
principal. A handoff is a checkpoint; it dispatches nothing.

```sh
python roomctl.py --token-file /private/path/client.token --operation-id import-001 \
  import SESSION_A /private/path/import.json
python roomctl.py --token-file /private/path/client.token receipt SESSION_A import-001
python roomctl.py --token-file /private/path/client.token read SESSION_A
```

Retain the exact payload and operation ID. Reconcile a lost response using the
receipt command, not a regenerated transcript. ID reuse with different content
fails. `file` and `link` retain the existing explicit destination convention.
All HTTP tools reject redirects, avoiding forwarding authorization to a new URL.

The first installation route allowlist is deliberately narrow: read/discover
sessions, publish/import, upload/download, link, export, receipts and transfer.
Global search and persistent-room UI navigation are not exposed to these scoped
clients yet. Owner UI and legacy routes retain compatibility. Different client
interfaces require separate acceptance; a CLI process does not attach a desktop
or browser chat.

## Cross-room disclosure

Owner-only `POST /api/v1/rooms/SOURCE/disclosures` authorizes a principal, exact
`event_ids`, `destination` and `expires_at`. Supplying `revoke: GRANT_ID` withdraws
it. The principal must still have source membership and destination contribution
access. The client uses `roomctl ... --operation-id KEY transfer DESTINATION GRANT_ID`.

The transfer rechecks source read access, destination write access and the
separate disclosure grant. Original kinds never become destination authority.
The destination receives only approved content and declared speaker metadata;
source room names, paths, participants and links are not projected. The complete
mapping remains in owner-side `room_transfers` audit. This cannot detect manual
copy/paste or paraphrases of already-read text and is not general data-loss
prevention.

## Acceptance and remaining work

New automated tests cover installation isolation, rotation/expiry/revocation,
legacy-token retirement across restart, cookie invalidation, multi-session scope,
receipt mismatches/conflicts, import attribution/export, cross-room disclosure,
and actual owner-provisioning/roomctl calls against a disposable loopback server.
Browser tests cover imported speaker markup and the inherited agent-response UI.

Before acceptance: independent review of this exact build and a signed-in Claude
Code session using the configured tool on agreed non-private fixture rooms.
Existing initial Records UI/worker changes are inherited, not silently accepted.
No merge, deployment, or saved-meeting write is performed by these tests.
