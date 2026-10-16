# Shared CoS / Master Craftsman handoffs

Both roles use handoffs.py and handoff-request.template.json. Role, conversation and job identify the handoff; the underlying agent/vendor does not. This is private transport, not a new Work, Board, Rooms or approval system. Link their existing records in record_refs. Creating a handoff never starts a builder or grants execution authority.

## Common request

Fill the same JSON template for either role:
- title, request, recipient: what is wanted, why, and who should receive it.
- conversation_id, job_id: the actual originating conversation and job identifiers.
- record_refs: existing spec, Work, Board, Room or artifact references.
- acceptance: the bounded checks/results requested.
- owner_decisions: attributed owner direction with its source; not a new approval issued by this tool.
- proposals, open_questions, next_actions: keep suggestions and uncertainties distinct from settled direction.

Do not put passwords, tokens, keys or whole unrelated conversations in the template. File intake is explicit, never a background crawl. Screenshots and binary documents are copied byte-for-byte and hashed; source originals remain untouched. There is no automatic upload to the Vault.

## Permanent homes and commands

On the laptop use the project Python and canonical scripts/agents/handoffs.py. The default private root is ~/minimoi-staging/data/handoffs. Inside the shared dev agent use:

```sh
python3 -B /minimoi/scripts/agents/handoffs.py template
python3 -B /minimoi/scripts/agents/handoffs.py create --role cos --request-json /absolute/private/request.json
```

Choose --role mc for Master Craftsman. Both use the same implementation. The mounted-agent default is /minimoi/data/handoffs. Override --root only for a specifically configured private home or disposable test. Permanent promotion roots are the platform data/artifacts and data/memory roots, independent of a temporary test root.

Each handoff has a UUID directory under its role, handoff.json and files/. Add selected inputs/results with add --role ROLE --id ID --file ABSOLUTE_PATH. Default files must be promoted before expiry. Use --disposable only for an explicitly temporary copy (for example a throwaway screenshot), never to evade preserving an original or accepted result. Renamed numbered copies prevent overwriting another file.

The recipient acknowledges actual delivery using ack --actor ACTOR --receipt ACTUAL_REFERENCE. Record completion using complete --receipt ACTUAL_JOB_OR_RESULT_REFERENCE. These fields are caller-attributed receipts, not authentication or owner approval. Never invent an actor, job, acknowledgement or success. Adding content after acknowledgement requires reopen, which clears acknowledgement and completion.

## Six calendar months

The expiry clock starts at the later of completion and acknowledgement, using six calendar months with month-end clamping. Open, reopened, unacknowledged and uncertain work is retained. A needed file also requires promote --name STORED_NAME --durable-file ABSOLUTE_PERMANENT_COPY; the exact copy must still hash-match when expiry is reviewed. Disposable copies need no promotion. Canonical Work/Vault files and source originals are never deleted by this utility.

plan-expiry shows eligibility and an exact manifest checksum. expire requires --execute and --expected-manifest-sha, revalidates current bytes and the policy, and removes only eligible transport copies. It retains a small receipt and request hash; the old brief/proposals/decisions are removed from that receipt. No scheduled purge is enabled. Any interrupted expiry leaves an intent record and an explicit operator-review hold; do not remove a writer lock or retry an uncertain expiry automatically. Verification is point-in-time; it is not a transaction with independent artifact publishers, an off-host backup, or a permission boundary against the owner's full tools.

## Existing transport

Legacy flat MC deliveries remain retained and outside automatic expiry until their real provenance and receipts are known. Never infer acknowledgement/completion from modification times. Compatibility transport directories do not become canonical project storage. Draft back-and-forth stays in _working; running tools and handoff storage do not.
