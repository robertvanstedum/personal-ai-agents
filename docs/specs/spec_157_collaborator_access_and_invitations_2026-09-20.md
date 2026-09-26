# MiniMoi collaborator access and invitations — v0.6

20 September 2026 · INIT-2026-0006 Working Room · Decision owner: Robert van Stedum

**Status:** OFFICIAL — adopted by Robert on 20 September 2026: “Let's make this the final and log the spec as official and start build together with whatever is left on initial build.” Registered as Spec 157. Implementation authorized; reviewed-diff acceptance, merge and deployment remain separate.

**Build disposition:** Start the bounded already-running-client slice alongside the remaining initial Records work. Implementation defaults chosen by Codex under that authorization: Claude Code CLI first; shared roomctl/HTTP path; platform installation/credential lifecycle included (no legacy-token exception for new integrations); no cross-request authentication cache; explicit source disclosure authority; installation credential with server-checked destination grants; external-source declared-speaker metadata; no launcher, MCP or CoS tool widening in this increment. These defaults do not claim live client acceptance. Preserve Claude's separate model-role work.

**Authority:** Robert requested this full version. Reviews inform the design; they do not select options on Robert's behalf. Requirements below capture the requested product behavior. Implementation defaults are proposals until selected. Robert has now authorized implementation and spec registration. No production credential changes, live agent launches, merge or deployment are authorized by that instruction.

**Amendment (proposed September 25, 2026; effective only on Robert's acceptance):** §15 adds typed non-room destinations for standalone records and work runs, Workshop executor identity, and implementation ownership for the Spec 158 §21 scope. Nothing above is relaxed.

**Relationship:** This candidate consolidates collaborator-invite v0.3/v0.4, Codex's review, Claude Code's returned review, both chat reviews, Robert's multi-room and platform-ownership requirements, and the independent-client workflow in Records & Rooms v6 §§7.1–7.3. It extends that substrate; it does not replace Records durability, lifecycle, privacy, export, or Work effect rules. Earlier invite drafts remain historical inputs. v0.6 is the current consolidated adoption candidate, superseding v0.5 as the working text. The adoption includes the controls below; options outside the selected initial increment remain open. It is the official invite specification following Robert’s adoption; source candidates and reviews remain preserved.

## 1. Purpose and user workflow

Robert works with Claude, Codex and Grok in their own supported interfaces, sometimes independently and on several threads of work. He can explicitly publish an available conversation transcript, a handoff, or both into a chosen MiniMoi meeting session. Other authorized participants can retrieve the result from their own interfaces. CoS need not mediate these exchanges. Clients operate through tools available on demand; this workflow promises no standing connection or push notifications. A new process using the same credential does not attach or transfer an existing chat automatically.

The first publication slice can demonstrate room-membership removal. It demonstrates revocable client credentials only if the new platform credential lifecycle is included and tested. Otherwise it remains an explicitly limited PoC under §8's retirement gate.

Separately, Robert may ask Chief of Staff to invite or start a collaborator for a bounded task. CoS proposes; MiniMoi records Robert's authorization; an approved connection or launcher performs the effect. The collaborator speaks through its own MiniMoi identity. CoS need not proxy subsequent messages.

This preserves interface choice, reduces repeated context transfer, supports concurrent work, and avoids CoS inference costs for transporting already-produced records. Reading and reasoning about records or drafting a handoff can still consume the chosen collaborator's allowance or API usage.

Chief of Staff is a MiniMoi capability. OpenClaw is its current runtime and must remain replaceable. MiniMoi owns the room contract, authorization, identities, records, connectors and receipts.

## 2. Scope and explicit boundaries

In scope:

- Already-running clients connecting to multiple authorized rooms/sessions.
- Explicit read, transcript/handoff publication, bounded uploads and receipt retrieval through MiniMoi-owned interfaces.
- Independent membership, credential lifecycle and revocation.
- Proposed, authorized and supervised collaborator launch as a later separable capability.
- Honest identity, runtime, model, billing and evidence metadata.

Out of this specification's authority:

- Automatic monitoring or ingestion of private client conversations.
- Dispatch merely because a room message describes an assignment.
- General host execution from CoS, Docker socket exposure, personal OpenClaw acting as the product launcher, or enabling CoS-container ACP spawning.
- Automatic paid API fallback, subscription upgrades or credit purchases.
- Changes to Claude's separate CoS/Guild model-role configuration work.

Existing saved meetings, immutable records, receipts and external references must survive any eventual implementation or migration.

## 3. Current implementation evidence and baseline selection

There are two relevant checkouts. They must not be treated as the same build:

| Checkout | Observed source behavior | Limitation |
|---|---|---|
| Repository root PoC, `prototype-lab/projects/project-records-room-poc/store.py` | Rooms serve as sessions; principals, membership and receipts exist | Does not contain the persistent-room/session additions below |
| Records candidate, `/Users/vanstedum/.codex/worktrees/records-room-registration/personal-ai-agents`, same relative file | `create_persistent_room`, `create_session`, principals and membership exist | Candidate code is not proof of production deployment or acceptance |

The source contract for Records & Rooms v6 distinguishes durable topic rooms from bounded sessions. This candidate follows that conceptual contract. Implementation planning must name the reviewed commit/tree and dirty patch, migration needs and API compatibility. An adapter for the earlier one-ID PoC must resolve the intended bounded session unambiguously; it must not quietly grant future-session access.

The checked-in CoS Dockerfile pins OpenClaw 2026.7.1. Earlier research consulted the personal 2026.9.4 installation. Extension APIs must be verified against the selected runtime version. No image bump is implied.

The current `principals` table conflates stable identity and one bearer credential (`token_hash`). It has no expiry field, no separate client-installation object, and no supported token-revocation path. Existing room-membership removal changes a `(room, actor)` membership; it does not invalidate the credential. Full credential lifecycle is **new platform scope**, not an existing room capability or a service already available to wire in. Claude Code reports finding no reusable platform client-installation credential service across the inspected CoS, Guild and portal code; domain-specific secrets/authentication are not that service.

The server exposes receipt lookup through `GET /api/v1/operations/<operation_key>` backed by `Store.operation()`. The existing `roomctl.py` adapter does not expose an operation/receipt lookup subcommand. It does provide generic room/read/search/post/file/link operations; the first usable client slice should extend/reuse this shared mechanism where suitable, rather than presume all integration is absent or duplicate a server connector for each vendor. Client-specific calling/configuration and end-to-end tests remain necessary. If an interface cannot invoke the shared adapter, give it a separately named support entry, transport implementation and acceptance results against the same application contract. Testing that path does not establish that the interface supports roomctl; testing roomctl does not establish support in a browser chat.

Existing UI/CoS connector tests do not establish expiring credentials, credential revocation or complete external-client integration. Support is recorded per interface and tested build, not inferred from a vendor name.

## 4. Identity, membership and multiple rooms

Use separate concepts:

| Entity | Meaning |
|---|---|
| Collaborator principal | Stable MiniMoi identity, independent of model or plan |
| Client installation | Platform-managed client/connector registration with independently managed credentials; proposed entity, not present in the current flat principal table |
| Client session or run | Particular connected or launched execution; optional reported runtime ID |
| Room | Durable work topic |
| Meeting session | Bounded capture and authorization context within a room |
| Membership/grant | Allowed principal, destination, operations and validity |
| Record | Accepted contribution with source and submitter attribution |

One collaborator may participate in several rooms and sessions concurrently, with different permissions in each. Membership in one session grants no sibling-session access by implication. Room discovery returns only authorized metadata; names and paths can themselves be sensitive.

Every read, post, upload, export and receipt request names an explicit destination. A UI may offer a default, but the server receives and validates the resolved IDs. Missing or ambiguous destinations must not silently select the last-used room. A UI default is convenience, never an authorization default. At request time authorize the authenticated principal, verified installation/credential context, resolved destination and requested operation together. The legacy-token exception must explicitly report installation identity as unavailable rather than invent one.

**Ownership — Robert's distinction:** ending a participant's access to a room/session is a room-membership operation. Issuing, expiring, rotating or revoking a client-installation credential is a platform identity/access operation. The room consumes authenticated identity/installation status and enforces local membership; it does not own the credential lifecycle. Logical platform ownership does not by itself require a new network service.

Revoking one destination membership leaves unrelated valid memberships intact. Proposed platform credential revocation invalidates that credential across its granted destinations while preserving the collaborator identity and other installations' independently valid credentials. Disabling an entire principal would be a separate explicit platform policy/action, not an implicit consequence of leaving a room. The UI must distinguish these actions. Only membership removal is available in the current PoC.

Validation occurs at entry **and on every protected request**, including reads, writes, upload finalization, export and receipt retrieval. If authentication is cached or a long-lived connection is later added, its design must define bounded freshness/revocation propagation and fail-closed behavior. A successful join must not preserve access after authorization is withdrawn.

Multi-room membership does not authorize copying content between rooms. The proposed cross-room transfer contract requires **source disclosure authority distinct from source read access**, plus destination write/disclosure policy. An authorized owner can grant disclosure for an explicit source range and destination; read A plus write B alone is insufficient. This is a proposed policy for adoption, not an already-built permission.

Use an explicit transfer operation recording actor, source and destination IDs, bounded record/artifact scope, authorization reference, and an authoritative transfer receipt. Metadata visible at the destination must not expose protected room names, participant lists, paths or inaccessible reference labels. Preserve complete audit provenance only where its audience is authorized. Revalidate transfer permission when committing the publication. An uncertain transfer reconciles using its existing request and payload.

This controls the declared transfer path, not every possible exfiltration path. A client that has read source text may manually paste or paraphrase it into an ordinary post or another system. Without further client/tool/data-flow confinement, the server cannot reliably identify that as a transfer. Acceptance must describe this residual risk and cannot claim general data-loss prevention from the transfer permission test.

A connector can restrict retrieval and publication. It cannot erase previously retrieved information or retroactively isolate an already-running client's accumulated context. For sensitive separation, offer distinct client sessions/profiles; do not claim that a room switch sanitizes model context.

## 5. Independent-client connection and publication

The already-running-client path does not require CoS or a process launcher. Robert authorizes membership and the client is configured with an appropriate credential through a protected setup mechanism. Credentials must not appear in conversation text, room records, exported transcripts, process arguments or routine logs.

“Already-running” means that a supported client has tools available to authenticate and operate on demand. It does not require a continuously running connector, persistent socket, background watcher or unsolicited notifications. No push/notify mechanism is implied.

A usable slice includes a tested connection through the supported client's tools, not just a token and join instructions. A client may require setup or reconnection; preserve existing conversation continuity only where verified. A new CLI session is not the same as attaching an existing desktop or browser chat.

The connector exposes bounded capabilities:

- Discover authorized destinations; read a selected range with source IDs, coverage and freshness.
- Publish a transcript, a labelled handoff, or both with linked coverage.
- Upload an authorized artifact and retrieve an immutable reference.
- Retrieve and reconcile the exact saved receipt for a prior request.
- Report access/connection status without asserting unsupported runtime attestation.

Transcripts preserve available text, source ordering, speakers and known source timestamps, separately from ingestion time. Missing history remains missing; summaries do not become verbatim transcripts. Hidden reasoning is excluded. The submitting principal remains distinct from quoted speakers and declared source runtime/model.

**Declared-speaker authority rule:** an imported speaker label is source metadata only. Matching an existing principal ID, including `robert`, confers no identity, membership, approval, decision or execution authority. Known-principal, unknown and outside-team labels receive the same unverified-attribution treatment and safe rendering; their different literal text is preserved. Do not resolve labels to authenticated principals or trusted identity badges. The event's authenticated actor stays the actual submitting principal, including when the owner legitimately submits it; a label never changes that actor.

The current import representation needs an explicit versioned schema mapping for declared speaker, source times and coverage. Claude Code proposes `context_class="external_source"` with `origin.declared_speaker`, reusing the existing declared/not-attested pattern. A typed source-metadata field is another option. The storage choice remains open; both must satisfy the same actor separation, display and export tests. Existing free-form provenance alone is not proof that the full import path is implemented.

A handoff is a derived record, identifies its covered source material, and distinguishes proposed assignments from recorded approvals. Posting it does not start work. When both are published, retain explicit links between the transcript and handoff.

Expose receipt/operation retrieval in the client tool that ships first. Test it independently after an ambiguous write; a successful `post` response alone does not demonstrate the recovery path.

For each publication, persist the exact payload and stable request ID before delivery. Identical retries reconcile to the existing result; conflicting reuse fails. On uncertain delivery, query the saved result rather than regenerating content or invoking a model again.

Uploads require destination authorization, explicit size/type limits and safe storage/rendering. Uploaded instructions are data, never an executable setup mechanism. Artifact receipts identify the saved object/version and integrity evidence where available. Upload permission is independent from discussion-post permission.

Paused or closed sessions reject discussion writes. Where Records permits later notes/references, use those explicit routes. Never silently reopen a meeting or redirect to another session.

## 6. Owned architecture and replaceability

Logical boundaries (platform ownership does not prescribe a network service):

```text
Platform identity / client-installation credential boundary [NEW SCOPE]
  owns issue / rotate / expire / revoke
  supplies current verified auth context to every protected API below
  Records owns destination membership, not this credential lifecycle


Already-running client ---------------------> MiniMoi Records access API
       |                                               ^
       +-- optional MiniMoi MCP facade ----------------+

CoS runtime -> runtime tool adapter -> MiniMoi invitation/approval service
                                                 |
                                      approved launch job (optional)
                                                 |
                                      paired host launcher
                                                 |
                                      collaborator launch adapter
                                                 |
                                      MiniMoi Records access API
```

MiniMoi owns identity, membership, approval state, policy, job/receipt storage and record authority. Transport adapters do not become the source of truth. No shared SQLite file is opened directly by multiple agents; writes cross authenticated application boundaries.

The CoS tool adapter and collaborator launch adapter have different responsibilities even if packaged in one service. Replacing OpenClaw must not require migrating room records or replacing client identities, approval storage or client room connectors. No OpenClaw session identifier may be the sole durable record identity.

MCP is an optional facade over the same application authorization rules. `openclaw mcp serve` exposes OpenClaw conversations and does not implement this Records connector. ACP may be an optional harness-control implementation. A2A may support future peers. None of these protocols selects billing or proves model authorship.

## 7. Invitation and approval contract

Illustrative operations, subject to concrete API naming:

| Operation | Authority/effect |
|---|---|
| List targets / preview | Permission-filtered read; no process or membership mutation |
| Propose access or launch | Stores a bounded proposal; does not grant access or launch |
| Approve/reject | Authenticated Robert action; emits exact disposition |
| Connect/acknowledge | Authenticated client action against currently valid access |
| Claim/start | Paired launcher executing a valid approved launch job |
| Status/reconcile | Authorized read of observed evidence |
| Revoke/cancel | Explicit scope; immediate access revocation is distinct from process cancellation |

An access-only request and a process-launch request are distinct effect types. Direct owner membership setup does not need a CoS proposal. Existing Work effect enums are not silently widened.

Live grants bind a versioned immutable request: principal/client target, room/session, operations, approved brief/artifact scope, permission profile, host and launch adapter when relevant, workspace, billing policy, limits and expiry. Derive approver identity from authenticated server context, never a request-body name. Revalidate current authorization at connection, claim and each record operation.

Changing a bound field invalidates the approval; read-only status updates do not mutate its scope. Approval views show the concrete request and its provenance. Rate-limit proposals and do not treat room text or a model assertion as owner authorization.

CoS can be offered preview/propose/status tools through reviewed config. It cannot confirm its own proposals. Cancellation authority must be selected explicitly; default proposal is Robert-controlled cancellation/revocation, with any narrower delegated cancellation scope separately configured.

## 8. Platform credentials and room enforcement

The platform client-installation lifecycle owns credential issuance, protected provisioning, rotation, expiry and revocation, separately from stable collaborator identity. Records & Rooms consumes its authentication result and checks current destination membership. Introducing that platform capability is real scope; this specification establishes the boundary without selecting standalone-service versus in-process-module deployment.

The authentication result should distinguish principal, installation/credential identity, validity and applicable scope. Authorization must not treat a caller-supplied installation ID or scope as trusted. **Authentication freshness:** membership is checked on every protected request. Credential validity must also be current at that boundary. Default: no authentication-result cache across requests. A proposed cache requires an explicit maximum staleness, invalidation/revocation propagation mechanism, outage behavior and tested upper bound on continued access after revocation. These become a named approved contract, not an accidental implementation detail. If current authorization cannot be established within that contract, deny the protected operation. This includes streamed/chunked operations at their defined protected boundaries.

An in-process implementation can check authoritative local state directly. A remote platform service introduces latency and availability dependencies that the implementation plan must measure and handle; it must not silently retain the last successful result during outage.

Platform identity records and room records use stable references; token rotation must not rewrite historical event actors or receipts. A migration must preserve identity, existing memberships and historical references while explicitly handling old credential retirement.

Two credential strategies remain viable:

| Strategy | Benefit | Tradeoff |
|---|---|---|
| Separate scoped credentials per destination or run | Narrow compromise exposure; clear expiry | More client credential selection/rotation complexity |
| Client credential plus server-checked membership grants | Convenient multi-room use; centralized independent revocation | Credential compromise exposes all currently granted memberships |

Both strategies require the room to check current membership on every protected request and the platform to supply current credential validity. Expiry, operation limits and credential storage must be explicit. B3 acceptance of room-membership revocation does not establish platform credential revocation.

**Sequencing decision remains open:** build the installation/credential lifecycle with B3, or explicitly defer it while B3 uses the existing principal token under a bounded owner-approved PoC exception. If deferred, state plainly that the token has no supported expiry/revocation, enumerate the destinations and data allowed for that demonstration, and name the platform follow-on and retirement gate. Do not mark full credential-revocation acceptance passed. This exception is not permission to describe the system as having independently revocable installations.

**Proposed legacy-token retirement gate (LT-1), if Robert selects deferral:** the approval record must name the first client/interface, token reference (never its value), exact destination IDs, approved non-sensitive data classes, responsible owner, an absolute expiry time, and triggering events. No blank/implicit exception is valid. The exception ends at the earliest of its expiry, a suspected compromise, an attempted destination/data-class expansion, introduction of private-personal/restricted employer-or-customer material, or promotion beyond the bounded first-slice demonstration. “Client-derived” here means employer/customer/client confidential source material; an agent-generated synthetic test transcript is not automatically such material. Unclassified sources are denied.

Name the follow-on **Platform client-installation credential lifecycle** and its acceptance: separate installation identity, secure provisioning, expiry/rotation/revocation, old-credential rejection and unaffected sibling installation. Completing LT-1 is a prerequisite to continued live integration in a later delivery slice. Offline synthetic simulation with no credentials or room access may proceed independently; requiring credential work to run a fake launcher would add no access protection.

At LT-1, stop the exception's use, remove its room memberships, and prevent the legacy token from being accepted by the exposed service before reconnecting or expanding access. Current membership removal alone does not prove token invalidation. If credential retirement cannot yet be enforced, keep the exception environment inaccessible/disabled; deleting a local token file or writing an expired date in a document is not enforcement. Test rejection of the old token when the platform migration is built. This is a proposed gate, not an already-running automatic control. No actual exception is granted by this specification.

Provisioning options include an owner-run registration flow writing a protected token file or a platform-managed secret store/delivery flow. The client adapter may consume a credential internally, but the model conversation receives only the configured reference, never the raw secret. Avoid claiming the client process cannot access the value it must use. Confirm secure local file ownership/permissions or equivalent secret-store access, and keep secrets out of logs and review evidence.

Launcher pairing credentials are separate from collaborator credentials. Owner, CoS gateway and model gateway credentials never become room credentials. HTTP/MCP authorization must validate the intended service/audience; no unchecked upstream-token passthrough.

Permission enforcement belongs in the room service and actual client/OS controls. An allowlisted cwd is not a filesystem sandbox. A connector's narrow tool set does not imply the whole client lacks other tools. Live automated profiles must enumerate effective shell, filesystem, network, plugin and MCP capabilities and validate them before private data is provided.

## 9. Launching and supervision — optional later slice

Launch runs on a selected host with the intended vendor-supported authentication, outside the CoS container and personal OpenClaw. No CoS general exec, Docker socket, nested launch or dynamic community-plugin installation is introduced.

Use pinned reviewed executables/adapters, fixed argument arrays without shell interpolation, an explicitly constructed environment and reviewed startup configuration. Briefs are data. Inherited API keys, hooks, MCP servers and repository startup settings must not silently broaden authority. Secret-pattern filtering is hygiene, not an injection boundary.

A launched collaborator receives a fresh run-scoped credential with bounded destination/operations and validity; do not reuse the already-running client's standing credential. Launch cannot inherit unrelated memberships merely because both runs represent the same principal. This is a requirement for the later launch slice, not proof that credential issuance is built.

First live launch proposal: bounded review, selected session read and one post, limited duration/turns/concurrency where supported. If a client cannot enforce the selected permissions, it is not eligible for that profile. Native permission prompts produce needs-attention, not automatic unrestricted mode. A coding profile with worktree writes is a later separately authorized capability.

Durable job IDs, exclusive claims, process tracking and recovery reconcile lost acknowledgements and restarts. Do not promise exactly-once OS process creation. An uncertain start is reconciled before another launch. Expired/revoked grants or closed sessions during startup fail closed.

Cancellation requests, access revocation, attempted process termination and confirmed process stop are distinct events. Revocation cannot retract downloaded records or undo completed effects. Session closure does not cancel unrelated jobs automatically.

## 10. Receipts and truthful presentation

| Evidence stage | Required source | Allowed interpretation |
|---|---|---|
| Proposed | MiniMoi proposal record | Request exists |
| Approved | Authenticated owner disposition | Exact scope authorized |
| Access granted | Membership/grant storage | Client may connect within scope |
| Claimed | Launcher job record | Host accepted job |
| Process started | Host execution evidence | Process started; not proof of connection |
| Joined | Explicit authenticated room acknowledgement | Client connected to the selected destination |
| Saved | Atomic Records write/receipt | Exact contribution/artifact durably accepted |
| Failed / expired / revoked / stopped | Responsible subsystem | Only the evidenced transition |
| Simulated | Test double | Simulation, never live join or authorship attestation |

Access membership alone is not presence. Historical join evidence is not proof the client remains connected. Show last-observed connection status and freshness where available, otherwise unknown. Runtime self-report is labelled; authentication identifies the submitting credential, not cryptographic proof of the model behind it.

Records and their receipts commit atomically under existing Records rules. No polling or receipt recovery triggers inference. Keep receipts permission-filtered; neither IDs nor status endpoints become an information leak. Client receipt requests carry the expected destination. The server resolves the saved operation's authoritative destination, requires an exact match and checks current access. Requesting room A's receipt while declaring B fails even if the principal belongs to both. Explicitly requesting A may succeed when authorized. Globally unique IDs are permitted: the defect would be missing destination/authority validation, not global uniqueness itself. Non-room platform receipts need an explicit typed authorization context, not a guessed room.

## 11. Billing and cost visibility

Logical identity survives changes to subscription plan, runtime and model. Each run records requested billing mode, observed authentication mode when available, reported model/runtime, connector version and usage evidence with its source. A configured expectation and verified observation are distinct.

Robert's intended modes are subscription-backed collaborator clients where supported, and deliberately selected API-backed MiniMoi/Master Craftsman work. Model and plan choices remain under his control. No protocol or vendor label proves billing mode. Unknown cost remains unknown, not zero. Persist explicit evidence availability, for example `usage_evidence.status = "none" | "reported" | "verified"`, with source/time/measurement fields when present and an absence reason when none. Cost/usage values are null when unavailable, never coerced to zero. Distinguish unknown authentication observation from a configured subscription expectation. A schema-defined equivalent is acceptable; an omitted field is not a successful measurement.

Subscription-only execution stops when authentication or allowance is unavailable or cannot satisfy policy. No automatic API-key fallback, plan upgrade or credit purchase. An explicitly approved API run uses its own configured model and budget policy. Do not promise exact per-run subscription usage where a client does not expose it.

Saving, retrieving, uploading and reconciling existing records require no CoS inference. Instrument those paths to verify the claim. Optional CoS reasoning or synthesis is a separately requested operation and cost.

## 12. Options and proposed sequence

The following remain design choices for Robert after the completed reviews. The v0.6 safety amendments are proposed for adoption together; no reviewer selects them on his behalf:

| Decision | Options | Proposed starting point |
|---|---|---|
| First delivery | Already-running client; simulation first; live launch first | Already-running client (B3) |
| First client/interface | Claude Code CLI; Codex CLI; Grok CLI; individually verified chat/desktop path | Name one exact interface and version before build acceptance; Claude Code CLI is Codex’s initial recommendation |
| Proposed safety amendments | LT-1 if deferred; no auth caching absent contract; source disclosure permission | Adopt with final spec; not yet owner-selected |
| Room access transport | Owned HTTP connector; MCP facade over same API | Test concrete client ergonomics; retain one authorization contract |
| Credential strategy | Per-destination/run tokens; client credential plus scoped memberships | Select after client setup/revocation comparison |
| Platform credential lifecycle timing | Build installation lifecycle with B3; explicitly bounded legacy-token PoC first | Design the split now; Robert selects delivery timing and any exception |
| Imported-speaker representation | External-source origin metadata; typed source-metadata representation | Same no-authority rule and acceptance tests either way |
| CoS integration | Thin version-compatible plugin; another reviewed HTTP-tool adapter | Avoid requiring a CoS runtime upgrade |
| Launcher reachability | Host pulls approved jobs; authenticated inbound endpoint | Pull reduces inbound exposure; adds recovery/queue work |
| Launch implementation | Vendor CLI; ACP adapter; future A2A peer | Compare vendor CLI first; keep adapter replaceable |
| Packaging | Separate services; two logical ports in one service | Choose operationally; preserve boundaries |
| Isolation | Native restrictions; OS sandbox; separate user/container | Match enforced capability to approved data/profile |

Proposed delivery sequence:

1. **Already-running join and publication:** one supported client, two rooms, independent membership permissions/removal, actual read/post/upload and receipts. Platform credential lifecycle timing and first client/interface are selected explicitly; if deferred, record LT-1 next to all not-implemented credential tests. No credential-revocation claim from membership tests alone. No launcher or CoS requirement.
2. **Simulated launch lifecycle:** preview, approval, claims, failures/restarts and clearly simulated evidence.
3. **One live room-only launch:** bounded task with verified permissions and billing path.
4. **Additional clients and profiles:** independently validate Codex, Claude and Grok interfaces; coding and persistent participation only after their own acceptance.

The proposal for the first slice excludes a launcher, CoS mediation and a second vendor. Both chat reviewers recommend HTTP/shared-tool access first and deferring MCP; this is a sequencing recommendation, not proof that MCP has no value for a selected client's actual tools. If the selected client requires another transport, record that scope choice and its own tests.

The first slice does not claim all clients are supported. Record for each interface: read, transcript, handoff, upload, receipt, auth mode, setup/reconnect limitations and tests. Chat applications, CLIs and desktop sessions are separate entries.

## 13. Acceptance criteria

### Already-running multi-room slice

- Record the exact first client/interface, version and tool path. One stable principal uses that client in two authorized sessions with different operation grants. Explicit reads/posts target the intended session; unauthorized third-session access and omitted/ambiguous destination fail.
- Revoke one membership and prove the other still works on subsequent requests, without restarting the client. Do not count this as credential revocation.
- If the platform lifecycle is included, register two installations under one stable principal; revoke one credential and prove its subsequent requests fail across its granted rooms while the other installation remains valid. Exercise rotation and expiry and preserve historical actors/receipts. If deferred, record these tests as not implemented alongside the owner-approved LT-1 exception, its expiry, triggers and enforcement evidence; never report them passed.
- Exercise transcript-only, handoff-only and linked publication with source attribution/coverage preserved through export. Another authorized participant retrieves the exact saved content.
- Submit a transcript as a non-owner client with declared speaker labels matching `robert`, another real principal, an unknown identity and an outside-team person. In every case the authenticated event actor remains the submitting client, labels receive equivalent unverified-attribution treatment, and no matched-identity approval, owner control, decision, membership or dispatch behavior fires. Verify this in API records, UI presentation and exports, including adversarial markup in labels. Repeat through the supported import representation, rather than merely testing raw text in a body.
- Exercise permitted upload and denied upload, size/type controls, safe rendering and immutable reference retrieval.
- Reject paused/closed discussion writes and prevent silent reopening/redirection. Test permission-filtered discovery, exports and receipts.
- After a simulated lost post response, use the actual client tool's receipt/operation lookup to recover the authoritative saved result with the same request ID and payload. Test permitted lookup and denied lookup after membership removal, as well as lookup by another principal. No duplicates, regeneration or CoS inference.
- Explicitly submit the same request ID with a different payload; require a conflict response (409 or equivalent) and unchanged original record/receipt. This is a separate case from same-payload replay.
- As a principal authorized in both rooms A and B, request an A operation receipt with B as the expected destination; reject with no A metadata. Request it explicitly as A and permit only under A's current authorization. Preserve destination checks in legacy-route compatibility wrappers as well as new client tools.
- Confirm that posting an assignment neither launches an agent nor authorizes work. Demonstrate explicit cross-room sharing with a receipt naming actor, source, destination and scope. As a principal with source read and destination write but no source disclosure grant, require refusal. Grant the specific disclosure and verify permitted transfer, filtered source metadata, idempotent retry and revalidation after revocation. This proves declared-transfer policy, not interception of manually pasted/paraphrased source text.
- Document residual exposure for the actual client: every destination exposed by a stolen credential, already-ingested model context, downloaded artifacts, external tools/files and unavailable history. Test server-side revocation separately from those non-retractable copies; do not call profile separation a universal sandbox.
- Verify no cross-request authentication cache in the default profile. If an alternate freshness contract is selected, exercise invalidation, the maximum stale window and platform outage; deny access once fresh authorization cannot be established within the approved bound.
- Exercise explicit absent usage evidence and confirm reports show unknown, not zero or inferred subscription usage.

### Simulated and live launch slices

- Reject tampered, expired, replayed and misattributed approval requests. Wrong host, adapter, destination or permission profile cannot consume the grant.
- Simulate duplicate delivery, crash after spawn, lost acknowledgement, offline host and cancellation/revocation during join.
- Prove hostile content cannot change command arguments, credentials or scope; verify effective native/OS permissions, startup config and excluded inherited keys/hooks.
- Prove simulated/process-start evidence cannot render as a live joined collaborator. Verify historical presence is not presented as current without evidence.
- Verify subscription-only exhaustion/auth failure cannot trigger paid API fallback.
- Use a replacement CoS adapter/test double without changing Records identities, records, approval storage or client room connectors.
- Record exact tested build, client versions, simulated/live coverage and remaining limitations. Live tests require their specific authorization; no test claim crosses builds automatically.

## 14. Review, adoption and traceability

Review seats:

| Reviewer | Evidence and scope |
|---|---|
| Codex | Source/doc review and v0.4–v0.6 consolidation; not independent acceptance of its own new text |
| Claude Code | v0.4 review grounded in two named checkouts; corrected platform/room ownership finding |
| Claude Chat | v0.5 document review; concurs with amendments; no independent code/runtime verification |
| Grok Chat | Original v0.5 document review, then follow-up informed by Claude Chat; concurs with amendments; no repository inspection |

All requested seats have returned. These are different scopes of evidence: Claude Code's code-backed review is of v0.4; chat reviewers assessed v0.5 as documents. Neither independently validated the new v0.6 text or its unbuilt controls. Grok's follow-up is not independent confirmation of Claude's findings. Their use of “blocking” expresses review priority, not authority to select Robert's design.

The v0.6 review packet records the review sources. The build disposition above records the initial implementation defaults following adoption. No automatic extra architecture round is requested. Robert adopted this version and authorized the initial implementation; later option changes remain his decision. Reviewed-diff/build acceptance remains a separate future gate.

Source files are retained in the v0.6 review packet: Claude Code's exact v0.4 review, Claude Chat's exact v0.5 review, and Grok's original pasted review/follow-up (including its quotation of Claude). Original specs remain in their versioned directories. The older Records §7 assertion that existing principal tokens were revocable remains superseded by Claude Code's corrected finding.

The earlier Codex review checked primary OpenClaw/protocol documentation. Thus the chat assertion that no reviewer checked any third-party behavior is too broad: source/document verification exists, while no live runtime, compatibility or billing proof is claimed. Keep those evidence levels distinct.

This adoption candidate does not modify the parent Records v6 document or historical build packets. Align implementation contracts against the selected source baseline before coding. Continue the filesystem handoff convention until Robert selects its replacement/coexistence; no automatic migration or private transfer is authorized.

## 15. Amendment — September 25, 2026 (proposed)

**Status:** proposed by Claude Code under package A0, together with Spec 158 v0.7 §21. It takes effect only on Robert's acceptance after Codex's review. It extends this specification. It does not relax any rule above.

1. **Typed non-room scopes.** Spec 158 §21 adds standalone records and work-run records. Neither is a meeting session. For them:
   - **Named destination.** Every request names an explicit typed destination: `workspace:<id>` for records and `work:<assignment_id>` for work runs. A UI default never selects it on the caller's behalf.
   - **Grants.** Installation-credential grants use the same typed destinations, with the operations named in the implementation plan. The server authorizes against the scope stored with the resource, never against the client-supplied destination alone.
   - **Idempotency identity.** The typed scope and the operation are part of the idempotency identity.
   - **Receipts.** Receipts carry the typed scope and are rechecked against current access. This implements §10's requirement that "non-room platform receipts need an explicit typed authorization context, not a guessed room".
   - **What stays the same.** Session destinations and their tests are unchanged.
2. **Workshop executor identity.**
   - **Principal and grants.** A local Workshop executor is a collaborator principal with its own client installation and expiring credential. Its grants are limited to the assignment and session it serves.
   - **Manual runs.** A manually started local synthetic executor is not a launch under §9. It is labelled `manual` and `real` or `test`.
   - **Vendor runtimes.** Launching a vendor runtime remains governed by §9 and Spec 158 gate L.
3. **Standing read.** CoS standing read over standalone records follows Spec 158 §4.1. Both the owner-assigned role and a current non-legacy credential are required on every request. Neither grants disclosure into a session or any effect.
4. **Implementation ownership.** For the Spec 158 §21 scope, Claude Code implements and Codex validates. This supersedes, for that scope, the "Implementation defaults chosen by Codex" ownership in the build disposition. The defaults themselves remain in force:
   - no cross-request authentication cache;
   - explicit source-disclosure authority;
   - destination grants checked by the server.
5. **Capability entries.** Client support continues to be recorded per interface and tested build (§§3, 12). A cloud-origin chat client requires a reachable authorized endpoint and remains unproved until separately tested. A local synthetic transport test is not client acceptance.
