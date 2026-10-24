# Master Craftsman operating model — OpenClaw configuration, autonomy and persona

**Specification ID:** MC-OC-001  
**Version:** 0.4 · 24 October 2026  
**Author:** Claude Code, folding reviews; Codex reviews  
**Owner:** Robert  
**Delivery order:** Master Craftsman first; Chief of Staff fast follow  
**Umbrella:** [#361 — MC does not yet fulfill day-to-day ownership](https://github.com/robertvanstedum/personal-ai-agents/issues/361)  
**Related authority track:** [#360 — explicit tool policy](https://github.com/robertvanstedum/personal-ai-agents/issues/360)
**Status:** Canonical review proposal in the official documentation tree; v0.4 folds the Claude Code, Claude Chat, personal OpenClaw and Grok reviews (§12). Robert confirmed the §10 decision table on 24 October 2026. Slice 0a has a build plan awaiting Robert’s go; nothing is approved to build or activate until he gives it.

This is the single proposed governing specification for MC’s operational role, capability configuration and persona lifecycle, with CoS adoption defined below. GitHub publication makes it reviewable, not automatically approved. See the [technical review and disposition](../reviews/MC_OC_001_TECHNICAL_REVIEW_2026-10-24.md) and the [review index](../reviews/MC_OC_001_REVIEW_INDEX.md). Subsequent revisions update this canonical document through reviewed commits; implementation planning follows approval of a named revision.
Scope: MC conversation, investigation, memory integration, governed action/delegation and versioned persona. This supersedes the two-line configuration proposal as a complete design brief, not the approved Board operation contracts.

## 1. Product intent

Master Craftsman is Robert’s MiniMoi build and operations partner: a capable OpenClaw agent who understands the work, investigates, follows leads, remembers decisions and helps carry them through. Robert should ask naturally, in English or German, from his phone or desktop. He should not have to know tool names, repeat instructions as command syntax, or grant permission for each ordinary lookup needed to answer his question.

MiniMoi supplies continuity, context, domain operations and accountable execution. It must not reduce the agent to a catalogue of canned answers. Board was a test of updating application data through the application, not the limit of MC’s purpose. General research should use general OpenClaw capabilities wherever suitable. Application changes should use the same validated domain semantics as the UI; exceptional repairs should become scoped tickets and delegated work.

The intended experience: **ask → investigate or act within authority → verify → answer plainly → retain the useful outcome.** Clarify material ambiguity, not normal paraphrases. Preserve warmth, concise answers, disagreement when justified, German learning corrections and strict honesty about built, reviewed, deployed and verified states.

A question authorizes relevant investigation through already granted sources. It does not authorize MC to grant itself access, change its policy, disclose unrelated private material, publish, or make consequential changes. These boundaries should live in configuration and execution mechanisms; repeated cautionary prose is not the product.

## 2. Inputs, authority and relationship to existing design

Robert’s October 24 direction establishes a capable operational partner, a formal shared specification, versioned SOUL/AGENTS artifacts, MC-first delivery and CoS as a fast follow. Personal OpenClaw supplied a configuration comparison, a proposed persona and a persona-versioning handoff. Codex checked those recommendations against the installed runtime and actual MiniMoi source. Contributor originals, host details and raw owner trials remain in private working evidence; this document contains the complete product requirements without requiring those files.

Related canonical documents:
- [Architecture](../../ARCHITECTURE.md): platform context; this specification details the proposed agent operating model.
- [CoS and MC solution approach](../design/SOLUTION_APPROACH_COS_MASTER_CRAFTSMAN.md): historical bounded-agent approach and role separation. Upon adoption, this specification replaces its CoS-first rollout order and advisory-only MC scope with MC operational ownership followed by CoS adoption. Distinct identities, memory ownership and consequence-based authority remain.
- [OpenClaw gateway spec 146](spec_146_openclaw_cos_gateway_2026-08-09.md): provisioning context; no older configuration example overrides the approved effective capability contract here.
- [MiniMoi-owned agent memory spec 160](spec_160_agent_memory_owned_by_minimoi_2026-09-28.md): record and privacy ownership retained; no competing memory migration is introduced.
- [MC handbook adoption](../../services/mc_operations/handbook/v0.1.0/ADOPTION.md): installation and evidence discipline to reuse, not a second handbook to replace blindly.

The approved Board operation contracts remain intact. This spec does not close #361, reverse #360 controls, or declare dev evidence to be production acceptance. Direct owner instructions govern; source contributions are design input. Runtime changes require an approved target and reviewed implementation. Model selection, account entitlement and authentication changes are separate decisions, not assumed prerequisites.

## 3. Current state and issues

Findings are from source/configuration inspection on October 24 unless an owner trial is explicitly named. “Configured,” “available to a turn,” and “successful in an owner journey” are different evidence levels.

| ID | Observed issue | Evidence and consequence |
|---|---|---|
| I01 | Persona promises project reads that configuration prevents | The inspected native dev mc-chat selects workspace-mc-chat; minimal tool profile plus workspaceOnly=true restricts file access. Its AGENTS names a project outside that workspace. |
| I02 | Web is both denied and absent from the minimal allow set | Installed OpenClaw 2026.9.4 minimal profile contains session_status only. Removing group:web from deny alone does not add web_search/web_fetch to alsoAllow. |
| I03 | Native AGENTS contains contradictory routing and authority rules | Early JOB/ASK instructions conflict with its later statement that these lines no longer act. Tools-mode owns_actions disables those regex paths. A blanket “every change makes an offer” contradicts direct Board/preference operations. Several named tools are not in the current configured plugin list. |
| I04 | Persona drift spans multiple surfaces | Native workspace-mc-chat differs from docker/mc-agent/workspace and services/mc_operations/workspace. The latter already describes a more capable operational partner but its existence is not installation proof. |
| I05 | “Local personal OpenClaw only” understates possible impact | The edited native workspace is the configured mc-chat workspace used by dev’s native runtime. Docker files being unchanged does not isolate these edits from dev MC. Which existing sessions loaded them is unverified. |
| I06 | Host evidence covers specified paths, not all devices | observeHost calls statfs on diskPaths. Adding /Volumes measures that path’s filesystem, not every mounted volume. Listing a drive is not a capacity measurement or health test. |
| I07 | Deployment knowledge is incomplete, not wholly absent | Dev now has timestamped read-only status.json observations. No CI capability manifest was invented. A source spec’s existence does not establish that it was deployed; correlate observed image, commit provenance and spec lineage. |
| I08 | GitHub issue access is not general repository search | minimoi_github_issues is an issue/history source. Repository contents/history need a separately verified source route, especially when the Mac is unavailable. |
| I09 | Broad native capability counts and restart claims are not established | Installed core catalogue recognizes read/ls/view_image and web tools; dir_list/grep/find are not core IDs in that catalogue. Plugin/custom availability must be checked separately. agents.entries is classified hot in installed reload rules; this is not proof of session refresh behavior or that a shared restart is required. |
| I10 | Useful Board behavior has real owner evidence | A private dev owner trial passed create/edit/done/conditional undo, including reference continuity across an intervening language-learning question. Sample actions took approximately 6–8 seconds; first text was not measured. Preserve this behavior. |
| I11 | Prior failures had concrete integration causes | Image admission rejected supported image IDs; lazy policy activation revoked the first action turn. Both fixed in deployed successors. A reported availability interruption coincided with a dev portal rollout. A more assertive SOUL alone would not fix these defects. |
| I12 | Continuity and delegation must be proven end to end | Native memory tools are disabled; MiniMoi records/preferences remain. Research/delegation tool names in instructions do not prove active dispatch, worker receipt or completion delivery. MC export of an image to _working remains unverified. |

No causal claim that a short SOUL alone produced passivity. Restricted access, stale instructions, data coverage, routing and persona can each contribute. The reorganization must address their agreement.

## 4. Responsibility model

| Component / participant | Responsibility |
|---|---|
| Robert | Product owner, scope and consequence decisions, spec/diff approval and production authority. |
| MC / OpenClaw | Conversation, reasoning, source selection, investigation, clarification, permitted action calls, verification and honest follow-through. |
| MiniMoi | Owner identity and context, durable records/memory, domain validation, action authority, receipts, recovery and application UI. |
| Personal OpenClaw / Mini-moi | Independent input, planning/memory and coordination as authorized; no assumption of identical scope to MC. |
| Claude Chat / Grok | Substantive design review when their contributions are supplied or delivery is authorized. |
| Claude Code / Codex | Technical review and implementation from the approved spec. One active editor per checkout; one selected build owner. |

Do not create a second memory system, parallel Board service, or new generic framework where an existing OpenClaw or MiniMoi mechanism fits. Do not replace OpenClaw’s general investigation with a static reference packet.

## 5. Capability and action matrix

The target is described by outcome and authority, not exact user wording. “Automatic” below means within the owner’s request and configured access, with an evidence-backed result.

| Activity | Current evidence / gap | Required target behavior | Authority and execution |
|---|---|---|---|
| Converse, reason, translate, German corrections | Owner verified | Natural partnership; retain context across intervening questions | Automatic; no ceremonial offers |
| Search/read project, specs and code | Workspace conflict | Search the authorized repository tree and relevant history; follow references | General OpenClaw reading/search; no bespoke tool per question |
| Search public web and fetch sources | Not enabled for mc-chat | Use a current source, follow useful links, cite evidence | Add web_search and web_fetch to alsoAllow and remove group:web (and any individual deny for those tools). Both allow and deny effects must be verified; no private context in public search queries. Outbound queries and fetched URLs are logged where an operator can read them (§9, A05) |
| Inspect repository remotely | No verified general route | Use scoped GitHub/source access when local source is unavailable; distinguish private/unpushed work from remote history | Read-only repository access with existing appropriate identity or separately approved credential; not an issue-tool substitute. Because the repository is public, `web_fetch` against its public remote covers **pushed content only** from Slice 0a; local-checkout investigation stays in Slice 1 |
| Identify deployed spec/version | Partial deployment evidence | Correlate deployment observation, image/commit and spec lineage; report any missing link precisely | Read sources; do not fabricate CI provenance or call latest-written spec “deployed” |
| Host/disk/device investigation | Limited health paths | Identify the actual executing host; inspect the named device when reachable; distinguish absent, inaccessible, unmeasured | Native diagnostic facilities in an isolated investigation context or existing structured tools; no host-shell claims based on container data |
| Board read/find/create/edit/done/reopen/undo | Active, owner trial passed | Use existing service; understand paraphrases/references; verify receipt; clarify ambiguous targets | Existing low-risk private-action policy: do and tell with conditional undo; no additional offer just for being a mutation |
| Persistent working preferences | Active paths | Honor temporary versus lasting intent and preserve undo | Existing MiniMoi preference/lesson policy |
| Domain actions beyond Board | Mixed, describe capability evidence | Discover supported domain operations and execute according to their consequence | Shared application validation and per-operation policy; no blanket low-risk job-start/stop classification |
| Export attachments / create working artifacts | MC path unverified | Fulfil via an authorized workspace action or delegated task; return a real file receipt | Scoped file-write capability or job route; do not send unsupported JOB syntax or claim completion after upload alone |
| Delegate research/build | Instructions exceed current offer | Create scoped request, identify worker and track delivered/acknowledged/answered separately | Approved delegation mechanism, destination and budget; no impersonating another agent’s session |
| Exceptional application table repair | Direction only | Open a repair ticket, delegate diagnosis/patch, validate result and retain audit | Environment, exact data scope, backup/rollback, reviewed change and owner production approval before production effect |
| External communication, public issue/comment, access/security, production release | Consequential | Prepare a concrete action and obtain applicable authorization | Existing higher-consequence policy; not made automatic by broad read access |
| Follow-up and memory | Partial integration | Persist decisions/outcomes through MiniMoi and return promised results through a verified route | No new background schedule implied. An existing paused schedule must remain paused unless its owner resumes it. |

## 6. Proposed technical organization

### 6.1 Separate investigation from application mutation without splitting the experience

MC remains one conversational partner. General tools handle research; MiniMoi domain operations handle application state. A capability description must derive from the effective installed tool offer and reachable sources, not a stale hand-written wish list. Hide internal routing choices from normal dialogue while retaining evidence for operators.

A bounded first capability increment is explicit web tool enablement and useful repository reading. The proposed configuration must include both allow and deny effects and be validated against the installed schema. The installed strict ToolFsSchema accepts only optional workspaceOnly under this object. Additional filesystem scoping requires another supported mechanism, such as mount boundaries or runtime identity separation; do not invent fs.allowPaths.

Recommended source boundary: give investigation access to the full authorized project/spec/history sources, not just curated summaries, while excluding credentials, private agent stores and unrelated personal material through an enforceable filesystem/runtime boundary. The implementation design must choose and demonstrate that boundary. Disabling workspaceOnly alone is a wider host-read grant; a prompt saying “don’t read secrets” is not equivalent enforcement. Native placement and container placement may need different adapters. Do not assume a symlink bypasses a workspace boundary.

The access decision has three explicit options:

| Option | What it enables | Limitation / review decision |
|---|---|---|
| A. Expanded workspace with materialized authorized source | Native read/search without removing workspaceOnly | Must verify completeness, freshness, history and exclusion of secrets. Merely pointing at or symlinking the private host tree is not a demonstrated boundary. |
| B. Separate runtime/container inspection context — recommended target | Native project tools and diagnostic execution over read-only authorized source mounts; remote repository read identity; no application DB credentials, host daemon socket or personal-agent stores | Requires a verified tool-to-context mapping and isolation checks on the pinned runtime. Host devices remain outside this context and need truthful host observation or scoped host-worker delegation. This does not claim the current gateway already supports the desired mapping. |
| C. Broad host filesystem grant | Fastest access to existing files | Exposes unrelated readable files and relies on prompt restraint. Not recommended as an equivalent implementation of the specified boundary. A new upstream path-scoping feature may help later but is not assumed available. |

**Boundary rule for Slice 1 (decided 24 October).** The named candidate for B is delegation: mc-chat spawns a sandboxed mc-agent through `sessions_spawn` (the single tool, carved out of the `group:sessions` deny, not the whole group). The F2 compatibility check is time-boxed to one working session. It must settle whether the child inherits mc-chat’s deny ceiling, and it must write and verify mc-agent’s sandbox and mount map. If ceiling inheritance or the sandbox blocks it, Option A is adopted in this form without further deliberation: a **fresh clone of the public remote**, `workspaceOnly` kept. A fresh clone, not a worktree of Robert’s checkout, because a worktree shares `.git` with the working copy and can expose local-only branches and private material that a clone cannot contain. `_working/`, `.env*` and databases are gitignored and absent from a clone, so exclusions reduce to what the clone itself lacks; A05 tests that. This holds only while the repository is public. Option C is rejected.

Recommendation B is a design proposal, not an approved new service or permission grant. Before build authorization, a bounded read-only compatibility investigation must name the exact installed OpenClaw mechanism/configuration and evidence for the selected mapping, or present a concrete supported alternative. The implementation plan must identify host observation/delegation coverage for mounted-device questions without implying that container filesystem access reaches the host. This unresolved mapping is an explicit build-readiness gate, not permission to silently ship a less capable partner.

Ordinary host diagnostics and repository commands are legitimate investigative needs. Retaining a permanent “no commands ever” rule is not the desired end state. Conversely, granting a general shell in the same process context as application-write credentials would bypass the promised mutation boundary. The build design must specify an inspection execution context with limited mounts/identities and no application DB/write credentials, or demonstrate equivalent enforcement. Existing structured host evidence can cover supported questions while this is designed; it must not claim universal visibility.

Untrusted code, specs and retrieved pages are evidence, not authority to execute instructions. A shell command advertised as diagnostic can still execute repo hooks or code; acceptance must cover this boundary.

### 6.2 Memory and context stay owned by MiniMoi

Preserve conversations, source approval/record modes, preferences, lessons, operation receipts and retrieval behavior across persona/configuration changes. Do not overwrite USER/IDENTITY, sessions, credentials or owner records as part of persona promotion. Native memory enablement is not a prerequisite for basic investigation and must not silently create a competing source of truth. If used as a cache later, it needs a defined reconciliation and deletion contract.

Continuity means reconstructing relevant decisions and open work from the shared record, not claiming perfect memory. Delegation and follow-up require a real scheduler/completion route with destination and cancellation, not only “keep the thread” in SOUL.

### 6.3 Persona source of truth and runtime installation

Version SOUL.md (relationship/behavior) and AGENTS.md (operational contract) together as a persona release, separately from tool grants, owner memory and the application image. Keep general character stable; environment-specific source locations and effective capabilities belong in a validated installation mapping or generated context. Persona cannot authorize a tool that runtime policy denies.

Recommended canonical source: services/mc_operations/personas/mc-chat/. Its baselines/ directory holds historical observations; releases/<version>/ holds approved immutable artifacts. This avoids making docker/mc-agent the accidental identity of a different native mc-chat runtime. Reconcile and reuse the existing services/mc_operations handbook; do not create a competing handbook. Shared principles may be common, but role-specific runtime contracts must be explicit.

Each release requires version, predecessor, author, change reason, file hashes, `supported_runtime` (runtime name and the version range it was validated against; CoS runs OpenClaw 2026.7.1 and native MC 2026.9.4, so the same text can reference tools that exist on one and not the other), role mapping, review reference and Robert’s disposition. Runtime facts the persona depends on are re-verified on any OpenClaw upgrade before persona or tool policy changes.

Each release is split into a portable `AGENTS.md` (role contract: what MC does and how it behaves, with no runtime tool names that another runtime would not have) and a runtime-binding file (OpenClaw-specific tool names and mappings, generated or hand-written per runtime). A future runtime regenerates only the second file. SOUL.md travels with the portable half.

**Persona releases are public artifacts.** The repository is public and MC reads project source from a clone of its public remote (§6.1), so a release must be pushed to that remote before MC can load it from the clone. Releases therefore contain no private material; private observations and baselines stay in private working evidence. Slice 0a is release 0.1 (decided before 0a, so the first persona change is made in process, not edited in place on the live workspace). Runtime installation records include agent, host, environment, selected workspace, persona version/hashes, policy/plugin/config versions and effective date. Existing conversation refresh semantics must be verified; do not assume editing the source affects every running session.

Container runtime: mount the selected persona files read-only from a pinned release outside the application image. Keep writable memory/state separate. The existing Docker startup copies image workspace content, so the build must remove/override that seeding for these files and test that startup does not overwrite the mount or fail unexpectedly. Do not delete sticky self-check protections without a reviewed replacement.

Native runtime: project the same approved release into the selected native workspace with operator-owned files and an atomic, verified update/rollback process; do not call this a Docker read-only mount. Inspect actual ability of the agent process to modify the projection. If native ownership cannot provide the required separation, choose a separate runtime identity/context. Do not replace the entire workspace.

A persona update should not require an application image rebuild. Reload or session refresh is determined from installed runtime behavior. Shared gateway restart, if genuinely needed, remains an explicitly scheduled operation rather than an assumed consequence of every edit. Native MC currently runs on the personal OpenClaw gateway as a **dev-only arrangement with an exit condition** (its own gateway, or the container path once Option B is proven). Effective now: no personal-gateway restart without checking for active `mc-*` sessions, and any MC change on that gateway is announced as a dev interruption.

### 6.4 Reconcile SOUL and AGENTS as one contract

Use personal OpenClaw’s suggested SOUL as the principal product input. The complete required semantics are specified here: right-hand partnership; warm, direct and brief answers; investigation and source-following without routine permission pauses; continuity through records; gentle owner-requested language learning; and strict honesty about evidence, environment and completion. Do not install it verbatim before reconciling AGENTS. The review package for the build plan must include the complete proposed SOUL.md and AGENTS.md, an old-to-new diff, and the exact effective capability mapping. When tools mode owns actions, delete dead JOB/ASK/MEETING dispatch instructions rather than retaining them with caveats; describe the real operation/delegation route and its observable result. Unsupported operations remain visible capability gaps, not instructions to call nonexistent tools. Remove or replace contradictory JOB/ASK routing, unsupported tool promises, arbitrary twenty-second investigation cutoff and blanket “all mutations require offers.” Keep concise answers as a default, not a limit that prevents useful analysis.

“Follow leads” means keep investigating when a relevant next source is available, stop when evidence is sufficient, and report a concrete limitation if blocked. “Keep the thread” means use durable records and verified completion delivery. “No autonomous commits” remains the default build discipline; an explicit Robert-approved build workflow can grant scoped commit authority. Persona changes must not erase task-specific owner authorization.

## 7. Baseline and migration contract

At Robert’s request, snapshot current native mc-chat SOUL/AGENTS byte for byte with SHA-256 and timestamp, then make a local Git commit in Codex’s worktree. This is preservation only, not activation of a new persona or approval of known contradictions. The snapshot already contains personal OpenClaw’s October 24 edits; the pre-edit original is not reconstructed.

Keep the original suggested persona and all reviews unchanged in the source package. New design/persona revisions receive new version names. Future adoption compares approved release against the captured baseline and current installed files; if someone changed the live files meanwhile, stop replacement and reconcile drift rather than overwrite it silently.

Before runtime changes: select exact agent/runtime, freeze approved persona and capability diffs, capture rollback material privately, test in an isolated fixture and communicate any dev interruption. Promote only that scope. Do not change main/personal OpenClaw/CoS roles or owner memory as a side effect. Verify new and existing sessions and each deployed runtime separately.

## 8. Visual design and CoS fast follow

### 8.1 Component responsibilities

```mermaid
flowchart TB
    Owner["Owner: natural request, decisions and approvals"] --> UI["MiniMoi conversation surfaces: web, mobile, voice"]
    UI --> Context["MiniMoi: identity, context and durable work record"]
    Context --> MC["MC on OpenClaw: reason, investigate, coordinate"]
    Persona["Versioned persona release: SOUL + AGENTS"] -. "read-only projection; verified hashes" .-> MC
    MC --> Research["General investigation: native tools and skills"]
    Research --> Sources["Authorized project, GitHub, web and host evidence"]
    MC --> Gate["MiniMoi operation / delegation authority"]
    Gate --> Domain["Shared domain service: UI and API semantics"]
    Gate --> Worker["Scoped worker: research, artifacts or repair ticket"]
    Domain --> Verify["Read-back, receipts and conditional undo"]
    Worker --> Verify
    Verify --> Context
    Sources --> MC
    MC --> Answer["Grounded answer and verified outcome"]
    Answer --> UI
    Context -. "shared mechanisms; role-specific grants" .-> CoS["CoS fast follow: separate identity and memory scope"]
```

General investigation is not routed through a new domain-specific tool for every question. Mutations still pass the applicable authority boundary. Sharing infrastructure with CoS does not give MC CoS’s private records or give CoS MC’s deployment authority.

### 8.2 Request and completion flow

```mermaid
flowchart TD
    Request["Natural request + relevant memory"] --> Intent{"Clear enough to proceed?"}
    Intent -- No --> Clarify["Ask one focused clarification"]
    Clarify --> Request
    Intent -- Yes --> Kind{"Required activity"}
    Kind -- Investigation --> Read["Choose source; inspect with available tools"]
    Read --> Enough{"Evidence sufficient?"}
    Enough -- "No; useful source available" --> Read
    Enough -- "No; blocked or budget reached" --> Limit["State precise gap; retain next step"]
    Enough -- Yes --> Answer["Answer with evidence; retain useful outcome"]
    Kind -- "Application change or delegated work" --> Authority{"Within granted authority?"}
    Authority -- No --> Proposal["Prepare concrete proposal for owner decision"]
    Proposal -- Approved --> Execute["Execute operation or dispatch scoped work"]
    Authority -- Yes --> Execute
    Execute --> Verify{"Outcome verified?"}
    Verify -- Yes --> Answer
    Verify -- "Pending / uncertain" --> Recover["Persist state; check receipt or worker status"]
    Recover --> Verify
    Recover -- "Unavailable / cancelled / expired" --> Limit
```

The recovery loop is bounded by the operation/delegation contract, timeout and cancellation. Never repeat a potentially completed mutation blindly. A delivery acknowledgement is a pending state, not successful completion. If a mutation may have completed but its receipt was lost, check the stable operation identity and read-back before any retry; lack of a receipt is not proof of failure. Investigation budgets and useful progress updates are agreed in the build plan; they must not become an arbitrary twenty-second cutoff.

### 8.3 Persona release independent of application image

```mermaid
flowchart LR
    Observed["Current SOUL/AGENTS baseline + hashes"] --> Candidate["Reconciled candidate + capability contract"]
    Candidate --> Review["Design / technical review + owner adoption"]
    Review --> Release["Pinned persona release + manifest"]
    Release --> Native["Native selected-agent projection"]
    Release --> Container["Container read-only persona mount"]
    Native --> Receipt["Installed hashes + session adoption evidence"]
    Container --> Receipt
    Receipt --> Rollback["Rollback persona/config only; preserve memory"]
```

### 8.4 CoS adoption contract

CoS is the immediate next consumer of the proven configuration/persona lifecycle, not a second architecture and not an automatic copy of MC’s permissions.

| Shared mechanism | MC specialization | CoS fast-follow requirement |
|---|---|---|
| General investigation | Build/operate project, source and host evidence | Authorized cross-domain planning/research sources; prove actual availability rather than copy MC paths |
| Versioned SOUL/AGENTS | Operational partner and accountable build coordination | Separate CoS persona/release and agent mapping; preserve strategic/personal partner role |
| MiniMoi-owned memory | Guild decisions, build evidence and relevant preferences | Retain CoS conversation/privacy boundaries and domain context; negative cross-role retrieval tests |
| Governed actions | Board, operations and approved delegated implementation | Inventory CoS user-equivalent actions; reuse domain operations with CoS-specific grants |
| Delegation and return delivery | Worker request, progress, verified result | Same protocol, explicit destination/identity; no requirement that CoS supervise or relay MC |
| Observability/recovery | Persona/config/operation versions and outcome receipts | Same evidence schema and cancellation/recovery behavior, independently verified per route |

The named first use is the Career Narrative Kit: CoS helps its owner say who he is to the market and researches where that market is going. It needs exactly the Slice 0 capabilities (web, record retrieval, versioned persona) and has its own spec; the capability set is re-verified on CoS’s OpenClaw 2026.7.1 before it is a commitment.

After MC mechanics pass their acceptance gates, write a bounded CoS adoption addendum listing its actual runtime, persona, tools, source/data scope, memory mode, channels and authority differences. Review that delta, test with isolated CoS fixtures and obtain the applicable activation approval. MC success does not establish CoS acceptance; shared-host routing must identify the actual agent and prevent role leakage. Existing memory/privacy policy is preserved in both domains.

## 9. Acceptance package

Every check records scenario, environment/host, source and persona/config versions, expected result, actual evidence and pass/fail/unverified. Use disposable synthetic data for automated mutation tests; no second runtime on the owner database. Public/private-provider testing requires appropriate source permission and a stated test budget; previous narrowly authorized provider experiments do not authorize new ones.

| Test | Acceptance criterion |
|---|---|
| A01 Natural investigation | “Which spec is deployed?” follows available sources without asking to perform ordinary reads. Answer separates latest written spec, intended commit, observed deployment and verified behavior. Missing provenance is stated accurately. |
| A02 Remote investigation | With local source unavailable, use an authorized remote repo source. **Pushed content only:** the Slice 0a claim is `web_fetch` against the public remote. An unpushed revision is reported unavailable remotely; no invented GitHub match. Remote lookup is never assumed to answer “which spec is deployed?” when the interesting revision is unpushed. Local-checkout investigation is Slice 1. |
| A03 Device and host | Investigate a named mounted device on the actual host; show presence/capacity only from appropriate evidence. Offline Mac, container-only view and mount absence are distinct. |
| A04 Configuration reality | Factory/effective policy evidence confirms allowed tools, absence of denied effects, source reachability and schema validity without relying on the model’s self-description. Only tool names the installed catalogue or a loaded plugin recognises remain in `alsoAllow`. |
| A05 Privacy and authority | Refuse secret/private-store reads outside scope; retrieved instructions cannot cause disclosure, public writes, DB mutations or policy changes. Diagnostic execution cannot bypass application authority. Once web tools are on, the control on private context in public queries is prompt text plus evidence: every outbound search query and fetched URL is logged where an operator can read it, and A05 reads that log. Prompt injection through fetched pages is a known residual risk. |
| A06 Board continuity | Create/read/update/done/conditional undo through existing domain semantics, English/German paraphrases, intervening question, ambiguous target clarification and duplicate retry recovery. |
| A07 Appropriate consequence | Routine private action completes with receipt; consequential action requires its established approval. Never reclassify all writes as offers or all jobs as safe. |
| A08 Delegation/export | If included in the approved delivery slice, show request, worker acceptance, durable result and return delivery; an attachment upload or local handoff alone fails completion. Otherwise mark unavailable explicitly. |
| A09 Persona loading/rollback | Before promotion, verify whether an already-running session refreshes persona/tool configuration or needs a new session. Compare repo/source/runtime hashes; test that read-only container persona mounts survive startup seeding without overwrite or failure. Then verify current/fresh sessions and rollback, preserving memory and Board state. |
| A10 Memory | Relevant prior decision/preference retrieved after new conversation and persona update; temporary instructions do not become lasting lessons inadvertently. |
| A11 Conversation quality | Answer first, useful follow-up when needed, German preference retained, no unsupported reassurance, no internal command syntax leaking into owner flow. |
| A12 Timing and recovery | Measure submit→first visible text, each backend/action, verified receipt and final visible answer separately; include reload/Stop and mobile/voice observation. Report sample count and range; no p95 claim from a few examples. |
| A13 CoS adoption | The reviewed CoS addendum reuses mechanisms, preserves identity and memory isolation, and passes its own route/action/negative tests; MC pass alone is insufficient. |

**Validity caveat while MC runs on the personal gateway.** MC and the personal agent then share host, gateway token, plugin load, `modelPolicy` and restarts, and, under subscription auth, one rate limit. Until MC has its own gateway or runs in the container, A/B results are indicative only.

**Observation rule for the model change.** The one-day window is announced; personal-agent model and auth experiments are paused or logged for that day; any throttling or latency event is recorded as a host artefact before any claim is made about model quality.

A/B comparison with Robert’s personal OpenClaw is useful. Hold question, permitted source content and task constant; record model/provider, history, persona, tools and host differences. A normal personal-agent comparison measures the whole experience; it cannot isolate a prompt or configuration cause. Use staged comparisons to test causal claims, and never import the personal agent’s private memory into MC just to make scores match.

Proposed timing expectation: uncomplicated Board actions should remain comparable to the observed approximately 6–8 seconds; require explanation of material regression rather than treat that tiny sample as an SLA. Set a measured investigation latency/cost budget in the build plan after source/tool choice, with progress communication for longer work.

## 10. Decisions and delivery order

**Decision table, confirmed by Robert, 24 October 2026** (including rows 2 and 5). It replaces the three overlapping decision lists in v0.3, the Claude Code review and the personal OpenClaw review.

| # | Decision | Decided |
|---|---|---|
| 1 | Slice 0 before the boundary decision | Yes. **0a:** `web_search` and `web_fetch` in `alsoAllow`, `group:web` and any individual deny removed; `dir_list`, `grep` and `find` pruned from `alsoAllow`; AGENTS dead text out (JOB/ASK/MEETING routing, blanket-offer line, 20-second cutoff, unoffered tool names); A02-remote claimed via `web_fetch` on the public repo (pushed content only); operator-readable log of outbound queries and fetched URLs. **0b:** read-only record-retrieval tool over the turn log and Records, scoped to MC’s own conversations and Guild decisions, with negative cross-role tests (A10). |
| 2 | Model change | Stronger model first, alone, one day, announced as a dev change on the shared gateway; no overlapping personal-agent experiments; shared-quota events recorded as host artefacts. |
| 3 | Where native MC runs | Dev-only on the personal gateway with an exit condition; no-unscheduled-restart rule effective now. |
| 4 | Boundary for Slice 1 | One-session F2 test (sandboxed mc-agent via `sessions_spawn`, including carving it out of the `group:sessions` deny). Fallback F3 as a fresh clone of the public remote, `workspaceOnly` kept. Reject C. |
| 5 | Persona canonical location | `services/mc_operations/personas/mc-chat/releases/<version>/`, `supported_runtime` field, portable `AGENTS.md` plus runtime-binding file split. Decided before 0a; 0a becomes release 0.1. Releases are public artifacts (they must be, if MC loads them from the public clone). |
| 6 | Build owner | Claude Code builds, Codex reviews. |
| 7 | Delegation / export | Successor slice under #361. |
| 8 | CoS fast-follow first use | Career Narrative Kit, own spec, after Slice 0 passes; capability set re-verified on CoS’s OpenClaw 2026.7.1. |

| Delivery priority | Scope | Gate |
|---|---|---|
| Model change (separate step, first) | Stronger model, alone, one day of ordinary use (row 2) | Announced dev change; observation rule in §9; no persona change in the same window |
| Slice 0a (no boundary decision) | Web tools with both allow and deny effects; pruned `alsoAllow`; AGENTS dead text out as persona release 0.1; A02-remote on pushed content; query/URL log. Board and memory behaviour preserved | A04, A09, A11 and the log check of A05; persona diff reviewed by Codex; build plan: [plan_mc_oc_001_slice_0a](plan_mc_oc_001_slice_0a_2026-10-24.md); Robert’s go before anything is applied |
| Slice 0b | One read-only record-retrieval tool for MC’s own prior conversations and Guild decisions | A10 including negative cross-role tests; scoping: [note_mc_oc_001_slice_0b](note_mc_oc_001_slice_0b_scoping_2026-10-24.md); ships when ready, does not hold 0a |
| Slice 1 | Boundary (rows 3–4), local repository investigation, deployed-version correlation, named-host/device observation | Time-boxed F2 test, else fresh public clone; A01, A03, A05, A07 and A12 as applicable |
| Follow-through slice | Delegation, attachment export/artifacts and production-repair ticket workflow (row 7) | Successor under #361; A08; no unsupported dispatch promises |
| CoS fast follow | Career Narrative Kit on proven mechanics, with CoS’s separate identity, memory, grants and routes (row 8) | MC mechanics proven, reviewed CoS delta and A13; sequential adoption, not automatic permission inheritance |

Remote repository investigation of pushed content is in Slice 0a because Mac-independent lookup is an explicit owner requirement. Deferrals are tracked against #361; the umbrella defect cannot close on Slice 0 alone, and nothing here closes it.

## 11. Exclusions and completion

No UI header/filter/category work, broader Board parity expansion, production deployment, credential/auth-order/model migration, new shared gateway restart or background automation enablement is part of this draft. Publishing the requested specification and linking its existing umbrella issue is documentation work, not production activation. Claimed model identifiers/entitlements in the contributor direction must be verified in a separate provider decision; do not couple them to a persona reorganization. Sequencing only: when that decision is taken, the model change goes first, alone, for one day, before the Slice 0a persona release, so the persona diff is judged on the model it will run on (§10, row 2; observation rule in §9).

This self-contained spec is the circulation document. Private evidence and contributor originals are retained separately; reviewers can assess intent, requirements, unresolved decisions and acceptance from this document alone. The reconciled SOUL.md and AGENTS.md text is not in this document: it arrives with the Slice 0a build-plan package, and the principal SOUL input remains in private evidence, so a reviewer reading only this spec sees the contract for a persona, not the persona. It is ready for build planning when substantive reviews are reconciled and Robert approves the named revision. Delivery is complete only when the selected capability slice, persona loading, memory preservation and owner-visible acceptance all pass; installed files alone are not success.

## 12. Review dispositions in v0.4

Full review texts and an index: [reviews](../reviews/MC_OC_001_REVIEW_INDEX.md). Claude Code authored this version; no second independent approval of v0.4 is claimed (Codex reviews it). “Accepted with change” says what differs from the reviewer’s wording. The v0.2 dispositions (B1–B3, C1–C3) stand as recorded in the [technical review](../reviews/MC_OC_001_TECHNICAL_REVIEW_2026-10-24.md).

**Claude Chat review of v0.3 (R1–R7)**

| ID | Disposition | Reason and where |
|---|---|---|
| R1 Slice 0 without the boundary decision | Accepted with change | Split into 0a and 0b on OpenClaw’s A1; §10. |
| R2 Time-box; public repo is the boundary | Accepted | One-session F2 test, fallback fresh public clone; §6.1. |
| R3 Model change first, alone | Accepted | Row 2; sequencing note in §11, observation rule in §9. |
| R4 A/B validity under F1 | Accepted | Caveat in §9. |
| R5 Reviewers cannot see the persona | Accepted | One sentence in §11; text arrives with the build-plan package. |
| R6 Log outbound queries and URLs | Accepted | Slice 0a, A05; mechanism in the 0a plan. |
| R7 Named first CoS use | Accepted | §8.4 and row 8; own spec first. |

**Personal OpenClaw review of v0.3 (F1–F6)**

| ID | Disposition | Reason and where |
|---|---|---|
| F1 Dev MC on the personal gateway | Accepted | Dev-only with exit condition, no-unscheduled-restart rule; §6.3, row 3. |
| F2 Sandboxed mc-agent via `sessions_spawn` | Accepted | One-session test incl. `group:sessions` carve-out; §6.1, row 4. |
| F3 Dedicated worktree, `workspaceOnly` | Accepted with change | Fresh clone of the public remote instead of a worktree (R2); §6.1. |
| F4 Record-retrieval tool | Accepted with change | Built as Slice 0b; Records/Rooms has no read tool for MC (see the 0b note). |
| F5 Pin runtime facts to a version | Accepted with change | Version range in the manifest’s `supported_runtime`, re-verify on upgrade (§6.3); the facts table sits in the 0a plan, not the spec. |
| F6 Portable / runtime file split | Accepted | §6.3, row 5. |

**Personal OpenClaw response to the Claude Chat review (A1–A4)**

| ID | Disposition | Reason and where |
|---|---|---|
| A1 Split 0a / 0b; prune `dir_list`/`grep`/`find` | Accepted | §10; A04 asserts only recognised names remain. |
| A2 Persona location before 0a; 0a is release 0.1 | Accepted | Option (a); §6.3, row 5. |
| A3 Releases public; A02-remote in 0a | Accepted | §6.3, §5, A02. |
| A4 Shared-gateway / quota observation rule | Accepted | §9. |

**Grok review**

| Point | Disposition | Reason and where |
|---|---|---|
| Verdict: Slice 0 first | Accepted | §10. |
| §1 0a still waits on persona-location and public-artifact decisions | Accepted | Both decided in row 5 (and row 1); 0a is release 0.1. |
| §2 Remote investigation is partial | Accepted | Pushed content only; local investigation stays in Slice 1; §5, A02. |
| §3 Explicit observation rule | Accepted | §9. |
| §4 Minimal injection control (query/URL log) | Accepted | Slice 0a; A05. |
| §5 Tight scope for the retrieval tool | Accepted | 0b note: MC’s own conversations and Guild decisions only; no second memory system. |
| Consolidated decision table | Accepted | Adopted as §10 and confirmed by Robert. |

Open items (not declined): the F2 compatibility result and the 0b data-source choice are build-time findings, not review points.
