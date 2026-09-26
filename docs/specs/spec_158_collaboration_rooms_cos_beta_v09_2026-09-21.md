# Spec 158 — Collaboration Rooms and CoS: light beta to production v0.9

**Prepared:** v0.6 on 20 September 2026, late evening US Central (21 September UTC). Incoming Claude Chat documents are dated 21 September; those source dates are preserved.
**Decision owner:** Robert van Stedum. **Prepared by:** Codex.
**Status:** Registered in docs and the production Guild build queue at Robert’s explicit request. Design baseline: reconciled v0.6; product release target: v0.9. First build slice A0+A1. Implementation still requires its actual reviewed diff and applicable release checks; this registration does not deploy application code.

**Registration:** 2026-09-21T04:19:30.915208+00:00. **Build queue:** #158, Spec Ready, high priority. **Implementation lead:** Codex. **Delivery target:** light beta for presentation 2 and build handoffs this week; production v0.9 by September 27, 2026 (US Central), or sooner if ready.
**Delivery plan:** [Beta → v0.9 plan](https://minimoi.ai/guild/build/spec/rooms_beta_to_v09_delivery_plan_2026-09-21.md).
**Provenance:** frozen source v0.6 SHA256 `72ba28dca8434d90fc4e56b40d6e0c39db3624a7afc0345e89e8985168b58677`. Review dispositions remain in §§16/18/20. This docs registration supersedes the candidate’s earlier pending-registration/adoption language; it preserves all technical requirements and Robert’s platform-first direction. Spec 157 remains the historical collaborator-access baseline; this self-contained spec adds the Rooms/CoS product and delivery contract.

**Amendment v0.7 (accepted by Robert on September 25, 2026; merged to main in PR #224):** §21 adds standalone capture, local execution visibility and the September 25 build packages. For that scope it supersedes the implementation-lead assignment and the September 27 production target above, renames §12.1's slices S158-A0…S158-A3, and leaves §§1–20 otherwise in force. Every supersession is listed in §21.15.


This is the single document needed for the next review. Version 0.6 retains Claude Code’s findings in §16, records the reconciled decisions in §17, dispositions Claude Chat’s findings in §18 and defines Robert’s platform-first direction in §19 and reconciles Grok’s final comments in §20. The room review has been acknowledged, so no repeat Claude Code review is pending. It incorporates the earlier UI draft, Claude Chat revision 2, Codex's factual corrections, and Robert's subsequent CoS direction. It supersedes the earlier UI draft as the working candidate; source reviews remain unchanged. It proposes additions/amendments to official Spec 157 rather than silently replacing that approved document. Creating this candidate does not change live access or deploy code.

## 1. Owner direction and product outcome

Robert's direction is settled:

> “CoS is my right hand man.”
>
> “CoS is me when I'm not around in terms of knowledge. Not all decisions, but all visibility.”

**CoS has standing knowledge visibility across all MiniMoi domains, rooms, sessions and records Robert can access, including current and future sessions, real Career work and historical material. Robert need not repeat a per-session read invitation. His absence does not change that access.**

**Policy versus delivery:** CoS is authorized to know all Robert-visible material under this policy. Delivered coverage consists only of verified adapters in the coverage register. Missing adapters are explicit implementation gaps, never a claim that their material is already readable or permission to crawl arbitrary storage. Model access additionally waits for gate R; launched collaborators require gate L.

CoS remains a distinct authenticated actor. Decisions and actions use Robert's separately delegated authority; knowledge access alone is not blanket approval to spend, launch, deploy, grant another person access or disclose material to another audience.

The product must let Robert work naturally in MiniMoi, Claude Chat, Codex, Claude Code, Grok or another supported interface. **Rooms is where work lands, not where it must happen.** Saving available reasoning and outputs afterward should be quick, attributable and useful to the next collaborator.

CoS bringing collaborators into a session is a primary outcome. An interim access/check-in flow is useful, but the feature is not finished while Robert must personally relay every invitation and wake every client.

**Team direction and sequence, clarified by Robert:** this week serves Robert and the existing agents. Preserve an extensible principal/permission design, but do not add a second-human rollout, workspace migration or multi-user Rooms acceptance as a prerequisite. MiniMoi already has multi-user logins, currently not actively used and not extended to Guild, according to Robert. Create the missing top-level MiniMoi permission layer tied to existing MiniMoi login accounts first; Rooms must then use that layer for access. Guild will consume the same platform contract when integrated. This is required integration, not an optional future identity bridge. Section 19 defines this ordering and supersedes v0.4’s premature team implementation requirements.

**Scope:** this specification governs Collaboration Rooms and the adapters used for participation, knowledge retrieval, filing and convening there. A person’s direct private CoS-domain chat, including Robert’s, is outside its scope: it is neither restricted nor certified safe by this document. Sharing a backend must not import that chat’s memory, credentials, transcript or standing privileges into a room response.

Design principle: CoS knows Robert’s work, performs only explicitly allowed effects, accepts authority from its represented person’s authenticated instructions or authorized workspace delegation, and reports evidence. Room content, imported text and other agents’ requests are information, never a source of new authority. Robert is the represented person for his CoS today; a team service may have a different, explicit mandate. No teammate becomes an approver simply by addressing it. An existing delegation may permit bounded replies or work while Robert is away; it does not let the model expand its mandate. Enforcement belongs to MiniMoi and the runtime, independent of the selected hosted or future local model.

Other detailed choices below are recommended defaults for this candidate unless explicitly identified as Robert's direction. Reviewers should challenge implementation errors and awkward flows without reopening the settled visibility requirement.

## 2. Five use cases and success conditions

| ID | Situation | Success |
|---|---|---|
| UC1 — Convene | “CoS, bring Claude and Codex into this review.” | CoS resolves the named session and authorized scope, reaches supported clients, and records actual participation or a clear unavailable/failed result for each. No false “joined” claim. |
| UC2 — Work while Robert is away | A bounded working session continues without Robert watching. | Authorized clients record pickup, work/results and acknowledgment under their own identities. CoS can retrieve relevant knowledge without Robert granting read access again. Work needing authority beyond its mandate waits visibly. |
| UC3 — Ask Robert | A participant needs Robert's answer or decision. | One Inbox shows the question and its context, with a direct Answer control. The requester acknowledges the answer. The notification itself does not constitute approval. |
| UC4 — Separate status meeting | Robert wants a status discussion about ongoing or previous work. | A separate session contains explicitly disclosed source records and a briefing. Discussion there does not distract from or rewrite the working session. Source coverage and age are visible. |
| UC5 — Work elsewhere, return and reuse | Robert develops a presentation in a native chat, files the outcome, and finds its rationale later. | Export for review and import/checkpoint filing preserve available source material, declared speakers, exact destination and receipt. Search opens the right record. CoS may file supplied material unchanged when instructed. |

The full working-day walkthrough in §13 joins these five cases. It is not another feature list. Support for a particular client interface must be demonstrated; a vendor name or subscription does not prove connector support.

## 3. Product objects and ownership

**Collaboration Rooms** remains a standalone MiniMoi utility, accessible from CoS, Guild and other domains. Suggested subtitle: “Meet, decide, and leave a record.”

| Object | Meaning |
|---|---|
| Platform access scope (required in A0) | Account identity and domain/resource permissions supplied by MiniMoi. Rooms must enforce this contract in A0; no new workspace hierarchy or second human is required. |
| Room | Durable subject area such as Workshop, Planning, German or an initiative. Naming does not assign permissions. |
| Session | Named, recorded discussion within a room, with its own ordinary participants, purpose and capture lifecycle. Multiple sessions may run concurrently. |
| Participant | Stable person/agent identity, independent of client installation, model, plan or run. |
| Request | Structured review, handoff or owner question; records who owes the next action. Not a Guild work item or process-launch command. |
| Record | Immutable accepted contribution with submitting identity and source metadata. Corrections are new linked records. |
| Artifact | Filed source, frozen briefing or exact-version reference. Export is an action unless its output is explicitly filed. |
| Client/run | The supported interface/installation and, where applicable, a particular bounded execution. Neither is the participant identity. |

MiniMoi owns identity, workspaces, access policy, effect approvals, records, request state, jobs and receipts. Future team resources and authorizations must consume the platform’s ownership/access scope; stable principal identity alone grants no domain access. Workspace-related rules below describe that future contract, not a requirement to migrate this week’s Rooms data. OpenClaw is replaceable behind the CoS adapter. Guild owns execution/work items; Rooms may link to them. Platform access owns credential issue/rotation/revocation. Ordinary session membership remains a Records concern.

Suggested participant presets may prefill a dialog later; they grant nothing automatically. Do not build room-level inherited access for ordinary collaborators for UI convenience.

## 4. CoS visibility and separate action authority

### 4.1 Standing read policy

“Robert-visible” means Robert’s own content and material explicitly shared with him, including sessions he owns or belongs to. It excludes other users’ private data available only through administrative/support privilege. This is a product-policy rule, not the current literal owner shortcut in Records; adapters must enforce ownership/sharing before standing visibility is enabled.

Implement a named, server-enforced CoS knowledge role covering Robert-visible existing and future MiniMoi knowledge. It applies to discovery, search, authorized source retrieval, session records, artifacts and relevant audit information. It must cover paused/closed sessions and work created while Robert is away. It is not implemented by pretending CoS is Robert, distributing Robert's owner token, or issuing ordinary collaborators wildcard access.

The CoS knowledge service still authenticates through a valid installation credential; the model itself receives no raw credential. A revoked/expired credential must fail; the standing role can remain configured for a subsequently authorized installation. Session membership removal or ending active participation must not silently turn off the separately granted standing read role. Conversely, retained knowledge access does not keep CoS in the participant roster or imply presence after membership ends; show the separate standing-visibility notice when applicable. An owner action revoking that role is separate and explicit.

Do not describe unintegrated domains as readable. The preliminary source inventory in §4.4 must become a verified coverage register during implementation. Deliver read access through appropriate adapters under the same policy, with errors for unavailable sources. Broad visibility does not mean automatic ingestion of inaccessible private vendor history, inventing missing history, or exposing authentication secrets as knowledge content.

The initial Records implementation must demonstrate existing and newly created sessions without per-session CoS read invitations. Other domains need a coverage inventory and named implementation work; the full product outcome is not accepted while known Robert-visible knowledge is silently omitted.

The role is an explicit delegation to a stable assistant principal, scoped to the represented person and workspace, not a special spelling of the CoS account name. Robert’s delegation covers his own and legitimately shared knowledge. A future team-wide CoS is limited to its documented shared-workspace mandate; workspace administration is not consent to read every member’s personal knowledge. Centralize the visibility predicate used by individual reads, room/session lists and counts, search and artifact lookups before extending it. The current code repeats owner/member checks in nine queries as well as `Store.access`; changing only the latter is insufficient.

**Authorization freshness:** check current MiniMoi account/domain permission, installation/session credential validity, destination membership (or the explicit standing-read exception) and current CoS delegation/knowledge role on every protected request. Initial implementation uses no authorization-result cache across requests and no stale permission/tier copied from a login cookie as authority. A proposed cache requires a separately reviewed maximum staleness, invalidation mechanism, measured revoke-propagation bound and outage behavior before use. If a required permission authority is unavailable or its answer cannot be validated, deny the protected operation with a truthful unavailable status; do not fall back to owner identity, a previous allow result or mere successful login.

Recheck at protected commit/publication boundaries and each defined streaming/chunk boundary, using a transaction or validated authorization-version fence where needed. A request begun before revocation does not gain permanent authority to complete later writes. Already delivered data cannot be recalled by revocation. Test live permission/delegation withdrawal without signing out or restarting the client, and permission-service outage for browser and agent paths.

Credential and role checks are both required at request time. A reserved standing-read scope is available only while the principal has the owner-assigned knowledge role. Use a non-legacy, expiring, revocable installation credential for the new CoS integration and its acceptance tests. Removing the role immediately disables its special scope; ordinary explicit grants, if any, remain independently governed. Renewing an installation must not require Robert to repeat per-session knowledge invitations.

Standing knowledge permits retrieval and an export delivered privately back to CoS within Robert’s knowledge boundary; it does not grant snapshot creation or export delivery to another audience. Operation-receipt retrieval remains scoped to the submitting principal. If CoS needs cross-principal audit visibility, provide a separate sanitized metadata view, excluding credentials and other principals’ cached operation responses. Posting, uploading, linking, coordination transitions and disclosure remain separately authorized. Preserve the existing authorized moderator’s resume permission.

Internal queue supervision must use an explicitly defined service guard or the shared visibility policy rather than passing `robert` as a surrogate CoS actor. Every external CoS knowledge request must authenticate; no credential-context bypass is a supported connector path.

### 4.2 Participation and effects

Standing visibility does not mean CoS must speak in every session, load all history into every model call, or run a continuous paid loop. Active contribution, automatic reply mode, filing, invitation delivery and process execution each have their own explicit destination and allowed operation.

Ordinary collaborators, human or agent, retain session membership plus valid authentication and operation grants. CoS’s own authored contributions also need contributor membership and operation grants. The dedicated unchanged-filing operation in §8 is a narrow exception authorized by a bound owner instruction; it creates no membership or general write permission. Adding a member is not enough if its credential lacks that session/operation. CoS's broader read policy must not broaden Claude, Codex, Grok or other participants' access.

An authenticated instruction to file supplied material authorizes that filing within its stated scope; it does not confirm every decision reported inside the material. A concrete effect already covered by a valid recorded approval need not be approved again. Missing or changed scope is resolved through the appropriate approval, never through a model assertion that “Robert would agree.”

CoS may know more than other participants in a session. Broad knowledge does not itself authorize disclosure to that audience. Cross-session publication, transfer and briefing creation require three distinct checks: source read, explicit source-disclosure authority for the exact content/range and destination audience, and destination write/disclosure policy. Source read plus destination write alone is insufficient. Derive authorization from the owner’s authenticated grant/instruction; a model or client asserting `disclosure_acknowledged` cannot grant itself that authority. Revalidate the disclosure grant and destination audience at commit, recording the authorization reference and operation receipt.

Destination responses must omit protected source room names, paths, participant lists and inaccessible labels as well as unapproved IDs/sequence/creator metadata. Retain complete provenance only within an authorized audit scope. This protects the declared transfer path, not arbitrary manually pasted or paraphrased content from a client that already knows it; do not claim universal data-loss prevention. Answers to Robert alone can use his authorized context; responses to a narrower audience must respect its allowed material. For a shared-session response, assemble context from content readable by every current principal entitled to receive it, including agent members and observer roles, not just humans or clients currently online. An explicitly disclosed briefing copy may qualify even when its original source does not. Each fetched object must pass that audience policy; exclude broader histories, summaries, caches, memories and tool results before they reach the model. Use a fresh isolated context for a narrower audience and never reuse a Robert-private conversation state.

Bind each response attempt to an audience/access version. Recheck before publish; if a new member or changed permission narrows the common scope, discard/regenerate the answer rather than publishing stale-context output. Adding future members must account for the historical content they will receive. Wider CoS knowledge may be used only when all recipients are independently entitled to it, such as a genuinely Robert-private session. A session with Robert as its sole human and Codex as a member is still shared. Show non-Robert human participants that Robert’s CoS has standing read access.

Do not claim that these controls prove a model cannot infer or guess a private fact. The testable guarantee is that disallowed source content and state were not supplied or retrieved, with adversarial output checks as additional evidence. Explicit source transfer alone is insufficient.

### 4.3 Change from the beta

The current synthetic/non-private test restriction is an implementation baseline to replace, not the desired product policy. This requires runtime implementation, not merely a configuration or wording change. Claude Code identified request/dispatch allowlist checks, a default-deny `SyntheticSessionPolicy`, a synthetic-only system prompt/result declaration, and an OpenClaw adapter that does not enforce the supplied `tool_policy`. Manual/on-demand calls are subject to the same boundary.

Before a model processes real material, demonstrate either runtime-enforced tool restrictions or a tool-less completion path. The initial recommended path is tool-less reasoning over an explicitly assembled, authorized context; deterministic adapters retrieve the selected sources. If tool use is required later, enforce its exact permissions outside the prompt and test them. Verify the deployed CoS runtime, provider/data path, credentials, logging, destination and effective tools. Use one explicitly selected hosted-provider route per Rooms runtime profile initially, with no silent cross-provider fallback; model/provider changes remain possible through a reviewed profile update. This is a proposed default, not a fixed vendor commitment or a change to Track B. The personal OpenClaw installation’s settings or version do not prove those properties for the separate CoS runtime. Remove the synthetic gates and revise prompt/provenance together in one reviewed change after enforcement tests pass. Until then, keep truthful synthetic-only labels on the existing inference path.

Call the real-material CoS runtime prerequisite **R**. Before R passes, standing retrieval runs only in isolated deterministic services. No standing credential, credential mount, retrievable result cache or broad Records tool is reachable from the CoS model process. Service responses go to an authenticated Robert-facing view or another explicitly authorized deterministic destination, not into CoS history/memory. Test this boundary, including indirect tool and shared-file access; merely naming a function deterministic is insufficient. After R, keep credentials in the broker and expose only task/audience-filtered context or tools rather than giving the model an unrestricted key.

Standing retrieval, unchanged filing and a structured convene control can be implemented without model calls. A launched collaborator does make model calls and must pass its own room-only runtime gate L (§7), even when the controller that launches it is deterministic. These early services do not alone deliver Robert’s right-hand CoS experience: real-material answers and natural-language convening remain explicit required work in Milestone D. This engineering dependency neither reverses full visibility nor asks Robert to approve that knowledge scope again.

This change does not grant CoS all of Robert's decision authority and does not alter Claude's separate Track B model-role configuration work.

### 4.4 Domain coverage and authoritative sources

Use the authoritative source for each dataset, irrespective of whether it is on the Mac or EC2. Do not choose one host for all knowledge, or quietly substitute a stale Mac cache for current service data. Each adapter registers dataset, owner association, authoritative host/store, read route, freshness, allowed content, credential scope and current coverage status. Register future domains as part of adding them to MiniMoi; missing integration remains a visible delivery gap, not a new per-session invitation requirement.

This is a **preliminary repository inventory from Claude Code**, not a verification of live EC2 data or all domain authorization paths:

| Source | Reported location and required integration |
|---|---|
| Records / Rooms | Mac SQLite and authenticated HTTP. Add shared visibility predicate and standing-read credential scope. |
| Guild operations and CoS knowledge | Mac/EC2 files and database tables, partly mounted. Resolve current source per dataset; expose read-only content adapters, not unrestricted service or database access. |
| Career pipeline | Guild pipeline database plus legacy local opportunities file. Identify the authoritative dataset and make legacy coverage/freshness explicit. |
| Career source material | Local resume, letter and supporting-document collections. Register specific owned content sources and provide indexed/readable references; no unrestricted directory crawl. |
| Curator | Local files and EC2 research/service data. Verify tenant ownership and a machine-authenticated read path. |
| German and Portuguese | Service/database data and local files; German local copy reportedly stale. Preserve per-user scope. Legacy rows need a verified ownership mapping before inclusion, not a direct-file bypass around access control. |
| Planning Studio / Prototype Lab | Local project and design files. Register content sources with ownership and source-version metadata. |
| IoT Connect and future domains | Prototype/branch-dependent sources. Verify deployment and ownership before claiming integration. |

The portal’s browser login is not a machine identity service. Remote adapters need authenticated service access and server-side owner mapping; a caller-supplied identity header is insufficient. Local adapters may use constrained reads of registered content. Never index authentication databases, environment files, token-bearing configuration or secret stores as knowledge. Source registration defines a technical content boundary, not discretionary exclusion of ordinary Robert-visible work.

Career acceptance covers **all three**: career discussion in Records, the career pipeline, and original career materials. Robert has authorized that visibility, including private material. Test source ownership and actual retrieval separately for each. Report inaccessible/unavailable sources and stale caches explicitly. Seeing a room about Career does not establish coverage of the pipeline or the resumes themselves.

## 5. Daily interface

Use the familiar navigation / conversation / optional context layout. Keep room/session navigation, a dominant transcript and a compact composer. The initial page should answer: what session is this, why are we here, is recording on, who owes an action, and can I stop participation or return later?

    MiniMoi · Collaboration Rooms               Search       Inbox 2 · Robert
    ┌────────────────┬────────────────────────────────────────┬───────────┐
    │ Inbox          │ Workshop / UI review      Participants │ Requests  │
    │  For you 2     │ Purpose: settle the first useful layout │ Artifacts │
    │  New           │ Recording · Pause   CoS replies: on     │ Notes     │
    │                ├────────────────────────────────────────┤           │
    │ Rooms          │ Robert · 8:12                           │           │
    │  Workshop      │ Keep the conversation easy to follow.   │           │
    │   UI review    │                                         │           │
    │   Build        │ Claude Code · Agent · 8:13               │           │
    │  German        │ Here is the measured layout.            │           │
    │                │ Details                                 │           │
    │ Closed sessions│ ── New on this browser ──               │           │
    │                ├────────────────────────────────────────┤           │
    │                │ 1 question for you · Answer             │           │
    │                │ Message…                                │           │
    │                │ +   Message ▾   @                 Send  │           │
    └────────────────┴────────────────────────────────────────┴───────────┘

**One Inbox:** For you contains actions assigned to the viewer and submitted results awaiting that viewer's requester acknowledgment. New contains unread material. Keep the counts distinct. Activity is a catch-up/history view, not a fourth competing attention queue.

**Header:** one compact session header with purpose, recording, participants and visible stop/pause controls. A record count or technical sequence need not consume everyday header space. Full purpose is available by click/focus as well as hover. On phones, use two lines. A Recording chip may double as Pause if its action is explicit and its target is large enough. CoS reply state and pause must not disappear into an unreadable avatar stack.

**Participant evidence:** show membership and verified credential/access readiness separately. An authenticated check-in receipt may establish contact even before launcher work ships; a membership row does not. “Working” requires a current observed run, and old contact is never online presence.

**Context:** Requests / Artifacts / Notes open a single drawer, closed by default on laptops. Pinning is optional where the conversation retains space. Longer artifacts can use a full-width reader with a return path. Do not show tabs for unimplemented work-item or agent-plan features.

**Composer:** Message by default; Enter sends, Shift+Enter adds a line, IME composition cannot accidentally send. Keep a visible Send control. Type / addressing / attachment controls fit a compact row. A distinct Request review / Handoff / Ask a person action creates a structured request; ordinary Assignment text does not. Robert is a current selectable recipient, not a hard-coded destination. Mention completion uses actual principals, but mention text grants no access and starts no run.

Drafts are isolated by authenticated principal and session and cleared at sign-out. Current drafts survive navigation only within the loaded page and are lost on reload. Retain that explicit baseline unless durable draft storage is intentionally implemented and tested. Preserve uncertain/unsent text and reconcile an existing operation rather than blindly resending. Switching rooms never silently redirects a draft.

**Message presentation:** authenticated contributor and time stay visible. Imported text shows its declared speaker as unverified and its real submitter. Agent styling is quiet and never impersonates Robert. Details holds routine record IDs, origin and receipts; it cannot hide an unverified-attribution warning. Preserve safe rendering and actual destination hosts on links. Inline reply links to a record; a new subject gets a new session, not a second official sidebar transcript.

**These are acceptance targets for the new layout, not measurements or claims about the currently deployed beta.**

**Initial layout targets:** default drawer closed, compact composer, normal notices, 100% zoom: at 1280×720, transcript at least 432 CSS px high and conversation center at least 60% of page width; at 900×500, aim for at least 250 px transcript. Measure rather than infer from this sketch. Zoom, expanded writing, keyboard and essential notices take precedence over these percentages. Test 360×640, 390×844, 1024×600, 1280×720 and 1512×1040, plus narrow reflow and text zoom. Phone composer must remain reachable above the software keyboard. This is an acceptance target, not a current capability: `100dvh` alone is not evidence. Evaluate viewport/keyboard handling and verify it on a real device or suitable emulator, including focus, send, multiline entry and keyboard dismissal.

## 6. Requests and session lifecycle

Use one request component in the Inbox, context drawer and notification route. Show question/brief, source reference, latest state, next actor and the available action. Answer must be available there; Join session is optional navigation.

| State/action | Authorized actor |
|---|---|
| Request | Eligible participant, assigning a different eligible participant |
| Pickup | Named assignee |
| Submit result | Named assignee after pickup |
| Answer person-input | The named human assignee, directly from requested or picked up; no blanket admin right to answer as that person |
| Acknowledge | Original requester, distinct from assignee |
| Cancel | Requester or explicitly authorized session/workspace manager, recorded as the actual cancelling actor |

Reading a result does not automatically acknowledge it. Acknowledgment records receipt, not code/deployment approval. The current API’s owner-input type is a compatibility baseline; team requests store a named human recipient and explicit authority requirements. An answer is not an approval unless the item explicitly requests that effect and verifies the assignee’s current approval authority. Do not offer CoS as an assignee until a real handler for that request type exists; standing knowledge access alone is not such a handler.

After a stale-version error, refresh the item while retaining unsent text. Cancelling an answer editor returns to its Inbox position. Derive next actor from authoritative request state, not handoff-file timestamps.

**Recording:** new discussion and requests allowed under ordinary permissions.
**Paused:** new discussion and requests stop. The session owner or its explicitly appointed moderator may resume, subject to current contributor access and operation checks. Existing requests may complete. The moderator is an existing stored role; today only Robert appoints a contributing member. In the team design, a session owner or explicitly authorized manager appoints one within that session. It is never inferred from assistant identity or knowledge access. Default to the session owner (Robert today), so no extra assignment is required.
**Closed:** discussion cannot reopen; new subject/work context uses a new session. Existing obligations remain visible.

Recommended default: existing requests may still be picked up, answered, submitted, acknowledged or cancelled after pause/close. No new request, assignee expansion or launch authority follows from that. Claude Chat preferred no post-close pickup; that remains a reviewer disagreement, resolved provisionally here in favor of avoiding stranded unclaimed reviews. If changed, define cancellation/continuation handling for every unclaimed item. Post-close pickup does not wake or launch an agent: an authorized client must check in, whether already running or independently opened by its user. Each person may answer their own assigned input items; Robert or a workspace administrator cannot impersonate another assignee. If nobody checks in, the request remains visibly pending or is explicitly cancelled/continued elsewhere. Proposed initial stale threshold: 24 hours since request creation without pickup. Show a deterministic “Awaiting pickup · stale” indicator to the named assignee and a stalled-request indicator to its requester; retain the actual next actor. Refreshing the page does not reset its age, acknowledge it, reassign it or launch a client. Make the threshold configurable; this is an implementation default, not a settled owner policy.

Every later transition still checks current credential, operation scope, actor, access and request version. CoS read privilege alone cannot perform a write transition. Preserve existing records and create append-only operational activity after pause/close without adding those transitions to the frozen discussion. Use `coordination_steps` for those transitions; keep the active-session requirement on new requests and snapshots, while replacing it on existing-request transitions with the full current authorization/version checks. Record pause/close boundaries by sequence and timestamp; derive historical boundaries from existing state-change records without rewriting them. Activity during a pause remains distinguishable after resume. Existing explicit post-close note support remains separate from new discussion.

Keep `minimoi.transcript/1.0` valid: its strict schema rejects unknown properties and labels a closed discussion `final`. Introduce a separately versioned export bundle containing the frozen typed discussion and operational activity with an export/as-of timestamp, or an equivalent explicit sidecar. Do not silently add fields or post-close transitions to the 1.0 transcript. Markdown export presents Discussion and Activity after close separately. Later bundles may contain more activity while the frozen discussion stays identical; previously issued exports and receipts remain unchanged. A typed-schema change, if ultimately chosen, requires an explicit new version and consumer tests.

Close explains outstanding requests and offers Close session, Cancel open requests and close, or Keep recording. Bulk cancellation rechecks current versions and permissions per item, records each result/receipt, and reports partial or racing outcomes explicitly; do not claim all requests were cancelled if any failed. Closing itself uses a defined transaction boundary and never silently discards outstanding obligations. Closing does not silently launch or cancel unrelated external work.

Pause recording stops new automatic reply dispatch. Pause CoS replies leaves Robert's recording available. In-flight external completion may need reconciliation; never claim remote cancellation merely from a local toggle or publish a late reply into frozen discussion.

## 7. CoS invitations and bounded participation

Two deliverables are named distinctly:

1. **Access ready / client check-in:** CoS prepares an access proposal, MiniMoi validates owner authorization and provisions required membership/grants, and the supported client can read its inbox when asked. Robert may initially tell the client “check in on this session.” This is an interim milestone.
2. **CoS convenes:** an authorized adapter reaches a supported client, or starts a bounded new run when needed, and obtains authenticated participation evidence. This is the delivery/participation part of UC1 and a release priority. Full conversational UC1 also requires the verified natural-language route described below.

An invitation names the actual session, collaborator/client, purpose and context scope, operations, duration, limits and billing policy; launch includes host/workspace/effective permission profile. MiniMoi binds approval to the concrete immutable request, authorizing human, workspace and mandate. The human must have authority over that action and affected resources. CoS can propose and inspect status; it cannot approve itself. A room moderator’s coordination role alone does not authorize spending or deploying. Existing exact valid approval is reused; ambiguous or changed scope is resolved explicitly. Imported prose is never authorization.

The first convene route is a structured, model-free control (button or parsed command) handled by MiniMoi: resolve session/client, validate the exact owner instruction or existing delegated approval, then deliver or queue the bounded run. Label it as a structured CoS convene control, not evidence that a model interpreted the request. Natural-language “CoS, bring Claude in” requires the separately verified reasoning path in §4.3; do not declare full conversational UC1 complete from a button test alone.

Recommend Claude Code CLI as the first launch adapter to verify, followed by Codex. Validate actual client authentication/tool capabilities. A new CLI process does not attach to an existing desktop/browser conversation. The only demonstrated already-running-client route is check-in on request or on a client-owned polling schedule. No push route has been verified. A client-specific push adapter may be added once demonstrated, but a filed request alone cannot wake an arbitrary native chat. Do not assume every client needs a new process.

Keep access, delivery, authenticated join and observed run state separately in data. Show a concise derived label such as “Access ready, not joined,” “Joined 8:13,” “Working · updated 2 min ago,” “Waiting for the Mac,” or “Client not signed in.” An access grant or successful spawn is not proof of participation; old activity is not proof of current presence.

Use durable operation/job IDs, exclusive claims and reconciliation of uncertain starts. Check approval, credential, session lifecycle and limits at dequeue/start and protected operations. No launch into a closed session. Fresh launched runs use bounded run credentials rather than inheriting an existing client's unrelated grants. **Gate L — launched collaborator containment:** before launching against real material, verify and record a profile limited to the assigned session’s read, contribution and necessary receipt/status operations. No general shell, arbitrary file read/write, other room access, arbitrary network tools, subagent escape or inherited user/project hooks, plugins, memory or connectors. Allow only the necessary provider authentication/model transport and constrained Records broker transport; “no other network” does not mean no model connection. Runtime bookkeeping in a dedicated directory is separate from permission for model-directed filesystem effects.

The simplest initial profile may be a tool-less CLI completion over a broker-supplied authorized snapshot, with validated output posted by the broker under the collaborator identity and approved run scope. If a Records MCP facade is used instead, it exposes only the required scoped operations. Its credential remains outside model text. Local CLI help supports built-in-tool selection and strict MCP configuration, but available flags are not proof of containment. `--allowedTools` is a permission preapproval list, not a complete tool inventory restriction; use explicit tool/config isolation and verify the effective profile. Runtime/OS controls must support the claimed boundary. A hostile room fixture must fail to induce shell, file, arbitrary-network, unrelated-room or scope-expanding effects. Record executable/version, effective config, allowed transports/tools and results. If L cannot be demonstrated, do not launch on real material; continue the access/check-in milestone.

Keep launcher pairing separate from room and model-gateway credentials; use fixed reviewed adapters without general CoS shell/Docker access.

Default to one launcher-managed local run per host, queueing the rest visibly. Require an explicit host admission check at dequeue/start: current memory pressure, swap trend, disk headroom and observed active workload, including independently started clients. Configure and test thresholds for this host; stale/unknown health prevents a new launch with a visible reason. An OpenClaw configured concurrency maximum is not evidence of that many active runs. Recheck host health while supervising, preserving uncertain-run reconciliation. The launcher cannot enforce a global cap on programs it does not own. Reconcile a lost worker before freeing its slot or starting a duplicate. Queue waiting requires no model calls. Initial live launch is a bounded room review/contribution; broader file-writing build profiles need their separately authorized scope.

For CoS's own discussion, enable a bounded reply mode once, then eligible ordinary Robert messages trigger attempts without another Ask button. Bursts may be coalesced. Initial default remains replies triggered by the represented owner (Robert today), with explicit duration/attempt limits and visible stop/expiry. Standing read access neither expires with that mode nor automatically enables replies everywhere. Existing target/@ behavior must be checked before promising addressed-message filtering. A team can explicitly configure other authorized human triggers per session; ordinary membership or an @mention does not silently expand effect authority. Agent-to-agent cascades require a separate bounded turn policy.

Track billing route as subscription, API or unknown; track reported/estimated/unavailable usage distinctly. Before a paid or subscription-backed launch, verify the selected authentication route and sanitize inherited environment/config so an unintended API key cannot silently select another billing path. If the route cannot be established, keep the run pending with that reason. No automatic paid fallback, plan upgrade or credit purchase. Changing model or plan preserves the participant identity. Do not invent cost precision or call missing data zero. Pure storage, retrieval, reconciliation, queue supervision and notification delivery require no model call; reasoning and natural-language planning may.

## 8. Work outside Rooms: export, checkpoint and filing

Provide Export for review for an authorized session or selected range, with stable record references, coverage/omissions and downloadable text suitable for another interface. No automatic ingestion of a vendor's unavailable history is implied.

Offer this **optional default template**, while accepting full transcripts, plain handoffs, documents and partial excerpts:

    Checkpoint — <topic> · <date>
    Reported decisions: ...
    Rejected options and reasons: ...
    Open questions: ...
    Tags: <optional domain/topic labels>
    Status: imported context; filing does not confirm decisions or authorize actions.

    Dialog fragment
    Source / captured time / participants / coverage / omissions: ...
    Fidelity: Robert quotations supplied as verbatim; chair turns condensed where marked.
    Robert: <available quotation only>
    Chair [condensed]: <summary>

Preserve the supplied original as the source. For an attachment preserve original bytes/hash; for pasted text define fidelity against text received from the channel. Source-declared quotation accuracy is not independently verified simply by a label. Never invent missing Robert turns. Explicit decision confirmation, when needed, is a separate authenticated action linked to the imported report.

Slice A3 supports **deterministic filing only** through the authenticated portal form. A future verified Telegram route uses an explicit `/file <session>` command plus a document attachment, intercepted before ordinary CoS chat processing. Neither the payload nor a generated preview/summary goes to a model, chat memory or model-visible queue. Do not join several free-text messages by guesswork. Hash the received attachment bytes and bind the one complete payload; reject unsupported/oversize content with a clear file-based alternative.

The desired natural-language interaction, “CoS, file this in Presentation · round 2,” is part of Milestone D behind R whenever interpretation carries real material into a model. A phrase handled entirely by deterministic parsing is a different path and must be proved as such. Current ordinary Telegram text routes to CoS chat in the checked source; it is not evidence that the new `/file` interception or attachment handling exists.

CoS’s filing adapter passes content through unchanged. It adds no rewriting or opinion. Its own analysis is a separate contribution only when requested or otherwise authorized.

The filing operation binds verified owner instruction, exact source/payload, explicit session and allowed operation. Implement a server-issued instruction record created only through an authenticated authorized human channel or a specifically delegated instruction issuer. Bind the workspace and actual authorizing principal, and check that this principal can disclose the source and write to the destination. In Robert’s initial deployment he is that person; a future teammate’s valid instruction never derives authority from Robert’s identity. Bind issuer, allowed filing principal, destination, payload hash, operation, expiry and idempotency/replay scope. CoS cannot issue its own owner authority. A successful filing consumes or binds the instruction to that operation; uncertain retries retrieve the saved result, while a different operation cannot reuse an exhausted instruction. The actual submitter comes from authentication, never from a client-provided `filed_by` string.

Within its stated workspace and the issuer’s authority, the bound instruction is the write authorization for this exact filing even without standing contributor membership. Implement that as a dedicated delegated-filing operation with valid service-installation authentication, issuer authority at execution, active destination, payload/audience binding, expiry, revocation and replay checks. Do not bypass ordinary `post` or `import` membership checks, use an owner token on CoS’s behalf, or expose a general write route. If membership/audience changes before commit, invalidate the instruction when the authorized disclosure scope no longer matches. Own-authored CoS replies still require ordinary contribution rights. This new capability is not present in the baseline API. An agent-provided link or “Robert asked” string alone cannot prove instruction authority. For Telegram, verify the owner/channel mapping and attachment binding at the service boundary. Do not expose credentials in prompts or records.

Extend the strict import API through an explicit version/capability negotiation before clients send new fields. Required additions: original-source reference and content hash, declared fidelity, optional tags, opaque-source outcome, and instruction binding for delegated filing. Existing payloads remain supported. Today the contract permits at most 100 turns, each 16,000 characters, plus a 16,000-character handoff; documents are limited to 2,000,000 bytes and the request limit is 3,000,000 bytes. Reject oversize input clearly or use an explicitly supported exact-source reference; never silently truncate a transcript or claim unsupported larger uploads.

Recommended representation: one atomic import operation with original-source reference, parsed turns when available, and a linked checkpoint, returning all created IDs and one operation receipt. Present checkpoint/dialog as useful linked views. Do not promise exactly two database records. Current import stores N turns plus a checkpoint event; a literal Notes-table representation would be a deliberate schema/contract extension.

If structure cannot be parsed, preserve an opaque source and report that no structured dialog was extracted. Partial writes must not appear as a successful complete filing. Same actor/destination/request ID/exact payload returns the saved result; changed payload under that ID fails. Identical content under a new request ID may be intentional and is not silently discarded. Persist the request before delivery and reconcile uncertainty.

Show “Submitted by CoS · filed at Robert's request,” backed by authenticated instruction evidence; imported speaker names remain unverified. Return the actual receipt, counts and audience at the moment of filing through the originating supported channel. A receipt is evidence of that commit, not a guarantee that the audience can never change. Filing itself requires no inference; natural-language interpretation/clarification may, with no promise of exactly one model turn. Offer a structured zero-inference filing path.

New transcript import uses an active session. A later note/reference route may support post-close additions, but must be explicit; never reopen or redirect silently. “Offline work” here means work outside Rooms, not guaranteed browser operation without a server.

**Mis-filing (separate recovery increment, not promised in A3):** add a withdraw-from-view operation for the content owner or an explicitly authorized resource manager that suppresses affected content for recipients outside the retained owner/audit scope across reads, search, briefings, exports, downloads and future model context while preserving protected source/audit evidence. Propagate withdrawal to known derived copies/references or mark unresolved copies; a simple hidden UI card is insufficient. Already delivered exports, provider context or client copies cannot be recalled. Exposed credentials require issuer-side revocation/rotation; that is a separate authorized operation, never a promise that withdrawal erases exposure. Implementation and tests of withdrawal are required before presenting recovery as available; until then, require a destination/audience preview before filing and state that withdrawal is not yet available. A3 may ship with that explicit limitation; its acceptance does not claim recovery is built.

## 9. Search, catch-up, briefings and artifacts

Expose existing permission-scoped search from Rooms. Today the Inbox caps results at 100; search caps notes at 40, events at 60, documents at 40 and artifact links at 40. Disclose truncation until server pagination exists. Today search opens a session, not the precise record. Those are baseline limitations, not the target flow. Add server-side selected-session scope and completeness/pagination handling before claiming a full local result set; browser filtering of already-capped global results is insufficient. CoS's search obeys its standing knowledge role, while other viewers retain their own narrower scope.

Typed result destinations include session ID, object type and object ID. Events, notes, documents, artifact references and request steps open the corresponding viewer. Add request-history search explicitly; it is not already in the current search. Rich author/kind/date filters and quick switching can follow. Distinguish authenticated author from imported speaker.

Catch-up defaults to a deterministic view of new material, decisions, requests and artifacts. Optional generated summaries show source coverage and usage. Read cursors are currently browser-local; use “New on this browser” until synced cursors are implemented. Preserve scroll when new material arrives and offer a deliberate N-new jump.

A status briefing copies explicitly selected authorized material into a separate session with source range, capture time and provenance. Source discussion remains independent. The reader and exports provide destination-local citation IDs. Original session IDs, sequence numbers, actor metadata and direct source references are included only if explicitly covered by disclosure or the viewer’s source access. The checked baseline snapshot serializer returns the stored source metadata; selective serialization/redaction is required, not merely hiding a link in the browser. Refresh creates a new version. A destination reader can read the disclosed copy without having source membership; opening the original rechecks source authorization and does not leak protected metadata. CoS's broader source access is not inherited by the briefing audience.

Artifacts show exact versions/references; do not infer a version family from a repeated filename. In the current beta, a repeated document upload creates a new document, exports are generated responses, and uploads are limited to 2 MB. Use an existing supported exact-version reference for larger decks or explicitly implement a reviewed larger-upload capability. File the final deck before closing unless a distinct supported post-close filing route is chosen.

## 10. Notifications and availability

One Inbox is authoritative for owed actions. In-app badges/toasts deduplicate by item/version and do not repeat on every poll. A browser fetch does not count as pickup or acknowledgment. The close/cancel/error flows return users to the relevant item.

Optional follow-on: deterministic Telegram notices to Robert for actionable items. Use authorized destination binding, minimal content by default, durable delivery attempts and reconciliation/deduplication. No model call is needed for transport. Report queued, delivered/confirmed where supported, failed or uncertain accurately. Host awake, network and service availability still matter; do not promise laptop-off delivery until a suitable hosting path exists.

Show a stale/error state and last successful refresh when connection fails. Local server hosting is not proof of offline browser support. Unavailable model/client/host states do not fabricate activity or silently spend through another route.

### 10.1 Initial host and transport choice

Before R, **no CoS model runtime holds standing read**. In slice A2, a proposed separate deterministic Records service on the development Mac holds its own installation credential and accesses local Records on loopback port 18880. This is planned isolation work, not proof that same-user processes today cannot read one another’s files. Prove isolation from the CoS runtime before provisioning the standing scope.

The existing Records model worker explicitly targets `cos-agent-a` at `http://127.0.0.1:18790/v1`; that is distinct from the personal OpenClaw instance. Root source routes ordinary CoS Telegram text through `_chat` and backend selection. Deployment manifests include additional container paths, but this review did not verify which live runtime currently answers Robert’s Telegram messages. No remote Telegram-to-Mac filing/convene route is claimed. Enable one only after verifying the actual bot/runtime, owner mapping and an authenticated service path; never expose raw loopback Records or a standing key through the browser bridge.

The intended phone path is authenticated HTTPS `https://dev.minimoi.ai/app/records/` through the existing dev portal bridge to Mac Records. It requires the Mac, tunnel, portal and Records service to be available. This turn did not perform a phone or public-site availability test. The phone does not use its own `127.0.0.1` to reach the Mac.

If the host cannot be reached before dispatch, report “Mac unavailable — not sent” and retain the draft. If an attempted write/launch loses its response, report “Outcome unknown — checking receipt”; timeout does not prove failure. Reconcile with the original operation ID before retry. No deferred success is reported without a durable acceptance/commit receipt. The initial route does not promise a persistent cloud queue or laptop-off response; a Telegram notice itself may be impossible if its bot also sleeps. A verified always-on ingress is separate work.

## 11. Visual system and accessibility

Use sage/paper tones, restrained agent tint, warm action emphasis, and a readable UI font. Georgia may remain for selected headings. Suggested body size 15–16 px, secondary text 12–13 px. Avoid shrinking text to solve density. Hero art belongs on the domain card, not above an active conversation; whiteboard imagery is the preferred direction.

| Surface | Color | Contrast with ink #24362f |
|---|---|---|
| Page | #e6eadd | 10.45:1 |
| Navigation | #dde4d4 | 9.81:1 |
| Transcript | #f4f1e6 | 11.30:1 |
| Composer | #fffdf8 | 12.57:1 |
| Context drawer | #eaeee1 | 10.84:1 |
| Agent tint | #e4ebdf | 10.49:1 |
| Action tint | #f4e6d8 | 10.44:1 |

Muted #59655d is 5.39:1 on transcript and 4.68:1 on navigation. Accent #2f5442 is 7.53:1 on transcript. Decorative divider #cfd6c6 is only 1.32:1 on transcript; use #778371 or another tested contrasting indicator for meaningful control edges. That candidate is 3.52:1 on transcript and 3.06:1 on navigation. Use ink or #8a4f24 for unverified text on #f7efe6 (5.72:1 for the latter), with dashed outline only as an additional cue. These are opaque-color calculations, not certification of rendered pages.

Provide keyboard/touch alternatives for hover actions, named tabs/buttons, visible focus, sensible Escape/return-focus behavior, accessible status announcements, touch targets and reflow. Use a polite new-material announcement such as “3 new messages”; avoid rereading the entire transcript on every poll. A transcript log region must preserve focus and reading position. Test real focus/selected/error states, long names/code/URLs, zoom, screen-reader labels and mobile keyboard. Essential distinction must not rely on color alone. Optional themes and decorative polish follow functional acceptance.

## 12. Delivery milestones and baseline

Verified source baseline for this draft: records-live-cos HEAD 6410d44, corresponding to the previously deployed records-beta-inbox-06. Historical independent evidence reports 288 backend and 29 browser passes; these tests were not rerun to write this document. Claude Code’s v0.2 review confirms that source and deployment match, cites his independent 288-backend/29-browser review runs, and confirms all 14 contrast calculations. No new application test run was performed for this documentation reconciliation. Recheck baseline divergence when implementation actually starts.

| Capability | Current baseline | Required work |
|---|---|---|
| External client read/post/import/receipt | Shared local roomctl/HTTP path exists; receipt takes session ID and operation ID | Better setup/check-in; verified interface support |
| Inbox | Requests where caller is next actor | Addressed records/read cursors if offered; unified UI |
| Access | Owner membership UI and installation credential lifecycle exist | CoS standing read policy; safer convenient ordinary access setup; domain coverage |
| CoS replies | Synthetic gates plus unenforced adapter tool policy; bounded attempts/time | Tool-less or enforced-runtime path, then coordinated gate/prompt/provenance changes and real-material tests |
| CoS invitation/filing | No complete proposed connector path established | Proposal/effect adapter, deterministic filing, supported client delivery/run |
| Request completion | Active recording required; transitions write events | Post-close operational history and export contract |
| Search | Capped categories, session-level navigation | Scoped completeness, typed deep links, request-step search |
| Briefings/artifacts | Frozen copies, documents and version references | Source navigation, clear versions and artifact-size workflow |

### 12.1 Named initial release slices

> **v0.7:** cite these slices as S158-A0…S158-A3; the §21 build packages A0–A5 and U are different work. The ownership and first-release statements below are superseded as delivery planning by §21.1 and §21.15; slice content is unchanged.

The selected **first release is A0 + A1**, targeting this week with Robert and the current agents. It is accepted only when both named slices pass their applicable checks. Do not call A2/A3 delivered from an inventory, and do not claim the complete right-hand CoS experience from this release. Dates are targets, not evidence that the work fits a week: if A0 is larger than expected, report the release impact rather than substituting a Rooms-only login or weakening authorization.

| Slice | Included and exit evidence | Ownership/dependency |
|---|---|---|
| A0 — MiniMoi permissions into Rooms | Reuse Robert’s existing MiniMoi login. Build/extend account-linked domain permission decisions, then enforce them server-side in Rooms and its API/deep-link paths. Allow, synthetic deny, revoke-with-login-still-active, sign-out, outage and no-stale-allow tests. No second human or new tenant rollout. | Codex owns platform permission design/implementation and Rooms wiring; independent implementer reviews the actual diff before shipping. Platform contract/enforcement precedes Rooms integration. |
| A1 — Daily Rooms surface | One compact header/composer, one Inbox with direct Answer and receipt-backed next actor, drawer behavior, ordinary client setup/check-in, and read/post/receipt in two permitted sessions with a denied sibling. Verify real desktop flow and measured layout. Expose current limits truthfully. | Codex owns; ships on A0 authorization. Preserve existing records and reviewed behavior. |
| A2 — Deterministic standing read | Records-only first. Centralize duplicated visibility predicates before enabling the role; isolate credential/results from the model; existing/new/closed-session read tests, role/credential revocation, pre-R negative tests. Publish a verified coverage register. | Next bounded increment after the A0 contract is stable. Design/source inventory may start; build/release needs its own frozen diff and evidence. Not promised in A0+A1. |
| A3 — Bound portal filing | Portal-only exact-source import and one-operation authorized filing, including source-disclosure/audience binding, receipt, replay and size-limit handling. Preview destination/audience; disclose unavailable withdrawal. No model sees the payload. | Follow-on increment after platform instruction authority and import contract exist. Design may start; ship separately when its tests pass. Standing-read access is needed only if this path actually retrieves source content, not for a supplied attachment alone. |

No additional person, multi-workspace migration, Telegram filing, laptop-off delivery or mobile keyboard certification is part of A0+A1. Responsive checks may be performed without claiming a verified mobile end-to-end flow. Those promises enter a release only when its manifest explicitly includes and verifies them. A1 search may say “open matching session”; exact-record navigation is Milestone B. A3 filing may say “saved with receipt”; it does not by itself complete UC5’s later retrieval journey.

All slices preserve stable principals, scoped grants, source attribution and separate effect authority. An account permission is not a session grant; a session grant is not a model launch. Existing MiniMoi navigation source already contains tier/domain policy, so A0 must inventory/reuse what is sound and close the grant/enforcement gaps, not invent a second identity system or assume no prior permissions exist. No new Claude Code review task is dispatched merely by naming the independent-review role here.

### 12.2 Remaining product milestones

**Milestone B — complete retrieve/finish flow:** exact-record search/navigation, post-close completion/history export and source-linked briefing reader. The complete direct-client retrieve/finish flow requires the applicable A0–A3 slices plus B; the full CoS working-day test requires C and D as well. Do not claim later milestones in A’s acceptance.

**Milestone C — structured CoS convening:** only after gate L passes, one verified supported client reached/launched through a deterministic control with approved scope, authenticated participation, bounded execution, measured host admission and reconciliation. Schedule this alongside A/B, before decorative polish. The applicable A0–A3 slices plus B support the complete direct-client working-day flow; C adds supported deterministic invitation delivery. Neither implies a push adapter for every native chat.

**Milestone D — right-hand CoS in Rooms:** implement and verify the tool-less or runtime-enforced inference path, then real-material replies, natural-language filing and natural-language convening. Include the audience-filtered retrieval and isolated conversation-state tests; Robert’s separate direct CoS-domain chat remains outside this spec. Expand standing visibility across the verified domain adapters until §4 is satisfied. This is required for the full product outcome, not an optional future enhancement. A and C need not wait for the CoS runtime gate R; C still requires the separate launched-client gate L. Their release labels must not claim D is complete. Plan C and D as named workstreams with actual evidence, not indefinite deferrals. Neither C nor D may process real Career material until its respective L/R and audience gates pass; ordinary native-client check-in does not require a launcher gate.

Dependency rules: policy refactor before standing role; server import capability before upgraded client payloads; instruction binding before delegated filing; verified runtime before model calls over real material. Relative effort from Claude Code: standing role and UI/search/lifecycle/import are medium-sized work; first bounded launcher and real-material model path are larger, with domain adapters separate medium-to-large integrations. These are planning estimates, not delivery-time promises.

Track B remains Claude’s separate model-role configuration work. Deterministic retrieval, filing and convening do not inherently depend on it. Coordinate runtime/model routing if D uses its gateway. A local process needs the Docker-only gateway fixed only if its selected backend actually uses that gateway; a native subscription CLI is not automatically subject to that dependency. Verify the deployed CoS runtime itself: the reviewed personal OpenClaw installation is separate, and the CoS runtime’s effective tools/version were not established.

Release labels must also distinguish granted knowledge access from verified end-to-end knowledge coverage. Known missing sources remain visible on the delivery checklist. Backup/commit/push of the current local branch and approved Spec 157 is a separate durability follow-up; use a destination authorized for private project material, not an assumed public push.

Optional later work: richer filters, synced unread, team presets, alternative themes, additional client adapters and laptop-off notification hosting. These are not implied by the initial beta.

## 13. End-to-end acceptance

**Working-day scenario:** open a real authorized initiative session; use CoS to arrange collaborator access; check in supported clients; exchange actual review/result/acknowledgment; work on the argument in a native chat; file its checkpoint/dialog unchanged; answer an owner question from Inbox; hold a separate status session with selected source material; file the final artifact; close discussion; finish an existing outstanding request; find the original reasoning the next day. Use a synthetic fixture for preliminary connector testing, then a real session when the implemented data path is validated. The product target includes real work.

The cases below are a **product-wide acceptance catalog**, not one undifferentiated first-release test suite. Each frozen build packet must list its included slices and applicable cases; unimplemented cases remain pending, not silently passed or waived. First-release A0+A1 uses the platform checks in §19.4, native-client/Inbox/authority checks, implemented cost/availability states, and the desktop subset of case 10. A2 adds standing-read and pre-R cases; A3 adds filing and the narrow-filing part of case 15. B adds exact retrieval, post-close completion and briefing metadata checks. C requires L; D requires R and audience isolation. Withdrawal, phone/Telegram and later human rollout have their own explicitly included cases.

Required cases for the relevant slice, with durable evidence:

1. **CoS visibility:** under its own valid identity, the CoS knowledge service can discover/read/search existing and newly created Robert-visible sessions without ordinary membership grants, including paused/closed and Career examples. Repeat across inventoried domain adapters, explicitly testing Career room content, pipeline and source documents. Test role removal, credential expiry and revocation, ordinary member removal while the separate standing role remains, and every read/list/count/search/artifact route. Use a non-legacy CoS installation credential; no HTTP path may bypass authentication. A normal collaborator cannot do so. Revoking CoS's credential denies further requests; no shared owner token is used.
2. **Authority separation:** CoS standing read cannot approve an effect, grant another client access, publish into an unauthorized destination or acknowledge another requester's result. Separately authorized operations succeed. Destination audiences do not inherit CoS's wider source access. A caller with source read and destination write but no source-disclosure authority must be refused. After a correctly scoped disclosure grant, verify commit-time recheck, revocation and metadata filtering.
3. **Native contribution:** ordinary invited client reads/posts under its own scoped installation in two allowed sessions and is denied a sibling session. Receipt retrieval works independently after an uncertain response. Token rotation preserves historical authorship.
4. **Filing:** preserve source bytes/text per the transport contract, safely display unverified speakers, link authenticated filing instruction, and reconcile same-request retries. Exercise malformed structure, missing fragment, changed-payload retry and deliberate same-content/new-request filing. Also test absent/forged/expired/wrong-actor/wrong-destination instruction IDs, exhausted-instruction replay, old/new client compatibility and oversize sources. Imported Decided text neither approves nor launches anything.
5. **Inbox:** Robert answers directly; assignee submits; original requester acknowledges. Next actor/counts update correctly; stale-version errors preserve drafts and refresh state; cancel returns to the list. No raw CoS envelope in catch-up cards.
6. **Lifecycle:** close with one unpicked request and one submitted result; permitted existing transitions still work and new requests/discussion/launches do not. Export separates post-close activity; strict 1.0 transcript consumers still validate, historical discussion stays unchanged, and the new bundle exposes its activity cutoff. Exercise partial bulk cancellation and races. Preserve all old records and receipts. Paused can resume under the chosen moderator policy; Closed cannot reopen.
7. **Convene:** actual CoS command yields an exact authorized invitation and a verified client contribution. Demonstrate the structured route first and the natural-language route separately when D is ready. Test unavailable/sign-in-needed client, scope change, queue expiry, duplicate delivery, host overload/stale health and uncertain startup. Second managed run waits; no silent API fallback or unrelated inherited access.
8. **Replies:** invite/enable once; eligible ordinary messages trigger bounded attempts without another Ask click. Coalescing and retries do not duplicate output. Pause, expiry, missing status and in-flight cancellation report truthful state. Read access remains independent from reply-mode expiry. For real-material calls, demonstrate that unavailable/disallowed tools and out-of-scope effects cannot execute at runtime, and test that the tool-less path has no callable tools. Prompt text or a reported policy flag alone cannot pass this test. Verify audience-scoped context and source tools; a broad CoS memory must not silently enter a narrow-audience reply.
9. **Status meeting:** destination reader sees an explicitly disclosed snapshot and usable citations without gaining the source session. Source work continues independently. Refresh creates a version, and export includes copied source content. Test destination-only viewers for protected source names, paths, participants, IDs and labels on both API and export responses; browser hiding alone cannot pass.
10. **Retrieval/UI:** typed links open the right object, result limits are disclosed, browser-local unread is labeled, narrow/zoom/keyboard/IME flows work, and layout targets are measured with realistic content. Do not report prototype sketches as browser-test evidence.
11. **Cost/availability:** model-free operations invoke no inference; missing usage is unknown rather than zero; unavailable host/client does not claim progress. If Telegram is included, verify recipient scope, delivery state and deduplication without a model call.

12. **Pre-R separation:** with real fixture material in Records, no CoS model tool/process can obtain the standing credential, retrieved content, model-visible cache or filing payload. Exercise portal filing and any enabled attachment route with a model spy asserting no invocation or downstream context ingestion. Deterministic service reads succeed under their own audited identity.
13. **Managed-run containment:** before C on real material, hostile room/import instructions cannot cause shell, arbitrary file/network access, unrelated-room reads, authority expansion or disclosure outside the approved contribution. Verify the actual effective profile, not only the flags in a command string. Necessary provider/broker transports are declared and measured.
14. **Audience isolation:** put a unique fact solely in a Career source that Codex cannot read. A Robert-and-Codex session supplies none of that fact/source through context, history, tools or caches; a genuinely Robert-private room may use it after R. Name the separate private CoS-domain chat and its memory store as forbidden room-context sources, including when they share a backend. Test audience changes during generation and new-member history exposure. Adversarial answer checks supplement the retrieval proof.
15. **Narrow filing and recovery:** an exact authorized filing succeeds without contributor membership; ordinary posting, altered source/destination/audience, expired/revoked instructions and forged owner instructions fail. For A3, verify destination/audience preview and the explicit no-withdrawal limitation. When the separate withdrawal increment is included, exercise it across every supported read/export/copy route, preserving audit evidence and documenting unrecoverable external copies.
16. **Host unavailable:** exercise no-dispatch offline, dispatched-but-uncertain, expired delayed instruction and unchanged-ID retry separately. If that release includes phone support, verify its actual HTTPS path and software keyboard; otherwise mark those tests pending and claim desktop support only. No success/absence-of-write claim follows merely from a timeout.

The following are **later platform-to-Rooms integration acceptance**, not requirements for this week’s Robert-and-agents delivery:

17. **Two-human team walkthrough:** Robert and a second human have separate credentials, private sources and shared sessions. Each sees their own assigned Inbox items; requester/assignee receipt rules hold. Robert’s CoS sees his private and shared work, not the teammate’s private work through admin privilege. A shared-room reply uses their common authorized context. Verify explicit delegation and revocation, and that a moderator cannot gain spending/deployment rights. Use synthetic second-user data for this test; do not provision a real person as part of a spec review.
18. **Workspace isolation/migration:** two workspaces with distinct participants and private sources do not leak via list/count/search/ID lookup/receipts/exports/jobs/caches. Explicitly authorized transfers are separate audited operations. Migration assigns existing records to Robert’s initial scope without rewriting authorship, receipts, sessions or request history. Existing credentials are mapped or renewed explicitly, never promoted into global scope. Repeat checks after membership removal and credential revocation.

## 14. Review status and adoption

Claude Code’s local review, Claude Chat’s supplied findings/discussion and Grok’s final comments have all been received and reconciled. Their source versions and evidence limits are preserved in §§16, 18 and 20. Robert has requested this contract be saved in docs and entered in the production build queue; it does not claim a reviewer verified the final v0.6 hash or that all implementation gates passed.

No reviewer remains assigned work merely because a handoff pointer once said “pending.” A new independent implementation review must name its actual frozen diff, hash, slice and tests. The build packet for the first release names A0+A1; later A2/A3/B/C/D packets name their own coverage. An architecture review cannot replace runtime enforcement evidence or an authenticated walkthrough.

This registration records the source version/hash and the official Spec 158 file hash. Preserve the frozen candidate and reviewer dispositions as evidence. Spec 157 is not overwritten. Implementation and production release need their own reviewed-diff and deployment evidence; registration does not announce new deployed capabilities.

## 15. Traceability and remaining design choices

This consolidates: Claude Code UI ideas and local build check; Grok's operating-model note; Claude Chat initial return and rev2; Codex v0.1 and rev2 addendum; Robert's full CoS visibility direction and latest review-order instruction. Earlier files are provenance, not required attachments to understand this spec. Historical source files remain in shared _working. The previously approved Spec 157 is at docs/specs/spec_157_collaborator_access_and_invitations_2026-09-20.md; Claude Code reports it is currently untracked. Its approval and its repository/backup durability are different facts. This candidate does not silently amend it.

Recommended choices still subject to review/adoption: post-close pickup allowed; first launch adapter Claude Code CLI; one managed run per host; bounded owner-triggered replies initially; atomic source-plus-checkpoint representation; moderator resume retained. Exact cross-domain adapter implementation, run limits, notification transport and larger-artifact handling must be named by the implementation plan. Full CoS knowledge visibility and separate decision authority are owner direction, not open choices.

Primary design references previously checked: [Zulip topics](https://zulip.com/help/introduction-to-topics), [Mattermost replies](https://docs.mattermost.com/end-user-guide/collaborate/reply-to-messages), [W3C non-text contrast](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), and [reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html). Borrow familiar interaction patterns without assuming vendor feature/license equivalence or introducing an open-source chat dependency.

## 16. Claude Code review disposition and handoff evidence

Reviewed v0.2 source SHA256: `d90581c80998a4a7e2db48c12af13240890403c8de7035b1fcf3a7db1e4afcb5`.
Local review: `_working/CLAUDE_CODE_REVIEW_COLLABORATION_ROOMS_SPEC_v0.2.md`, SHA256 `e5f43e011f31c006eee542587bec2a1d0d8995eacf9c86fdd46e508b2cfa761e`.
Room request: `eeab9bf3-1b74-459f-8568-84ef0bc4c535`. Claude submitted at 22:27 CT on 20 September; Codex acknowledged at 22:33 CT, receipt `db325e12-1f20-4948-9195-c82bc3e1baea`. Robert’s direct handoff and subsequent room message refer to this one review, not duplicate work.

“Incorporated” below means the specification now defines the required behavior. It does not mean code has been built or the corresponding acceptance test has passed.

| Finding | Disposition in this candidate |
|---|---|
| B1 real-material runtime | Incorporated §4.3, D and acceptance: tool-less or enforced path before real-material inference; all synthetic gates/prompt/provenance changed together. Full visibility remains settled. |
| C1 standing role | Incorporated §4.1: owner-managed role data, centralized predicates, current role plus credential checks, isolated effects and sanitized audit metadata. |
| C2 host/source | Incorporated §4.4: authoritative source per dataset, remote service authentication, explicit freshness and coverage; no single-host shortcut. Survey is not live verification. |
| C3 Career | Incorporated §4.4/13: room discussion, pipeline and original materials all covered. No new approval of already-authorized knowledge scope is requested. |
| C4 lifecycle/export | Incorporated §6: operational steps outside frozen discussion, recorded boundaries, separate versioned export bundle, old strict transcript intact. |
| C5 import/instruction | Incorporated §8: additive negotiated contract, server-issued bound instruction, authenticated submitter and replay control. Upgrade server before client payloads. |
| C6 CoS credential | Incorporated §4.1/13: non-legacy installation for integration/testing; remove surrogate-owner knowledge calls. No claim that legacy credential revocation is impossible. |
| C7 deterministic convene | Incorporated §7/12: model-free structured route early, separate natural-language completion; no false conversational-success claim. |
| C8 push vs pull | Incorporated §7: only check-in/client-owned polling demonstrated; per-client push remains unverified. |
| C9 resource/billing | Incorporated §7: fresh host admission plus observed workloads and per-run billing verification; configured concurrency is not current usage. |
| C10 limits | Incorporated §8/9: actual import/upload/Inbox/search caps, pagination and exact-object navigation work explicit. |
| C11 phone | Incorporated §5/13: device/emulator keyboard test required; viewport CSS alone is insufficient. |
| O1 truthful reply status | Required by §4.3/7: existing synthetic-only route and disabled/expired/unavailable states remain explicit until replaced. |
| O2 accessible announcements | Incorporated §11: polite new-message summary without replaying the transcript. |
| O3 briefing citations | Incorporated §9: record references visible in the reader. |
| O4 stale/cancel flow | Retained §6 and acceptance: preserve text and return to the Inbox item/list. |
| O5 local durability | Recorded §12: authorized private backup/commit/push follow-up. No public publishing or push performed by this document update. |
| O6 polling scale | Deferred optimization: measure request volume/latency and polling load before widening deployment; no beta-scale performance guarantee beyond observed evidence. |

Evidence limits carried forward: no verified EC2-side domain integration, CoS installation-token type, effective CoS runtime tools, launcher billing environment, current Claude Code sign-in, client push adapter or Telegram instruction mapping. Historical test counts and code observations are attributed to the local review; this document makes no new live-service verification claim. The supplied review is preserved locally; this self-contained candidate omits unnecessary secret-bearing configuration excerpts from that review when sharing externally.

## 17. Reconciled decision register

Only settled owner direction is labeled settled. Other entries are the recommended defaults for adoption; receipt of a review is not adoption of its recommendations. IDs are defined here for this canonical candidate, not inherited as if the separate draft were adopted.

| ID | Decision and disposition |
|---|---|
| D1 | Permit authorized existing-request completion after pause/close, without automatic launch; clients must check in. Recommended, preserving moderator and actor boundaries. |
| D2 | Authoritative source per dataset across Mac/services, with verified owner scope and freshness. Recommended; no blanket stale-cache substitution. |
| D3 | Full Career visibility covers room discussion, pipeline and source materials. Settled outcome; adapter sequencing is implementation planning. |
| D4 | Full CoS knowledge with separate effect authority remains settled. The supplied Robert/Claude discussion accepts keeping standing credentials/results outside the model runtime before R. Deterministic filing only before R; real-material reasoning remains required delivery work. |
| D5 | First managed CLI adapter Claude Code, with gate L before real-material runs; Codex follows. Recommended, subject to measured containment and billing evidence. |
| D6 | Preserve private project/spec durability through an authorized backup/commit destination. Separate task; no public push inferred. |
| D7 | Robert-visible excludes admin-only access to another user’s private data. Recommended explicit scope definition. |
| D8 | Bound owner instruction grants exactly one delegated filing, without general contributor rights. Recommended new endpoint/capability, not an existing bypass. |
| D9 | A uses a separate deterministic service on the dev Mac; no model runtime receives standing scope before R. Phone through authenticated dev HTTPS; remote bot bridge unverified and disabled until proved. Recommended topology. |
| D10 | Retain existing owner-appointed session moderator, default Robert. Define and test it rather than remove existing rights in a UI redesign. |
| D11 | Shared-room context is filtered for all entitled recipients, agents included, with audience-version recheck and no broader conversation memory. Recommended prerequisite for real-material room replies. |
| D12 | Direct Robert–CoS domain chat is outside this Rooms specification. Scope clarified in the supplied discussion. |
| D13 | One selected provider route per initial Rooms profile, no automatic fallback; explicit model/provider swaps remain supported. Recommended, not a fixed-vendor decision. |
| D14 | Build MiniMoi permissions tied to existing MiniMoi login identities, then enforce them in Rooms; Guild uses the same platform foundation when integrated. Required connection, not an optional bridge. This week configures Robert and agents only; additional-human/multi-workspace rollout is not a gate. |
| D15 | Recommended first release A0+A1; A2/A3 separate follow-on increments with explicit exit evidence. No promise that all of Milestone A ships in one week. |

## 18. Claude Chat review reconciliation

Inputs: `CLAUDE_CHAT_FINAL_CHECK_COLLABORATION_ROOMS_SPEC_v0_3_2026-09-21.md` and `HANDOFF_TO_CODEX_ROOMS_SPEC_v0_3_CLAUDE_CHAT_FINDINGS_2026-09-21.md`. The source file’s companion reference uses a different punctuation form; these underscore-named files are the actual supplied artifacts.

**Version correction:** frozen Codex v0.3 has 16 sections, milestones A–D and 14 color calculations, SHA256 `d055e37add93b6c2cd89f2ff89218a513acaf36335f36e53f3d1dc462c2ffe61`. The review’s §17, C-4, A2/A6 and 26-color references match the separate `COLLABORATION_ROOMS_SPEC_v0.3_CLAUDE_CODE_DRAFT.md`. The exact review input hash was not checked by Claude Chat. Findings are therefore reconciled by substance, not presented as a hash-verified review of the canonical file. Both source drafts/reviews are preserved.

| Finding | Disposition |
|---|---|
| B1 | Accepted as a missing isolation constraint, not proof every service fetch is a model call. §4.3 and case 12 keep standing scope/results outside the model before R. Current presence of a broad Records tool in the actual runtime remains unverified. |
| B2 | Accepted: A is portal/verified structured attachment filing without inference; natural-language material-bearing filing waits for R. Checked ordinary Telegram text calls `_chat`; no new safe attachment route was demonstrated. |
| B3 | Accepted with stronger boundary: C needs its own gate L. CLI permission lists alone do not prove no network/filesystem access; provider transport and runtime bookkeeping must be distinguished from agent effects. |
| R1 | Accepted with correction: audience includes agents, not merely humans. Fresh context and current audience retrieval checks; unique Career-fact and membership-race tests. |
| R2 | Accepted as proposed scope definition: owned/shared work, not admin-only other-user data. |
| R3 | Accepted as new narrowly bound filing capability. Baseline grants/membership do not implement it. Ordinary posts still require both. |
| R4 | Resolved in §10.1: no model holder in A, proposed Mac service, existing worker target, intended phone path and uncertainty handling. Live Telegram host mapping remains explicitly unverified. |
| R5 | Accept attachment bytes and no guessed multipart assembly. Telegram sendMessage documents 1–4096 characters after entity parsing; that does not prove every incoming client message will be automatically split. |
| R6 | Corrected: owner-appointed moderator exists in store.py state/moderator methods. Retain and define it; owner-only resume would remove an existing capability, not merely clarify terminology. |
| R7 | Accept evidence-based labels; authenticated check-in can prove contact before C, so do not unnecessarily withhold it until launcher work. Working/online still need current evidence. |
| R8 | Clarify no automatic launch after close. A user can independently open a client to handle an existing obligation; Robert cannot submit as another assignee. |
| R9 | Accept recovery requirement with explicit limits: withdrawal across server views/derived records, retained audit, no recall of delivered copies; issuer-side secret revocation separate. |
| R10 | Accept visible notice of standing CoS read to other humans. |
| O1 | Accept receipt audience at commit time; no guarantee of future audience immutability. |
| O2 | Defer durable draft storage to an explicit implementation choice with per-identity clearing and reload tests. Current in-memory limitation remains visible. |
| O3 | Accept check-in guidance: source text is untrusted content, not authority. It supplements runtime restrictions; it cannot substitute for them. |
| O4 | Clarify local/UTC preparation and source dates; preserve the original dates. |
| O5 | Proposed per-profile provider selection without automatic fallback, preserving model independence and Track B ownership. No assertion that local models are inherently safe or unsafe by size. |
| Additional briefing finding | Confirmed stored snapshot serialization includes source metadata. §9 now specifies disclosure-aware server serialization and destination-local citations; this is required implementation work. |

Technical references checked for this reconciliation: [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference) distinguishes preapproval from available tools; local `claude --help` lists tool selection, permission mode, setting sources and strict MCP configuration. [Sandbox documentation](https://code.claude.com/docs/en/sandboxing) is relevant to enforcement but is not evidence that this installation’s proposed profile has passed. [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage) gives the outgoing text limit. No inference, credential provisioning, live runtime configuration or application deployment occurred in this review.

Local evidence checked during this reconciliation: `store.py:429–458` in the deployed Records source defines state changes and owner-only moderator assignment; `platform_access.py:80–103` enforces current explicit grants and membership; `integration/cos_request_worker.py:30` names the separate CoS runtime target; `integration/cos_agent_responder.py:54–71` shows isolated synthetic context and unenforced tool-policy warning; `coordination.py:14–16` serializes snapshot source metadata. Root `core/telegram/telegram_cos_bot.py:87–99` sends ordinary text to `_chat`, and `domains/cos/chief_of_staff.py:796` routes that facade through Confer. These are code-path observations, not an assertion that all source versions are currently deployed or that the live Telegram configuration was inspected.

## 19. Platform permissions first; Rooms integration later

### 19.1 Owner direction and current scope

Robert clarified the sequence after v0.4: no need to work with another person this week, only to build so permissions can support one later. MiniMoi already has multi-user logins, currently inactive and not available for Guild. This is owner-supplied baseline information; this spec update does not claim an audit of that login implementation or its authorization coverage.

This explicitly replaces v0.4’s requirement to migrate Rooms into multiple workspaces and pass two-human/two-workspace tests during Milestone A. The team outcome remains; the platform layer must lead it. Continue this week’s usable Rooms and CoS work for Robert and current agent collaborators without making future human onboarding a release blocker.

### 19.2 Sequence

1. **Top-level MiniMoi foundation first.** Inventory and reuse existing user identities/logins. Define ownership, account lifecycle, domain/resource permissions, delegations and permission administration at the platform level. Check permission enforcement, not just successful login. A valid MiniMoi account must not automatically gain Guild or Rooms access. Do not assume the existing login system already provides this authorization layer.
2. **Domain integration afterward.** Guild and Rooms consume the platform’s authenticated principal and permission decisions through a defined adapter. Rooms adds its session membership and operation checks; it does not mint a separate human-account system or duplicate platform access administration. Guild retains its own action/spend boundaries. The integration order between domains can follow need; neither precedes the required platform contract.
3. **Additional human when needed.** Grant the named person explicit domain and session permissions, analogous to adding an agent principal with scoped permissions. Human login and agent installation authentication remain different credential mechanisms. Both resolve to stable principals and grants; a model name is not an identity. Validate the two-person flows and any necessary migration before enabling that person, not as a gate for this week.

**Required connection clarified again by Robert:** the access chain is MiniMoi login → MiniMoi account permissions → Rooms/domain access → session/operation permissions. Authentication identifies the person; the new MiniMoi permission layer decides what that account can use. Rooms must enforce both platform access and its finer session rules. A separate Rooms human key, an owner-name shortcut or a browser UI link does not satisfy this requirement.

Build and test that connection using Robert’s existing MiniMoi account now; no real second person is required. The existing beta can remain available during the work, but it is not evidence the target permission integration is complete. Guild must use the same platform contract when its multi-user access is implemented rather than invent a separate login system. This document update specifies work; it performs no account, permission or service change.

### 19.3 What this week must preserve

- Stable participant IDs distinct from model, plan and installation; scoped grants and authenticated attribution.
- Explicit ownership and operation checks behind a replaceable authorization boundary. Avoid new literal-name special cases and new assumptions that every human is Robert.
- Request and UI structures that can accept a named recipient later; Robert can remain the sole current human choice. No second-human onboarding screen or new workspace switcher is required now.
- Separation of personal/shared knowledge and knowledge/action authority. Robert’s CoS does not acquire another person’s private material from an administrative login.
- Unchanged historical records, meetings and receipts. Any future identity mapping or migration is explicit and audited; do not migrate existing Rooms data merely to satisfy an unused team feature this week.

The team-oriented audience, scope and delegation constraints elsewhere in this specification remain forward contracts. Implement only the portions necessary for Robert and the current agents now, including their real access/disclosure boundaries. A working MiniMoi-account permission path into Rooms for Robert is the present requirement; merely leaving an extension point is insufficient. A functioning multi-human deployment is not being claimed.

### 19.4 Required single-human platform integration verification

Using Robert’s existing authenticated MiniMoi identity, verify an explicit Rooms permission allows entry and authorized session operations. An authenticated account without the domain permission is denied by the server, including direct API routes and deep links; a hidden menu alone does not enforce access. Revoking the platform permission denies subsequent Rooms requests despite an existing browser login or cached page. Sign-out invalidates the relevant access path. Membership and action grants can further restrict access and cannot override platform denial. Use isolated fixtures to test missing/revoked permissions without enrolling another real user or disrupting Robert’s live access.

Do not add a second human credential prompt inside Rooms. Browser principal mapping comes from verified MiniMoi authentication, never a caller-provided owner name/header. Agent installation credentials remain separately authenticated but resolve to platform-governed principals/grants, with revocation checked server-side; agent-only beta mechanisms do not become alternative human logins.

### 19.5 Later additional-human verification

Before extending Guild or Rooms to another person, verify the actual platform identity mapping and domain grants, then person-specific Inbox/approval flows, membership removal, credential revocation and private/shared content isolation. Use synthetic second-user data before involving a real teammate. Cases 17–18 in §13 apply at that stage. A second workspace or tenant test is required only when the platform actually introduces that scope; do not manufacture multi-tenancy as a prerequisite for adding one teammate.

Named authority, audience-filtered CoS context, separate billing sponsorship and auditable ownership transfer remain requirements when those features are enabled. Broader onboarding, enterprise SSO, group-policy management and multi-tenant hosting are separate scope.

## 20. Grok final review disposition

Source: `GROK_FINAL_CHECK_COLLABORATION_ROOMS_RECEIVED_2026-09-21.txt`, preserved verbatim from Robert’s attachment. Grok’s verdict is **CONCUR WITH AMENDMENTS**. It did not verify packet hashes, live services, permission tables, runtime tools or test results. The comments are reconciled against canonical v0.5 plus Robert’s latest platform-first direction; they are not treated as independent live validation. Its reference to 16 acceptance families omits the two explicitly later team cases; the revised catalog retains those separately.

| Finding | Disposition |
|---|---|
| B1 giant Milestone A | Accepted. §12 splits A0–A3; first release explicitly A0+A1. A2/A3 are next increments, not hidden first-release promises. Codex owns platform integration, with independent implementation review. Scope/acceptance mapping is explicit. |
| B2 policy versus coverage | Accepted. §1 distinguishes authorized knowledge policy, verified delivered adapters, missing coverage and R/L gating. No inferred disk crawl or all-domain completion. |
| B3 permission freshness | Accepted. §4.1 uses no cross-request authorization cache initially; four current checks, revoke/commit boundaries and fail-closed outage behavior. A0/A2 include their respective negative tests. This restores the explicit freshness rule from approved Spec 157. |
| B4 disclosure | Accepted. §4.2 names source-disclosure authority distinct from read, plus destination checks and commit-time revalidation. Cases 2/9 reject read+write alone and check server-side metadata redaction. This preserves Spec 157’s declared-transfer rule, not a universal exfiltration guarantee. |
| I1 stale post-close requests | Accepted default: 24 hours unpicked, visible to assignee/requester without changing next actor or launching work. Threshold is configurable, not owner-settled. |
| I2 first launch | Retained Claude Code first, with L evidenced on hostile fixtures before real-material managed runs. Native check-in does not depend on L. |
| I3 search | Exact-record search stays in B; A1 labels its actual session-navigation behavior. UC5’s full outcome is not claimed by filing alone. |
| I4 withdrawal | Separate recovery increment, not A3 acceptance. A3 requires preview and truthful limitation; no unsupported recall claim. |
| I5 phone/Telegram | First release targets desktop portal. Phone, Telegram and laptop-off behavior are included only in separately named verified scope. Full-product targets remain. |
| I6 duplicated access predicates | Retained refactor prerequisite for A2, with route-matrix tests before role activation. |
| I7 layout measurements | Targets explicitly labeled as targets for the new UI; measure the new build, do not treat the beta or ASCII sketch as proof. |
| Knowledge versus roster | Clarified that retained standing read is not membership or current presence. |
| Private CoS-domain memory | Explicitly excluded in shared-backend room-context tests; direct CoS chat remains outside this specification. |
| Optional styling/drafts | Retained restrained UI style and page-memory draft limitation; themes, presets and synchronized unread stay later. No new style review loop required. |

Local reconciliation evidence: approved Spec 157 §§8 and cross-room transfer provisions already require freshness and separate source disclosure. `minimoi_portal/workspaces.py` contains navigation decisions mirroring tier/domain policy and points to route decorators as the security boundary. That source inspection establishes some existing permission logic, not a complete live grant service or proof that every route uses it. A0 must inspect the actual route/authentication path before extending it. No application, credential, runtime or official-spec mutation occurred during this document reconciliation.

## 21. Amendment v0.7 — standalone capture, local execution visibility and the September 25 build

**Status:** accepted by Robert on September 25, 2026, after Codex's review (confirm with amendments, all incorporated), and merged to main in PR #224. Prepared by Claude Code under package A0. Sources: the reviewed v0.2 candidate (`REVISED_SPEC_v0.2.md`, SHA-256 `5b1fd8d2…76a7`) as amended by the shared build handoff Revision 2 §3, and the A0 implementation review. Both live in `planning-studio/initiatives/INIT-2026-0006-working-room/review-packets/2026-09-25-design-revision-v02/`. The candidate, Claude Chat's and Grok's reviews and Codex's reconciliation remain unchanged there as evidence. A builder reads this section and the implementation plan it links, not those sources.

### 21.1 Authority, ownership and relationship to this specification

- **Roles.** Claude Code is the single implementer for the scope below. Codex independently validates the actual diffs and runs end-to-end tests. Robert accepts specification, plan, each reviewed diff and any release separately. For this scope, this supersedes "Implementation lead: Codex" in the header and §12.1, and the delivery plan's ownership line.
- **Release target.** "Production v0.9 by September 27" was superseded by Guild Rev3 D5 (component releases, no Rooms production hosting this week). This amendment adds no production scope. Package A5 proposes release components separately.
- **Naming.** §12.1's slices are cited as **S158-A0…S158-A3** from v0.7 onward. The build packages defined here are **A0–A5 and U** (§21.12). The two sets are different work.
- **Earlier slices.** S158-A0 (MiniMoi login-linked permissions) is not a prerequisite for the local proofs U and A1–A4. Those use existing Records installation credentials behind the owner-gated development route. S158-A0 remains mandatory before any production Rooms route or any additional person, so A5 must either include it or exclude Rooms hosting. S158-A1, S158-A2 and S158-A3 are unchanged and scheduled separately. Standing read over the new standalone namespace (§21.5) does not implement S158-A2's session-wide role.
- **What stays in force.** §§1–20 remain in force except where §21.15 states a supersession. §13 remains the product-wide catalog. §21.14 defines this amendment's acceptance IDs and maps overlaps. Spec 157 continues to govern identity, installation credentials, membership, transfer and receipts; its amendment of the same date records the typed non-room scopes used here.

### 21.2 Outcomes

Robert can preserve useful thinking from an ordinary conversation without opening a Room, return to the actual sources with CoS, bring people and agents together when helpful, and understand what happened to authorized work even when execution was interrupted.

| Experience | Visible result |
|---|---|
| Save | One deliberate save, a receipt and honest coverage. No Room, project or classification is required |
| Continue | Source-linked understanding, disagreements and owner decisions, with the original material one step away |
| Collaborate | A persistent Room with separately authorized sessions, selected context and attributable contributions |
| Operate | Current work, evidence, exceptions, next owner and recovery options |

Rooms supports any discussion, not only builds. Saving creates no task, permission to act or launch. Robert decides how often to interact; no usage-frequency condition or demand proof gates collaboration.

### 21.3 Canonical ownership

| Material | Authority | Local build host |
|---|---|---|
| Standalone originals, revisions, provenance and capture receipts | Records | Records service, new additive namespace |
| Existing room-native events and session membership | Existing Rooms ledger | Unchanged tables |
| Work artifacts and approval/disposition | MiniMoi Work contract (closed eight effects) | Referenced by exact identity only. No new effect and no new field |
| Assignments, attempts, checkpoints, evidence, exceptions | Guild work-run contract | Separate module and tables inside the Records service (one SQLite writer). Production host decided at A5 |
| Interpretation | Derived Records item with cited source revisions and freshness. Never an approval authority | Records service |
| Diagnostic spans, metrics and logs | Replaceable telemetry storage with short retention | Local lab (§21.10) |
| Agent working memory and indexes | Rebuildable or portable supporting state; never the only evidence | Unchanged |

One logical view may read several owners but never keeps an editable copy of their status. No SQLite file is opened directly by more than one service process; other processes use the authenticated HTTP API. Locally, "central" means the Records service. Receipts continue to say `production: not_connected`.

### 21.4 Standalone capture

**Operations.** CoS tools, HTTP clients, file intake and any later MCP facade call the same governed operations: save, read, search, relate and request interpretation. Provider SDKs never enter the Records core.

**Record envelope.** Stable ID; owner and workspace; kind; immutable revision; bytes or managed attachment reference with SHA-256; media type; source application and reference; capture time; source time when known; coverage (complete or partial, with an explicit list of missing items such as attachments); fidelity; authenticated submitter; separately declared author or speakers; processing state; optional relations. No `room_id`, session or `work_id` is required.

- **Fidelity** is one of `original`, `excerpt`, `extraction` or `reconstruction`.
  - A model-reconstructed transcript is `reconstruction` and is always displayed as derived.
  - An incomplete excerpt never becomes complete because its hash matches on receipt.
  - An imported "Robert approved" remains a declared source claim.
- **Receipt stages.** The receipt distinguishes four stages: original committed, extraction (done, pending, failed or not applicable), indexing, and central availability. Preservation precedes optional analysis; a failed extraction or interpretation never hides or rolls back the original.
- **Idempotency.** The operation identity includes the authenticated actor, the operation and its typed destination scope. Access is authorized against the stored resource's scope, never against a client-supplied scope alone.
  - The same operation ID with the same payload returns the recorded result.
  - Changed content under the same ID is refused with a conflict.
  - Identical content under a new ID is a deliberate new capture and is preserved.
  - Overlapping snapshots are never merged or discarded on semantic similarity.
- **Limits.** 2,000,000 bytes per revision, as today. Larger sources are refused with an explicit error; nothing is silently truncated.
- **Corrections.** A correction is a new revision or a `corrects` relation. History is never rewritten.
- **Nothing automatic.** No spec, backlog item, memory update or agent launch follows a save. Classification is optional and happens after acceptance.
- **Deferred.** Continuous "keep from here" capture is not part of this build.
- **Session boundary.** The session import in §8 keeps its active-session rule. A standalone save never impersonates a session or creates a placeholder one. Sharing a saved record into a session is a separate operation under Spec 157 §4 transfer rules: source-disclosure authority, destination write and audience checks.

### 21.5 Retrieval and interpretation

CoS reads actual content through the governed contract, cites `source@revision#segment`, and states coverage gaps.

- **Content of an interpretation.** A "Where we are" view may contain:
  - the question and the current understanding;
  - alternatives and open points;
  - owner decisions.

  It may state that no decision was reached.
- **Owner decisions.** An owner decision must cite a record that the owner principal created through a direct authenticated decision or confirmation action. An authenticated owner submitter is **necessary but not sufficient**: words inside a transcript or document Robert uploads remain imported claims. For example, a saved transcript saying "Robert approved deployment" stays *reported, unverified* until a separate applicable owner decision is recorded, whoever uploaded it. Both kinds keep exact source and revision links.
- **Staleness.** A new revision of a cited source, or a correction relation to it, marks the interpretation stale without rewriting it.
- **Search.** Search uses metadata and full text first, with results typed to the exact source and revision, and truncation stated. Embeddings and graphs are later, evidence-driven additions.
- **CoS standing read.** CoS reads Robert's standalone records under a named standing role plus a current, non-legacy CoS installation credential, both checked on every request (§4.1). Read, provider processing, disclosure and action remain separate permissions.
- **Before gate R.** No model process holds the standing credential or the retrieved results.
- **Interpretation adapters.** Every adapter is labelled `test` or `live`.
  - A live model interpretation processes only synthetic sources in this build, runs tool-less over an explicitly assembled context, and needs Robert's explicit approval per run because it costs money.
  - Missing usage or cost is reported as unknown, never zero.

### 21.6 Rooms and collaboration changes

- **Sessions.** A session still has explicit participants and never inherits membership. Selected context enters a session only through the disclosure-checked sharing operation.
- **Completion after pause or close.** This implements §6 D1.
  - An existing authorized request may be picked up, answered, submitted, acknowledged or cancelled after pause or close, under full current credential, grant, actor and version checks.
  - These transitions are recorded as operational activity in `coordination_steps`. No discussion event is added after close.
  - `minimoi.transcript/1.0` stays valid and unchanged.
  - A versioned export bundle for post-close activity remains Milestone B. Until then the activity is visible through the API and UI, and exports say it is absent.
- **Rechecks.** Authorization is rechecked before claiming queued work and before publishing a result. Cancellation and revocation do not erase already delivered copies.
- **Join evidence.** A join acknowledgment is authenticated contact, not presence. "Working" requires a current observed attempt (§21.7).
- **Separate capability entries.** Grok CLI, Grok web, Claude Chat, Claude Code and Codex are separate capability entries. A cloud-origin client such as Claude Chat needs a reachable authorized endpoint. It is recorded as unproved until that separately authorized test exists. A CLI process never attaches to an existing chat.

### 21.7 Local execution and the durable work-run record

**Workshop.** A Workshop is a local execution environment, distinct from a development MiniMoi instance. This build has exactly one Workshop. It registers its capabilities, claims authorized work and pushes results; no inbound tunnel is required. Manually started runs are allowed and labelled manual. No heartbeat or live status is ever inferred.

| Record | Required information |
|---|---|
| Assignment | Typed reference to existing work (build-queue item, coordination request, Work artifact at an exact digest, or record); approved scope/spec revision; acceptance boundary; requester, builder, reviewer; mandate (operations, host, expiry, limits, retry bound, expected quiet period) |
| Attempt | Stable ID; assignment; previous attempt; client/runtime/version; adapter and whether it is `real` or `test`; host and environment; repository, worktree, base revision and dirty-patch SHA-256 where relevant; claim token hash, fence and lease; start and end; the four state dimensions (§21.8); diagnostic trace reference |
| Event/checkpoint | Monotonic per-attempt sequence; source-observed and received times; actor; `observation` or `self_report`; step; wait reason; operation ID; evidence references. Heartbeats are recorded separately from progress |
| Evidence | Diff/artifact SHA-256; test command and result; reviewed revision; output location; source. No "tested" status from prose |
| Exception | Category; first and last observation; impact; affected attempts; known and unknown effects; recovery owner; actions tried; next action; state (open, dispositioned or resolved); closure evidence |
| Observation | Subject (attempt, monitor, store, exporter); observer; time; freshness (current, stale, unavailable or unknown) |
| Disposition reference | Independent review, Robert's acceptance and release, each by its own authenticated actor with exact-version references. The owner's acceptance is never set by a builder, monitor or model |

**Rules.**

- **Intent and completion.** Intent is recorded before an external effect, and completion after it is observed. A crash in between yields *outcome uncertain*, never a false failure or an automatic retry.
- **Claims and fencing.** Claims are exclusive. Every write presents the current claim token and fence, so a stale worker is refused. A repository-writer lease keeps one active editor per repository or worktree.
- **Recoverable claims.** An acquired claim survives a lost response.
  - The claimant generates its claim secret and stores it durably before dispatch. The server stores only its hash.
  - A retry under the same operation returns the same attempt and fence, and no secret.
  - A changed secret or payload conflicts.
  - Replays still recheck credentials, mandate, assignment scope and lease.
  - Claim secrets never appear in receipts, evidence, telemetry or roster output.
- **Outbox.** The Workshop keeps a durable local outbox with idempotent central acceptance. The UI distinguishes three states: saved locally, acknowledgment pending, and centrally recorded. On reconnect, mandate expiry is enforced. Offline operation creates no sharing or launch authority.
- **Store outage.** If intent cannot be recorded durably, no new controlled effect starts. Already-uncertain effects are preserved for reconciliation.
- **Checkpoints and commits.** A checkpoint never requires a commit; reviewed-diff approval still governs commits.
- **Billing.** The billing route is recorded per runtime. There is no silent switch from subscription to metered API. Unknown usage or cost stays unknown.

### 21.8 State dimensions

These are four independent dimensions, never collapsed into a single "done". They are conceptual states mapped onto the records above, not replacement enums for existing contracts.

- **Execution:** queued, claimed, running, waiting, outcome uncertain, finished, failed, cancelled.
- **Delivery of evidence:** local only, central acknowledgment pending, centrally recorded.
- **Assurance:** produced, builder-verified, independently reviewed, accepted, released. Each needs its own evidence and authority.
- **Observation:** current, stale, unavailable, unknown. Last contact and last meaningful progress are shown separately, with the observation source (heartbeat or checkpoint).

Several further rules follow:

- A process exit does not establish business acceptance.
- A model's completion statement is self-report.
- Freshness is computed from the receiving clock, so a skewed source clock cannot make stale data look current.
- Missing instrumentation is shown as *unavailable*, never as inactive.

### 21.9 Exceptions, monitoring and recovery

Monitoring runs outside the agent being monitored. Deterministic checks detect expired leases, stale contact, repeated failures, overdue milestones, resource limits and delivery backlog. Thresholds and quiet periods are configuration per task class; a long test is not automatically a stuck agent, and lost contact is not proof that a process stopped. Master Craftsman interprets evidence and proposes recovery to Robert; CoS may surface the effect on Robert's priorities; neither becomes an enforcing supervisor here.

| Condition | Required response |
|---|---|
| Host offline before dispatch | Keep queued; name the unavailable host and last contact; do not claim pickup |
| Contact lost after possible effect | Outcome uncertain; inspect the saved operation, receipt or artifact before any replacement attempt |
| Sign-in expired or quota unavailable | Stop automatic attempts; name the prerequisite and recovery owner; no billing fallback |
| Repeated same error or no meaningful progress | Pause at configured bounds; preserve checkpoint and attempts; request a changed plan or input |
| Test or review failure | Keep work open; attach findings to the exact diff; assign the correction to the builder |
| Stale baseline or simultaneous writer | Stop the conflicting write; preserve both patches; resolve ownership; one active repository editor |
| Durable work-store failure | Fail closed before new controlled effects; preserve local evidence for reconciliation |
| Telemetry exporter failure | Continue authorized work within bounded buffering; show degraded diagnostics and drops |
| Monitoring cannot read its source | Show unknown or degraded, never zero exceptions or healthy |

**Unknown is never zero.** This rule includes the existing Guild Operations escalation count, which today reports 0 when its database read fails. The fix returns an explicit unknown with a reason, keeps a true 0 distinct, and shows unknown on the Operate page, in Telegram status and in CoS context.

**Recovery.**

- **Where it appears.** Recovery is a state of Work detail, not a separate page.
- **Retries.** A retry is allowed only when the mandate covers it and the operation is idempotent or has been reconciled to a known non-effect.
- **Changed plans.** A changed plan starts a new attempt; it never rewrites history.
- **Cancellation.** Cancellation records the request and the observed stop separately. It is not a rollback.
- **Resolution.** Resolving an exception requires verification evidence. Acknowledging or dismissing it does not resolve it.
- **Notifications.** In this build, exceptions appear in the overview. Routed and deduplicated notifications are later work.

### 21.10 Observability and the operations console

**Boundary.** OpenTelemetry supplies portable traces, metrics and logs over OTLP to a replaceable backend. Assignment, attempt, receipt, disposition and approval truth stays in MiniMoi records; telemetry is never sampled for that evidence and never drives business status.

- **Correlation.** Each attempt has its own trace. Related attempts use span links; one indefinitely open span across queue waits is not used. Spans and logs carry opaque assignment, attempt and operation references, never as metric labels. IDs are correlation only, never authorization. Standard attributes (`service.*`, `deployment.environment`, `host.*`, GenAI conventions where a model call is visible) are kept separate from `minimoi.*` attributes. Versions are pinned in the plan.
- **Coverage.** The owned boundary is instrumented first. Where a runtime exposes only process start, end and output, only that is shown; no internal spans, tokens or cost are invented.
- **Privacy.** An allowlist is applied before emission. Diagnostics carry metadata, timings, error classes and authorized evidence links. By default they never contain prompts, transcripts, document bodies, credentials, raw tool arguments, personal paths or customer content. A diagnostic link never bypasses record authorization.
- **Resilience.** Exporter memory, queue, retry time and disk are bounded from the first instrumented run. Export age, failures and drops are visible. Telemetry buffers are separate from the durable work outbox. A telemetry outage never blocks record preservation, never claims work success and never causes a business operation to repeat. The first synthetic proof retains all test traces. Production sampling, retention, limits and overhead are specified before any deployment.
- **Console.** The console is a free, self-hosted native console, read-only for investigation. A **separate window is acceptable**; embedding in Guild is optional and needs no JWT or iframe work. Access is local only and controlled: loopback binding, non-default credentials held outside the repository, anonymous access off, and no credentials in URLs. No private content is forwarded to make a demonstration work. No paid feature or hosted vendor is a dependency.
- **Live roster.** The live roster comes from MiniMoi attempt and checkpoint records through a read-only application endpoint; traces and logs explain it. A configured agent, an old span or a healthy process is never evidence of current useful work. Trace backends show spans after export; that is not presented as streaming internal activity.
- **Tools.**
  - Grafana OSS is the starting console.
  - `grafana/otel-lgtm` is a development lab, not production packaging.
  - OpenLIT is a candidate second viewer for the exporter swap.
  - The Grafana `agento11y` local mode is an untested local-console candidate. Its documented default stores full session content locally, so any evaluation uses synthetic material only and verifies data destinations first.

### 21.11 Modular structure

| Responsibility | Owns | Dependency rule |
|---|---|---|
| Records | Source, revision, receipt contracts and repositories | No vendor SDK, Room prerequisite or telemetry backend |
| Policy | Authenticated identity, current grants, standing roles, disclosure and action decisions | Shared by all transports; nothing prompt-controlled |
| Coordination and execution | Request, attempt and exception transitions; bounded recovery | Narrow persistence and delivery interfaces |
| Adapters | CoS tool facade, HTTP and file intake, Workshop transport, execution and interpretation adapters | Translate at edges; never own approvals or source truth |
| Interpretation | Source reading and derived views | Cannot overwrite originals or authorize effects |
| Observability | OTel setup, attribute allowlist, correlation, export | The only module importing telemetry libraries; observes, never drives status |
| Presentation | Save, Continue, Collaborate, Work overview and detail | Uses application APIs; no duplicated permission or workflow rules |

Concrete adapters are composed at one entry point per process, with no dependency cycles. UI fixture and real adapters implement the same versioned contract, validated by shared schemas. Every fixture response and screen is marked simulated, and sample data never appears in live results. Mixed modules are refactored only where a tested boundary needs it. There are no sweeping framework changes or broad renames.

### 21.12 Delivery packages

| Package | Required result | Evidence gate |
|---|---|---|
| A0 | Code review, this revision and the implementation plan | Codex review; Robert accepts before application edits |
| U | Save, Continue, Collaborate, Work overview and Work detail over a fixture adapter. Every screen shows sources, receipt coverage, participants, selected context, next owner, unknown, stale and error states, and recovery within detail. All data and actions are marked simulated | Starts after A0 acceptance; local preview and screenshots; Robert's usability review. Not evidence of backend completion |
| A1 | Standalone save, read, search, relate and interpretation; CoS tool contract; receipts, idempotency, corrections, permissions, additive migration and restore | Tested through the API with the UI disconnected; Codex reviews the bounded diff |
| A2 | One manually started, instrumented synthetic Python job with assignment, attempt, checkpoints and result evidence. A read-only roster and console with freshness. Killed job, unavailable store and configured-but-idle behavior. The Guild Operations unknown fix | Live local run evidence; Codex review |
| A3 | One Workshop path with a selected-context session, authenticated pickup, result and acknowledgment. Expiry, revocation and authorized late results. Interruption after a possible effect; idempotent reconciliation; outbox and restart; duplicate-claim fencing; exporter outage and recovery; one execution-adapter swap and one exporter swap | Failures tested directly before UI connection; Codex review. No arbitrary paid model sessions |
| A4 | Fixture adapter replaced by real services; integrated journeys, refusals, stale state, restart and recovery; per-client capability shown as live, test or unavailable | Codex tests the complete candidate and reviews the diff |
| A5 | Reviewed candidate, evidence, limitations and proposed release components; for any AWS proposal, access, capacity, backup/restore, rollback and deployed-version verification | Robert accepts and separately authorizes any release |

Every build and review handback names the accepted specification and plan revisions. A discovery that changes required behavior becomes an explicit amendment before it is treated as agreed. Build completion is not release approval. Local service tests are not native-client tests: real-client coverage is recorded separately, and synthetic transport tests never count as client acceptance.

### 21.13 Outside this amendment

- A general multi-agent scheduler.
- Automated production repair.
- Multiple Workshops.
- Universal continuous native-chat capture.
- A new telemetry database or viewer.
- Semantic graph or embedding search.
- A new OpenClaw gateway rollout. Existing OpenClaw capability is preserved.
- A Master Craftsman runtime.
- Career migration as a prerequisite.
- Private Career content as test fixtures.
- AWS deployment.
- Record withdrawal/deletion (§8's separate recovery increment).
- Notification routing.

### 21.14 Acceptance catalog

The plan maps every ID to concrete tests and records deferrals with reasons. Passing a mockup never substitutes for these.

| ID | Passing evidence | Stage |
|---|---|---|
| C01 Standalone save | Original, coverage and receipt readable with no Room or session row and no classification | A1 |
| C02 Fidelity | Distinct original, excerpt, extraction and reconstruction; missing attachments visible; comparison against the source export catches omissions | A1 |
| C03 Reading and continuation | CoS reads actual content through the contract, cites exact revisions and segments, preserves disagreement and never manufactures an owner decision. An owner-uploaded transcript claiming "Robert approved deployment" stays reported and unverified | A1 |
| C04 Retry and overlap | Lost response reconciles by the same ID and payload; conflicting payload refused; repeat captures preserve originals | A1 |
| C05 Permissions | CoS standing read works under role plus credential; disclosure and effect remain separate; forged speaker or approval labels confer nothing | A1 |
| C06 Sessions | A new session inherits no participants; actual client contribution and acknowledgment demonstrated, or unavailability stated per client | A3 |
| C07 Revocation and closure | Queued and reply operations recheck grants; authorized late results use their route without reopening discussion | A3 |
| C08 Offline continuity | Local receipt and outbox survive restart; central acknowledgment shown separately; expiry enforced on reconnect | A3 |
| C09 Preservation | Existing records, URLs and receipts retained; additive migration and restore prove source hashes and permission behavior | A1 |
| O01 Work chain | Assignment → attempt → exact diff/artifacts → test/review → disposition followed without a manual recap | A2 |
| O02 Truthful state | Process exit and model completion grant no verified, accepted or released status; stale data never renders current | A2 |
| O03 Independent monitoring | Killing the job yields a stale-contact observation from the monitor; stopping the monitor or its read source yields unknown, never zero | A2 |
| O04 Interruption | Stop after a simulated effect and before the reply; reconcile without repeating the effect or launching a model | A3 |
| O05 Ownership | Duplicate claims and stale workers cannot produce conflicting accepted results; one repository writer. A claim whose response is lost is recovered by the same operation with the same attempt and fence and runs exactly once | A3 |
| O06 Bounded effort | Repeated errors, quota or sign-in failure and task limits stop attempts and produce actionable exceptions | A3 |
| O07 Closure | An exception records owner, evidence and recovery; acknowledgment alone never resolves it | A3 |
| O08 Record-store outage | No new controlled effect without durable intent; uncertain effects preserved | A3 |
| O09 Agent roster | During a real synthetic run the roster distinguishes active from configured-but-not-running, shows current step and wait reason, marks missing or stale evidence unknown, and keeps completed runs separate | A2 |
| T01 Correlation | Test work links to its trace and service versions; asynchronous attempts stay distinguishable | A2 |
| T02 Exporter outage | Work receipts stay correct; bounded buffering and drops visible; restoring telemetry repeats no effect | A3 |
| T03 Content protection | Synthetic secrets and content in inputs and error text never appear in exported diagnostics; drill-downs respect audience | A2 |
| T04 Limited instrumentation | An opaque runtime shows only observed boundaries; missing tokens, cost and internal spans labelled unavailable | A2 |
| T05 Resource bounds | Measured tracing overhead, queue growth and disk stay within the plan's dev limits | A3 |
| P01 Replaceability | One execution adapter and one exporter replaced without migrating identities, weakening policy or losing history; real versus test adapters identified | A3 |
| P02 Local first | Capture, evidence and recovery work without any Microsoft service, public endpoint or cloud dependency; unproved cloud-client integrations labelled | A1–A3 |
| U01 Usability | Robert identifies the next owner and needed decision on each screen; a second reader resumes from evidence | U, A4 |
| U02 Accessibility | Keyboard use, status not by color alone, narrow-width reflow, empty, error and stale states verified | U, A4 |
| U03 Operations console (replacement) | A free self-hosted console, embedded or in a separate window, shows a real synthetic execution's agent/job identity, current observed step, waits, freshness, available measurements and diagnostic drill-down. Missing instrumentation is unavailable, not inferred. Degradation and recovery demonstrated. No paid dependency, fabricated span or anonymous access | A2, A4 |

Overlaps with §13: C06 exercises part of case 3; C07 part of case 6; C05 applies case 1's role-plus-credential rule to the standalone namespace only; case 11 applies to every stage. No §13 case is claimed complete by this amendment. v0.1 references A06 (keep from here), A13 (revocation mid-response, beyond C07), A15 (laptop off), A19 (withdrawal) and A16's remote half are deferred with §21.13.

### 21.15 Supersessions and cross-references

| Clause | Change |
|---|---|
| Header: implementation lead Codex; production v0.9 by September 27 | Superseded for this scope (§21.1). Release follows Guild Rev3 D5 component releases and A5 |
| §12.1 slice labels A0–A3 | Renamed S158-A0…S158-A3; content unchanged |
| §12.1 "first release is A0 + A1" and its ownership column | Superseded as a delivery plan; S158-A0 placement per §21.1 |
| §8 "New transcript import uses an active session" | Unchanged for session imports; standalone saves use §21.4's separate route |
| §3 "Guild owns execution/work items" | Clarified: Guild owns the work-run contract; the local build hosts it in the Records service (§21.3) |
| §6 recommended default D1 | Now required and implemented in A3 as §21.6 |
| v0.2 candidate: embedded console requirement, old delivery sequence, review prompts | Not adopted; §§21.10 and 21.12 govern |
| Claude Chat v0.2 review M1 (drop agento11y) and M3 (JWT only route) | Not adopted; §21.10 |
| Delivery plan `rooms_beta_to_v09_delivery_plan_2026-09-21.md` | Historical for dates and ownership; its release gates still apply to any Rooms release |

### 21.16 Decision register additions

Settled owner direction (Robert, September 25): D16 capture without a Room; D17 no usage-frequency gate on collaboration; D18 UI built early with labelled sample data, foundation tested independently, then integrated; D19 embedding optional, separate window acceptable; D20 roster from MiniMoi records; D21 unknown never zero, including Guild Operations; D22 recovery within Work detail; D23 proof split A1/A2/A3/A4 with U separate; D24 free self-hosted tooling, Grafana OSS as the starting point; D25 agento11y retained as an untested candidate; D26 no JWT requirement, and no anonymous exposure, URL credentials or content forwarding; D27 existing OpenClaw preserved, new runtime integration later; D28 local UI now, production hosting later; D29 MiniMoi owns identities, records, policy and receipts, and execution and telemetry are replaceable adapters.

Owner decisions from the A0 implementation review §4, recorded September 25:

- **Decided by Robert as recommended:** D-A0-2 (S158-A0 required before any production Rooms route or additional person, not before the local proofs), D-A0-6 (Rooms merged to main, redeploying cos-bot and cos-scheduler; done in PR #223), D-A0-9 (no paid model run without his approval at the time) and D-A0-11 (this amendment merged after PR #221, in its own docs PR).
- **Robert's direction:** D-A0-5 (main is the build baseline; each package is one short-lived branch and PR).
- **Routine defaults accepted with the revision:** D-A0-1, -3, -4, -7, -8, -10, -12, -13 and -14.

Robert may change any of these before the package it affects.
