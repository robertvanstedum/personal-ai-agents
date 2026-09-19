# Operations, build sequence and acceptance

Revision 6 companion · 19 September 2026. See [publication authority](README.md).

## 1. Required operations

SQLite is an explicit mini-moi storage option with an accountable operator. Register backup/restore, export reconciliation and later central-sync monitoring as separate operational responsibilities. This document does not claim a scheduler or monitor is installed. The planning/operations role registers tasks; implementers do not independently rewrite the build queue or roadmap.

Inventory authoritative databases, source files, export bundles, required configuration/keys and uncertainty journals. Explain excluded caches and how dependencies are recovered. Use a supported consistent SQLite backup, not an uncontrolled copy of the live file. Keep encryption keys recoverable and separately protected. Snapshot completion, verified off-device arrival and successful restore are separate outcomes. A local cloud-sync folder does not prove upload.

Before valuable-data reliance, approve destination/access, RPO, RTO, retention, restore cadence, missed-job threshold and offline-host handling. Suggested policy for disposition: nightly copies and monthly restore exercises; no deletion or activation follows merely from this suggestion. Perform one off-device isolated restore before R1 preservation passes. Verify integrity, IDs, representative message/note/reference relationships, attachments, access recovery and export regeneration. Do not overwrite the live store or activate a restored copy as a second writer.

Observe scheduled due/start/end, latest consistent snapshot, confirmed remote arrival, last restore result, disk pressure, export pending/revision lag and later destination checkpoints. A crashed job cannot report itself; an out-of-host observer must detect missing evidence. A sleeping laptop is unavailable, not healthy. Test an actual metadata-only notification and recovery notice through the approved channel. Deduplicate alerts and never include transcript bodies or credentials.

Publish-on-change is a progress obligation, not a 60-second SLA. Make pending and failed export states visible; exercise crash/restart reconciliation and eventual publication while the service is available. Backups and sync need independent missed-job observation regardless of UI freshness labels.

Retention/redaction must account for DB records, artifacts, projections and backups. No automatic purge is introduced. State limits on recalling external copies.

## 2. Ordered drops

1. Register this exact documentation baseline through independent final-text review and owner disposition; preserve the supersession map. No broad architecture restart is required.
2. Reconcile the reviewed connector with the new route-based freshness policy; add negative tests. Preserve existing exact-build passes as historical evidence, not acceptance of modifications.
3. Implement schema/fixtures and stable origin/revision mapping; retain links, IDs, access isolation and old API compatibility where specified. Implement Markdown/JSON parity, automatic in-progress/final bundles and recovery together as a bounded reviewed drop.
4. Prove approved backup/restore and observed monitoring before valuable/private material relies on the system. Synthetic development before this is explicitly unprotected.
5. Complete reviewed dev CoS wiring, tool/privacy checks and owner-watched synthetic participation/briefing. Freeze the local R1 candidate and independently execute acceptance.
6. First integration follow-up: authorized one-way central read with approval gate, destination acknowledgment, coverage and age. Design it alongside local work once prerequisites are clear; do not defer it behind the harness. Production changes still require explicit deployment approval. This slice cannot post comments back into a room.
7. Separate subsequent drops: central authenticated writing and laptop-off proof; controlled write-authority transfer/recovery; graph portability demonstration; unattended coordination/full H1; additional workstations and optional open-source surfaces.

The graph demonstration remains in scope. Its first-release gating is unresolved: reviewers recommend the portability follow-up, while Robert previously approved the demonstration. Do not silently remove it or replace it with an unapproved mandatory PostgreSQL deployment. Record the scope disposition before marking any release complete.

The local release provides development participation, not production participation. The first central-read increment adds production-readable authorized evidence, not production room writing. Each report declares delivered/absent capabilities; no date or guaranteed number of releases is invented.

## 3. R1 test contract

| Case | Required evidence |
|---|---|
| R1-01 Capture | Quiet rooms start no capture/agents; explicit acknowledged sessions retain accepted contributions; pause rejects new discussion but preserves history; closure terminal; ordinary CoS conversation excluded |
| R1-02 Durability | Concurrent authorized appends do not clobber; same request does not duplicate; conflicting reuse rejected; restart preserves history; uncertainty reconciled without blind effect replay |
| R1-03 Access/identity | Submitter/speaker/agent distinctions preserved; sibling/revoked access denied across reads, search, export and receipts; displayed agent name is not runtime verification |
| R1-04 Artifacts | Schema-valid JSON and safely rendered multiline Markdown match IDs/order/text/notes/references from one snapshot; automatic in-progress/final bundles valid; timestamps/fallbacks/coverage visible; no credentials/base64 source archive |
| R1-05 Recovery | Failure before/after closure, render and publication never exposes a half-bundle as complete; last good version survives; startup repairs open/paused/closed pending revisions; no export-event loop; no fabricated freshness guarantee |
| R1-06 Portability | JSON schema and explicit mapping reviewed; fixture IDs/links preserved. Isolated PostgreSQL importer, when delivered, proves duplicate no-op, conflict rejection and no rollback. Unbuilt central import is explicitly deferred, not passed. Neo4j gating follows the recorded scope disposition |
| R1-07 Notes | Attributed build/reasoning notes cite exact coverage and artifact versions; later notes create identified bundle revision without changing transcript; formal Work disposition remains separate |
| R1-08 Preservation | One verified off-device application recovery, including required related material, plus observed missed-job/failure and recovery notification. A local copy or configured script alone fails |
| R1-09 Participation | Actual configured dev CoS responds and briefs with source links on an authorized watched synthetic request; absent agents not impersonated; record route and evidence assurance; private forwarding stays gated |
| R1-10 Freshness/effects | Permissive server routes record earlier source coverage honestly; strict/unknown kinds reject stale state; revoked/paused/closed writes denied; a stale message asking for an action produces text only, never executes it |

Maintain native UI regression coverage across the API behavior it exposes; adding another surface cannot silently remove the fallback. CLI/API tests remain valid complementary consumers. Fixtures/model doubles test contracts only, not real-agent participation.

## 4. Independent acceptance and release gates

Freeze source, dependencies, configuration and test identifiers. Independent reviewer inspects actual diff and runs a clean copy, without author patches during review. Findings produce a new candidate. Report passed/failed/blocked/not-run/deferred separately; owner accepts or rejects. Do not aggregate a deferred importer or graph demonstration into an all-pass claim.

Full H1 is retained as the later harness contract, not certified by R1. No simulated participant, gateway-only acknowledgment or development route satisfies production participation. Published docs, internal test counts and review concurrence are not deployment authorization.

Before activation: owner operational parameters, effective private-runtime controls, source-transfer approvals and any production change authorization. Before final local release disposition: settle graph gating explicitly. These are operational/scope gates, not a reason to leave the current direction ambiguous.
